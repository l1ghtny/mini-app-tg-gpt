from datetime import datetime, timedelta, timezone

from app.db.models import Conversation
from app.services.openai_chain import INVALIDATING_CHAIN_REASONS


def resolve_previous_interaction_id_for_chain(
    conversation: Conversation,
    *,
    current_fingerprint: str,
    chaining_enabled: bool,
    max_inactivity_days: int,
) -> tuple[str | None, str | None]:
    if not chaining_enabled:
        return None, "disabled"

    interaction_id = (conversation.last_google_interaction_id or "").strip()
    if not interaction_id:
        return None, "no_interaction_id"

    updated_at = conversation.google_chain_updated_at
    if updated_at is None:
        return None, "no_chain_timestamp"

    stored_fingerprint = (conversation.google_chain_context_fingerprint or "").strip()
    if not stored_fingerprint:
        return None, "no_context_fingerprint"
    if stored_fingerprint != current_fingerprint:
        return None, "context_fingerprint_mismatch"

    max_age = timedelta(days=max(1, max_inactivity_days))
    age = datetime.now(timezone.utc).replace(tzinfo=None) - updated_at
    if age > max_age:
        return None, "expired_inactivity_window"

    return interaction_id, "eligible"


def invalidate_google_chain_state(conversation: Conversation) -> None:
    conversation.last_google_interaction_id = None
    conversation.google_chain_updated_at = None
    conversation.google_chain_context_fingerprint = None
