from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Unlike `get_db`, this hands back the *factory* rather than an open
    session — used where a request handler needs to open its own session
    later (e.g. inside an SSE generator that outlives the request's own
    `Depends` session, which FastAPI tears down as soon as the endpoint
    function returns, before a streaming body is actually consumed).
    Overridden in tests so that later session also points at the test DB."""
    return async_session_factory
