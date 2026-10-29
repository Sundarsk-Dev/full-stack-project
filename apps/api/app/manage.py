"""Read-only checks for a configured MySQL database."""
import argparse
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .db import SCHEMA_REVISION, make_database
from .diagnostics import inspect_job, runtime_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Check termslens_test with external review disabled")
    parser.add_argument("--migrations", action="store_true", help="Also require the current schema migration")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--diagnostics", action="store_true", help="Report dependency/config presence without connecting or revealing secrets")
    modes.add_argument("--job", type=UUID, help="Inspect safe metadata for one job; no changes or provider calls")
    args = parser.parse_args()
    settings = Settings.from_env(test=args.test)
    if args.diagnostics:
        print(json.dumps(runtime_report(settings), indent=2))
        return
    try:
        engine, factory = make_database(settings.database_url, test=args.test)
        if args.job:
            with factory() as db:
                result = inspect_job(db, str(args.job))
                if result is None:
                    print("Job not found.")
                    raise SystemExit(1)
                print(json.dumps(result, indent=2))
            engine.dispose()
            return
        with engine.connect() as connection:
            version, database, charset = connection.execute(text("SELECT VERSION(), DATABASE(), @@character_set_database")).one()
            if "MariaDB" in version or int(version.split(".")[0]) < 8 or charset != "utf8mb4":
                raise ValueError("MySQL 8+ with utf8mb4 is required.")
            if args.migrations:
                if connection.execute(text("SELECT version_num FROM alembic_version")).scalar() != SCHEMA_REVISION:
                    raise ValueError("Apply the current schema migration.")
            print(f"PASS: MySQL {version}; database={database}; charset={charset}")
            if args.migrations: print(f"PASS: schema revision {SCHEMA_REVISION} is applied.")
        engine.dispose()
    except (SQLAlchemyError, ValueError, ImportError):
        print("NOT READY: verify installed dependencies, MySQL service/login, the isolated database and migrations. Credentials are not printed.")
        raise SystemExit(1)


if __name__ == "__main__": main()
