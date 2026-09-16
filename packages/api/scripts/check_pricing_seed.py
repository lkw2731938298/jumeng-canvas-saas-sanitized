import asyncio

from app.models.database import async_session
from app.services.model_catalog import sync_model_catalog


async def main() -> None:
    async with async_session() as session:
        stats = await sync_model_catalog(session)
        await session.commit()
        print(stats)


if __name__ == "__main__":
    asyncio.run(main())
