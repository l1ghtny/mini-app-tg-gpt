from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from evals.work_quality.contracts import (
    ArtifactObservation,
    EvalCase,
    EvalObservation,
    EvalSuite,
    RunObservation,
)


_TERMINAL_STATUSES = {"cancelled", "failed", "refunded", "succeeded"}
_RUNNING_STATUSES = {
    "accepted",
    "queued",
    "reserved",
    "running",
    "validating",
    "storing",
}


class WorkEvalClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str | None = None,
        session_cookie: str | None = None,
        session_cookie_name: str = "lightny_beta_session",
        timeout_seconds: float = 900,
        poll_interval_seconds: float = 2,
    ) -> None:
        if bool(bearer_token) == bool(session_cookie):
            raise ValueError("provide exactly one of bearer_token or session_cookie")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        if bearer_token:
            headers = {"Authorization": f"Bearer {bearer_token}"}
        else:
            parsed_base_url = urlsplit(self.base_url)
            origin = f"{parsed_base_url.scheme}://{parsed_base_url.netloc}"
            headers = {"Origin": origin, "Referer": f"{origin}/"}
        cookies = {session_cookie_name: session_cookie} if session_cookie else None
        self.client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            cookies=cookies,
            timeout=httpx.Timeout(60, connect=20),
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def run_case(
        self,
        suite: EvalSuite,
        case: EvalCase,
        *,
        suite_root: Path,
        output_dir: Path,
        environment: str,
    ) -> EvalObservation:
        started_at = datetime.now(timezone.utc)
        api_errors: list[str] = []
        document_ids: list[str] = []
        thread_id: str | None = None
        current_run_id: str | None = None
        thread: dict = {}
        try:
            for attachment in case.attachments:
                document_ids.append(self._upload_document(suite_root / attachment.path))
            response = self._request(
                "POST",
                "api/v1/work-conversations",
                headers={"Idempotency-Key": _idempotency_key(case.id, "start")},
                json={
                    "goal": case.goal,
                    "document_ids": document_ids,
                    "output_language": case.output_language,
                },
            )
            thread = response["thread"]
            thread_id = str(thread["id"])
            current_run_id = _response_run_id(response)

            for index, interaction in enumerate(case.interactions, start=1):
                thread = self._wait_for(
                    thread_id,
                    run_id=current_run_id,
                    condition=interaction.wait_for,
                )
                if interaction.delay_seconds:
                    time.sleep(interaction.delay_seconds)
                response = self._perform_interaction(
                    case=case,
                    index=index,
                    interaction=interaction,
                    thread=thread,
                    run_id=current_run_id,
                )
                if "thread" in response:
                    thread = response["thread"]
                current_run_id = _response_run_id(response) or current_run_id

            thread = self._wait_for(
                thread_id,
                run_id=current_run_id,
                condition="terminal",
            )
        except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
            api_errors.append(_safe_error(exc))
            if thread_id:
                try:
                    thread = self._get_thread(thread_id)
                except httpx.HTTPError:
                    pass

        observation = self._observation_from_thread(
            suite=suite,
            case=case,
            thread=thread,
            output_dir=output_dir,
            environment=environment,
            started_at=started_at,
            api_errors=api_errors,
        )
        for document_id in document_ids:
            try:
                self._request(
                    "DELETE", f"api/v1/documents/{document_id}", expect_json=False
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                observation.api_errors.append(
                    f"synthetic document cleanup failed for {document_id}: {_safe_error(exc)}"
                )
        return observation

    def _perform_interaction(
        self,
        *,
        case: EvalCase,
        index: int,
        interaction,
        thread: dict,
        run_id: str | None,
    ) -> dict:
        thread_id = str(thread["id"])
        headers = {
            "Idempotency-Key": _idempotency_key(
                case.id, f"{index}-{interaction.action}"
            )
        }
        if interaction.action == "message":
            return self._request(
                "POST",
                f"api/v1/work-threads/{thread_id}/messages",
                headers=headers,
                json={"content": interaction.content, "document_ids": []},
            )
        if interaction.action == "retry":
            return self._request(
                "POST",
                f"api/v1/work-threads/{thread_id}/retry",
                headers=headers,
            )
        if run_id is None:
            raise RuntimeError(f"cannot {interaction.action} without a Work run")
        if interaction.action == "cancel":
            return {
                "thread": thread,
                "run": self._request("POST", f"api/v1/work-runs/{run_id}/cancel"),
            }

        pending = next(
            (
                request
                for request in reversed(thread.get("human_input_requests", []))
                if request.get("status") == "pending"
                and str(request.get("work_run_id")) == run_id
            ),
            None,
        )
        if pending is None:
            raise RuntimeError("Work did not expose the expected pending clarification")
        return self._request(
            "POST",
            f"api/v1/work-runs/{run_id}/human-input/{pending['id']}/answer",
            headers=headers,
            json={"answer": interaction.content},
        )

    def _wait_for(self, thread_id: str, *, run_id: str | None, condition: str) -> dict:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            thread = self._get_thread(thread_id)
            run = _find_run(thread, run_id)
            status = str(run.get("status")) if run else str(thread.get("status"))
            if condition == "terminal" and status in _TERMINAL_STATUSES:
                return thread
            if condition == "running" and status in _RUNNING_STATUSES:
                return thread
            if condition == "waiting_for_user" and (
                status == "waiting_for_user"
                or thread.get("status") == "waiting_for_user"
            ):
                return thread
            if run is None and thread.get("status") in {"planning_failed", "failed"}:
                return thread
            time.sleep(self.poll_interval_seconds)
        raise RuntimeError(
            f"timed out waiting for {condition} on Work thread {thread_id}"
        )

    def _upload_document(self, path: Path) -> str:
        with path.open("rb") as handle:
            uploaded = self._request(
                "POST",
                "api/v1/documents/upload",
                files={"file": (path.name, handle)},
            )
        document_id = str(uploaded["id"])
        deadline = time.monotonic() + min(self.timeout_seconds, 240)
        while time.monotonic() < deadline:
            listing = self._request("GET", "api/v1/documents")
            document = next(
                (
                    item
                    for item in listing.get("documents", listing.get("items", []))
                    if str(item.get("id")) == document_id
                ),
                None,
            )
            status = str((document or uploaded).get("status", "ready"))
            if status == "ready":
                return document_id
            if status in {"failed", "rejected"}:
                raise RuntimeError(f"synthetic document {path.name} failed ingestion")
            time.sleep(self.poll_interval_seconds)
        raise RuntimeError(f"timed out ingesting synthetic document {path.name}")

    def _observation_from_thread(
        self,
        *,
        suite: EvalSuite,
        case: EvalCase,
        thread: dict,
        output_dir: Path,
        environment: str,
        started_at: datetime,
        api_errors: list[str],
    ) -> EvalObservation:
        messages_by_run: dict[str, dict] = {}
        for message in thread.get("messages", []):
            run_id = str(message.get("metadata", {}).get("work_run_id", ""))
            if run_id and message.get("role") == "assistant":
                messages_by_run[run_id] = message
        human_requests = thread.get("human_input_requests", [])
        run_observations: list[RunObservation] = []
        for run in thread.get("runs", []):
            run_id = str(run.get("id", ""))
            summary = _result_summary(run.get("result_summary"))
            evidence = summary.get("evidence", {})
            activity = summary.get("activity", {})
            artifacts = [
                self._download_artifact(
                    artifact,
                    output_dir=output_dir / case.id / "artifacts",
                )
                for artifact in run.get("artifacts", [])
            ]
            result_text = str(
                messages_by_run.get(run_id, {}).get("content")
                or summary.get("content")
                or ""
            )
            run_observations.append(
                RunObservation(
                    id=run_id or None,
                    status=str(run.get("status", "unknown")),
                    stage=run.get("stage"),
                    result_text=result_text,
                    error_code=run.get("error_code"),
                    error_message=run.get("error_message"),
                    actual_cost_usd=float(run.get("actual_cost_usd") or 0),
                    duration_seconds=_duration_seconds(run),
                    tool_counts={
                        "web_search": int(activity.get("web_search_calls", 0)),
                        "file_search": int(activity.get("file_search_calls", 0)),
                        "code_interpreter": int(
                            activity.get("code_interpreter_calls", 0)
                        ),
                    },
                    sources=list(evidence.get("sources", [])),
                    citations=list(evidence.get("citations", [])),
                    artifacts=artifacts,
                    clarification_count=sum(
                        str(request.get("work_run_id")) == run_id
                        for request in human_requests
                    ),
                )
            )
        return EvalObservation(
            suite_version=suite.version,
            case_id=case.id,
            environment=environment,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            thread_id=str(thread.get("id")) if thread.get("id") else None,
            runs=run_observations,
            api_errors=api_errors,
        )

    def _download_artifact(
        self, artifact: dict, *, output_dir: Path
    ) -> ArtifactObservation:
        observation = ArtifactObservation.model_validate(artifact)
        if artifact.get("status") != "ready" or not artifact.get("id"):
            observation.download_error = "artifact is not ready for download"
            return observation
        try:
            grant = self._request("GET", f"api/v1/artifacts/{artifact['id']}/download")
            url = urljoin(self.base_url, str(grant["url"]))
            response = self.client.get(url)
            response.raise_for_status()
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / _safe_filename(observation.filename)
            path.write_bytes(response.content)
            observation.content_path = str(path.resolve())
        except (httpx.HTTPError, KeyError, OSError, RuntimeError) as exc:
            observation.download_error = _safe_error(exc)
        return observation

    def _get_thread(self, thread_id: str) -> dict:
        return self._request("GET", f"api/v1/work-threads/{thread_id}")

    def _request(self, method: str, path: str, *, expect_json: bool = True, **kwargs):
        response = self.client.request(method, path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:500]
            raise RuntimeError(
                f"{method} {path} returned {response.status_code}: {detail}"
            ) from exc
        if not expect_json or response.status_code == 204:
            return {}
        return response.json()


def _response_run_id(response: dict) -> str | None:
    run = response.get("run")
    return str(run["id"]) if run and run.get("id") else None


def _find_run(thread: dict, run_id: str | None) -> dict | None:
    runs = thread.get("runs", [])
    if not runs:
        return None
    if run_id is None:
        return runs[-1]
    return next((run for run in runs if str(run.get("id")) == run_id), runs[-1])


def _result_summary(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {"content": value}
    return {}


def _duration_seconds(run: dict) -> float | None:
    started = run.get("started_at") or run.get("queued_at") or run.get("created_at")
    completed = run.get("completed_at") or run.get("cancelled_at")
    if not started or not completed:
        return None
    try:
        return max(
            0,
            (
                datetime.fromisoformat(completed) - datetime.fromisoformat(started)
            ).total_seconds(),
        )
    except (TypeError, ValueError):
        return None


def _idempotency_key(case_id: str, action: str) -> str:
    return f"work-eval-{case_id}-{action}-{uuid.uuid4().hex[:12]}"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "artifact.bin"


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:1000]
