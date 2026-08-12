import json
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from server.auth import (
    create_session, ensure_setup_token, hash_password, init_auth_database,
    initialize_admin, load_auth_config, migrate_legacy_auth, reset_admin,
    update_admin, verify_password, verify_session,
)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "port-grove.db"
        self.token_path = root / "setup-token"
        init_auth_database(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_run_setup_is_one_time(self):
        token = ensure_setup_token(self.database, self.token_path)
        self.assertTrue(token.startswith("pg_setup_"))
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.token_path.stat().st_mode), 0o600)
        config = initialize_admin("admin", "correct horse battery", token, self.database, self.token_path)
        self.assertFalse(self.token_path.exists())
        self.assertTrue(verify_password("correct horse battery", config["passwordHash"]))
        with self.assertRaises(ValueError):
            initialize_admin("second", "correct horse battery", token, self.database, self.token_path)

    def test_account_update_invalidates_existing_sessions(self):
        reset_admin("admin", "correct horse battery", self.database, self.token_path)
        old_config = load_auth_config(self.database)
        old_session = create_session(old_config)
        with self.assertRaises(PermissionError):
            update_admin("wrong password", "new-admin", "another secure phrase", self.database)
        new_config = update_admin("correct horse battery", "new-admin", "another secure phrase", self.database)
        self.assertIsNone(verify_session(new_config, old_session))
        self.assertTrue(verify_password("another secure phrase", new_config["passwordHash"]))
        self.assertEqual(new_config["username"], "new-admin")

    def test_concurrent_setup_creates_only_one_administrator(self):
        token = ensure_setup_token(self.database, self.token_path)

        def attempt(username):
            try:
                initialize_admin(username, "concurrent secure phrase", token, self.database, self.token_path)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ("first-admin", "second-admin")))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

    def test_legacy_json_migrates_once(self):
        legacy = self.database.parent / "legacy.json"
        legacy.write_text(json.dumps({
            "username": "legacy-admin",
            "passwordHash": hash_password("legacy secure phrase"),
            "sessionSecret": "s" * 48,
        }), encoding="utf-8")
        self.assertTrue(migrate_legacy_auth(self.database, legacy))
        self.assertFalse(migrate_legacy_auth(self.database, legacy))
        self.assertEqual(load_auth_config(self.database)["username"], "legacy-admin")


if __name__ == "__main__":
    unittest.main()
