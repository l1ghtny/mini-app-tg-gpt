import uuid
from datetime import datetime, timedelta, timezone
from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.models import RequestLedger, ImageQualityPricing


async def check_image_pacing(
        session: AsyncSession,
        user_id: uuid.UUID,
        daily_target: float = 4.0,
        max_burst_days: int = 5,
        cost: float = 1.0,
) -> tuple[bool, timedelta]:
    """
    Calculates if the user is throttled based on a Leaky Bucket algorithm.
    Returns (is_throttled, wait_time).
    """
    # 1. Configuration
    # How many images flow into the bucket per second
    refill_rate_per_sec = daily_target / 86400.0
    # Maximum capacity (e.g., 5 days worth of accumulation)
    capacity = daily_target * max_burst_days
    # Cost per image (usually 1, but GPT-image-1.5 could be 2)
    # For pacing, let's simplify to 1 request = 1 unit, or pass cost dynamically if needed

    # 2. Define the window
    # We only need to look back far enough to fill the bucket from empty.
    # If the bucket takes 'max_burst_days' to fill, looking back slightly more is safe.
    # However, to be accurate with irregular history, looking back 30 days is robust.
    window_days = 30
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_window = now - timedelta(days=window_days)

    # 3. Fetch History
    # Get all image requests in the window, ordered by time
    query = (
        select(RequestLedger)
        .where(
            RequestLedger.user_id == user_id,
            RequestLedger.feature == "image",
            RequestLedger.state.in_(("reserved", "consumed")),
            RequestLedger.created_at >= start_window
        )
        .order_by(RequestLedger.created_at.asc())
    )
    history = (await session.exec(query)).all()

    # 4. Replay Bucket State
    # Assume bucket was full at the start of the window (or empty, but full gives benefit of doubt)
    current_tokens = capacity
    last_time = start_window

    for request in history:
        req_time = request.created_at

        # Calculate refill since last event
        elapsed = (req_time - last_time).total_seconds()
        refilled = elapsed * refill_rate_per_sec

        # Add refill, clamp to capacity
        current_tokens = min(capacity, current_tokens + refilled)

        # Consume tokens for this request
        # Note: We are replaying history, so we just subtract.
        # If history goes negative, it means they were throttled but (hypothetically) bypassed it,
        # or the algorithm parameters changed. We let it go negative to penalize future usage.
        req_cost = request.cost if request.cost else 1  # Use actual cost if stored
        current_tokens -= req_cost

        last_time = req_time

    # 5. Calculate State Now
    elapsed_since_last = (now - last_time).total_seconds()
    current_tokens = min(capacity, current_tokens + (elapsed_since_last * refill_rate_per_sec))

    # 6. Determine Throttling
    if current_tokens >= cost:
        return False, timedelta(seconds=0)
    else:
        # Calculate how long to wait until we have enough tokens
        missing = cost - current_tokens
        wait_seconds = missing / refill_rate_per_sec
        return True, timedelta(seconds=wait_seconds)


async def get_image_quality_cost(session: AsyncSession, quality_name: str) -> float:
    statement = select(ImageQualityPricing).where(ImageQualityPricing.quality == quality_name)
    result = await session.exec(statement)
    pricing = result.first()
    return pricing.credit_cost if pricing else 1.0 # Default fallback