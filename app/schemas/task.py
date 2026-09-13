from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.harness.models import TaskStatus


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=255)
    instruction: str = Field(min_length=1)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    instruction: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
