"""Deterministic, versioned execution plans shared by quotes and generation."""

from dataclasses import asdict, dataclass
import re

from app.services.allowance_policy import input_upper_bound

PLAN_VERSION = "2026-10-02-v2"
PROFILES = {
    "gpt-5.6-luna": (8_000, 16_000, "low"),
    "gpt-5.6-terra": (12_000, 24_000, "low"),
    "claude-sonnet-5": (16_000, 32_000, "medium"),
    "gpt-5.6-sol": (16_000, 32_000, "low"),
    "claude-opus-5": (24_000, 40_000, "medium"),
    "gpt-6-astra": (24_000, 48_000, "low"),
    "claude-fable-5-1": (32_000, 48_000, "medium"),
}
EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
TASK_TARGETS = {
    "claude-opus-5": (20_000, 32_000, 40_000),
    "claude-fable-5-1": (24_000, 40_000, 48_000),
}


def latest_user_text(messages):
    for message in reversed(messages):
        if message.get("role") == "user":
            return "\n".join(
                part.get("text", "")
                for part in message.get("content", [])
                if part.get("type") in {"input_text", "text"}
            )
    return ""


def asks_for_long_output(text):
    # Only explicit quantities promote capacity; short prompts are never downgraded.
    for match in re.finditer(
        r"\b(\d[\d, ]{0,6})[-\s]*(words?\b|pages?\b|слов\w*|страниц\w*)",
        text.lower(),
    ):
        quantity = int(re.sub(r"[, ]", "", match[1]))
        pages = match[2].startswith(("page", "страниц"))
        if quantity >= (3 if pages else 1000):
            return True
    return False


@dataclass(frozen=True)
class ExecutionPlan:
    model: str
    effort: str
    profile: str
    max_output_tokens: int
    routing_tokens: int
    task_budget: int | None
    version: str = PLAN_VERSION

    def dump(self):
        return asdict(self)

    @classmethod
    def load(cls, value):
        plan = cls(**value)
        if plan.version != PLAN_VERSION or plan.model not in PROFILES:
            raise ValueError("Unsupported generation plan")
        return plan


def execution_plan(
    model,
    messages,
    *,
    instructions="",
    reasoning_effort=None,
    thinking_enabled=None,
    response_length="auto",
    tool_work=False,
):
    normal, expanded, default_effort = PROFILES[model]
    explicit_effort = reasoning_effort is not None
    effort = reasoning_effort or (
        "none" if thinking_enabled is False else default_effort
    )
    if effort not in EFFORTS:
        raise ValueError("Unsupported reasoning effort")
    if model in {"gpt-6-astra", "claude-fable-5-1"} and effort == "none":
        raise ValueError("This model requires reasoning")
    long_output = response_length == "long" or asks_for_long_output(
        latest_user_text(messages)
    )
    deeper = explicit_effort and effort in {"medium", "high", "xhigh", "max"}
    profile = "expanded" if long_output or deeper else "normal"
    maximum = expanded if profile == "expanded" else normal
    if effort == "high":
        maximum = max(maximum, 32_000 if normal <= 16_000 else 48_000)
    elif effort in {"xhigh", "max"}:
        maximum = max(maximum, 64_000)
    routing = min(maximum, 16_000 if effort in {"high", "xhigh", "max"} else 8_000)
    target = None
    if model in TASK_TARGETS:
        target = TASK_TARGETS[model][
            2 if tool_work else 1 if profile == "expanded" else 0
        ]
        if effort in {"high", "xhigh", "max"}:
            target = max(target, 48_000 if effort == "high" else 64_000)
        target += max(0, input_upper_bound(messages, instructions, model=model) - 8_000)
    return ExecutionPlan(model, effort, profile, maximum, routing, target)


def evidence_messages(messages, *, tokens):
    """Bounded allowance for tool/continuation input; never a prompt sent upstream."""
    return [
        *messages,
        {
            "role": "user",
            "content": [{"type": "input_text", "text": " evidence" * tokens}],
        },
    ]
