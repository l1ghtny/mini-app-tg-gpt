import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import Conversation

INVALIDATING_CHAIN_REASONS = {
    "context_fingerprint_mismatch",
    "no_context_fingerprint",
    "expired_inactivity_window",
    "no_chain_timestamp",
}


def build_chain_context_fingerprint(
    *,
    model: str,
    system_prompt: str,
    ledger_tool_choice: str,
    image_model: str,
    image_quality: str,
    tools: list[Any],
    extract_tool_type: Any,
) -> str:
    tool_types = sorted({str(extract_tool_type(tool) or "") for tool in tools if extract_tool_type(tool)})
    payload = {
        "model": model,
        "system_prompt": system_prompt or "",
        "ledger_tool_choice": ledger_tool_choice or "auto",
        "image_model": image_model or "",
        "image_quality": image_quality or "",
        "tool_types": tool_types,
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_previous_response_id_for_chain(
    conversation: Conversation,
    *,
    current_fingerprint: str,
    chaining_enabled: bool,
    max_inactivity_days: int,
) -> tuple[str | None, str | None]:
    if not chaining_enabled:
        return None, "disabled"

    response_id = (conversation.last_openai_response_id or "").strip()
    if not response_id:
        return None, "no_response_id"

    updated_at = conversation.openai_chain_updated_at
    if updated_at is None:
        return None, "no_chain_timestamp"

    stored_fingerprint = (conversation.openai_chain_context_fingerprint or "").strip()
    if not stored_fingerprint:
        return None, "no_context_fingerprint"
    if stored_fingerprint != current_fingerprint:
        return None, "context_fingerprint_mismatch"

    max_age = timedelta(days=max(1, max_inactivity_days))
    age = datetime.now(timezone.utc).replace(tzinfo=None) - updated_at
    if age > max_age:
        return None, "expired_inactivity_window"

    return response_id, "eligible"


def invalidate_openai_chain_state(conversation: Conversation) -> None:
    conversation.last_openai_response_id = None
    conversation.openai_chain_updated_at = None
    conversation.openai_chain_context_fingerprint = None

