"""Private audio downloads must consume short streaming reads completely."""

import pytest

from app.r2 import private_audio


class ChunkedBody:
    def __init__(self, contents: bytes):
        self.contents = contents
        self.offset = 0
        self.closed = False

    async def read(self, amount: int) -> bytes:
        # StreamingBody.read(amount) may return less than amount before EOF.
        chunk = self.contents[self.offset:self.offset + min(amount, 3)]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, body: ChunkedBody, length: int | None):
        self.body = body
        self.length = length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get_object(self, **_kwargs):
        response = {"Body": self.body}
        if self.length is not None:
            response["ContentLength"] = self.length
        return response


@pytest.mark.asyncio
async def test_download_audio_joins_partial_reads_through_eof(monkeypatch):
    contents = b"abcdefghijklmnopqrstuvwxyz"
    body = ChunkedBody(contents)
    monkeypatch.setattr(private_audio, "_bucket", lambda: "private-test")
    monkeypatch.setattr(private_audio, "_client", lambda: FakeClient(body, len(contents)))

    result = await private_audio.download_audio("transcriptions/test.mp3", max_bytes=len(contents))

    assert result == contents
    assert body.offset == len(contents)
    assert body.closed


@pytest.mark.asyncio
async def test_download_audio_rejects_oversize_without_content_length(monkeypatch):
    body = ChunkedBody(b"abcdef")
    monkeypatch.setattr(private_audio, "_bucket", lambda: "private-test")
    monkeypatch.setattr(private_audio, "_client", lambda: FakeClient(body, None))

    with pytest.raises(ValueError, match="oversized"):
        await private_audio.download_audio("transcriptions/test.mp3", max_bytes=5)

    assert body.closed


@pytest.mark.asyncio
async def test_download_audio_rejects_declared_oversize_and_closes_body(monkeypatch):
    body = ChunkedBody(b"abcdef")
    monkeypatch.setattr(private_audio, "_bucket", lambda: "private-test")
    monkeypatch.setattr(private_audio, "_client", lambda: FakeClient(body, 999))

    with pytest.raises(ValueError, match="oversized"):
        await private_audio.download_audio("transcriptions/test.mp3", max_bytes=5)

    assert body.offset == 0
    assert body.closed
