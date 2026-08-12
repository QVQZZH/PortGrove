import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path

ITERATIONS = 310_000
SESSION_TTL = int(os.environ.get("SESSION_TTL", "43200"))
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", Path(__file__).resolve().parent / "port_grove.db"))
LEGACY_AUTH_CONFIG = Path(os.environ.get("AUTH_CONFIG", "/etc/port-grove-auth.json"))
SETUP_TOKEN_PATH = Path(os.environ.get("SETUP_TOKEN_PATH", DATABASE_PATH.parent / "setup-token"))


def b64encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_username(username):
    clean_username = str(username).strip()
    if not 3 <= len(clean_username) <= 80:
        raise ValueError("username must contain between 3 and 80 characters")
    return clean_username


def hash_password(password, salt=None):
    if not 10 <= len(password) <= 1024:
        raise ValueError("password must contain between 10 and 1024 characters")
    salt_bytes = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${b64encode(salt_bytes)}${b64encode(derived)}"


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b64decode(salt), int(iterations))
        return hmac.compare_digest(b64encode(derived), expected)
    except (ValueError, TypeError):
        return False


def valid_password_hash(stored):
    try:
        algorithm, iterations, salt, derived = stored.split("$", 3)
        return (
            algorithm == "pbkdf2_sha256" and int(iterations) >= 100_000
            and len(b64decode(salt)) >= 16 and len(b64decode(derived)) >= 32
        )
    except (ValueError, TypeError):
        return False


def db_connection(database=DATABASE_PATH):
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_auth_database(database=DATABASE_PATH):
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with db_connection(database) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                session_secret TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
    os.chmod(database, 0o600)


def load_auth_config(database=DATABASE_PATH):
    try:
        with db_connection(database) as db:
            row = db.execute("""
                SELECT username, password_hash, session_secret
                FROM admin_account WHERE id=1
            """).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {
        "username": row["username"],
        "passwordHash": row["password_hash"],
        "sessionSecret": row["session_secret"],
    }


def migrate_legacy_auth(database=DATABASE_PATH, legacy_path=LEGACY_AUTH_CONFIG):
    if load_auth_config(database):
        return False
    try:
        payload = json.loads(Path(legacy_path).read_text(encoding="utf-8"))
        username = validate_username(payload["username"])
        password_hash = payload["passwordHash"]
        session_secret = payload["sessionSecret"]
        if not valid_password_hash(password_hash):
            return False
        if len(session_secret) < 32:
            return False
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False

    now = int(time.time())
    with db_connection(database) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM admin_account WHERE id=1").fetchone():
            return False
        db.execute("""
            INSERT INTO admin_account (id, username, password_hash, session_secret, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
        """, (username, password_hash, session_secret, now, now))
    return True


def _write_private_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        handle.write("\n")
        temp_name = handle.name
    os.chmod(temp_name, 0o600)
    os.replace(temp_name, path)
    return path


def ensure_setup_token(database=DATABASE_PATH, token_path=SETUP_TOKEN_PATH):
    token_path = Path(token_path)
    if load_auth_config(database):
        token_path.unlink(missing_ok=True)
        return None
    try:
        existing = token_path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            os.chmod(token_path, 0o600)
            return existing
    except OSError:
        pass
    return_value = "pg_setup_" + secrets.token_urlsafe(24)
    _write_private_text(token_path, return_value)
    return return_value


def initialize_admin(username, password, setup_token, database=DATABASE_PATH, token_path=SETUP_TOKEN_PATH):
    clean_username = validate_username(username)
    password_hash = hash_password(password)
    try:
        expected = Path(token_path).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError("setup token is unavailable; use the recovery CLI") from error
    if not expected or not hmac.compare_digest(str(setup_token), expected):
        raise ValueError("invalid setup token")

    now = int(time.time())
    with db_connection(database) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM admin_account WHERE id=1").fetchone():
            raise ValueError("administrator is already configured")
        db.execute("""
            INSERT INTO admin_account (id, username, password_hash, session_secret, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
        """, (clean_username, password_hash, secrets.token_urlsafe(48), now, now))
    Path(token_path).unlink(missing_ok=True)
    return load_auth_config(database)


def update_admin(current_password, username, new_password="", database=DATABASE_PATH):
    clean_username = validate_username(username)
    with db_connection(database) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT password_hash FROM admin_account WHERE id=1").fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise PermissionError("current password is incorrect")
        password_hash = hash_password(new_password) if new_password else row["password_hash"]
        db.execute("""
            UPDATE admin_account
            SET username=?, password_hash=?, session_secret=?, updated_at=?
            WHERE id=1
        """, (clean_username, password_hash, secrets.token_urlsafe(48), int(time.time())))
    return load_auth_config(database)


def reset_admin(username, password, database=DATABASE_PATH, token_path=SETUP_TOKEN_PATH):
    clean_username = validate_username(username)
    password_hash = hash_password(password)
    now = int(time.time())
    with db_connection(database) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            INSERT INTO admin_account (id, username, password_hash, session_secret, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username,
                password_hash=excluded.password_hash,
                session_secret=excluded.session_secret,
                updated_at=excluded.updated_at
        """, (clean_username, password_hash, secrets.token_urlsafe(48), now, now))
    Path(token_path).unlink(missing_ok=True)
    return load_auth_config(database)


def create_session(config):
    payload = {
        "username": config["username"],
        "expires": int(time.time()) + SESSION_TTL,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = b64encode(hmac.new(config["sessionSecret"].encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_session(config, token):
    try:
        encoded, signature = token.split(".", 1)
        expected = b64encode(hmac.new(config["sessionSecret"].encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(b64decode(encoded))
        if payload.get("username") != config["username"] or int(payload.get("expires", 0)) < time.time():
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
