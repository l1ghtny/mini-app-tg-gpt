import asyncio
import logging
import os

from app.services.conversation_search import run_search_job_once

logger = logging.getLogger("uvicorn")


async def main() -> None:
    poll_interval = float(os.getenv("CONVERSATION_SEARCH_POLL_SECONDS", "2.0"))
    idle_interval = max(0.1, poll_interval)

    while True:
        try:
            did_work = await run_search_job_once()
        except Exception:
            logger.exception("Conversation search worker loop failed")
            did_work = False

        if not did_work:
            await asyncio.sleep(idle_interval)


if __name__ == "__main__":
    asyncio.run(main())
