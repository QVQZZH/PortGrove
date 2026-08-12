#!/usr/bin/env python3
import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.auth import (
    DATABASE_PATH, SETUP_TOKEN_PATH, ensure_setup_token, init_auth_database,
    load_auth_config, migrate_legacy_auth, reset_admin,
)


def confirmed_password(parser):
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("passwords do not match")
    return password


def main():
    parser = argparse.ArgumentParser(description="Manage the Port Grove dashboard administrator")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--setup-token-file", type=Path, default=SETUP_TOKEN_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="show whether the administrator is configured")
    commands.add_parser("setup-code", help="show or generate the one-time first-run setup code")
    reset = commands.add_parser("reset", help="create or reset the only administrator")
    reset.add_argument("--username", default="admin")
    args = parser.parse_args()

    init_auth_database(args.database)
    migrate_legacy_auth(args.database)
    config = load_auth_config(args.database)

    if args.command == "status":
        print(f"Administrator: {config['username']}" if config else "Administrator is not configured")
        return
    if args.command == "setup-code":
        if config:
            parser.error("administrator is already configured; use reset for account recovery")
        print(ensure_setup_token(args.database, args.setup_token_file))
        return

    password = confirmed_password(parser)
    config = reset_admin(args.username, password, args.database, args.setup_token_file)
    print(f"Administrator '{config['username']}' saved in {args.database}; all previous sessions are invalid")


if __name__ == "__main__":
    main()
