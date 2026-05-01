"""PostgreSQL client for metadata."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
Base = declarative_base()
engine = create_async_engine("postgresql+asyncpg://cloudsense:cloudsense@localhost:5432/cloudsense",
    echo=False, future=True, pool_size=10, max_overflow=20)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
