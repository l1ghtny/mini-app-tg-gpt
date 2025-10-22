from sqlmodel import create_engine, Session
from app.core.config import settings

connect_args = {"options": "-c search_path=public"}
engine = create_engine(settings.DATABASE_URL, echo=True, connect_args=connect_args)


def get_session():
    with Session(engine) as session:
        yield session