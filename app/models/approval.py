from datetime import datetime

from sqlalchemy import DateTime, JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.task import utc_now


class PendingApproval(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128))
    task_id: Mapped[int]
    tool_name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    arguments_summary: Mapped[dict] = mapped_column(JSON)
    encrypted_payload: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(24))
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
