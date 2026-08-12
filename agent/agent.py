#!/usr/bin/env python3
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.collector import AGENT_VERSION, collect_report


def submit_report(server_url, token):
    report = collect_report()
    request = Request(
        server_url.rstrip("/") + "/api/agent/report",
        data=json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read())
    print(f'[{report["scannedAt"]}] reported {report["listenerCount"]} listeners as {result["serverId"]}', flush=True)
    return result


def acknowledge_command(server_url, token, command_id, server_id):
    request = Request(
        server_url.rstrip("/") + f"/api/agent/commands/{command_id}/ack",
        data=json.dumps({"serverId": server_id}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def decommission(server_url, token, command, server_id):
    if os.geteuid() != 0:
        raise PermissionError("decommission requires root privileges")
    service = "port-grove-agent.service"
    subprocess.run(["systemctl", "disable", service], check=True, timeout=15)
    try:
        acknowledge_command(server_url, token, command["id"], server_id)
    except Exception:
        subprocess.run(["systemctl", "enable", service], check=False, timeout=15)
        raise
    for filename in ("/etc/port-grove-agent.env", "/etc/systemd/system/port-grove-agent.service"):
        try:
            Path(filename).unlink()
        except FileNotFoundError:
            pass
    shutil.rmtree("/opt/port-grove/agent", ignore_errors=True)
    subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=15)
    print("Port Grove Agent decommissioned by the central server", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Port Grove lightweight server agent")
    parser.add_argument("--server", default=os.environ.get("PORT_GROVE_SERVER", "http://127.0.0.1:2381"))
    parser.add_argument("--token", default=os.environ.get("AGENT_TOKEN", ""))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("PROBE_INTERVAL", "30")))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--version", action="version", version=AGENT_VERSION)
    args = parser.parse_args()
    if not args.token:
        parser.error("--token or AGENT_TOKEN is required")
    target = urlparse(args.server)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    allow_insecure = os.environ.get("ALLOW_INSECURE_HTTP", "").lower() in ("1", "true", "yes")
    if target.scheme != "https" and target.hostname not in local_hosts and not allow_insecure:
        parser.error("remote reporting requires HTTPS; set ALLOW_INSECURE_HTTP=1 only on a trusted private network")
    while True:
        try:
            result = submit_report(args.server, args.token)
            for command in result.get("commands", []):
                if command.get("type") == "decommission":
                    decommission(args.server, args.token, command, result["serverId"])
                    return
        except (OSError, HTTPError, URLError, ValueError, subprocess.SubprocessError) as error:
            print(f"Port Grove report failed: {error}", file=sys.stderr, flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(max(10, args.interval) + random.uniform(0, 2.5))


if __name__ == "__main__":
    main()
