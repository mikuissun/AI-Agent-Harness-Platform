from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.task import TaskCreate, TaskRead
from app.services import tasks as task_service

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, session: SessionDependency):
    return task_service.create_task(session, payload)


@router.get("", response_model=list[TaskRead])
def list_tasks(session: SessionDependency):
    return task_service.list_tasks(session)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, session: SessionDependency):
    return task_service.get_task(session, task_id)
