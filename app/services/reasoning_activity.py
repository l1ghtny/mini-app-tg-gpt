from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


MAX_REASONING_ACTIVITY_CHARS = 16_000
VISIBLE_REASONING_LANGUAGE_INSTRUCTION = (
    "Use the language of the user's latest message for the reply and any visible "
    "reasoning summary, unless the user explicitly asks for another language."
)


def extract_summary_text(value: Any) -> str:
    """Extract text from provider summary blocks without depending on SDK classes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(extract_summary_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "summary"):
            if key in value:
                extracted = extract_summary_text(value[key])
                if extracted:
                    return extracted
        return ""
    for attribute in ("text", "content", "summary"):
        if hasattr(value, attribute):
            extracted = extract_summary_text(getattr(value, attribute))
            if extracted:
                return extracted
    return ""


@dataclass
class ReasoningActivity:
    """Provider-neutral, bounded state for user-visible reasoning summaries."""

    provider: str
    max_chars: int = MAX_REASONING_ACTIVITY_CHARS
    activity_id: str = "reasoning-0"
    _segments: dict[str, str] = field(default_factory=dict)
    _segment_order: list[str] = field(default_factory=list)
    _active_segments: set[str] = field(default_factory=set)
    _completed_segments: set[str] = field(default_factory=set)
    truncated: bool = False

    def _render_text(self) -> str:
        return "\n\n".join(
            text.strip()
            for segment_id in self._segment_order
            if (text := self._segments.get(segment_id, "")).strip()
        )

    @property
    def text(self) -> str:
        return self._render_text()[: self.max_chars]

    def start(
        self,
        *,
        segment_id: str,
        source_event: str,
        sequence_number: int | None = None,
    ) -> list[dict[str, Any]]:
        if (
            segment_id in self._active_segments
            or segment_id in self._completed_segments
        ):
            return []
        self._active_segments.add(segment_id)
        return [
            {
                "type": "status",
                "stage": "thinking",
                "phase": "thinking",
                "status": "active",
                "label": "Thinking",
                "source_event": source_event,
                "provider": self.provider,
                "activity_id": self.activity_id,
                "segment_id": segment_id,
                "sequence_number": sequence_number,
                "ts": int(time.time() * 1000),
            }
        ]

    def append(
        self,
        text: str,
        *,
        segment_id: str,
        source_event: str,
        sequence_number: int | None = None,
    ) -> list[dict[str, Any]]:
        if segment_id in self._completed_segments:
            return []
        events = self.start(
            segment_id=segment_id,
            source_event=source_event,
            sequence_number=sequence_number,
        )
        normalized = text.replace("\x00", "")
        if not normalized or self.truncated:
            return events

        if segment_id not in self._segments:
            self._segments[segment_id] = ""
            self._segment_order.append(segment_id)

        current_total = len(self.text)
        separator_cost = 2 if self._segments[segment_id] == "" and current_total else 0
        remaining = max(0, self.max_chars - current_total - separator_cost)
        accepted = normalized[:remaining]
        if len(accepted) < len(normalized):
            self.truncated = True
        if not accepted:
            return events

        self._segments[segment_id] += accepted
        events.append(
            {
                "type": "reasoning.summary.delta",
                "delta": accepted,
                "provider": self.provider,
                "activity_id": self.activity_id,
                "segment_id": segment_id,
                "sequence_number": sequence_number,
            }
        )
        return events

    def complete(
        self,
        *,
        segment_id: str,
        source_event: str,
        full_text: str | None = None,
        sequence_number: int | None = None,
    ) -> list[dict[str, Any]]:
        events = self.start(
            segment_id=segment_id,
            source_event=source_event,
            sequence_number=sequence_number,
        )
        if segment_id in self._completed_segments:
            return events
        if full_text and not self.truncated:
            normalized = full_text.replace("\x00", "")
            if segment_id not in self._segments:
                self._segment_order.append(segment_id)
            self._segments[segment_id] = normalized[: self.max_chars]
            if len(normalized) > self.max_chars or len(self._render_text()) > self.max_chars:
                self.truncated = True

        self._active_segments.discard(segment_id)
        self._completed_segments.add(segment_id)

        if self.text:
            events.append(
                {
                    "type": "reasoning.summary.done",
                    "text": self.text,
                    "provider": self.provider,
                    "activity_id": self.activity_id,
                    "segment_id": segment_id,
                    "sequence_number": sequence_number,
                    "truncated": self.truncated,
                }
            )
        events.append(
            {
                "type": "status",
                "stage": "thinking",
                "phase": "thinking",
                "status": "done",
                "label": "Thinking complete",
                "source_event": source_event,
                "provider": self.provider,
                "activity_id": self.activity_id,
                "segment_id": segment_id,
                "sequence_number": sequence_number,
                "ts": int(time.time() * 1000),
            }
        )
        return events
