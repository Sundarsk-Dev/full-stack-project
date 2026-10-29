from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from scripts import configure_database as setup


class DatabaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        root = setup.ROOT / "var/tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / ".env"

    def test_password_encoding_preserves_credentials_and_existing_file_bytes(self):
        original = b"# Keep this file private\r\nGROQ_API_KEY=test-only-private-key\r\nTERMSLENS_GROQ_ENABLED=false\r\nTERMSLENS_DATABASE_URL="
        self.path.write_bytes(original)
        passwords = ["p@ss:/?#%${SECRET}' \\+\U0001f4dc", "other/secret@word"]
        with patch("sqlalchemy.create_engine", side_effect=AssertionError("no connection")):
            keys = setup.configure(self.path, read_password=lambda _: passwords.pop(0))
        self.assertEqual(keys, tuple(item[0] for item in setup.ACCOUNTS))
        self.assertTrue(self.path.read_bytes().startswith(original))
        values = dotenv_values(self.path)
        app, test = (make_url(values[name]) for name in keys)
        self.assertEqual(app.password, "p@ss:/?#%${SECRET}' \\+\U0001f4dc")
        self.assertEqual(test.password, "other/secret@word")
        self.assertEqual((app.username, app.database, test.username, test.database),
                         ("termslens", "termslens_db", "termslens_test", "termslens_test"))
        self.assertEqual((app.drivername, app.host, app.port, dict(app.query)),
                         ("mysql+pymysql", "127.0.0.1", 3306, {"charset": "utf8mb4"}))
        self.assertEqual(values["GROQ_API_KEY"], "test-only-private-key")
        self.assertEqual(values["TERMSLENS_GROQ_ENABLED"], "false")

    def test_existing_url_is_preserved_and_only_missing_password_is_requested(self):
        self.path.write_text("TERMSLENS_DATABASE_URL=existing-private-value\n", encoding="utf-8")
        prompts = []
        def password(prompt):
            prompts.append(prompt)
            return "test-password"
        self.assertEqual(setup.configure(self.path, read_password=password), ("TERMSLENS_TEST_DATABASE_URL",))
        self.assertEqual(len(prompts), 1)
        self.assertIn("termslens_test@", prompts[0])
        self.assertEqual(dotenv_values(self.path)["TERMSLENS_DATABASE_URL"], "existing-private-value")
        with patch.object(setup, "getpass", side_effect=AssertionError("no prompt")) as hidden:
            self.assertEqual(setup.configure(self.path, read_password=hidden), ())
        hidden.assert_not_called()

    def test_missing_password_leaves_file_unchanged(self):
        original = b"GROQ_API_KEY=test-secret\n"
        self.path.write_bytes(original)
        passwords = iter(["first-password", ""])
        with self.assertRaises(setup.ConfigurationError):
            setup.configure(self.path, read_password=lambda _: next(passwords))
        self.assertEqual(self.path.read_bytes(), original)

    def test_copied_example_prompts_for_both_passwords(self):
        original = (setup.ROOT / ".env.example").read_bytes()
        self.path.write_bytes(original)
        passwords = iter(["application-password", "test-password"])
        keys = setup.configure(self.path, read_password=lambda _: next(passwords))
        self.assertEqual(keys, tuple(item[0] for item in setup.ACCOUNTS))
        self.assertTrue(self.path.read_bytes().startswith(original))
        self.assertEqual(dotenv_values(self.path)["TERMSLENS_GROQ_ENABLED"], "false")

    def test_replaces_only_passwords_and_preserves_existing_bytes_and_groq(self):
        self.path.write_bytes(b"GROQ_API_KEY=test-only-key\r\nTERMSLENS_GROQ_ENABLED=false\r\n")
        setup.configure(self.path, read_password=lambda _: "old-test-password")
        original = self.path.read_bytes()
        before = dotenv_values(self.path)
        passwords = iter(["new/@:application-password", "new/@:test-password"])
        keys = setup.configure(self.path, read_password=lambda _: next(passwords), replace_passwords=True)
        self.assertEqual(keys, tuple(item[0] for item in setup.ACCOUNTS))
        self.assertTrue(self.path.read_bytes().startswith(original))
        after = dotenv_values(self.path)
        for key, password in zip(keys, ["new/@:application-password", "new/@:test-password"]):
            self.assertEqual(make_url(after[key]), make_url(before[key]).set(password=password))
        self.assertEqual(after["GROQ_API_KEY"], before["GROQ_API_KEY"])
        self.assertEqual(after["TERMSLENS_GROQ_ENABLED"], "false")

    def test_replacement_rejects_other_accounts_hosts_schemas_and_invalid_urls(self):
        for value in ["not-a-url", "mysql+pymysql://root:old@127.0.0.1:3306/termslens_db",
                      "mysql+pymysql://termslens:old@remote:3306/termslens_db",
                      "mysql+pymysql://termslens:old@127.0.0.1:3306/termslens_test",
                      "mysql+pymysql://termslens:old@127.0.0.1:invalid/termslens_db"]:
            with self.subTest(value=value):
                original = ("TERMSLENS_DATABASE_URL=" + value + "\n").encode()
                self.path.write_bytes(original)
                with self.assertRaises(setup.ConfigurationError) as error:
                    setup.configure(self.path, read_password=lambda _: self.fail("no prompt"), replace_passwords=True)
                self.assertNotIn(value, str(error.exception))
                self.assertEqual(self.path.read_bytes(), original)

    def test_cancelled_replacement_keeps_both_existing_passwords(self):
        setup.configure(self.path, read_password=lambda _: "old-test-password")
        original = self.path.read_bytes()
        passwords = iter(["new-test-password", ""])
        with self.assertRaises(setup.ConfigurationError):
            setup.configure(self.path, read_password=lambda _: next(passwords), replace_passwords=True)
        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_edit_is_preserved_without_adding_urls(self):
        self.path.write_text("# original\n", encoding="utf-8")
        def password(_):
            self.path.write_text("# user changed this\n", encoding="utf-8")
            return "private-password"
        with self.assertRaises(setup.ConfigurationError): setup.configure(self.path, read_password=password)
        self.assertEqual(self.path.read_text(), "# user changed this\n")

    def test_creates_missing_env_with_only_database_settings(self):
        setup.configure(self.path, read_password=lambda _: "private-password")
        self.assertEqual(set(dotenv_values(self.path)), {item[0] for item in setup.ACCOUNTS})

    def test_noninteractive_entrypoint_refuses_password_input(self):
        with patch("sys.argv", ["configure_database.py"]), patch("sys.stdin.isatty", return_value=False), \
                patch.object(setup, "configure") as configure, self.assertRaises(SystemExit) as error:
            setup.main()
        self.assertIn("interactive terminal", str(error.exception))
        configure.assert_not_called()

    def test_getpass_cannot_fall_back_to_echoing_passwords(self):
        def unavailable(_, **kwargs):
            warnings.warn("Password input would echo", setup.GetPassWarning)
            self.fail("The hidden-input warning must stop configuration")
        with patch("sys.argv", ["configure_database.py"]), patch("sys.stdin.isatty", return_value=True), \
                patch.object(setup, "configure", side_effect=unavailable), self.assertRaises(SystemExit) as error:
            setup.main()
        self.assertIn("Hidden password input is unavailable", str(error.exception))


if __name__ == "__main__": unittest.main()
