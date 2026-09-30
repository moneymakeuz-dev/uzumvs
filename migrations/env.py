from alembic import context
from sqlalchemy import create_engine

from app import models
from app.config import get_settings
from app.db import Base

target_metadata = Base.metadata


def run_migrations() -> None:
    database_url = get_settings().database_url.get_secret_value()
    if context.is_offline_mode():
        context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


assert models.__all__
run_migrations()
