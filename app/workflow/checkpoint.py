from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy.engine import make_url

from app.harness.errors import HarnessError


def checkpoint_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
        if (url.drivername != "sqlite" or url.host or url.username or url.password
                or url.query or not url.database or url.database == ":memory:"):
            raise ValueError("file_backed_sqlite_required")
        return Path(url.database).resolve()
    except Exception:
        raise HarnessError("invalid_checkpoint_database_url") from None


@asynccontextmanager
async def open_checkpointer(database_url: str):
    path = checkpoint_path(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        yield saver
