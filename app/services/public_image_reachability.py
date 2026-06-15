from __future__ import annotations

import asyncio
import logging
import random

import httpx


class ImageReachabilityError(RuntimeError):
    def __init__(self, url: str):
        super().__init__(f"Image URL did not become reachable: {url}")
        self.url = url


async def wait_for_image_url_reachability(
    url: str,
    *,
    max_retries: int = 8,
    delay: float = 0.75,
    logger: logging.Logger | None = None,
    require_success: bool = False,
) -> bool:
    """
    Poll the public URL until it is actually downloadable.

    HEAD alone is not enough here: edge/CDN propagation can briefly report a
    healthy object while GET still fails for the provider.
    """
    timeout = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(max_retries):
            try:
                async with client.stream("GET", url, headers={"Range": "bytes=0-65535"}) as resp:
                    content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if resp.status_code in (200, 206) and (
                        content_type.startswith("image/") or content_type == "application/octet-stream"
                    ):
                        async for _chunk in resp.aiter_bytes():
                            return True
                    if logger:
                        logger.info(
                            "Image URL not ready yet url=%s status=%s content_type=%s",
                            url,
                            resp.status_code,
                            content_type or "unknown",
                        )
            except Exception as exc:
                if logger:
                    logger.info("Image URL not reachable yet url=%s error=%r", url, exc)

            sleep_s = delay * (2 ** min(attempt, 4)) + random.random() * 0.25
            await asyncio.sleep(sleep_s)

    if logger:
        logger.warning("Image URL did not become downloadable within retries url=%s", url)
    if require_success:
        raise ImageReachabilityError(url)
    return False
