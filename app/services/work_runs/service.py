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
from typing import Callable

from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from pydantic import ValidationError
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
    ProviderOperation,
    RequestLedger,
    State,
    UserDocument,
    WorkRun,
    WorkRunActivityEvent,
    WorkRunPolicy,
    utcnow_naive,
)
from app.db.work_agent_models import WorkPlan, WorkThread, WorkThreadRun
from app.r2.private_artifacts import (
    build_artifact_key,
    build_artifact_preview_key,
    download_artifact_content,
    download_artifact_preview,
    get_private_artifacts_bucket,
)
from app.r2.private_documents import (
    PrivateDocumentStorageConfigurationError,
    download_document_source,
)
from app.redis.event_bus import RedisEventBus
from app.schemas.work_runs import (
    ArtifactDownloadResponse,
    ArtifactInlinePreviewResponse,
    ArtifactPreviewResponse,
    ArtifactResponse,
    ArtifactSourceResponse,
    CreateWorkRunRequest,
    ReviseArtifactRequest,
    SpreadsheetWorkRunResultSummary,
    WorkRunCapabilitiesResponse,
    WorkRunListResponse,
    WorkRunPlanResponse,
    WorkRunResponse,
)
from app.services.work_runs.activity import (
    activity_response,
    finish_active_activity_events,
    list_activity_events,
    record_activity_event,
)
from app.services.work_runs.comparison import (
    ComparisonColumnSchema,
    SourceTable,
    load_source_tables,
    render_comparison_workbook,
    validate_rendered_workbook,
)
from app.services.work_runs.artifact_delivery import (
    ARTIFACT_DELIVERY_TTL_SECONDS,
    ArtifactDisposition,
)
from app.services.work_runs.normalization import (
    ComparisonNormalizationResponseError,
    NORMALIZATION_MODEL,
    NormalizationUsage,
    estimate_comparison_normalization_usage,
    normalize_comparison_columns,
    normalization_usage,
    requires_model_normalization,
    restore_comparison_column_schema,
    serialize_comparison_column_schema,
)
from app.services.work_runs.contracts import (
    WorkRunErrorCode,
    WorkRunKind,
    WorkRunOutputFeature,
    WorkRunStatus,
    get_work_run_definition,
    list_work_run_definitions,
)
from app.services.work_runs.telemetry import (
    record_artifact_download,
    record_work_run_event,
    record_work_run_retry,
)
from app.services.pricing_service import PricingService


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
DEFAULT_WEB_SEARCH_CALL_COST_USD = Decimal("0.010000")
ArtifactDeliveryUrlBuilder = Callable[[Artifact, ArtifactDisposition], str]


class WorkRunExecutionError(RuntimeError):
    def __init__(self, code: WorkRunErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _artifact_storage_identity(
    artifact: Artifact,
    *,
    rendered_size_bytes: int,
    rendered_sha256: str,
) -> tuple[int, str]:
    if artifact.sha256:
        return artifact.size_bytes, artifact.sha256
    return rendered_size_bytes, rendered_sha256


def _preview_storage_identity(
    artifact: Artifact,
    *,
    rendered_size_bytes: int,
    rendered_sha256: str,
) -> tuple[int, str]:
    metadata = artifact.artifact_metadata or {}
    stored_size = metadata.get("_preview_size_bytes")
    stored_sha256 = metadata.get("_preview_sha256")
    if isinstance(stored_size, int) and isinstance(stored_sha256, str):
        return stored_size, stored_sha256
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
    preview_key: str,
    preview_path: Path,
    preview_rendered_size_bytes: int,
    preview_rendered_sha256: str,
    preview_stored_size_bytes: int,
    preview_stored_sha256: str,
) -> tuple[bool, bool]:
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
        "--preview-key",
        preview_key,
        "--preview-path",
        str(preview_path),
        "--preview-rendered-size",
        str(preview_rendered_size_bytes),
        "--preview-rendered-sha256",
        preview_rendered_sha256,
        "--preview-stored-size",
        str(preview_stored_size_bytes),
        "--preview-stored-sha256",
        preview_stored_sha256,
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
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("uploaded"), bool)
        or not isinstance(payload.get("preview_uploaded"), bool)
    ):
        raise RuntimeError("artifact storage subprocess returned an invalid result")
    return payload["uploaded"], payload["preview_uploaded"]


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).replace(tzinfo=None)


def _day_start() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).replace(
        tzinfo=None
    )


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
    definitions = list_work_run_definitions()
    available = [
        definition.kind
        for definition in definitions
        if definition.kind.value in policy_by_kind
    ]
    definitions_by_kind = {
        definition.kind: definition for definition in definitions
    }
    selected = policy_by_kind.get(available[0].value) if available else None
    return WorkRunCapabilitiesResponse(
        enabled=bool(available),
        available_kinds=available,
        max_active_per_user=selected.max_active_per_user if selected else 0,
        monthly_allowance_per_user=(
            selected.monthly_allowance_per_user if selected else 0
        ),
        unavailable_reason=None if available else WorkRunErrorCode.DISABLED,
        plans=[
            WorkRunPlanResponse(
                kind=kind,
                kind_version=definitions_by_kind[kind].version,
                min_documents=definitions_by_kind[kind].min_documents,
                max_documents=definitions_by_kind[kind].max_documents,
                steps=list(definitions_by_kind[kind].plan_steps),
            )
            for kind in available
        ],
    )


async def create_run(
    *,
    session: AsyncSession,
    user: AppUser,
    request: CreateWorkRunRequest,
    client_request_id: str,
    retry_of_work_run_id: uuid.UUID | None = None,
    revision_of_artifact_id: uuid.UUID | None = None,
    artifact_version: int = 1,
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
    if revision_of_artifact_id is not None:
        input_manifest["revision_of_artifact_id"] = str(revision_of_artifact_id)
        input_manifest["artifact_version"] = artifact_version
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


async def create_artifact_revision(
    *,
    session: AsyncSession,
    user: AppUser,
    artifact: Artifact,
    revision: ReviseArtifactRequest,
    client_request_id: str,
) -> WorkRun:
    source = await owned_run(session, user.id, artifact.work_run_id)
    if (
        source.status != WorkRunStatus.SUCCEEDED.value
        or artifact.status != "ready"
        or artifact.deleted_at is not None
    ):
        raise _work_error(
            WorkRunErrorCode.REVISION_NOT_ALLOWED,
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
        if existing.input_manifest.get("revision_of_artifact_id") != str(artifact.id):
            raise _work_error(
                WorkRunErrorCode.INVALID_INPUT,
                status.HTTP_409_CONFLICT,
            )
        return existing

    combined_instructions = revision.instructions
    if source.instructions:
        combined_instructions = (
            f"{source.instructions}\n\nRevision request:\n{revision.instructions}"
        )
    try:
        request = CreateWorkRunRequest.model_validate(
            {
                "kind": source.kind,
                "conversation_id": source.conversation_id,
                "folder_id": source.folder_id,
                "document_ids": source.input_manifest["document_ids"],
                "instructions": combined_instructions,
                "options": source.options,
            }
        )
    except (KeyError, ValueError) as exc:
        raise _work_error(
            WorkRunErrorCode.REVISION_NOT_ALLOWED,
            status.HTTP_409_CONFLICT,
        ) from exc

    return await create_run(
        session=session,
        user=user,
        request=request,
        client_request_id=client_request_id,
        revision_of_artifact_id=artifact.id,
        artifact_version=artifact.version + 1,
    )


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


async def owned_artifact(
    session: AsyncSession,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> Artifact:
    artifact = (
        await session.exec(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.user_id == user_id,
            )
        )
    ).first()
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="artifact_not_found",
        )
    return artifact


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


async def _artifact_sources(
    session: AsyncSession,
    artifacts: list[Artifact],
) -> dict[uuid.UUID, list[ArtifactSource]]:
    sources_by_artifact: dict[uuid.UUID, list[ArtifactSource]] = {
        artifact.id: [] for artifact in artifacts
    }
    if not artifacts:
        return sources_by_artifact
    sources = (
        await session.exec(
            select(ArtifactSource)
            .where(
                col(ArtifactSource.artifact_id).in_(
                    [artifact.id for artifact in artifacts]
                )
            )
            .order_by(ArtifactSource.artifact_id, ArtifactSource.ordinal)
        )
    ).all()
    for source in sources:
        sources_by_artifact[source.artifact_id].append(source)
    return sources_by_artifact


def artifact_response(
    artifact: Artifact,
    sources: list[ArtifactSource] | None = None,
) -> ArtifactResponse:
    public_metadata = {
        key: value
        for key, value in (artifact.artifact_metadata or {}).items()
        if not key.startswith("_")
    }
    return ArtifactResponse(
        id=artifact.id,
        work_run_id=artifact.work_run_id,
        parent_artifact_id=artifact.parent_artifact_id,
        version=artifact.version,
        kind=artifact.kind,
        status=artifact.status,
        filename=artifact.filename,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        metadata=public_metadata,
        sources=[
            ArtifactSourceResponse(
                document_id=source.document_id,
                title=source.title,
                sheet_name=source.sheet_name,
                row_start=source.row_start,
                row_end=source.row_end,
                ordinal=source.ordinal,
            )
            for source in (sources or [])
        ],
        created_at=artifact.created_at,
    )


async def run_response(session: AsyncSession, run: WorkRun) -> WorkRunResponse:
    artifacts = await _artifacts(session, run)
    sources_by_artifact = await _artifact_sources(session, artifacts)
    activity_events = await list_activity_events(session, run.id)
    return _run_response(run, artifacts, sources_by_artifact, activity_events)


def _run_response(
    run: WorkRun,
    artifacts: list[Artifact],
    sources_by_artifact: dict[uuid.UUID, list[ArtifactSource]] | None = None,
    activity_events: list[WorkRunActivityEvent] | None = None,
) -> WorkRunResponse:
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
        artifacts=[
            artifact_response(
                artifact,
                (sources_by_artifact or {}).get(artifact.id, []),
            )
            for artifact in artifacts
        ],
        activity_events=[
            activity_response(event) for event in (activity_events or [])
        ],
    )


async def list_run_responses(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    offset: int,
    limit: int,
) -> WorkRunListResponse:
    runs = list(
        (
            await session.exec(
                select(WorkRun)
                .where(WorkRun.user_id == user_id)
                .order_by(col(WorkRun.created_at).desc(), col(WorkRun.id).desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    has_more = len(runs) > limit
    runs = runs[:limit]
    if not runs:
        return WorkRunListResponse(items=[], offset=offset, limit=limit, has_more=False)

    artifacts = list(
        (
            await session.exec(
                select(Artifact)
                .where(
                    col(Artifact.work_run_id).in_([run.id for run in runs]),
                    Artifact.deleted_at.is_(None),
                )
                .order_by(Artifact.work_run_id, Artifact.version)
            )
        ).all()
    )
    artifacts_by_run: dict[uuid.UUID, list[Artifact]] = {run.id: [] for run in runs}
    for artifact in artifacts:
        artifacts_by_run[artifact.work_run_id].append(artifact)
    sources_by_artifact = await _artifact_sources(session, artifacts)

    return WorkRunListResponse(
        items=[
            _run_response(
                run,
                artifacts_by_run[run.id],
                sources_by_artifact,
            )
            for run in runs
        ],
        offset=offset,
        limit=limit,
        has_more=has_more,
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
    await finish_active_activity_events(session, run, status="cancelled")
    await record_activity_event(
        session,
        run,
        event_key="cancelled",
        kind="cancelled",
        status="cancelled",
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
    operations = (
        await session.exec(
            select(ProviderOperation).where(
                ProviderOperation.work_run_id == run.id,
                ProviderOperation.status == "running",
            )
        )
    ).all()
    for operation in operations:
        operation.status = "cancelled"
        operation.completed_at = now
        session.add(operation)
    await finish_active_activity_events(session, run, status="cancelled")
    activity_event = await record_activity_event(
        session,
        run,
        event_key="cancelled",
        kind="cancelled",
        status="cancelled",
    )
    await _set_linked_thread_status(session, run.id, "cancelled")
    session.add(run)
    await session.commit()
    await _publish(
        redis,
        run,
        "work.cancelled",
        activity_event=activity_event,
    )


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
    delivery_url: ArtifactDeliveryUrlBuilder,
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
    expires = ARTIFACT_DELIVERY_TTL_SECONDS
    url = delivery_url(artifact, "attachment")
    record_artifact_download(artifact)
    return ArtifactDownloadResponse(url=url, expires_in=expires)


async def artifact_inline_preview(
    session: AsyncSession,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    delivery_url: ArtifactDeliveryUrlBuilder,
) -> ArtifactInlinePreviewResponse:
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="artifact_preview_not_found",
        )
    preview_kind = (artifact.artifact_metadata or {}).get("preview_kind")
    if preview_kind in {"image", "pdf"}:
        expires = ARTIFACT_DELIVERY_TTL_SECONDS
        url = delivery_url(artifact, "inline")
        return ArtifactInlinePreviewResponse(
            kind=preview_kind,
            mime_type=artifact.mime_type,
            url=url,
            expires_in=expires,
        )
    if preview_kind == "text":
        try:
            payload = await download_artifact_content(
                bucket=artifact.bucket,
                key=artifact.storage_key,
            )
            content = payload.decode("utf-8-sig")
        except (ClientError, UnicodeDecodeError, ValueError) as exc:
            logger.exception(
                "artifact inline preview is invalid",
                extra={"artifact_id": artifact.id},
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="artifact_preview_invalid",
            ) from exc
        return ArtifactInlinePreviewResponse(
            kind="text",
            mime_type=artifact.mime_type,
            content=content,
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="artifact_preview_not_found",
    )


async def artifact_content(
    session: AsyncSession,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> Artifact:
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="artifact_not_found",
        )
    return artifact


async def artifact_preview(
    session: AsyncSession,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> ArtifactPreviewResponse:
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
    metadata = artifact.artifact_metadata if artifact is not None else {}
    if (
        artifact is None
        or not artifact.bucket
        or not artifact.storage_key
        or not metadata.get("preview_available")
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="artifact_preview_not_found",
        )

    try:
        payload = await download_artifact_preview(
            bucket=artifact.bucket,
            key=build_artifact_preview_key(artifact.storage_key),
        )
        decoded = json.loads(payload.decode("utf-8"))
        return ArtifactPreviewResponse.model_validate(decoded)
    except HTTPException:
        raise
    except (
        ClientError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        logger.exception(
            "artifact preview is invalid", extra={"artifact_id": artifact.id}
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="artifact_preview_invalid",
        ) from exc


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


async def _publish(
    redis: Redis,
    run: WorkRun,
    event_type: str,
    *,
    activity_event: WorkRunActivityEvent | None = None,
) -> None:
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
                **(
                    {"activity_event": activity_response(activity_event).model_dump(mode="json")}
                    if activity_event is not None
                    else {}
                ),
            },
        )
    )
    publish_task.add_done_callback(_consume_publish_task_result)


def _provider_failure_is_ambiguous(error: Exception) -> bool:
    if isinstance(error, ComparisonNormalizationResponseError):
        return False
    if type(error).__name__ in {"APIConnectionError", "APITimeoutError"}:
        return True
    status_code = getattr(error, "status_code", None)
    return isinstance(status_code, int) and status_code >= 500


def _provider_request_id(value: object) -> str | None:
    for attribute in ("_request_id", "request_id"):
        request_id = getattr(value, attribute, None)
        if isinstance(request_id, str) and request_id:
            return request_id
    response = getattr(value, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        request_id = headers.get("x-request-id") or headers.get("x-request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    return None


async def _normalization_cost(
    session: AsyncSession,
    *,
    model: str,
    usage: NormalizationUsage,
    web_search_calls: int = 0,
) -> tuple[Decimal, dict[str, object]]:
    pricing_service = PricingService(session)
    pricing = await pricing_service.get_pricing("openai", model)
    if pricing is None or pricing.currency.upper() != "USD":
        raise WorkRunExecutionError(
            WorkRunErrorCode.INTERNAL_ERROR,
            "normalization model USD pricing is unavailable",
        )
    cached_input_tokens = max(
        0,
        min(usage.cached_input_tokens, usage.input_tokens),
    )
    uncached_input_tokens = max(0, usage.input_tokens - cached_input_tokens)
    cached_input_rate = (
        pricing.unit_price_cached_input_per_1m
        if pricing.unit_price_cached_input_per_1m is not None
        else pricing.unit_price_input_per_1m
    )
    cost_input = pricing_service.cost_per_1m(
        pricing.unit_price_input_per_1m,
        uncached_input_tokens,
    )
    cost_cached_input = pricing_service.cost_per_1m(
        cached_input_rate,
        cached_input_tokens,
    )
    cost_output = pricing_service.cost_per_1m(
        pricing.unit_price_output_per_1m,
        usage.output_tokens,
    )
    cost_reasoning = pricing_service.cost_per_1m(
        pricing.unit_price_reasoning_per_1m,
        usage.reasoning_tokens,
    )
    configured_web_search_rate = Decimal(
        getattr(pricing, "unit_price_web_search_call", Decimal("0")) or 0
    )
    web_search_rate = (
        configured_web_search_rate
        if configured_web_search_rate > 0 or web_search_calls == 0
        else DEFAULT_WEB_SEARCH_CALL_COST_USD
    )
    cost_web_search = (
        web_search_rate * Decimal(max(0, web_search_calls))
    ).quantize(Decimal("0.000001"))
    total_cost = (
        cost_input
        + cost_cached_input
        + cost_output
        + cost_reasoning
        + cost_web_search
    ).quantize(Decimal("0.000001"))
    return total_cost, {
        "model": model,
        "currency": pricing.currency,
        "pricing_id": str(pricing.id),
        "unit_price_input_per_1m": str(pricing.unit_price_input_per_1m),
        "unit_price_cached_input_per_1m": str(cached_input_rate),
        "unit_price_output_per_1m": str(pricing.unit_price_output_per_1m),
        "unit_price_reasoning_per_1m": str(pricing.unit_price_reasoning_per_1m),
        "unit_price_web_search_call": str(web_search_rate),
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "web_search_calls": max(0, web_search_calls),
        "cost_input_usd": str(cost_input),
        "cost_cached_input_usd": str(cost_cached_input),
        "cost_output_usd": str(cost_output),
        "cost_reasoning_usd": str(cost_reasoning),
        "cost_web_search_usd": str(cost_web_search),
        "total_cost_usd": str(total_cost),
    }


async def _reserve_normalization_budget(
    session: AsyncSession,
    *,
    run: WorkRun,
    tables: tuple[SourceTable, ...],
) -> tuple[Decimal, dict[str, object]]:
    policy = (
        await session.exec(
            select(WorkRunPolicy)
            .where(WorkRunPolicy.kind == run.kind)
            .with_for_update()
        )
    ).first()
    if policy is None or not policy.enabled:
        raise WorkRunExecutionError(
            WorkRunErrorCode.DISABLED,
            "work-run policy is disabled",
        )
    estimated_usage = estimate_comparison_normalization_usage(
        tables=tables,
        instructions=run.instructions,
        currency=run.options.get("currency"),
        language=run.options.get("output_language", "ru"),
        desired_columns=tuple(run.options.get("desired_columns", ())),
    )
    estimated_cost, usage_payload = await _normalization_cost(
        session,
        model=NORMALIZATION_MODEL,
        usage=estimated_usage,
    )
    if estimated_cost <= 0:
        raise WorkRunExecutionError(
            WorkRunErrorCode.INTERNAL_ERROR,
            "normalization model pricing is unavailable",
        )
    if estimated_cost > policy.per_run_budget_usd:
        raise WorkRunExecutionError(
            WorkRunErrorCode.PER_RUN_BUDGET_EXCEEDED,
            "normalization estimate exceeds the per-run budget",
        )

    actual_today = (
        await session.exec(
            select(func.coalesce(func.sum(WorkRun.actual_cost_usd), 0)).where(
                WorkRun.created_at >= _day_start()
            )
        )
    ).one()
    planning_today = (
        await session.exec(
            select(func.coalesce(func.sum(WorkPlan.actual_cost_usd), 0)).where(
                WorkPlan.created_at >= _day_start()
            )
        )
    ).one()
    active_estimates = (
        await session.exec(
            select(func.coalesce(func.sum(WorkRun.estimated_cost_usd), 0)).where(
                WorkRun.created_at >= _day_start(),
                col(WorkRun.status).in_(_ACTIVE_STATUSES),
                WorkRun.id != run.id,
            )
        )
    ).one()
    ambiguous_estimates = (
        await session.exec(
            select(
                func.coalesce(func.sum(ProviderOperation.estimated_cost_usd), 0)
            ).where(
                ProviderOperation.created_at >= _day_start(),
                ProviderOperation.status == "ambiguous",
            )
        )
    ).one()
    projected_daily_cost = (
        Decimal(actual_today)
        + Decimal(planning_today)
        + Decimal(active_estimates)
        + Decimal(ambiguous_estimates)
        + estimated_cost
    )
    if projected_daily_cost > policy.global_daily_budget_usd:
        raise WorkRunExecutionError(
            WorkRunErrorCode.DAILY_BUDGET_EXCEEDED,
            "normalization estimate exceeds the daily work-run budget",
        )
    return estimated_cost, usage_payload


async def _normalize_columns_for_run(
    *,
    session: AsyncSession,
    run: WorkRun,
    tables: tuple[SourceTable, ...],
) -> ComparisonColumnSchema:
    desired_columns = tuple(run.options.get("desired_columns", ()))
    force_model = run.kind == WorkRunKind.SPREADSHEET_BUILDER_XLSX.value
    if not requires_model_normalization(
        tables,
        desired_columns=desired_columns,
        force=force_model,
    ):
        result = await normalize_comparison_columns(
            tables=tables,
            instructions=run.instructions,
            currency=run.options.get("currency"),
            language=run.options.get("output_language", "ru"),
            desired_columns=desired_columns,
            force=force_model,
        )
        return result.schema

    operation_key = "normalize-columns-v1"
    operation = (
        await session.exec(
            select(ProviderOperation).where(
                ProviderOperation.work_run_id == run.id,
                ProviderOperation.operation_key == operation_key,
            )
        )
    ).first()
    if operation is not None:
        stored = run.input_manifest.get("normalization_v1")
        if operation.status == "succeeded" and isinstance(stored, dict):
            return restore_comparison_column_schema(
                tables=tables,
                payload=stored.get("schema"),
            )
        raise WorkRunExecutionError(
            WorkRunErrorCode.PROVIDER_AMBIGUOUS,
            "normalization operation already exists without a reusable result",
        )

    estimated_cost, estimated_usage = await _reserve_normalization_budget(
        session,
        run=run,
        tables=tables,
    )

    operation = ProviderOperation(
        work_run_id=run.id,
        operation_key=operation_key,
        provider="openai",
        operation_kind=(
            "spreadsheet_column_normalization"
            if run.kind == WorkRunKind.SPREADSHEET_BUILDER_XLSX.value
            else "comparison_column_normalization"
        ),
        status="running",
        attempt_count=1,
        usage={**estimated_usage, "estimate": True},
        estimated_cost_usd=estimated_cost,
        started_at=utcnow_naive(),
    )
    run.estimated_cost_usd = estimated_cost
    session.add(run)
    session.add(operation)
    await session.commit()

    try:
        result = await normalize_comparison_columns(
            tables=tables,
            instructions=run.instructions,
            currency=run.options.get("currency"),
            language=run.options.get("output_language", "ru"),
            desired_columns=desired_columns,
            force=force_model,
        )
    except Exception as error:
        operation.provider_request_id = _provider_request_id(error)
        operation.completed_at = utcnow_naive()
        if isinstance(error, ComparisonNormalizationResponseError):
            response = error.response
            usage = normalization_usage(response)
            cost, usage_payload = await _normalization_cost(
                session,
                model=NORMALIZATION_MODEL,
                usage=usage,
            )
            operation.provider_response_id = getattr(response, "id", None)
            operation.provider_request_id = _provider_request_id(response)
            operation.usage = usage_payload
            operation.actual_cost_usd = cost
            run.actual_cost_usd += cost
            operation.status = "failed"
            operation.error_code = WorkRunErrorCode.VALIDATION_FAILED.value
            code = WorkRunErrorCode.VALIDATION_FAILED
        else:
            ambiguous = _provider_failure_is_ambiguous(error)
            operation.status = "ambiguous" if ambiguous else "failed"
            operation.error_code = (
                WorkRunErrorCode.PROVIDER_AMBIGUOUS.value
                if ambiguous
                else WorkRunErrorCode.PROVIDER_FAILED.value
            )
            code = (
                WorkRunErrorCode.PROVIDER_AMBIGUOUS
                if ambiguous
                else WorkRunErrorCode.PROVIDER_FAILED
            )
        session.add(operation)
        session.add(run)
        await session.commit()
        raise WorkRunExecutionError(code, str(error)) from error

    cost, usage_payload = await _normalization_cost(
        session,
        model=result.model,
        usage=result.usage,
    )
    operation.status = "succeeded"
    operation.provider_response_id = result.provider_response_id
    operation.provider_request_id = result.provider_request_id
    operation.usage = usage_payload
    operation.actual_cost_usd = cost
    operation.completed_at = utcnow_naive()
    run.actual_cost_usd += cost
    manifest = dict(run.input_manifest)
    manifest["normalization_v1"] = {
        "schema": serialize_comparison_column_schema(tables, result.schema),
        "provider": "openai",
        "model": result.model,
        "response_id": result.provider_response_id,
    }
    run.input_manifest = manifest
    session.add(operation)
    session.add(run)
    await session.commit()
    return result.schema


async def process_spreadsheet_run(
    *, session: AsyncSession, redis: Redis, run: WorkRun, worker_id: str
) -> None:
    try:
        run_kind = WorkRunKind(run.kind)
    except ValueError as exc:
        raise WorkRunExecutionError(
            WorkRunErrorCode.KIND_NOT_SUPPORTED,
            f"unsupported spreadsheet workflow kind: {run.kind}",
        ) from exc
    if run_kind not in {
        WorkRunKind.OFFER_COMPARISON_XLSX,
        WorkRunKind.SPREADSHEET_BUILDER_XLSX,
    }:
        raise WorkRunExecutionError(
            WorkRunErrorCode.KIND_NOT_SUPPORTED,
            f"unsupported spreadsheet workflow kind: {run.kind}",
        )
    definition = get_work_run_definition(run_kind)
    comparison_mode = run_kind == WorkRunKind.OFFER_COMPARISON_XLSX
    artifact_stem = "offer-comparison" if comparison_mode else "spreadsheet"
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

        column_schema = None
        if (comparison_mode and run.kind_version >= 2) or not comparison_mode:
            column_schema = await _normalize_columns_for_run(
                session=session,
                run=run,
                tables=tuple(tables),
            )

        if await _cancel_if_requested(session=session, redis=redis, run=run):
            return

        run.stage = "rendering_artifact"
        run.progress_percent = 60
        session.add(run)
        await session.commit()
        await _publish(redis, run, "work.stage")
        output_path = temp_path / f"{artifact_stem}.xlsx"
        rendered = render_comparison_workbook(
            tables=tuple(tables),
            target_path=output_path,
            language=run.options.get("output_language", "ru"),
            currency=run.options.get("currency"),
            instructions=run.instructions,
            column_schema=column_schema,
            comparison_mode=comparison_mode,
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
        preview = ArtifactPreviewResponse.model_validate(rendered.preview)
        preview_path = temp_path / f"{artifact_stem}.preview.json"
        preview_path.write_text(
            preview.model_dump_json(),
            encoding="utf-8",
        )
        preview_size_bytes = preview_path.stat().st_size
        preview_sha256 = hashlib.sha256(preview_path.read_bytes()).hexdigest()
        try:
            artifact_version = int(run.input_manifest.get("artifact_version", 1))
            revision_of_artifact_id = run.input_manifest.get("revision_of_artifact_id")
            parent_artifact_id = (
                uuid.UUID(revision_of_artifact_id) if revision_of_artifact_id else None
            )
        except (TypeError, ValueError) as exc:
            raise WorkRunExecutionError(
                WorkRunErrorCode.INVALID_INPUT,
                "invalid artifact revision lineage",
            ) from exc
        if artifact_version < 1:
            raise WorkRunExecutionError(
                WorkRunErrorCode.INVALID_INPUT,
                "invalid artifact version",
            )
        artifact_id = uuid.uuid5(run.id, f"artifact-v{artifact_version}")
        artifact = await session.get(Artifact, artifact_id)
        artifact_was_preview_capable = bool(
            artifact and (artifact.artifact_metadata or {}).get("preview_available")
        )
        artifact_metadata = {
            "rows": rendered.row_count,
            "columns": rendered.column_count,
            "source_count": len(rendered.sources),
            "renderer_version": 2,
            "preview_available": True,
            "preview_version": preview.version,
            "preview_rows": len(preview.rows),
            "preview_columns": len(preview.columns),
            "_preview_size_bytes": preview_size_bytes,
            "_preview_sha256": preview_sha256,
            "normalization_version": (
                1
                if (comparison_mode and run.kind_version >= 2) or not comparison_mode
                else 0
            ),
            "normalization_mode": (
                "model" if "normalization_v1" in run.input_manifest else "exact"
            ),
            "revision_of_artifact_id": (
                str(parent_artifact_id) if parent_artifact_id else None
            ),
        }
        if artifact is None:
            artifact = Artifact(
                id=artifact_id,
                work_run_id=run.id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
                folder_id=run.folder_id,
                parent_artifact_id=parent_artifact_id,
                version=artifact_version,
                kind=definition.artifact_kind,
                status="rendering",
                filename=(
                    f"{artifact_stem}.xlsx"
                    if artifact_version == 1
                    else f"{artifact_stem}-v{artifact_version}.xlsx"
                ),
                mime_type=_ARTIFACT_MIME,
                size_bytes=rendered_size_bytes,
                sha256=sha256,
                artifact_metadata=artifact_metadata,
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
        else:
            artifact.artifact_metadata = artifact_metadata
            session.add(artifact)
            await session.commit()

        run.status = WorkRunStatus.STORING.value
        run.stage = "storing_artifact"
        run.progress_percent = 90
        session.add(run)
        await session.commit()
        await _publish(redis, run, "work.stage")
        bucket = get_private_artifacts_bucket()
        key = build_artifact_key(
            user_id=run.user_id,
            work_run_id=run.id,
            artifact_id=artifact.id,
            version=artifact.version,
        )
        preview_key = build_artifact_preview_key(key)
        if artifact_was_preview_capable:
            stored_size_bytes, stored_sha256 = _artifact_storage_identity(
                artifact,
                rendered_size_bytes=rendered_size_bytes,
                rendered_sha256=sha256,
            )
            preview_stored_size, preview_stored_sha256 = _preview_storage_identity(
                artifact,
                rendered_size_bytes=preview_size_bytes,
                rendered_sha256=preview_sha256,
            )
        else:
            stored_size_bytes, stored_sha256 = rendered_size_bytes, sha256
            preview_stored_size, preview_stored_sha256 = (
                preview_size_bytes,
                preview_sha256,
            )
        artifact_uploaded, preview_uploaded = await _store_artifact_in_subprocess(
            bucket=bucket,
            key=key,
            output_path=output_path,
            rendered_size_bytes=rendered_size_bytes,
            rendered_sha256=sha256,
            stored_size_bytes=stored_size_bytes,
            stored_sha256=stored_sha256,
            preview_key=preview_key,
            preview_path=preview_path,
            preview_rendered_size_bytes=preview_size_bytes,
            preview_rendered_sha256=preview_sha256,
            preview_stored_size_bytes=preview_stored_size,
            preview_stored_sha256=preview_stored_sha256,
        )
        if artifact_uploaded:
            artifact.size_bytes = rendered_size_bytes
            artifact.sha256 = sha256
        if preview_uploaded:
            artifact.artifact_metadata = {
                **artifact.artifact_metadata,
                "_preview_size_bytes": preview_size_bytes,
                "_preview_sha256": preview_sha256,
            }
        artifact.bucket = bucket
        artifact.storage_key = key
        artifact.status = "ready"
        run.status = WorkRunStatus.SUCCEEDED.value
        run.stage = "completed"
        run.progress_percent = 100
        run.result_summary = SpreadsheetWorkRunResultSummary(
            rows=rendered.row_count,
            columns=rendered.column_count,
            sources=len(rendered.sources),
            normalization_mode=(
                "model" if "normalization_v1" in run.input_manifest else "exact"
            ),
            output_features=[
                WorkRunOutputFeature.NATIVE_EXCEL_TABLE,
                WorkRunOutputFeature.SUMMARY_SHEET,
                WorkRunOutputFeature.SOURCES_SHEET,
                WorkRunOutputFeature.INLINE_PREVIEW,
            ],
        ).model_dump_json(
            exclude_none=True,
        )
        run.completed_at = utcnow_naive()
        run.lease_expires_at = None
        ledger = await session.get(RequestLedger, run.request_ledger_id)
        if ledger and ledger.state == State.reserved:
            ledger.state = State.consumed
            session.add(ledger)
        await _set_linked_thread_status(session, run.id, "completed")
        session.add(artifact)
        session.add(run)
        await session.commit()
        await _publish(redis, run, "artifact.ready")
        await _publish(redis, run, "work.done")


async def process_comparison_run(
    *, session: AsyncSession, redis: Redis, run: WorkRun, worker_id: str
) -> None:
    """Compatibility entrypoint for older worker imports and focused tests."""

    await process_spreadsheet_run(
        session=session,
        redis=redis,
        run=run,
        worker_id=worker_id,
    )


async def fail_run(
    *, session: AsyncSession, redis: Redis, run: WorkRun, error: Exception
) -> None:
    await session.refresh(run, attribute_names=["status", "cancelled_at"])
    if run.status == WorkRunStatus.CANCELLING.value or run.cancelled_at is not None:
        await complete_cancellation(session=session, redis=redis, run=run)
        return
    run.status = WorkRunStatus.FAILED.value
    run.stage = "failed"
    run.error_code = (
        error.code.value
        if isinstance(error, WorkRunExecutionError)
        else WorkRunErrorCode.INTERNAL_ERROR.value
    )
    run.error_message = str(error)[:1000]
    run.completed_at = utcnow_naive()
    run.lease_expires_at = None
    ledger = await session.get(RequestLedger, run.request_ledger_id)
    if ledger and ledger.state == State.reserved:
        ledger.state = State.refunded
        session.add(ledger)
    operations = (
        await session.exec(
            select(ProviderOperation).where(
                ProviderOperation.work_run_id == run.id,
                ProviderOperation.status == "running",
            )
        )
    ).all()
    for operation in operations:
        operation.status = "failed"
        operation.error_code = run.error_code
        operation.completed_at = run.completed_at
        session.add(operation)
    await finish_active_activity_events(session, run, status="failed")
    activity_event = await record_activity_event(
        session,
        run,
        event_key="failed",
        kind="failed",
        status="failed",
        metadata={"error_code": run.error_code},
    )
    await _set_linked_thread_status(session, run.id, "failed")
    session.add(run)
    await session.commit()
    await _publish(
        redis,
        run,
        "work.error",
        activity_event=activity_event,
    )


async def _set_linked_thread_status(
    session: AsyncSession,
    work_run_id: uuid.UUID,
    status_value: str,
) -> None:
    link = (
        await session.exec(
            select(WorkThreadRun).where(WorkThreadRun.work_run_id == work_run_id)
        )
    ).first()
    if link is None:
        return
    thread = await session.get(WorkThread, link.thread_id)
    if thread is not None:
        thread.status = status_value
        session.add(thread)
