import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionStatus(str, enum.Enum):
    CLARIFYING = "clarifying"
    SELECTING_POSITIONS = "selecting_positions"
    DISCUSSING = "discussing"
    COMPLETED = "completed"
    FAILED = "failed"


class DiscussionSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    refined_topic: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.CLARIFYING
    )
    max_rounds: Mapped[int] = mapped_column(Integer, default=3)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    minutes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    has_data_clerk: Mapped[bool] = mapped_column(default=False)
    preliminary_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", order_by="Message.seq"
    )
    positions: Mapped[list["Position"]] = relationship(back_populates="session")
    data_pool: Mapped[list["DataPoolItem"]] = relationship(
        back_populates="session", order_by="DataPoolItem.created_at"
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("sessions.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_custom: Mapped[bool] = mapped_column(default=False)

    session: Mapped["DiscussionSession"] = relationship(back_populates="positions")


class MessageRole(str, enum.Enum):
    MODERATOR = "moderator"
    PERSPECTIVE = "perspective"
    SYSTEM = "system"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("sessions.id"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(50), nullable=True)
    position_id: Mapped[str] = mapped_column(String(12), nullable=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped["DiscussionSession"] = relationship(back_populates="messages")


class DataPoolItem(Base):
    __tablename__ = "data_pool"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("sessions.id"))
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "data_clerk" or "user"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    publish_date: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    key_facts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: extracted key facts
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped["DiscussionSession"] = relationship(back_populates="data_pool")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "snippet": self.snippet,
            "url": self.url,
            "publish_date": self.publish_date,
            "key_facts": self.key_facts,
            "is_public": self.is_public,
            "round_number": self.round_number,
        }
