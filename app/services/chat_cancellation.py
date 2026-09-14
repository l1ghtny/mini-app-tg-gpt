"""Message-scoped cancellation shared by API workers through Redis."""
import asyncio
from contextlib import suppress


class GenerationStopped(Exception):
    pass


def cancellation_key(message_id: str) -> str:
    return f"msg:{message_id}:cancel"


async def cancellable_events(source, bus, message_id: str):
    # Minimal buses are used by unit tests and do not support distributed signals.
    redis = getattr(bus, "r", None)
    if redis is None:
        async for event in source:
            yield event
        return

    async def wait_for_stop():
        while not await redis.get(cancellation_key(message_id)):
            await asyncio.sleep(0.25)

    waiter = asyncio.create_task(wait_for_stop())
    pending = None
    try:
        # A stop may have arrived before the background task started.
        if await redis.get(cancellation_key(message_id)):
            raise GenerationStopped()
        while True:
            if waiter.done():
                waiter.result()  # Redis failures are failures, not user cancellation.
                raise GenerationStopped()
            pending = asyncio.create_task(anext(source))
            done, _ = await asyncio.wait({pending, waiter}, return_when=asyncio.FIRST_COMPLETED)
            # Finish an event already received, including its storage/accounting work.
            if pending in done:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                yield event
                if event.get("type") in {"done", "error"}:
                    # Provider bookkeeping may follow the terminal event (usage
                    # logging, metrics). Completion wins over a late Stop click.
                    waiter.cancel()
                    async for remaining in source:
                        yield remaining
                    return
            else:
                waiter.result()
                raise GenerationStopped()
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter
        await source.aclose()
