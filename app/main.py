from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes.tasks import router as tasks_router
from app.api.routes.approvals import router as approvals_router
from app.core.config import Settings, get_settings
from app.core.exceptions import TaskNotFoundError
from app.db.base import Base
from app.db.session import create_db_engine
from app.governance.approval import ApprovalConflict, ApprovalNotFound, ApprovalService
from app.tools.workspace import WorkspacePolicy
from app.tools.write import WriteTextFile


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        engine = create_db_engine(settings.database_url)
        application.state.engine = engine
        application.state.approvals = ApprovalService(engine, WriteTextFile(WorkspacePolicy(settings.workspace_root), settings.write_max_chars), settings.approval_encryption_key)
        try:
            Base.metadata.create_all(engine)
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title=settings.app_name,
        version=version("ai-agent-harness-platform"),
        lifespan=lifespan,
    )

    @application.exception_handler(TaskNotFoundError)
    async def task_not_found_handler(request: Request, exc: TaskNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/info", tags=["system"])
    def info() -> dict[str, str]:
        return {
            "project_name": settings.app_name,
            "version": application.version,
            "environment": settings.app_env,
        }

    application.include_router(tasks_router)
    application.include_router(approvals_router)

    @application.exception_handler(ApprovalNotFound)
    async def approval_not_found(request: Request, exc: ApprovalNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(ApprovalConflict)
    async def approval_conflict(request: Request, exc: ApprovalConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    return application


app = create_app()
