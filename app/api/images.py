import os
import time
import uuid
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.params import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.api.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import AppUser
from app.r2.methods import upload_fileobject
from app.r2.settings import Settings
from app.schemas.images import ImageUploaded

images = APIRouter(tags=["images"], prefix="/images")
_PROXY_ALLOWED_HOSTS_ENV = "IMAGE_FETCH_PROXY_ALLOWED_HOSTS"
_PROXY_ACCEPT = "image/*,application/octet-stream;q=0.9,*/*;q=0.1"
_PROXY_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
_PROXY_MAX_REDIRECTS = int(os.getenv("IMAGE_FETCH_PROXY_MAX_REDIRECTS", "3"))


def _get_proxy_allowed_hosts() -> set[str]:
    allowed: set[str] = set()

    if Settings.R2_PUBLIC_BASE_URL:
        base_host = urlsplit(Settings.R2_PUBLIC_BASE_URL).hostname
        if base_host:
            allowed.add(base_host.lower())

    extra_hosts = os.getenv(_PROXY_ALLOWED_HOSTS_ENV, "")
    for raw in extra_hosts.split(","):
        host = raw.strip().lower()
        if not host:
            continue
        if "://" in host:
            parsed_host = urlsplit(host).hostname
            if not parsed_host:
                continue
            host = parsed_host.lower()
        host = host.split("/", 1)[0]
        if host:
            allowed.add(host)

    return allowed


def _is_allowed_proxy_host(host: str, allowed_hosts: set[str]) -> bool:
    normalized = host.strip().lower().strip(".")
    if not normalized:
        return False

    for candidate in allowed_hosts:
        candidate = candidate.strip().lower().strip(".")
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]  # ".example.com"
            if normalized.endswith(suffix) and normalized != suffix.lstrip("."):
                return True
            continue
        if normalized == candidate:
            return True

    return False


def _image_filename(url: str, content_type: str | None) -> str:
    path = urlsplit(url).path or ""
    tail = path.rsplit("/", 1)[-1]
    if tail:
        decoded = unquote(tail)
        if decoded not in {".", ".."}:
            safe = decoded.replace("\\", "_").replace('"', "").replace("\r", "").replace("\n", "")
            if safe:
                return safe

    mime = (content_type or "").split(";", 1)[0].strip().lower()
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
    }.get(mime, ".bin")
    return f"image{ext}"


def _proxy_response_headers(upstream_headers, filename: str) -> dict[str, str]:
    headers = {"X-Content-Type-Options": "nosniff"}

    for src, dst in (
        ("cache-control", "Cache-Control"),
        ("etag", "ETag"),
        ("last-modified", "Last-Modified"),
        ("content-length", "Content-Length"),
    ):
        value = upstream_headers.get(src)
        if value:
            headers[dst] = value

    headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return headers


async def _close_proxy_stream(client: httpx.AsyncClient, upstream_response: httpx.Response) -> None:
    await upstream_response.aclose()
    await client.aclose()


@images.post("/upload", response_model=ImageUploaded)
async def upload_image(
    image: UploadFile,
    app_user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # 0. Generate a file key
    if not image.filename:
        file_name = "image"
        image.filename = file_name

    ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "png"
    key = f"{time.strftime('%Y/%m/%d')}/{uuid.uuid4()}.{ext}"
    # 1. Save the image to the R2 bucket
    bucket, key = await upload_fileobject(
        key,
        image,
        content_type=image.content_type,
        extra_metadata={"author": str(app_user.id), "type": "image"},
    )
    return ImageUploaded(key=key, url=f"{Settings.R2_PUBLIC_BASE_URL}{bucket}/{key}")


@images.get("/proxy")
async def proxy_image(url: str = Query(..., min_length=8, description="Public image URL")):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid image URL")

    allowed_hosts = _get_proxy_allowed_hosts()
    if not allowed_hosts:
        raise HTTPException(status_code=500, detail=f"Image proxy hosts are not configured ({_PROXY_ALLOWED_HOSTS_ENV})")
    if not _is_allowed_proxy_host(parsed.hostname, allowed_hosts):
        raise HTTPException(status_code=403, detail="Host is not allowed for image proxy")

    client = httpx.AsyncClient(timeout=_PROXY_TIMEOUT, follow_redirects=True, max_redirects=_PROXY_MAX_REDIRECTS)
    try:
        request = client.build_request("GET", url, headers={"Accept": _PROXY_ACCEPT})
        upstream = await client.send(request, stream=True)
    except httpx.TimeoutException:
        await client.aclose()
        raise HTTPException(status_code=504, detail="Timed out while fetching image")
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="Could not fetch image")

    final_host = upstream.url.host.lower() if upstream.url and upstream.url.host else None
    if not final_host or not _is_allowed_proxy_host(final_host, allowed_hosts):
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=403, detail="Redirect target host is not allowed")

    if upstream.status_code >= 400:
        if upstream.status_code in {400, 401, 403, 404, 410, 429}:
            status_code = upstream.status_code
        else:
            status_code = 502
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=status_code, detail="Upstream image fetch failed")

    content_type = (upstream.headers.get("content-type") or "application/octet-stream").split(";", 1)[0].strip().lower()
    if not (content_type.startswith("image/") or content_type == "application/octet-stream"):
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=415, detail="URL did not return an image")

    filename = _image_filename(str(upstream.url), content_type)
    headers = _proxy_response_headers(upstream.headers, filename)

    return StreamingResponse(
        upstream.aiter_bytes(chunk_size=64 * 1024),
        media_type=content_type,
        headers=headers,
        background=BackgroundTask(_close_proxy_stream, client, upstream),
    )
