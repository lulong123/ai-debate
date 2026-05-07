from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Message, DiscussionSession, Position, SessionStatus, DataPoolItem


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_session(self, topic: str, max_rounds: int = 3) -> DiscussionSession:
        session = DiscussionSession(topic=topic, max_rounds=max_rounds)
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get_session(self, session_id: str) -> DiscussionSession | None:
        result = await self.db.execute(
            select(DiscussionSession).where(DiscussionSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def update_session(self, session: DiscussionSession) -> DiscussionSession:
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def add_message(self, session_id: str, seq: int, role, content: str, **kwargs) -> Message:
        msg = Message(session_id=session_id, seq=seq, role=role, content=content, **kwargs)
        self.db.add(msg)
        await self.db.commit()
        return msg

    async def get_messages(self, session_id: str) -> list[Message]:
        result = await self.db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.seq)
        )
        return list(result.scalars().all())

    async def add_position(self, session_id: str, name: str, description: str, is_custom: bool = False, position_id: str | None = None) -> Position:
        position = Position(id=position_id, session_id=session_id, name=name, description=description, is_custom=is_custom)
        self.db.add(position)
        await self.db.commit()
        return position

    async def get_active_positions(self, session_id: str) -> list[Position]:
        result = await self.db.execute(
            select(Position).where(Position.session_id == session_id)
        )
        return list(result.scalars().all())

    async def list_sessions(self, limit: int = 20) -> list[DiscussionSession]:
        result = await self.db.execute(
            select(DiscussionSession)
            .order_by(DiscussionSession.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_data_pool_item(
        self, session_id: str, source: str, title: str,
        snippet: str = "", url: str = "", round_number: int | None = None,
    ) -> DataPoolItem:
        item = DataPoolItem(
            session_id=session_id, source=source, title=title,
            snippet=snippet, url=url, round_number=round_number,
        )
        self.db.add(item)
        await self.db.commit()
        return item

    async def get_data_pool(self, session_id: str) -> list[DataPoolItem]:
        result = await self.db.execute(
            select(DataPoolItem)
            .where(DataPoolItem.session_id == session_id)
            .order_by(DataPoolItem.created_at)
        )
        return list(result.scalars().all())
