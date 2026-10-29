"""Save missing local MySQL URLs using hidden password prompts; never connects."""
import argparse
from getpass import GetPassWarning, getpass
from io import StringIO
from pathlib import Path
import sys
import warnings

from dotenv import dotenv_values
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = (("TERMSLENS_DATABASE_URL", "termslens", "termslens_db"),
            ("TERMSLENS_TEST_DATABASE_URL", "termslens_test", "termslens_test"))


class ConfigurationError(Exception):
    """Fixed user-facing messages only; no credentials or URL values."""


def configure(path, *, read_password=getpass, replace_passwords=False):
    path = Path(path)
    original = path.read_bytes() if path.exists() else b""
    current = dotenv_values(stream=StringIO(original.decode("utf-8")), interpolate=False)
    updates = []
    for key, username, database in ACCOUNTS:
        if current.get(key) and not replace_passwords:
            continue  # Preserve any configured value, including user-selected accounts/hosts.
        url = URL.create("mysql+pymysql", username=username, host="127.0.0.1", port=3306,
                         database=database, query={"charset": "utf8mb4"})
        if current.get(key):
            try:
                url = make_url(current[key])
                if (url.drivername, url.username, url.host, url.port or 3306, url.database) != (
                        "mysql+pymysql", username, "127.0.0.1", 3306, database):
                    raise ValueError()
            except (ArgumentError, ValueError):
                raise ConfigurationError("Password replacement requires the documented local MySQL accounts. "
                                         "No configuration was saved.") from None
        password = read_password(f"Password for {username}@127.0.0.1 (hidden): ")
        if not password:
            raise ConfigurationError("A password is required. No configuration was saved.")
        updates.append((key, url.set(password=password).render_as_string(hide_password=False)))
    if not updates:
        return ()
    if (path.read_bytes() if path.exists() else b"") != original:
        raise ConfigurationError("The .env file changed while entering passwords. Run again; nothing was saved.")
    # Append missing settings or explicit password overrides. Keep existing bytes/Groq settings intact.
    # dotenv uses the last definition, including when replacing a rotated password.
    addition = ("\n" if original and not original.endswith(b"\n") else "")
    addition += "".join(f"{key}={url}\n" for key, url in updates)
    with path.open("ab") as stream:
        stream.write(addition.encode("utf-8"))
    return tuple(key for key, _ in updates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-passwords", action="store_true",
                        help="After local MySQL rotation, save new passwords for both local accounts; "
                             "append URL overrides while preserving existing .env bytes")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Run in an interactive terminal; password input must remain hidden.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            keys = configure(ROOT / ".env", replace_passwords=args.replace_passwords)
    except GetPassWarning:
        raise SystemExit("Hidden password input is unavailable. Use a normal local terminal.") from None
    except ConfigurationError as error:
        raise SystemExit(str(error)) from None
    except (OSError, UnicodeError):
        raise SystemExit("Could not read or save .env. Check local file access; credentials are not printed.") from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("Password entry cancelled.") from None
    print("Saved " + ", ".join(keys) + ". Passwords and URLs are not printed." if keys else
          "Database URLs already have values; no changes made. Verify them using app.manage.")


if __name__ == "__main__": main()
