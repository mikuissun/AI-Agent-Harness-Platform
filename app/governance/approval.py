import hashlib
import json
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from app.harness.errors import HarnessError
from app.harness.models import ToolExecutionResult, ToolRiskLevel
from app.models.approval import PendingApproval
from app.models.task import utc_now
from app.tools.write import WriteTextFile


class ApprovalNotFound(HarnessError):
    pass


class ApprovalConflict(HarnessError):
    pass


class ApprovalService:
    def __init__(self, engine: Engine, writer: WriteTextFile, encryption_key: SecretStr | None = None):
        self.engine, self.writer = engine, writer
        self._encryption_key = encryption_key

    def _cipher(self) -> Fernet:
        database = self.engine.url.database
        if not database or database == ":memory:":
            raise HarnessError("approval_requires_persistent_database")
        if self._encryption_key is None:
            raise HarnessError("approval_encryption_key_required")
        try:
            return Fernet(self._encryption_key.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeError):
            raise HarnessError("invalid_approval_encryption_key") from None

    @staticmethod
    def _view(row: PendingApproval) -> dict:
        return {key: getattr(row, key) for key in (
            "approval_id", "run_id", "task_id", "tool_name", "risk_level", "status",
            "arguments_summary", "requested_by", "decided_by", "decision", "result",
            "created_at", "decided_at", "finished_at",
        )}

    def get(self, approval_id: str) -> dict:
        with Session(self.engine) as session:
            row = session.get(PendingApproval, approval_id)
            if row is None:
                raise ApprovalNotFound("approval_not_found")
            return self._view(row)

    def request(self, task_id: int, run_id: str, role: str, tool_name: str, arguments: dict) -> str:
        if tool_name != self.writer.definition.name or role != "DEVELOPER":
            raise HarnessError("permission_denied")
        path, payload = self.writer.validate(arguments)
        encrypted = self._cipher().encrypt(json.dumps({
            "arguments": payload.model_dump(), "workspace": str(self.writer.policy.root),
            "target": str(path), "risk": self.writer.definition.risk_level.value,
        }).encode())
        identifier = str(uuid4())
        with Session(self.engine) as session:
            session.add(PendingApproval(
                approval_id=identifier, task_id=task_id, run_id=run_id, tool_name=tool_name,
                risk_level=ToolRiskLevel.WRITE.value, requested_by=role, encrypted_payload=encrypted,
                arguments_summary={"path": payload.path, "characters": len(payload.content),
                                   "sha256": hashlib.sha256(payload.content.encode()).hexdigest()},
            ))
            session.commit()
        return identifier

    def _claim(self, approval_id: str, decision: str) -> None:
        self.get(approval_id)
        with Session(self.engine) as session:
            values = {"status": "APPROVED" if decision == "APPROVE" else "REJECTED",
                      "decision": decision, "decided_by": "human", "decided_at": utc_now()}
            if decision == "REJECT":
                values.update(encrypted_payload=None, finished_at=utc_now(),
                              result=ToolExecutionResult(success=False, error="permission_denied").model_dump())
            updated = session.execute(update(PendingApproval).where(
                PendingApproval.approval_id == approval_id, PendingApproval.status == "PENDING",
            ).values(**values))
            if updated.rowcount != 1:
                raise ApprovalConflict("approval_already_decided")
            session.commit()

    def reject(self, approval_id: str) -> dict:
        self._claim(approval_id, "REJECT")
        return self.get(approval_id)

    async def approve(self, approval_id: str) -> dict:
        self._claim(approval_id, "APPROVE")
        try:
            with Session(self.engine) as session:
                row = session.get(PendingApproval, approval_id)
                payload = json.loads(self._cipher().decrypt(row.encrypted_payload))
                definition = self.writer.definition
                if (row.tool_name != definition.name or row.risk_level != ToolRiskLevel.WRITE.value
                        or definition.risk_level != ToolRiskLevel.WRITE
                        or payload["workspace"] != str(self.writer.policy.root)
                        or payload["risk"] != row.risk_level):
                    raise HarnessError("approval_policy_changed")
                target, _ = self.writer.validate(payload["arguments"])
                if str(target) != payload["target"]:
                    raise HarnessError("approval_target_changed")
            result = await self.writer.execute(payload["arguments"])
        except HarnessError as exc:
            result = ToolExecutionResult(success=False, error=str(exc))
        except Exception:
            result = ToolExecutionResult(success=False, error="approval_execution_failed")
        with Session(self.engine) as session:
            row = session.get(PendingApproval, approval_id)
            row.status = "EXECUTED" if result.success else "FAILED"
            row.result = result.model_dump(mode="json")
            row.encrypted_payload = None
            row.finished_at = utc_now()
            session.commit()
        return self.get(approval_id)

    def resolved_result(self, approval_id: str, task_id: int, run_id: str) -> ToolExecutionResult:
        record = self.get(approval_id)
        if record["task_id"] != task_id or record["run_id"] != run_id:
            raise HarnessError("approval_run_mismatch")
        if record["status"] in {"PENDING", "APPROVED"}:
            raise ApprovalConflict("approval_not_resolved")
        return ToolExecutionResult.model_validate(record["result"])
