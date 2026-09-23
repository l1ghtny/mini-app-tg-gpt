"""Safe completion metadata without provider payloads or conversation text."""


class ProviderResponseError(RuntimeError):
    def __init__(self, *, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(
            f"The model did not complete its response (status={status}, reason={reason})"
        )
