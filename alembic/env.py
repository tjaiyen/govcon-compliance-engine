from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import govcon.models  # noqa: F401 - registers every table on Base.metadata
from alembic import context
from govcon.db.base import Base
from govcon.db.engine import get_db_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# DB URL comes from GOVCON_DB_URL (defaults to sqlite:///govcon.db), not from
# alembic.ini — one source of truth shared with the application engine.
config.set_main_option("sqlalchemy.url", get_db_url())


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTERs require batch mode
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # SQLite does not enforce FKs by default, and the app engine's
    # connect-event pragma does NOT fire for Alembic's own connection — a
    # stress test found FKs silently OFF during migrations. Attach the pragma
    # as a connect-event on the engine (NOT inline on the connection, which
    # would open a transaction and break Alembic's own commit).
    if connectable.dialect.name == "sqlite":
        from sqlalchemy import event

        @event.listens_for(connectable, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite ALTERs require batch mode
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
