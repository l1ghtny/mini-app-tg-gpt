from __future__ import annotations

from app.db.work_agent_models import WorkThreadMessage


def bounded_thread_history(
    messages: list[WorkThreadMessage],
    *,
    current_request: str,
    max_chars: int = 24000,
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    remaining = max_chars
    for message in reversed(messages):
        if message.kind not in {"follow_up", "result"}:
            continue
        if message.role == "user" and message.content == current_request:
            continue
        content = message.content[-remaining:]
        if not content:
            break
        selected.append(
            {
                "role": message.role,
                "kind": message.kind,
                "content": content,
                "metadata": message.message_metadata,
            }
        )
        remaining -= len(content)
        if remaining <= 0:
            break
    return list(reversed(selected))
