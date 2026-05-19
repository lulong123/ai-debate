from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent migration for existing databases
        result = await conn.execute(text("PRAGMA table_info(sessions)"))
        columns = [row[1] for row in result]
        if "has_data_clerk" not in columns:
            await conn.execute(text(
                "ALTER TABLE sessions ADD COLUMN has_data_clerk BOOLEAN DEFAULT 0"
            ))
        if "preliminary_data" not in columns:
            await conn.execute(text(
                "ALTER TABLE sessions ADD COLUMN preliminary_data JSON"
            ))

        # Data pool publish_date migration
        result = await conn.execute(text("PRAGMA table_info(data_pool)"))
        pool_columns = [row[1] for row in result]
        if "publish_date" not in pool_columns:
            await conn.execute(text(
                "ALTER TABLE data_pool ADD COLUMN publish_date VARCHAR(50) DEFAULT ''"
            ))


async def get_db():
    async with async_session() as session:
        yield session
