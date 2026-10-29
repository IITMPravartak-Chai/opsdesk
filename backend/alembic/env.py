"""Alembic environment. Runs as the schema OWNER (MIGRATION_DATABASE_URL), never as the RLS-bound app role."""
from logging.config import fileConfig

import pgvector.sqlalchemy  # noqa: F401  (registers the VECTOR type for reflection/autogenerate)
from alembic import context
from sqlalchemy import engine_from_config, pool

from opsdesk.db import models  # noqa: F401  (imports every model into Base.metadata)
from opsdesk.db.base import Base
from opsdesk.settings import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", settings.migration_sqlalchemy_url.replace("%", "%%"))
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # PostgreSQL rewrites the expression of this functional GIN index (adds casts), so it never
    # compares equal to the model's text. It is verified by name in tests instead.
    if type_ == "index" and name == "ticket_fts_idx":
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"}, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
