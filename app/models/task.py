from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.harness.models import TaskStatus


def utc_now() -> datetime:
    # SQLite stores naive timestamps; all persisted timestamps are UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, create_constraint=True, validate_strings=True),
        default=TaskStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
