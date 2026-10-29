from pathlib import Path
import sys

from alembic import context

# Alembic 1.13 splits prepend_sys_path on spaces/colons, corrupting Windows paths.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.db import make_database
from app.models import Base

test = context.get_x_argument(as_dictionary=True).get("test") == "true"

if context.is_offline_mode():
    context.configure(url="mysql+pymysql://", target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    settings = Settings.from_env(test=test)
    engine, _ = make_database(settings.database_url, test=test)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
