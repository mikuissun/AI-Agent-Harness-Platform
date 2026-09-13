import os
from pathlib import Path

from app.harness.errors import HarnessError


class WorkspacePolicy:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise HarnessError("invalid_workspace")

    def resolve(self, value: str = ".", *, directory: bool = False) -> Path:
        if not isinstance(value, str) or not value or "\0" in value:
            raise HarnessError("invalid_path")
        if os.name == "nt" and ":" in value.removeprefix(Path(value).drive):
            raise HarnessError("invalid_path")  # Block NTFS alternate data streams.
        path = Path(value)
        if path.drive and not path.is_absolute():
            raise HarnessError("invalid_path")
        try:
            resolved = (self.root / path).resolve(strict=True)
            relative = resolved.relative_to(self.root)
        except ValueError:
            raise HarnessError("workspace_escape") from None
        except (OSError, RuntimeError):
            # Check lexical traversal even when an escaped target does not exist.
            try:
                (self.root / path).resolve().relative_to(self.root)
            except ValueError:
                raise HarnessError("workspace_escape") from None
            raise HarnessError("path_unavailable") from None
        for part in relative.parts:
            if self.is_sensitive(part):
                raise HarnessError("sensitive_path_denied")
        if directory and not resolved.is_dir():
            raise HarnessError("not_a_directory")
        return resolved

    @staticmethod
    def is_sensitive(name: str) -> bool:
        name = name.lower()
        return (
            name in {".git", ".ssh", ".aws", ".azure", "id_rsa", "id_ed25519"}
            or (name.startswith(".env") and name != ".env.example")
            or name.endswith((".pem", ".key", ".pfx", ".p12", ".approval-key"))
        )
