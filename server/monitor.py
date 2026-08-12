#!/usr/bin/env python3
import hmac
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.collector import collect_report, slug
from server.auth import (
    SESSION_TTL, create_session, ensure_setup_token, init_auth_database, initialize_admin,
    load_auth_config, migrate_legacy_auth, update_admin, verify_password, verify_session,
)

DIST = ROOT / "dist"
REGISTRY = Path(__file__).resolve().parent / "registry.json"
DATABASE = Path(os.environ.get("DATABASE_PATH", Path(__file__).resolve().parent / "port_grove.db"))
PROBE_INTERVAL = max(10, int(os.environ.get("PROBE_INTERVAL", "30")))
EMBEDDED_AGENT = os.environ.get("EMBEDDED_AGENT", "1").lower() not in ("0", "false", "no")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
TRUST_PROXY = os.environ.get("TRUST_PROXY", "0").lower() in ("1", "true", "yes")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "0").lower() in ("1", "true", "yes")
MAX_REPORT_BYTES = 2 * 1024 * 1024
COLORS = ["app-teal", "app-blue", "app-orange", "app-green", "purple", "app-yellow"]
PROJECT_PRIORITY = {"system": 0, "proxy": 1, "database": 2, "cache": 3, "queue": 4, "storage": 5, "monitoring": 6, "container": 7, "web": 8, "api": 9, "default": 10}
DB_LOCK = threading.Lock()
LOGIN_LOCK = threading.Lock()
LOGIN_ATTEMPTS = {}
AGENT_DOWNLOADS = {
    "agent.py": ROOT / "agent" / "agent.py",
    "collector.py": ROOT / "agent" / "collector.py",
    "__init__.py": ROOT / "agent" / "__init__.py",
    "port-grove-agent.service": ROOT / "deploy" / "port-grove-agent.service",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_registry():
    try:
        with REGISTRY.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"server": {}, "projects": [], "projectOverrides": {}}


def db_connection():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with db_connection() as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                hostname TEXT NOT NULL,
                public_ip TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                agent_version TEXT NOT NULL DEFAULT '',
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                embedded INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS project_overrides (
                server_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                name TEXT,
                owner TEXT,
                description TEXT,
                icon TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (server_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS port_state (
                server_id TEXT NOT NULL,
                port INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY (server_id, port)
            );
            CREATE TABLE IF NOT EXISTS enrollment_tokens (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                token_prefix TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                server_id TEXT,
                created_at TEXT NOT NULL,
                last_used TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agent_commands (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                completed_at TEXT
            );
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(servers)")}
        if "embedded" not in columns:
            db.execute("ALTER TABLE servers ADD COLUMN embedded INTEGER NOT NULL DEFAULT 0")


def token_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_enrollment_token(label=""):
    token = "pg_" + secrets.token_urlsafe(32)
    token_id = secrets.token_hex(8)
    created_at = utc_now()
    clean_label = str(label).strip()[:80] or "New server"
    with DB_LOCK, db_connection() as db:
        active = db.execute("SELECT COUNT(*) FROM enrollment_tokens WHERE server_id IS NULL AND revoked=0").fetchone()[0]
        if active >= 20:
            raise ValueError("too many unused enrollment tokens; revoke an old token first")
        db.execute("""
            INSERT INTO enrollment_tokens (id, label, token_prefix, token_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (token_id, clean_label, token[:11], token_digest(token), created_at))
    return {"id": token_id, "label": clean_label, "token": token, "tokenPrefix": token[:11], "createdAt": created_at}


def enrollment_tokens():
    with db_connection() as db:
        rows = db.execute("""
            SELECT id, label, token_prefix, server_id, created_at, last_used, revoked
            FROM enrollment_tokens ORDER BY created_at DESC LIMIT 50
        """).fetchall()
    return [{
        "id": row["id"], "label": row["label"], "tokenPrefix": row["token_prefix"],
        "serverId": row["server_id"], "createdAt": row["created_at"],
        "lastUsed": row["last_used"], "revoked": bool(row["revoked"]),
        "status": "revoked" if row["revoked"] else "bound" if row["server_id"] else "unused",
    } for row in rows]


def authorize_agent_token(value, server_id):
    if AGENT_TOKEN and hmac.compare_digest(value, AGENT_TOKEN):
        return True
    digest = token_digest(value)
    with DB_LOCK, db_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM enrollment_tokens WHERE token_hash=?", (digest,)).fetchone()
        if not row or row["revoked"] or (row["server_id"] and row["server_id"] != server_id):
            return False
        db.execute("UPDATE enrollment_tokens SET server_id=?, last_used=? WHERE id=?", (server_id, utc_now(), row["id"]))
    return True


def revoke_enrollment_token(token_id):
    with DB_LOCK, db_connection() as db:
        changed = db.execute("UPDATE enrollment_tokens SET revoked=1 WHERE id=?", (token_id,)).rowcount
    if not changed:
        raise ValueError("enrollment token not found")


def login_allowed(address):
    cutoff = time.time() - 300
    with LOGIN_LOCK:
        attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(address, []) if stamp >= cutoff]
        LOGIN_ATTEMPTS[address] = attempts
        return len(attempts) < 5


def record_login_failure(address):
    with LOGIN_LOCK:
        LOGIN_ATTEMPTS.setdefault(address, []).append(time.time())


def clear_login_failures(address):
    with LOGIN_LOCK:
        LOGIN_ATTEMPTS.pop(address, None)


def delete_server_records(db, server_id):
    db.execute("DELETE FROM project_overrides WHERE server_id=?", (server_id,))
    db.execute("DELETE FROM port_state WHERE server_id=?", (server_id,))
    db.execute("UPDATE enrollment_tokens SET revoked=1 WHERE server_id=?", (server_id,))
    db.execute("DELETE FROM servers WHERE id=?", (server_id,))


def request_server_removal(server_id, force=False):
    with DB_LOCK, db_connection() as db:
        row = db.execute("SELECT report_json, embedded, agent_version FROM servers WHERE id=?", (server_id,)).fetchone()
        if not row:
            raise ValueError("server not found")
        if row["embedded"]:
            raise ValueError("the embedded central server cannot be removed")
        if force:
            delete_server_records(db, server_id)
            db.execute("UPDATE agent_commands SET status='cancelled', completed_at=? WHERE server_id=? AND status!='completed'", (utc_now(), server_id))
            return {"status": "removed"}
        if not supports_decommission(row["agent_version"]):
            raise ValueError("this agent must be upgraded before remote decommission")
        existing = db.execute("SELECT id FROM agent_commands WHERE server_id=? AND command='decommission' AND status IN ('pending','delivered')", (server_id,)).fetchone()
        command_id = existing["id"] if existing else secrets.token_hex(12)
        if not existing:
            db.execute("INSERT INTO agent_commands (id, server_id, command, created_at) VALUES (?, ?, 'decommission', ?)", (command_id, server_id, utc_now()))
        return {"status": "pending", "commandId": command_id}


def pending_agent_commands(server_id):
    delivered_at = utc_now()
    with DB_LOCK, db_connection() as db:
        rows = db.execute("SELECT id, command FROM agent_commands WHERE server_id=? AND status IN ('pending','delivered') ORDER BY created_at", (server_id,)).fetchall()
        db.execute("UPDATE agent_commands SET status='delivered', delivered_at=? WHERE server_id=? AND status='pending'", (delivered_at, server_id))
    return [{"id": row["id"], "type": row["command"]} for row in rows]


def complete_agent_command(command_id, server_id):
    with DB_LOCK, db_connection() as db:
        row = db.execute("SELECT command FROM agent_commands WHERE id=? AND server_id=? AND status IN ('pending','delivered')", (command_id, server_id)).fetchone()
        if not row:
            raise ValueError("agent command not found")
        db.execute("UPDATE agent_commands SET status='completed', completed_at=? WHERE id=?", (utc_now(), command_id))
        if row["command"] == "decommission":
            delete_server_records(db, server_id)


def observed_client_ip(handler):
    candidate = handler.client_address[0]
    if TRUST_PROXY:
        forwarded = handler.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            candidate = forwarded
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


def apply_observed_address(report, address):
    server = report.get("server") or {}
    if not server.get("publicIp") and address:
        server["publicIp"] = address
    public_ip = server.get("publicIp", "")
    host = f"[{public_ip}]" if ":" in public_ip and not public_ip.startswith("[") else public_ip
    if not host:
        return report
    for project in report.get("projects", []):
        for item in project.get("ports", []):
            web = item.get("web") or {}
            scheme = web.get("scheme")
            if not item.get("webUrl") and item.get("exposure") == "公网" and scheme in ("http", "https"):
                item["webUrl"] = f'{scheme}://{host}:{int(item["port"])}/'
    return report


def validate_report(report):
    if report.get("schemaVersion") != 1:
        raise ValueError("unsupported agent schemaVersion")
    server = report.get("server")
    if not isinstance(server, dict) or not re.fullmatch(r"[a-zA-Z0-9._-]{3,80}", str(server.get("id", ""))):
        raise ValueError("invalid server id")
    projects = report.get("projects")
    if not isinstance(projects, list) or len(projects) > 2000:
        raise ValueError("invalid projects")
    ports = []
    for project in projects:
        if not isinstance(project, dict) or not isinstance(project.get("ports", []), list):
            raise ValueError("invalid project")
        for item in project["ports"]:
            port = int(item.get("port", 0))
            if not 1 <= port <= 65535:
                raise ValueError("invalid port")
            ports.append((project.get("id", ""), item))
    report["listenerCount"] = len({item["port"] for _, item in ports})
    return report, ports


def store_report(report, embedded=False):
    report, ports = validate_report(report)
    server = report["server"]
    server_id = server["id"]
    seen_at = utc_now()
    encoded = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    with DB_LOCK, db_connection() as db:
        existing = db.execute("SELECT embedded FROM servers WHERE id=?", (server_id,)).fetchone()
        if existing and existing["embedded"] and not embedded:
            raise ValueError("embedded server reports cannot be replaced remotely")
        db.execute("""
            INSERT INTO servers (id, name, hostname, public_ip, provider, agent_version, report_json, created_at, last_seen, embedded)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, hostname=excluded.hostname, public_ip=excluded.public_ip,
                provider=excluded.provider, agent_version=excluded.agent_version,
                report_json=excluded.report_json, last_seen=excluded.last_seen,
                embedded=MAX(servers.embedded, excluded.embedded)
        """, (
            server_id, str(server.get("name", server_id))[:120], str(server.get("hostname", ""))[:255],
            str(server.get("publicIp", ""))[:255], str(server.get("provider", ""))[:120],
            str(report.get("agentVersion", ""))[:40], encoded, seen_at, seen_at, int(embedded),
        ))
        db.execute("UPDATE port_state SET active=0 WHERE server_id=?", (server_id,))
        for project_id, item in ports:
            metadata = json.dumps({"projectId": project_id, **item}, ensure_ascii=False, separators=(",", ":"))
            db.execute("""
                INSERT INTO port_state (server_id, port, active, first_seen, last_seen, metadata_json)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(server_id, port) DO UPDATE SET
                    active=1, last_seen=excluded.last_seen, metadata_json=excluded.metadata_json
            """, (server_id, int(item["port"]), seen_at, seen_at, metadata))
    return server_id


def default_icon(project):
    text = f'{project.get("name", "")} {project.get("description", "")}'.lower()
    if any(word in text for word in ("sshd", "systemd", "system service", "系统服务", "系统网络")):
        return "system"
    if any(word in text for word in ("mysql", "postgres", "mongo", "database", "数据库")):
        return "database"
    if any(word in text for word in ("redis", "memcached", "cache", "缓存")):
        return "cache"
    if any(word in text for word in ("rabbit", "kafka", "nats", "queue", "队列")):
        return "queue"
    if any(word in text for word in ("nginx", "proxy", "gateway", "网关")):
        return "proxy"
    if any(word in text for word in ("prometheus", "grafana", "monitor", "监控")):
        return "monitoring"
    if any(word in text for word in ("minio", "sftp", "nfs", "storage", "存储")):
        return "storage"
    if any(word in text for word in ("docker", "container", "容器")):
        return "container"
    port_types = {item.get("type") for item in project.get("ports", [])}
    if "frontend" in port_types:
        return "web"
    if "backend" in port_types:
        return "api"
    return "default"


def merge_legacy_registry(report, registry):
    legacy_projects = registry.get("projects") or []
    if not legacy_projects:
        return report
    discovered = {}
    for group in report.get("projects", []):
        for item in group.get("ports", []):
            discovered[int(item["port"])] = item
    projects = []
    claimed = set()
    registry_overrides = registry.get("projectOverrides", {})
    for index, legacy in enumerate(legacy_projects):
        ports = []
        for configured in legacy.get("ports", []):
            number = int(configured["port"])
            claimed.add(number)
            detected = discovered.get(number)
            ports.append({
                **(detected or {}), **configured,
                "id": f'{legacy["id"]}-{number}',
                "port": number,
                "host": detected.get("host", "未监听") if detected else "未监听",
                "process": detected.get("process", "-") if detected else "-",
                "pid": detected.get("pid") if detected else None,
                "status": detected.get("status", "offline") if detected else "offline",
                "latency": detected.get("latency") if detected else None,
                "exposure": detected.get("exposure", "未监听") if detected else "未监听",
                "web": detected.get("web") if detected else None,
                "webUrl": detected.get("webUrl") if detected else None,
            })
        override = registry_overrides.get(legacy["id"], {})
        project = {
            **legacy, **{key: value for key, value in override.items() if value},
            "managed": "manual", "confidence": "high", "source": "registry",
            "icon": override.get("icon") or legacy.get("icon") or default_icon({**legacy, "ports": ports}),
            "color": legacy.get("color") or COLORS[index % len(COLORS)], "ports": ports,
        }
        projects.append(project)
    for group in report.get("projects", []):
        remaining = [item for item in group.get("ports", []) if int(item["port"]) not in claimed]
        if remaining:
            projects.append({**group, "ports": remaining})
    return {**report, "projects": projects}


def local_report():
    registry = read_registry()
    report = collect_report(registry.get("server", {}))
    return merge_legacy_registry(report, registry)


def embedded_probe_loop():
    while True:
        try:
            store_report(local_report(), embedded=True)
        except Exception as error:
            print(f"Port Grove embedded agent failed: {error}", flush=True)
        time.sleep(PROBE_INTERVAL)


def server_rows():
    with db_connection() as db:
        return db.execute("SELECT * FROM servers ORDER BY last_seen DESC").fetchall()


def online_status(last_seen):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_seen)).total_seconds()
        return "online" if age <= max(90, PROBE_INTERVAL * 3) else "offline"
    except (TypeError, ValueError):
        return "offline"


def supports_decommission(version):
    try:
        return tuple(int(part) for part in str(version).split(".")[:3]) >= (1, 3, 0)
    except ValueError:
        return False


def server_summaries(rows):
    with db_connection() as db:
        removing = {row["server_id"] for row in db.execute("SELECT DISTINCT server_id FROM agent_commands WHERE command='decommission' AND status IN ('pending','delivered')")}
    summaries = []
    for row in rows:
        report = json.loads(row["report_json"])
        summaries.append({
            "id": row["id"], "name": row["name"], "hostname": row["hostname"],
            "publicIp": row["public_ip"], "provider": row["provider"],
            "agentVersion": row["agent_version"], "lastSeen": row["last_seen"],
            "status": "removing" if row["id"] in removing else online_status(row["last_seen"]), "listenerCount": report.get("listenerCount", 0),
            "projectCount": len(report.get("projects", [])),
            "embedded": bool(row["embedded"]),
            "supportsDecommission": supports_decommission(row["agent_version"]),
        })
    return summaries


def apply_overrides(server_id, report):
    with db_connection() as db:
        rows = db.execute("SELECT * FROM project_overrides WHERE server_id=?", (server_id,)).fetchall()
    overrides = {row["project_id"]: dict(row) for row in rows}
    projects = []
    for index, project in enumerate(report.get("projects", [])):
        override = overrides.get(project["id"], {})
        resolved = {**project}
        for key in ("name", "owner", "description", "icon"):
            if override.get(key):
                resolved[key] = override[key]
        resolved.setdefault("icon", default_icon(resolved))
        resolved.setdefault("color", COLORS[index % len(COLORS)])
        projects.append(resolved)
    projects.sort(key=lambda item: (PROJECT_PRIORITY.get(item.get("icon", "default"), 10), item.get("name", "").lower()))
    return {**report, "projects": projects}


def status_payload(server_id=None):
    rows = server_rows()
    if not rows:
        return {"server": None, "servers": [], "projects": [], "listenerCount": 0, "serverCount": 0, "scannedAt": None}
    selected = next((row for row in rows if row["id"] == server_id), rows[0])
    report = apply_overrides(selected["id"], json.loads(selected["report_json"]))
    summaries = server_summaries(rows)
    selected_status = next(item["status"] for item in summaries if item["id"] == selected["id"])
    return {
        **report,
        "server": {**report["server"], "status": selected_status, "lastSeen": selected["last_seen"], "agentVersion": selected["agent_version"], "embedded": bool(selected["embedded"]), "supportsDecommission": supports_decommission(selected["agent_version"])},
        "servers": summaries,
        "serverCount": len(summaries),
        "onlineServerCount": sum(item["status"] == "online" for item in summaries),
        "selectedServerId": selected["id"],
        "probeInterval": PROBE_INTERVAL,
    }


def save_override(server_id, project_id, payload):
    allowed_icons = {"web", "api", "database", "proxy", "cache", "queue", "container", "monitoring", "storage", "system", "default"}
    values = {}
    for key in ("name", "owner", "description"):
        value = str(payload.get(key, "")).strip()
        if value:
            values[key] = value[:500 if key == "description" else 120]
    icon = str(payload.get("icon", "")).strip()
    if icon in allowed_icons:
        values["icon"] = icon
    if not values:
        raise ValueError("no valid project fields")
    with DB_LOCK, db_connection() as db:
        existing = db.execute("SELECT * FROM project_overrides WHERE server_id=? AND project_id=?", (server_id, project_id)).fetchone()
        merged = {key: (existing[key] if existing else None) for key in ("name", "owner", "description", "icon")}
        merged.update(values)
        db.execute("""
            INSERT INTO project_overrides (server_id, project_id, name, owner, description, icon, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id, project_id) DO UPDATE SET
                name=excluded.name, owner=excluded.owner, description=excluded.description,
                icon=excluded.icon, updated_at=excluded.updated_at
        """, (server_id, project_id, merged["name"], merged["owner"], merged["description"], merged["icon"], utc_now()))


class MonitorHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def send_json(self, payload, status=200, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, content_type="application/octet-stream", status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, limit=MAX_REPORT_BYTES):
        length = int(self.headers.get("Content-Length", "0"))
        if length > limit:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def auth_config(self):
        return load_auth_config(DATABASE)

    def dashboard_session(self):
        if AUTH_DISABLED:
            return {"username": "development"}
        config = self.auth_config()
        if not config:
            return None
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("port_grove_session")
        return verify_session(config, morsel.value) if morsel else None

    def require_dashboard_auth(self):
        if self.dashboard_session():
            return True
        self.send_json({"error": "authentication required"}, 401)
        return False

    def session_cookie(self, token, clear=False):
        secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        parts = [f"port_grove_session={token}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if clear:
            parts.extend(("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"))
        else:
            parts.append(f"Max-Age={SESSION_TTL}")
        if secure:
            parts.append("Secure")
        return "; ".join(parts)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/status":
            config = self.auth_config()
            session = self.dashboard_session()
            self.send_json({
                "authenticated": bool(session),
                "setupRequired": not AUTH_DISABLED and not bool(config),
                "username": session.get("username") if session else None,
            })
            return
        if parsed.path == "/api/agent/install.sh":
            self.send_bytes((ROOT / "scripts" / "install-agent.sh").read_bytes(), "text/x-shellscript; charset=utf-8")
            return
        download_match = re.fullmatch(r"/api/agent/files/([^/]+)", parsed.path)
        if download_match and download_match.group(1) in AGENT_DOWNLOADS:
            self.send_bytes(AGENT_DOWNLOADS[download_match.group(1)].read_bytes(), "text/plain; charset=utf-8")
            return
        if parsed.path.startswith("/api/") and not self.require_dashboard_auth():
            return
        if parsed.path == "/api/status":
            server_id = parse_qs(parsed.query).get("server", [None])[0]
            try:
                self.send_json(status_payload(server_id))
            except Exception as error:
                self.send_json({"error": str(error)}, 500)
            return
        if parsed.path == "/api/servers":
            rows = server_rows()
            self.send_json({"servers": server_summaries(rows)})
            return
        if parsed.path == "/api/enrollment-tokens":
            self.send_json({"tokens": enrollment_tokens()})
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/auth/setup":
                if AUTH_DISABLED:
                    self.send_json({"error": "setup is disabled in development auth mode"}, 400)
                    return
                if self.auth_config():
                    self.send_json({"error": "administrator is already configured"}, 409)
                    return
                address = observed_client_ip(self)
                if not login_allowed(address):
                    self.send_json({"error": "too many setup attempts"}, 429)
                    return
                payload = self.read_json(16 * 1024)
                try:
                    config = initialize_admin(
                        payload.get("username", ""), payload.get("password", ""),
                        payload.get("setupToken", ""), DATABASE,
                    )
                except ValueError:
                    record_login_failure(address)
                    time.sleep(0.25)
                    raise
                clear_login_failures(address)
                session = create_session(config)
                self.send_json({"authenticated": True, "username": config["username"]}, 201, {"Set-Cookie": self.session_cookie(session)})
                return

            if path == "/api/auth/login":
                config = self.auth_config()
                if not config and not AUTH_DISABLED:
                    self.send_json({"error": "administrator is not configured"}, 503)
                    return
                address = observed_client_ip(self)
                if not login_allowed(address):
                    self.send_json({"error": "too many login attempts"}, 429)
                    return
                payload = self.read_json(16 * 1024)
                username = str(payload.get("username", ""))
                password = str(payload.get("password", ""))
                valid = AUTH_DISABLED or (hmac.compare_digest(username, config["username"]) and verify_password(password, config["passwordHash"]))
                if not valid:
                    record_login_failure(address)
                    time.sleep(0.25)
                    self.send_json({"error": "invalid username or password"}, 401)
                    return
                clear_login_failures(address)
                session = create_session(config) if config else "development"
                self.send_json({"authenticated": True, "username": username}, 200, {"Set-Cookie": self.session_cookie(session)})
                return

            if path == "/api/auth/logout":
                self.send_json({"authenticated": False}, 200, {"Set-Cookie": self.session_cookie("", clear=True)})
                return

            if path == "/api/agent/report":
                report = apply_observed_address(self.read_json(), observed_client_ip(self))
                validate_report(report)
                authorization = self.headers.get("Authorization", "")
                supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
                if not supplied or not authorize_agent_token(supplied, report["server"]["id"]):
                    self.send_json({"error": "invalid agent token"}, 401)
                    return
                server_id = store_report(report)
                self.send_json({"ok": True, "serverId": server_id, "receivedAt": utc_now(), "commands": pending_agent_commands(server_id)}, 202)
                return

            command_match = re.fullmatch(r"/api/agent/commands/([a-f0-9]+)/ack", path)
            if command_match:
                payload = self.read_json(16 * 1024)
                server_id = str(payload.get("serverId", ""))
                authorization = self.headers.get("Authorization", "")
                supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
                if not supplied or not authorize_agent_token(supplied, server_id):
                    self.send_json({"error": "invalid agent token"}, 401)
                    return
                complete_agent_command(command_match.group(1), server_id)
                self.send_json({"ok": True}, 200)
                return

            if path.startswith("/api/") and not self.require_dashboard_auth():
                return

            payload = self.read_json()
            if path == "/api/auth/account":
                try:
                    config = update_admin(
                        payload.get("currentPassword", ""), payload.get("username", ""),
                        payload.get("newPassword", ""), DATABASE,
                    )
                except PermissionError as error:
                    self.send_json({"error": str(error)}, 401)
                    return
                session = create_session(config)
                self.send_json({"authenticated": True, "username": config["username"]}, 200, {"Set-Cookie": self.session_cookie(session)})
                return

            if path == "/api/enrollment-tokens":
                token = create_enrollment_token(payload.get("label", ""))
                self.send_json(token, 201)
                return

            token_match = re.fullmatch(r"/api/enrollment-tokens/([a-f0-9]+)/revoke", path)
            if token_match:
                revoke_enrollment_token(token_match.group(1))
                self.send_json({"ok": True}, 200)
                return

            remove_match = re.fullmatch(r"/api/servers/([^/]+)/remove", path)
            if remove_match:
                server_id = unquote(remove_match.group(1))
                result = request_server_removal(server_id, force=bool(payload.get("force")))
                self.send_json(result, 202 if result["status"] == "pending" else 200)
                return

            match = re.fullmatch(r"/api/servers/([^/]+)/projects/([^/]+)/rename", path)
            if match:
                server_id, project_id = map(unquote, match.groups())
                save_override(server_id, project_id, payload)
                self.send_json(status_payload(server_id), 200)
                return

            legacy_match = re.fullmatch(r"/api/projects/([^/]+)/rename", path)
            if legacy_match:
                current = status_payload()
                save_override(current["selectedServerId"], unquote(legacy_match.group(1)), payload)
                self.send_json(status_payload(current["selectedServerId"]), 200)
                return
            self.send_json({"error": "Not found"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send_json({"error": f"Invalid request: {error}"}, 400)
        except Exception as error:
            self.send_json({"error": str(error)}, 500)


if __name__ == "__main__":
    init_database()
    init_auth_database(DATABASE)
    migrated = migrate_legacy_auth(DATABASE)
    setup_token = ensure_setup_token(DATABASE)
    if migrated:
        print("Migrated the legacy administrator account into SQLite", flush=True)
    elif setup_token:
        print("Administrator setup is required; read the local setup token with scripts/manage-admin.py setup-code", flush=True)
    if EMBEDDED_AGENT:
        store_report(local_report(), embedded=True)
        threading.Thread(target=embedded_probe_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "2381"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MonitorHandler)
    print(f"Port Grove central server listening on 0.0.0.0:{port} ({'embedded agent enabled' if EMBEDDED_AGENT else 'remote agents only'})", flush=True)
    server.serve_forever()
