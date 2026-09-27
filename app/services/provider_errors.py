"""Safe completion metadata without provider payloads or conversation text."""


class ProviderResponseError(RuntimeError):
    def __init__(self, *, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(
            f"The model did not complete its response (status={status}, reason={reason})"
        )


class ImageModerationError(RuntimeError):
    code = "image_moderation_blocked"
    public_message = (
        "OpenAI's safety checks blocked this image request. No AI allowance was charged."
    )

    def __init__(self, *, stage=None):
        self.stage = stage if isinstance(stage, str) and stage in {"input", "output"} else "unknown"
        super().__init__(self.public_message)

    @classmethod
    def from_api_error(cls, exception):
        body = getattr(exception, "body", None)
        if not isinstance(body, dict):
            return None
        error = body.get("error", body)
        if not isinstance(error, dict) or error.get("code") != "moderation_blocked":
            return None
        details = error.get("moderation_details")
        stage = details.get("moderation_stage") if isinstance(details, dict) else None
        return cls(stage=stage)
