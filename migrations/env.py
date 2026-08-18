import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util import CommandError
from sqlalchemy import engine_from_config, pool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Client360 migrations are HAND-AUTHORED. We import DATABASE_URL from schema.py only for the connection
# URL — Alembic has NO target_metadata. app/database/schema.py is a PARTIAL, drifting MetaData (~245 of
# ~381 live tables, some columns stale), so using it as the autogenerate target would emit destructive
# DROP TABLE / DROP COLUMN / NOT-NULL operations. Rather than silently produce an empty revision,
# `alembic revision --autogenerate` is DISABLED and fails closed (see _reject_autogenerate_if_requested()
# and docs/DATABASE.md, Phase 0A). upgrade/downgrade/current/heads are unaffected.
from app.database.schema import DATABASE_URL  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_AUTOGEN_DISABLED_MESSAGE = (
    "alembic revision --autogenerate is DISABLED for Client360.\n"
    "Migrations are hand-authored: run\n"
    '    alembic revision -m "<short description>"\n'
    "and write upgrade()/downgrade() by hand.\n"
    "(Autogenerate against the partial app/database/schema.py would emit destructive DROP TABLE / "
    "DROP COLUMN / NOT-NULL operations.) See docs/DATABASE.md."
)


def _reject_autogenerate_if_requested() -> None:
    """Fail closed: ``alembic revision --autogenerate`` terminates HERE, before any revision file is
    created. Plain upgrade / downgrade / current / heads / ``revision`` (hand-authored) never set the
    autogenerate flag and are unaffected."""
    if getattr(getattr(config, "cmd_opts", None), "autogenerate", False):
        raise CommandError(_AUTOGEN_DISABLED_MESSAGE)


def run_migrations_offline() -> None:
    _reject_autogenerate_if_requested()
    context.configure(
        url=DATABASE_URL,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _reject_autogenerate_if_requested()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
