import asyncio

from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.payment_helpers import process_due_subscription_renewals
from app.db.database import engine


async def main() -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await process_due_subscription_renewals(session)
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
