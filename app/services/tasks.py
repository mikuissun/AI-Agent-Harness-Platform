from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TaskNotFoundError
from app.models.task import Task
from app.schemas.task import TaskCreate


def create_task(session: Session, payload: TaskCreate) -> Task:
    task = Task(title=payload.title, instruction=payload.instruction)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def get_task(session: Session, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    return task


def list_tasks(session: Session) -> list[Task]:
    return list(session.scalars(select(Task).order_by(Task.id)))
