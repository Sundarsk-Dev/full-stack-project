from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

SCHEMA_REVISION = "20261029_lifecycle"


def utcnow():
    """Database datetimes are UTC, stored without a timezone for MySQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value):
    return value.isoformat(timespec="milliseconds") + "Z" if value else None


def make_database(url: str, *, test: bool = False):
    if not url:
        raise ValueError("Set TERMSLENS_DATABASE_URL in the project .env file.")
    parsed = make_url(url)
    expected = "termslens_test" if test else "termslens_db"
    if parsed.drivername != "mysql+pymysql" or parsed.database != expected:
        raise ValueError(f"Use mysql+pymysql and the isolated {expected} database.")
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800, isolation_level="READ COMMITTED",
                           connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10, "charset": "utf8mb4"})
    return engine, sessionmaker(engine, expire_on_commit=False)
