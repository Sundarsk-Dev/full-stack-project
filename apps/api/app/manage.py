"""Read-only checks for a configured MySQL database."""
import argparse
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from .config import Settings
from .db import SCHEMA_REVISION, make_database
from .diagnostics import inspect_job, runtime_report


def database_failure(error, stage):
    """Return only fixed diagnostics; driver messages can contain credentials/SQL."""
    original = getattr(error, "orig", error)
    code = original.args[0] if original.args and type(original.args[0]) is int else None
    socket_error = getattr(original, "original_exception", None)
    if getattr(socket_error, "winerror", None) == 10013:
        return "socket_access_denied", "Windows blocked socket access. Run from a normal local terminal and check host restrictions."
    messages = {
        1045: "MySQL rejected the saved login. Check the existing app account/password; update private .env with scripts/configure_database.py --replace-passwords.",
        1044: "The saved account cannot access its database. Check the existing account's scoped grants.",
        1049: "The configured database does not exist. Follow the README schema setup.",
        2002: "Cannot connect to MySQL. Check its service and the configured host/port.",
        2003: "Cannot connect to MySQL. Check its service and the configured host/port; a running service alone does not prove this connection works.",
        2005: "The configured MySQL hostname could not be resolved. Check the private database URL.",
        2026: "MySQL TLS negotiation failed. Check the local server/client TLS configuration.",
    }
    if code in messages: return f"mysql_{code}", messages[code]
    if code in {1054, 1146}:
        return "schema_missing", "Required schema objects are missing. Stop API/worker and apply the README migrations to the selected database."
    if isinstance(error, ImportError):
        return "dependency_missing", "Use the project .venv and install apps/api/requirements-dev.txt."
    if stage == "configuration" and isinstance(error, (ValueError, ArgumentError)):
        return "database_configuration", "Check the private database URL: mysql+pymysql and the selected termslens_db/termslens_test schema are required."
    if isinstance(error, ValueError):
        if stage == "server_validation":
            return "server_configuration", "The selected server must be MySQL 8+ with an utf8mb4 database."
        if stage == "schema_validation":
            return "schema_outdated", f"Apply the README migrations; the required revision is {SCHEMA_REVISION}."
    return "database_check_failed", "The database check failed. Inspect local setup privately; credentials and driver messages are not printed."


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
    engine, stage = None, "configuration"
    try:
        engine, factory = make_database(settings.database_url, test=args.test)
        stage = "connection"
        if args.job:
            with factory() as db:
                result = inspect_job(db, str(args.job))
                if result is None:
                    print("Job not found.")
                    raise SystemExit(1)
                print(json.dumps(result, indent=2))
            return
        with engine.connect() as connection:
            version, database, charset = connection.execute(text("SELECT VERSION(), DATABASE(), @@character_set_database")).one()
            stage = "server_validation"
            if "MariaDB" in version or int(version.split(".")[0]) < 8 or charset != "utf8mb4":
                raise ValueError("MySQL 8+ with utf8mb4 is required.")
            if args.migrations:
                stage = "schema_validation"
                if connection.execute(text("SELECT version_num FROM alembic_version")).scalar() != SCHEMA_REVISION:
                    raise ValueError("Apply the current schema migration.")
            print(f"PASS: MySQL {version}; database={database}; charset={charset}")
            if args.migrations: print(f"PASS: schema revision {SCHEMA_REVISION} is applied.")
    except (SQLAlchemyError, ValueError, ImportError) as error:
        code, hint = database_failure(error, stage)
        print(f"NOT READY [{code}]: {hint}")
        raise SystemExit(1)
    finally:
        if engine is not None: engine.dispose()


if __name__ == "__main__": main()
