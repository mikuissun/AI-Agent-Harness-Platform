from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.task import utc_now


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[int]
    thread_id: Mapped[str] = mapped_column(String(128))
    workspace: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trace: Mapped[list] = mapped_column(JSON)
    pending_approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    usage: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
