from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


def create_db_engine(database_url: str) -> Engine:
    return create_engine(database_url, connect_args={"check_same_thread": False})


def get_session(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.engine) as session:
        yield session
