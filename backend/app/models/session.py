import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.database import Base


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionStatus(str, enum.Enum):
    CLARIFYING = "clarifying"
    SELECTING_ANGLES = "selecting_angles"
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

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", order_by="Message.seq"
    )
    angles: Mapped[list["Angle"]] = relationship(back_populates="session")


class Angle(Base):
    __tablename__ = "angles"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("sessions.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_custom: Mapped[bool] = mapped_column(default=False)
    conceded: Mapped[bool] = mapped_column(default=False)

    session: Mapped["DiscussionSession"] = relationship(back_populates="angles")


class MessageRole(str, enum.Enum):
    MODERATOR = "moderator"
    PERSPECTIVE = "perspective"
    SCORER = "scorer"
    SYSTEM = "system"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(12), ForeignKey("sessions.id"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(50), nullable=True)
    angle_id: Mapped[str] = mapped_column(String(12), nullable=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped["DiscussionSession"] = relationship(back_populates="messages")
