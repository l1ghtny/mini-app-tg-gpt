"""Single-attempt audio transcription worker; run one replica per lane."""

import asyncio
import signal

from app.services.audio_jobs import run_worker


async def main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    await run_worker(stop_event)


if __name__ == "__main__":
    asyncio.run(main())
