#!/usr/bin/env python3
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.auth import DATABASE_PATH, SETUP_TOKEN_PATH, init_auth_database, migrate_legacy_auth, reset_admin


def main():
    parser = argparse.ArgumentParser(description="Create or update the Port Grove dashboard administrator")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("passwords do not match")
    init_auth_database(DATABASE_PATH)
    migrate_legacy_auth(DATABASE_PATH)
    config = reset_admin(args.username, password, DATABASE_PATH, SETUP_TOKEN_PATH)
    print(f"Administrator '{config['username']}' saved in {DATABASE_PATH}; all previous sessions are invalid")


if __name__ == "__main__":
    main()
