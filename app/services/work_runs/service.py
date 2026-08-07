from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.work_run_settings import WorkRunDeploymentGate
from app.db.models import (
    AppUser,
    Artifact,
    ArtifactSource,
    ChatFolder,
    Conversation,
    RequestLedger,
    State,
    UserDocument,
    WorkRun,
    WorkRunPolicy,
    utcnow_naive,
)
from app.r2.private_artifacts import (
    build_artifact_key,
    get_private_artifacts_bucket,
    presign_artifact_download,
)
from app.r2.private_documents import (
    PrivateDocumentStorageConfigurationError,
    download_document_source,
)
from app.redis.event_bus import RedisEventBus
from app.schemas.work_runs import (
    ArtifactDownloadResponse,
    ArtifactResponse,
    CreateWorkRunRequest,
    WorkRunCapabilitiesResponse,
    WorkRunResponse,
)
from app.services.work_runs.comparison import (
    load_source_tables,
    render_comparison_workbook,
    validate_rendered_workbook,
)
from app.services.work_runs.contracts import (
    WorkRunErrorCode,
    WorkRunKind,
    WorkRunStatus,
    get_work_run_definition,
    list_work_run_definitions,
)
from app.services.work_runs.telemetry import (
    record_artifact_download,
    record_work_run_event,
    record_work_run_retry,
)


_ACTIVE_STATUSES = (
    WorkRunStatus.ACCEPTED.value,
    WorkRunStatus.RESERVED.value,
    WorkRunStatus.QUEUED.value,
    WorkRunStatus.RUNNING.value,
    WorkRunStatus.VALIDATING.value,
    WorkRunStatus.STORING.value,
    WorkRunStatus.CANCELLING.value,
)
_TERMINAL_STATUSES = (
    WorkRunStatus.SUCCEEDED.value,
    WorkRunStatus.FAILED.value,
    WorkRunStatus.CANCELLED.value,
    WorkRunStatus.REFUNDED.value,
)
_ARTIFACT_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ARTIFACT_STORAGE_TIMEOUT_SECONDS = 90
_ARTIFACT_STORAGE_CALL_TIMEOUT_SECONDS = 100
_EVENT_PUBLISH_TIMEOUT_SECONDS = 5
logger = logging.getLogger(__name__)


def _artifact_storage_identity(
    artifact: Artifact,
    *,
    rendered_size_bytes: int,
    rendered_sha256: str,
) -> tuple[int, str]:
    if artifact.sha256:
        return artifact.size_bytes, artifact.sha256
    return rendered_size_bytes, rendered_sha256


def _consume_publish_task_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception("work-run progress publication failed")


def _consume_storage_task_result(
    task: asyncio.Task[subprocess.CompletedProcess[bytes]],
) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception("detached artifact storage process failed")


def _run_artifact_storage_process(
    command: list[str],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=_ARTIFACT_STORAGE_TIMEOUT_SECONDS,
        check=False,
    )


async def _store_artifact_in_subprocess(
    *,
    bucket: str,
    key: str,
    output_path: Path,
    rendered_size_bytes: int,
    rendered_sha256: str,
    stored_size_bytes: int,
    stored_sha256: str,
) -> bool:
    command = [
        sys.executable,
        "-m",
        "app.services.work_runs.artifact_storage_process",
        "--bucket",
        bucket,
        "--key",
        key,
        "--path",
        str(output_path),
        "--rendered-size",
        str(rendered_size_bytes),
        "--rendered-sha256",
        rendered_sha256,
        "--stored-size",
        str(stored_size_bytes),
        "--stored-sha256",
        stored_sha256,
    ]
    storage_task = asyncio.create_task(
        asyncio.to_thread(_run_artifact_storage_process, command)
    )
    done, _ = await asyncio.wait(
        {storage_task},
        timeout=_ARTIFACT_STORAGE_CALL_TIMEOUT_SECONDS,
    )
    if storage_task not in done:
        storage_task.cancel()
        storage_task.add_done_callback(_consume_storage_task_result)
        raise TimeoutError("artifact storage did not complete before the deadline")

    try:
        result = await storage_task
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "artifact storage did not complete before the deadline"
        ) from exc

    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()[:1000]
        raise RuntimeError(
            f"artifact storage subprocess failed: {detail or result.returncode}"
        )
    try:
        payload = json.loads(result.stdout.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "artifact storage subprocess returned invalid output"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("uploaded"), bool):
        raise RuntimeError("artifact storage subprocess returned an invalid result")
    return payload["uploaded"]


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def _gate() -> WorkRunDeploymentGate:
    return WorkRunDeploymentGate.from_env()


def _work_error(code: WorkRunErrorCode, http_status: int) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"error_code": code.value})


async def _policy(session: AsyncSession, kind: WorkRunKind) -> WorkRunPolicy | None:
    return (
        await session.exec(
            select(WorkRunPolicy).where(WorkRunPolicy.kind == kind.value)
        )
    ).first()


def _private_storage_ready() -> bool:
    try:
        get_private_artifacts_bucket()
    except PrivateDocumentStorageConfigurationError:
        return False
    return True


async def capabilities(
    session: AsyncSession,
    user: AppUser,
) -> WorkRunCapabilitiesResponse:
    gate = _gate()
    if not gate.master_enabled:
        return WorkRunCapabilitiesResponse(
            enabled=False,
            available_kinds=[],
            max_active_per_user=0,
            monthly_allowance_per_user=0,
            unavailable_reason=WorkRunErrorCode.DISABLED,
        )
    if user.id not in gate.beta_allowed_user_ids:
        return WorkRunCapabilitiesResponse(
            enabled=False,
            available_kinds=[],
            max_active_per_user=0,
            monthly_allowance_per_user=0,
            unavailable_reason=WorkRunErrorCode.USER_NOT_ALLOWED,
        )
    if not _private_storage_ready():
        return WorkRunCapabilitiesResponse(
            enabled=False,
            available_kinds=[],
            max_active_per_user=0,
            monthly_allowance_per_user=0,
            unavailable_reason=WorkRunErrorCode.STORAGE_FAILED,
        )

    policies = (
        await session.exec(select(WorkRunPolicy).where(WorkRunPolicy.enabled.is_(True)))
    ).all()
    policy_by_kind = {policy.kind: policy for policy in policies}
    available = [
        definition.kind
        for definition in list_work_run_definitions()
        if definition.kind.value in policy_by_kind
    ]
    selected = policy_by_kind.get(available[0].value) if available else None
    return WorkRunCapabilitiesResponse(
        enabled=bool(available),
        available_kinds=available,
        max_active_per_user=selected.max_active_per_user if selected else 0,
        monthly_allowance_per_user=(
            selected.monthly_allowance_per_user if selected else 0
        ),
        unavailable_reason=None if available else WorkRunErrorCode.DISABLED,
    )


async def create_run(
    *,
    session: AsyncSession,
    user: AppUser,
    request: CreateWorkRunRequest,
    client_request_id: str,
    retry_of_work_run_id: uuid.UUID | None = None,
) -> WorkRun:
    existing = (
        await session.exec(
            select(WorkRun).where(
                WorkRun.user_id == user.id,
                WorkRun.client_request_id == client_request_id,
            )
        )
    ).first()
    if existing:
        return existing

    gate = _gate()
    if not gate.master_enabled:
        raise _work_error(WorkRunErrorCode.DISABLED, status.HTTP_404_NOT_FOUND)
    if user.id not in gate.beta_allowed_user_ids:
        raise _work_error(WorkRunErrorCode.USER_NOT_ALLOWED, status.HTTP_403_FORBIDDEN)
    if not _private_storage_ready():
        raise _work_error(
            WorkRunErrorCode.STORAGE_FAILED, status.HTTP_503_SERVICE_UNAVAILABLE
        )
    policy = await _policy(session, request.kind)
    if policy is None or not policy.enabled:
        raise _work_error(
            WorkRunErrorCode.KIND_NOT_SUPPORTED, status.HTTP_404_NOT_FOUND
        )

    active_count = (
        await session.exec(
            select(func.count())
            .select_from(WorkRun)
            .where(
                WorkRun.user_id == user.id, col(WorkRun.status).in_(_ACTIVE_STATUSES)
            )
        )
    ).one()
    if active_count >= policy.max_active_per_user:
        raise _work_error(
            WorkRunErrorCode.ACTIVE_LIMIT_REACHED, status.HTTP_409_CONFLICT
        )

    monthly_count = (
        await session.exec(
            select(func.count())
            .select_from(RequestLedger)
            .where(
                RequestLedger.user_id == user.id,
                RequestLedger.feature == "work",
                col(RequestLedger.state).in_((State.reserved, State.consumed)),
                RequestLedger.created_at >= _month_start(),
            )
        )
    ).one()
    if monthly_count >= policy.monthly_allowance_per_user:
        raise _work_error(
            WorkRunErrorCode.MONTHLY_ALLOWANCE_EXHAUSTED,
            status.HTTP_402_PAYMENT_REQUIRED,
        )

    definition = get_work_run_definition(request.kind)
    documents = (
        await session.exec(
            select(UserDocument).where(
                UserDocument.user_id == user.id,
                col(UserDocument.id).in_(request.document_ids),
                UserDocument.deleted_at.is_(None),
            )
        )
    ).all()
    if len(documents) != len(request.document_ids):
        raise _work_error(
            WorkRunErrorCode.INVALID_INPUT, status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    documents_by_id = {document.id: document for document in documents}
    for document_id in request.document_ids:
        document = documents_by_id[document_id]
        extension = Path(document.filename).suffix.lower()
        if (
            document.status != "ready"
            or extension not in definition.accepted_extensions
            or document.source_storage_status != "stored"
            or not document.source_bucket
            or not document.source_storage_key
        ):
            raise _work_error(
                WorkRunErrorCode.DOCUMENTS_NOT_READY,
                status.HTTP_409_CONFLICT,
            )

    if request.conversation_id:
        conversation = (
            await session.exec(
                select(Conversation).where(
                    Conversation.id == request.conversation_id,
                    Conversation.user_id == user.id,
                )
            )
        ).first()
        if conversation is None:
            raise _work_error(
                WorkRunErrorCode.INVALID_INPUT, status.HTTP_422_UNPROCESSABLE_ENTITY
            )
    if request.folder_id:
        folder = (
            await session.exec(
                select(ChatFolder).where(
                    ChatFolder.id == request.folder_id,
                    ChatFolder.user_id == user.id,
                )
            )
        ).first()
        if folder is None:
            raise _work_error(
                WorkRunErrorCode.INVALID_INPUT, status.HTTP_422_UNPROCESSABLE_ENTITY
            )

    run_id = uuid.uuid4()
    ledger = RequestLedger(
        id=uuid.uuid4(),
        user_id=user.id,
        conversation_id=request.conversation_id,
        request_id=f"work:{client_request_id}",
        model_name=f"work:{request.kind.value}",
        feature="work",
        cost=1.0,
        access_path="work_run",
        workflow_kind=request.kind.value,
        state=State.reserved,
    )
    input_manifest = {
        "document_ids": [str(document_id) for document_id in request.document_ids]
    }
    if retry_of_work_run_id is not None:
        input_manifest["retry_of_work_run_id"] = str(retry_of_work_run_id)
    run = WorkRun(
        id=run_id,
        user_id=user.id,
        conversation_id=request.conversation_id,
        folder_id=request.folder_id,
        request_ledger_id=ledger.id,
        kind=request.kind.value,
        kind_version=definition.version,
        status=WorkRunStatus.QUEUED.value,
        stage="waiting_for_worker",
        progress_percent=5,
        client_request_id=client_request_id,
        workflow_id=f"work:{run_id}",
        input_manifest=input_manifest,
        options=request.options.model_dump(mode="json"),
        instructions=request.instructions,
        reserved_units=Decimal("1"),
        queued_at=utcnow_naive(),
    )
    session.add(ledger)
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.exec(
                select(WorkRun).where(
                    WorkRun.user_id == user.id,
                    WorkRun.client_request_id == client_request_id,
                )
            )
        ).first()
        if existing:
            return existing
        raise
    await session.refresh(run)
    record_work_run_event(run, "work.queued")
    return run


async def retry_run(
    *,
    session: AsyncSession,
    user: AppUser,
    source: WorkRun,
    client_request_id: str,
) -> WorkRun:
    if source.status not in (
        WorkRunStatus.FAILED.value,
        WorkRunStatus.CANCELLED.value,
        WorkRunStatus.REFUNDED.value,
    ):
        raise _work_error(
            WorkRunErrorCode.RETRY_NOT_ALLOWED,
            status.HTTP_409_CONFLICT,
        )
    existing = (
        await session.exec(
            select(WorkRun).where(
                WorkRun.user_id == user.id,
                WorkRun.client_request_id == client_request_id,
            )
        )
    ).first()
    if existing:
        if existing.input_manifest.get("retry_of_work_run_id") != str(source.id):
            raise _work_error(
                WorkRunErrorCode.INVALID_INPUT,
                status.HTTP_409_CONFLICT,
            )
        return existing
    try:
        request = CreateWorkRunRequest.model_validate(
            {
                "kind": source.kind,
                "conversation_id": source.conversation_id,
                "folder_id": source.folder_id,
                "document_ids": source.input_manifest["document_ids"],
                "instructions": source.instructions,
                "options": source.options,
            }
        )
    except (KeyError, ValueError) as exc:
        raise _work_error(
            WorkRunErrorCode.INVALID_INPUT,
            status.HTTP_409_CONFLICT,
        ) from exc
    retried = await create_run(
        session=session,
        user=user,
        request=request,
        client_request_id=client_request_id,
        retry_of_work_run_id=source.id,
    )
    record_work_run_retry(source)
    return retried


async def owned_run(
    session: AsyncSession, user_id: uuid.UUID, run_id: uuid.UUID
) -> WorkRun:
    run = (
        await session.exec(
            select(WorkRun).where(WorkRun.id == run_id, WorkRun.user_id == user_id)
        )
    ).first()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="work_run_not_found"
        )
    return run


async def _artifacts(session: AsyncSession, run: WorkRun) -> list[Artifact]:
    return list(
        (
            await session.exec(
                select(Artifact)
                .where(Artifact.work_run_id == run.id, Artifact.deleted_at.is_(None))
                .order_by(Artifact.version)
            )
        ).all()
    )


def artifact_response(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        work_run_id=artifact.work_run_id,
        version=artifact.version,
        kind=artifact.kind,
        status=artifact.status,
        filename=artifact.filename,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata=artifact.artifact_metadata,
        created_at=artifact.created_at,
    )


async def run_response(session: AsyncSession, run: WorkRun) -> WorkRunResponse:
    artifacts = await _artifacts(session, run)
    retry_of_work_run_id = run.input_manifest.get("retry_of_work_run_id")
    return WorkRunResponse(
        id=run.id,
        kind=WorkRunKind(run.kind),
        kind_version=run.kind_version,
        status=WorkRunStatus(run.status),
        stage=run.stage,
        progress_percent=run.progress_percent,
        conversation_id=run.conversation_id,
        folder_id=run.folder_id,
        instructions=run.instructions,
        options=run.options,
        result_summary=run.result_summary,
        retry_of_work_run_id=(
            uuid.UUID(retry_of_work_run_id) if retry_of_work_run_id else None
        ),
        reserved_units=run.reserved_units,
        estimated_cost_usd=run.estimated_cost_usd,
        actual_cost_usd=run.actual_cost_usd,
        error_code=WorkRunErrorCode(run.error_code) if run.error_code else None,
        error_message=run.error_message,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cancelled_at=run.cancelled_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        artifacts=[artifact_response(artifact) for artifact in artifacts],
    )


async def cancel_run(session: AsyncSession, run: WorkRun) -> WorkRun:
    await session.refresh(
        run,
        attribute_names=[
            "status",
            "stage",
            "cancelled_at",
            "completed_at",
            "lease_expires_at",
        ],
        with_for_update=True,
    )
    if run.status in _TERMINAL_STATUSES:
        return run
    if run.status == WorkRunStatus.CANCELLING.value:
        return run
    if run.status in (
        WorkRunStatus.VALIDATING.value,
        WorkRunStatus.STORING.value,
    ):
        raise _work_error(
            WorkRunErrorCode.CANCEL_TOO_LATE,
            status.HTTP_409_CONFLICT,
        )
    now = utcnow_naive()
    ledger = await session.get(RequestLedger, run.request_ledger_id)
    if run.status in (
        WorkRunStatus.ACCEPTED.value,
        WorkRunStatus.RESERVED.value,
        WorkRunStatus.QUEUED.value,
    ):
        run.status = WorkRunStatus.CANCELLED.value
        run.stage = "cancelled"
        run.cancelled_at = now
        run.completed_at = now
        run.lease_expires_at = None
        if ledger and ledger.state == State.reserved:
            ledger.state = State.refunded
    elif run.status == WorkRunStatus.RUNNING.value:
        run.status = WorkRunStatus.CANCELLING.value
        run.stage = "cancelling"
        run.cancelled_at = now
    else:
        raise _work_error(
            WorkRunErrorCode.CANCEL_TOO_LATE,
            status.HTTP_409_CONFLICT,
        )
    session.add(run)
    if ledger:
        session.add(ledger)
    await session.commit()
    await session.refresh(run)
    record_work_run_event(
        run,
        "work.cancelled"
        if run.status == WorkRunStatus.CANCELLED.value
        else "work.cancelling",
    )
    return run


async def complete_cancellation(
    *, session: AsyncSession, redis: Redis, run: WorkRun
) -> None:
    now = utcnow_naive()
    run.status = WorkRunStatus.CANCELLED.value
    run.stage = "cancelled"
    run.cancelled_at = run.cancelled_at or now
    run.completed_at = now
    run.lease_expires_at = None
    ledger = await session.get(RequestLedger, run.request_ledger_id)
    if ledger and ledger.state == State.reserved:
        ledger.state = State.refunded
        session.add(ledger)
    session.add(run)
    await session.commit()
    await _publish(redis, run, "work.cancelled")


async def _cancel_if_requested(
    *, session: AsyncSession, redis: Redis, run: WorkRun
) -> bool:
    await session.refresh(run, attribute_names=["status", "cancelled_at"])
    if run.status in _TERMINAL_STATUSES:
        return run.status == WorkRunStatus.CANCELLED.value
    if run.status != WorkRunStatus.CANCELLING.value and run.cancelled_at is None:
        return False
    await complete_cancellation(session=session, redis=redis, run=run)
    return True


async def artifact_download(
    session: AsyncSession,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> ArtifactDownloadResponse:
    artifact = (
        await session.exec(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.user_id == user_id,
                Artifact.status == "ready",
                Artifact.deleted_at.is_(None),
            )
        )
    ).first()
    if artifact is None or not artifact.bucket or not artifact.storage_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="artifact_not_found"
        )
    expires = 900
    url = await presign_artifact_download(
        bucket=artifact.bucket,
        key=artifact.storage_key,
        filename=artifact.filename,
        expires=expires,
    )
    record_artifact_download(artifact)
    return ArtifactDownloadResponse(url=url, expires_in=expires)


async def _publish_with_deadline(
    redis: Redis,
    *,
    work_run_id: str,
    event_type: str,
    payload: dict[str, object],
) -> None:
    publish_task = asyncio.create_task(
        RedisEventBus(redis).publish_work(work_run_id, payload)
    )
    done, _ = await asyncio.wait(
        {publish_task},
        timeout=_EVENT_PUBLISH_TIMEOUT_SECONDS,
    )
    if publish_task in done:
        await publish_task
        return
    publish_task.cancel()
    publish_task.add_done_callback(_consume_publish_task_result)
    logger.warning(
        "work-run progress publication timed out",
        extra={"work_run_id": work_run_id, "event_type": event_type},
    )


async def _publish(redis: Redis, run: WorkRun, event_type: str) -> None:
    record_work_run_event(run, event_type)
    work_run_id = str(run.id)
    publish_task = asyncio.create_task(
        _publish_with_deadline(
            redis,
            work_run_id=work_run_id,
            event_type=event_type,
            payload={
                "type": event_type,
                "work_run_id": work_run_id,
                "status": run.status,
                "stage": run.stage,
                "progress_percent": run.progress_percent,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    )
    publish_task.add_done_callback(_consume_publish_task_result)


async def process_comparison_run(
    *, session: AsyncSession, redis: Redis, run: WorkRun, worker_id: str
) -> None:
    if run.status == WorkRunStatus.CANCELLING.value:
        await complete_cancellation(session=session, redis=redis, run=run)
        return
    run.worker_id = worker_id
    run.status = WorkRunStatus.RUNNING.value
    run.stage = "loading_sources"
    run.progress_percent = 15
    run.started_at = run.started_at or utcnow_naive()
    run.lease_expires_at = utcnow_naive() + timedelta(minutes=10)
    session.add(run)
    await session.commit()
    await _publish(redis, run, "work.stage")

    document_ids = [uuid.UUID(value) for value in run.input_manifest["document_ids"]]
    documents = (
        await session.exec(
            select(UserDocument).where(
                UserDocument.user_id == run.user_id,
                col(UserDocument.id).in_(document_ids),
                UserDocument.deleted_at.is_(None),
            )
        )
    ).all()
    documents_by_id = {document.id: document for document in documents}
    if len(documents_by_id) != len(document_ids):
        raise ValueError("one or more work-run source documents no longer exist")

    with tempfile.TemporaryDirectory(prefix=f"work-{run.id}-") as temp_dir:
        temp_path = Path(temp_dir)
        tables = []
        for index, document_id in enumerate(document_ids):
            document = documents_by_id[document_id]
            if not document.source_bucket or not document.source_storage_key:
                raise ValueError("source document is not stored")
            source_path = (
                temp_path / f"source-{index}{Path(document.filename).suffix.lower()}"
            )
            await download_document_source(
                bucket=document.source_bucket,
                key=document.source_storage_key,
                target_path=str(source_path),
            )
            tables.extend(
                load_source_tables(
                    document_id=document.id,
                    filename=document.filename,
                    path=source_path,
                )
            )

        if await _cancel_if_requested(session=session, redis=redis, run=run):
            return

        run.stage = "normalizing_data"
        run.progress_percent = 40
        run.lease_expires_at = utcnow_naive() + timedelta(minutes=10)
        session.add(run)
        await session.commit()
        await _publish(redis, run, "work.stage")

        run.stage = "rendering_artifact"
        run.progress_percent = 60
        session.add(run)
        await session.commit()
        await _publish(redis, run, "work.stage")
        output_path = temp_path / "offer-comparison.xlsx"
        rendered = render_comparison_workbook(
            tables=tuple(tables),
            target_path=output_path,
            language=run.options.get("output_language", "ru"),
            currency=run.options.get("currency"),
            instructions=run.instructions,
        )

        if await _cancel_if_requested(session=session, redis=redis, run=run):
            return

        run.status = WorkRunStatus.VALIDATING.value
        run.stage = "validating_artifact"
        run.progress_percent = 75
        session.add(run)
        await session.commit()
        await _publish(redis, run, "work.stage")
        validate_rendered_workbook(output_path)
        if await _cancel_if_requested(session=session, redis=redis, run=run):
            return
        rendered_size_bytes = output_path.stat().st_size
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        artifact_id = uuid.uuid5(run.id, "artifact-v1")
        artifact = await session.get(Artifact, artifact_id)
        if artifact is None:
            artifact = Artifact(
                id=artifact_id,
                work_run_id=run.id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
                folder_id=run.folder_id,
                version=1,
                kind="offer_comparison_xlsx",
                status="rendering",
                filename="offer-comparison.xlsx",
                mime_type=_ARTIFACT_MIME,
                size_bytes=rendered_size_bytes,
                sha256=sha256,
                artifact_metadata={
                    "rows": rendered.row_count,
                    "columns": rendered.column_count,
                    "source_count": len(rendered.sources),
                    "renderer_version": 1,
                },
            )
            session.add(artifact)
            await session.flush()
            for ordinal, source in enumerate(rendered.sources):
                session.add(
                    ArtifactSource(
                        artifact_id=artifact.id,
                        document_id=source.document_id,
                        title=source.filename,
                        sheet_name=source.sheet_name,
                        row_start=source.row_start,
                        row_end=source.row_end,
                        ordinal=ordinal,
                        provider_metadata={
                            "output_row_start": source.output_row_start,
                            "output_row_end": source.output_row_end,
                        },
                    )
                )
            await session.commit()

        run.status = WorkRunStatus.STORING.value
        run.stage = "storing_artifact"
        run.progress_percent = 90
        session.add(run)
        await session.commit()
        await _publish(redis, run, "work.stage")
        bucket = get_private_artifacts_bucket()
        key = build_artifact_key(
            user_id=run.user_id, work_run_id=run.id, artifact_id=artifact.id
        )
        stored_size_bytes, stored_sha256 = _artifact_storage_identity(
            artifact,
            rendered_size_bytes=rendered_size_bytes,
            rendered_sha256=sha256,
        )
        artifact_uploaded = await _store_artifact_in_subprocess(
            bucket=bucket,
            key=key,
            output_path=output_path,
            rendered_size_bytes=rendered_size_bytes,
            rendered_sha256=sha256,
            stored_size_bytes=stored_size_bytes,
            stored_sha256=stored_sha256,
        )
        if artifact_uploaded:
            artifact.size_bytes = rendered_size_bytes
            artifact.sha256 = sha256
        artifact.bucket = bucket
        artifact.storage_key = key
        artifact.status = "ready"
        run.status = WorkRunStatus.SUCCEEDED.value
        run.stage = "completed"
        run.progress_percent = 100
        run.result_summary = json.dumps(
            {
                "rows": rendered.row_count,
                "columns": rendered.column_count,
                "sources": len(rendered.sources),
            },
            separators=(",", ":"),
        )
        run.completed_at = utcnow_naive()
        run.lease_expires_at = None
        ledger = await session.get(RequestLedger, run.request_ledger_id)
        if ledger and ledger.state == State.reserved:
            ledger.state = State.consumed
            session.add(ledger)
        session.add(artifact)
        session.add(run)
        await session.commit()
        await _publish(redis, run, "artifact.ready")
        await _publish(redis, run, "work.done")


async def fail_run(
    *, session: AsyncSession, redis: Redis, run: WorkRun, error: Exception
) -> None:
    await session.refresh(run, attribute_names=["status", "cancelled_at"])
    if run.status == WorkRunStatus.CANCELLING.value or run.cancelled_at is not None:
        await complete_cancellation(session=session, redis=redis, run=run)
        return
    run.status = WorkRunStatus.FAILED.value
    run.stage = "failed"
    run.error_code = WorkRunErrorCode.INTERNAL_ERROR.value
    run.error_message = str(error)[:1000]
    run.completed_at = utcnow_naive()
    run.lease_expires_at = None
    ledger = await session.get(RequestLedger, run.request_ledger_id)
    if ledger and ledger.state == State.reserved:
        ledger.state = State.refunded
        session.add(ledger)
    session.add(run)
    await session.commit()
    await _publish(redis, run, "work.error")
