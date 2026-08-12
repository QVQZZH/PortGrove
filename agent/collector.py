#!/usr/bin/env python3
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

AGENT_VERSION = "1.3.0"
PUBLIC_BINARIES = {"nginx", "caddy", "apache2", "httpd", "traefik"}
SERVICE_PROCESSES = {
    "sshd", "sftpgo", "systemd-resolve", "cupsd", "dnsmasq", "named",
    "redis-server", "mongod", "postgres", "mysqld", "containerd",
}
FRONTEND_HINTS = ("vite", "next", "nuxt", "react", "vue", "astro", "webpack", "parcel")
BACKEND_HINTS = (
    "uvicorn", "gunicorn", "fastapi", "flask", "django", "express", "nestjs",
    "spring", "tomcat", "gin", "fiber", "rails", "php-fpm",
)
GENERIC_DIRS = {
    "", "/", "root", "home", "www", "html", "current", "releases", "shared",
    "src", "app", "dist", "build", "bin", "sbin",
}


def slug(value):
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return cleaned or "service"


def stable_server_id(hostname=None):
    configured = os.environ.get("SERVER_ID", "").strip()
    if configured:
        return slug(configured)
    machine_id = ""
    try:
        machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    seed = f"{machine_id}:{hostname or socket.gethostname()}"
    return "srv-" + hashlib.sha256(seed.encode()).hexdigest()[:16]


def read_proc_text(pid, name):
    try:
        return Path(f"/proc/{pid}/{name}").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_proc_link(pid, name):
    try:
        return os.readlink(f"/proc/{pid}/{name}")
    except OSError:
        return ""


def process_user(pid):
    try:
        import pwd
        return pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
    except (OSError, KeyError):
        return "unknown"


def manifest_details(cwd):
    if not cwd:
        return {}
    root = Path(cwd)
    for _ in range(3):
        package = root / "package.json"
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
                dependencies = " ".join({**data.get("dependencies", {}), **data.get("devDependencies", {})}).lower()
                framework = next((name for name in FRONTEND_HINTS if name in dependencies), "node")
                return {"root": str(root), "name": data.get("name", root.name), "kind": "node", "framework": framework}
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        markers = (
            ("pyproject.toml", "python"), ("requirements.txt", "python"),
            ("go.mod", "go"), ("Cargo.toml", "rust"), ("pom.xml", "java"),
            ("composer.json", "php"),
        )
        for filename, kind in markers:
            if (root / filename).is_file():
                return {"root": str(root), "name": root.name, "kind": kind, "framework": kind}
        if root.parent == root:
            break
        root = root.parent
    return {}


def process_details(pid):
    if not pid:
        return {"cmdline": "", "cwd": "", "exe": "", "systemdUnit": "", "user": "system", "manifest": {}}
    cmdline = read_proc_text(pid, "cmdline").replace("\x00", " ").strip()[:1024]
    cgroup = read_proc_text(pid, "cgroup")
    cwd = read_proc_link(pid, "cwd")
    unit_match = re.search(r"([^/\s]+\.service)", cgroup)
    container_match = re.search(r"(?:docker-|cri-containerd-|libpod-)?([a-f0-9]{12,64})(?:\.scope)?", cgroup)
    return {
        "cmdline": cmdline,
        "cwd": cwd,
        "exe": read_proc_link(pid, "exe"),
        "systemdUnit": unit_match.group(1) if unit_match else "",
        "containerId": container_match.group(1)[:12] if container_match else "",
        "user": process_user(pid),
        "manifest": manifest_details(cwd),
    }


def listeners():
    try:
        result = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"cannot execute ss: {error}") from error
    if result.returncode and not result.stdout:
        raise RuntimeError(result.stderr.strip() or "ss returned no listener data")
    found = {}
    process_cache = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        endpoint = parts[3]
        match = re.search(r":(\d+)$", endpoint)
        if not match:
            continue
        port = int(match.group(1))
        bind = endpoint[:match.start()].strip("[]")
        process_match = re.search(r'users:\(\("([^\"]+)",pid=(\d+)', line)
        process = process_match.group(1) if process_match else "system"
        pid = int(process_match.group(2)) if process_match else None
        details = process_cache.setdefault(pid, process_details(pid)) if pid else process_details(None)
        candidate = {"port": port, "bind": bind, "process": process, "pid": pid, **details}
        current = found.get(port)
        if not current or bind in ("0.0.0.0", "*", "::"):
            found[port] = candidate
    return found


def tcp_probe(port):
    started = time.perf_counter()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return "healthy", max(1, round((time.perf_counter() - started) * 1000, 1))
    except OSError:
        return "offline", None


def _http_request(port, https=False):
    connection_class = http.client.HTTPSConnection if https else http.client.HTTPConnection
    kwargs = {"timeout": 0.45}
    if https:
        kwargs["context"] = ssl._create_unverified_context()
    connection = connection_class("127.0.0.1", port, **kwargs)
    try:
        connection.request("GET", "/", headers={"Host": "localhost", "User-Agent": "Port-Grove-Agent/1", "Accept": "text/html,application/json;q=0.8"})
        response = connection.getresponse()
        content_type = response.getheader("Content-Type", "").lower()
        body = response.read(4096).decode("utf-8", errors="ignore")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        return {
            "scheme": "https" if https else "http",
            "status": response.status,
            "contentType": content_type.split(";", 1)[0],
            "title": re.sub(r"\s+", " ", title_match.group(1)).strip()[:120] if title_match else "",
            "isHtml": "text/html" in content_type or bool(re.search(r"<!doctype html|<html", body, re.I)),
        }
    finally:
        connection.close()


def web_probe(port):
    for https in (False, True):
        try:
            return _http_request(port, https=https)
        except (OSError, ssl.SSLError, http.client.HTTPException):
            continue
    return None


def project_from_path(path):
    if not path:
        return None
    resolved = Path(path)
    anchors = ("/home/codex_project", "/var/www", "/srv", "/opt", "/workspace")
    for anchor in anchors:
        try:
            relative = resolved.relative_to(anchor)
            first = relative.parts[0] if relative.parts else ""
            if first and first.lower() not in GENERIC_DIRS:
                return first
        except ValueError:
            continue
    name = resolved.name
    return name if name and name.lower() not in GENERIC_DIRS else None


def infer_project(listener):
    manifest = listener.get("manifest") or {}
    if manifest.get("name"):
        return manifest["name"], "manifest", manifest.get("root", ""), "high"
    for value in (listener.get("cwd"), listener.get("exe")):
        project = project_from_path(value)
        if project:
            return project, "path", value, "high"
    unit = listener.get("systemdUnit", "")
    if unit:
        unit_name = unit.removesuffix(".service")
        if unit_name not in ("ssh", "nginx", "cron", "dbus", "systemd-resolved", "cups"):
            return unit_name, "systemd", unit, "high"
    fallback = listener.get("process") or "system-service"
    return fallback, "process", fallback, "low"


def infer_service_type(listener, web):
    process = listener.get("process", "").lower()
    text = " ".join((listener.get("cmdline", ""), listener.get("cwd", ""), listener.get("systemdUnit", ""))).lower()
    if process in SERVICE_PROCESSES or process in PUBLIC_BINARIES:
        return "service"
    if web and web.get("isHtml") and not any(hint in text for hint in BACKEND_HINTS):
        return "frontend"
    if any(hint in text for hint in FRONTEND_HINTS):
        return "frontend"
    if any(hint in text for hint in BACKEND_HINTS) or (web and not web.get("isHtml")):
        return "backend"
    return "service"


def infer_icon(listener, service_type):
    text = " ".join((listener.get("process", ""), listener.get("cmdline", ""), listener.get("systemdUnit", ""))).lower()
    if any(value in text for value in ("postgres", "mysql", "maria", "mongo", "sqlite")):
        return "database"
    if any(value in text for value in ("redis", "memcached")):
        return "cache"
    if any(value in text for value in ("rabbit", "kafka", "nats", "pulsar")):
        return "queue"
    if any(value in text for value in ("nginx", "caddy", "traefik", "haproxy")):
        return "proxy"
    if any(value in text for value in ("prometheus", "grafana", "uptime", "node_exporter")):
        return "monitoring"
    if any(value in text for value in ("minio", "sftp", "nfs", "ceph", "object-storage")):
        return "storage"
    if listener.get("containerId"):
        return "container"
    return {"frontend": "web", "backend": "api", "service": "system"}.get(service_type, "default")


def server_metrics():
    memory = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                memory[key] = int(value.strip().split()[0])
        total_mem = memory.get("MemTotal", 1)
        available_mem = memory.get("MemAvailable", 0)
    except OSError:
        total_mem, available_mem = 1, 0
    disk = shutil.disk_usage("/")
    try:
        uptime_seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        uptime_seconds = 0
    return {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "cpuCount": os.cpu_count() or 1,
        "load": round(os.getloadavg()[0], 2),
        "memoryPercent": round((total_mem - available_mem) / total_mem * 100),
        "memoryUsedGb": round((total_mem - available_mem) / 1024 / 1024, 1),
        "memoryTotalGb": round(total_mem / 1024 / 1024, 1),
        "diskPercent": round(disk.used / disk.total * 100),
        "diskUsedGb": round(disk.used / 1024 ** 3, 1),
        "diskTotalGb": round(disk.total / 1024 ** 3, 1),
        "uptimeDays": round(uptime_seconds / 86400, 1),
    }


def public_web_url(server, listener, web):
    if not web or listener.get("bind") not in ("0.0.0.0", "*", "::"):
        return None
    host = server.get("publicIp", "").strip()
    if not host:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f'{web["scheme"]}://{host}:{listener["port"]}/'


def collect_report(server_config=None):
    config = server_config or {}
    hostname = socket.gethostname()
    server = {
        "id": config.get("id") or stable_server_id(hostname),
        "name": os.environ.get("SERVER_NAME") or config.get("name") or hostname,
        "hostname": hostname,
        "publicIp": os.environ.get("PUBLIC_IP") or config.get("publicIp", ""),
        "provider": os.environ.get("SERVER_PROVIDER") or config.get("provider") or "Self-hosted",
        **server_metrics(),
    }
    active = listeners()
    groups = {}
    for port_number, listener in sorted(active.items()):
        status, latency = tcp_probe(port_number)
        web = web_probe(port_number) if status == "healthy" else None
        service_type = infer_service_type(listener, web)
        name, source, detail, confidence = infer_project(listener)
        project_id = "auto-" + slug(name)
        group = groups.setdefault(project_id, {
            "id": project_id,
            "code": slug(name).upper()[:12],
            "name": str(name).replace("-", " ").replace("_", " ").title(),
            "description": f"自动发现 · {source}: {detail}",
            "owner": listener.get("user") or "auto",
            "managed": "auto",
            "source": source,
            "confidence": confidence,
            "icon": infer_icon(listener, service_type),
            "ports": [],
        })
        port_name = (web or {}).get("title") or f'{listener["process"]} 进程'
        group["ports"].append({
            "id": f"{project_id}-{port_number}",
            "name": port_name,
            "type": service_type,
            "port": port_number,
            "host": listener["bind"],
            "process": listener["process"],
            "pid": listener["pid"],
            "status": status,
            "latency": latency,
            "exposure": "公网" if listener["bind"] in ("0.0.0.0", "*", "::") else "本机",
            "web": web,
            "webUrl": public_web_url(server, listener, web),
        })
    return {
        "schemaVersion": 1,
        "agentVersion": AGENT_VERSION,
        "server": server,
        "projects": list(groups.values()),
        "listenerCount": len(active),
        "scannedAt": datetime.now(timezone.utc).isoformat(),
    }
