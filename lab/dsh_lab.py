#!/usr/bin/env python3
"""Initialize, launch and verify isolated DSH profiles without printing credentials."""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

REPO = Path(__file__).resolve().parent.parent
DEFAULT_STATE = Path.home() / "work/geo-sleuth-lab"
PROFILES = {"headless": "geo-lab-headless", "web": "geo-lab-web"}
PRESET = "geo-lab"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")


def creation_id(pid):
    text = Path(f"/proc/{pid}/stat").read_text()
    return text[text.rfind(")") + 2:].split()[19]


class Lab:
    def __init__(self, state, dsh):
        self.state = state.expanduser().resolve()
        self.home = self.state / "dsh-home"
        self.workspace = self.state / "workspace"
        self.dsh = Path(shutil.which(dsh) or dsh).resolve()
        self.install = self.dsh.parent.parent
        self.marker = self.state / "lab.json"
        if self.home == (Path.home() / ".dsh").resolve():
            raise RuntimeError("Refusing the ordinary DSH home")

    def env(self):
        return {**os.environ, "DSH_HOME": str(self.home), "DSH_TELEMETRY_DISABLED": "1",
                "UV_CACHE_DIR": str(self.workspace / ".cache/uv"),
                "HF_HOME": str(self.workspace / ".cache/huggingface")}

    def owned(self):
        if not self.marker.is_file():
            raise RuntimeError("Run init first")
        info = json.loads(self.marker.read_text())
        if info.get("checkout", info["workspace"]) != str(REPO) or info["dsh_home"] != str(self.home):
            raise RuntimeError("This state belongs to another workspace")
        return info

    def command(self, mode):
        return [str(self.dsh), "--profile", PROFILES[mode], "--patch", str(self.state / f"{mode}.patch.yml")]

    def init(self):
        if self.marker.exists():
            self.owned()
        elif self.home.exists():
            raise RuntimeError("Refusing to adopt an unowned existing DSH home")
        package = json.loads((self.install / "package.json").read_text())
        if package["name"] != "@deepseek-ai/dsh":
            raise RuntimeError("Executable does not resolve to the DSH package")
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state, 0o700)
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = {"schema_version": 1, "checkout": str(REPO), "workspace": str(self.workspace), "dsh_home": str(self.home),
                "dsh_version": package["version"], "skill_root": str(REPO / "skills"),
                "profiles": PROFILES, "model_authentication": "not_checked"}
        save(self.marker, info)
        for mode, profile in PROFILES.items():
            cmd = [str(self.dsh), "--profile", profile]
            if not (self.home / "profiles" / profile / "package.json").is_file():
                cmd += ["--from-default-profile", mode]
            result = subprocess.run(cmd + ["--dump-default-config"], cwd=REPO, env=self.env(), capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f"Template initialization failed: {mode} ({result.returncode})")
        source = self.install / "node_modules/@deepseek-ai/dsh-agent-presets/presets/standard"
        target = self.state / "presets" / PRESET
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        original = (source / "agent.cordis.yml").read_text()
        needle = "- id: skill-filesystem\n  name: '@deepseek-ai/dsh-skill-filesystem'\n"
        if original.count(needle) != 1:
            raise RuntimeError("Installed preset changed; inspect before adapting")
        replacement = needle + "  config:\n    includeDefaultRoots: false\n    customSkillDirs:\n      - " + json.dumps(str(REPO / "skills")) + "\n"
        (target / "agent.cordis.yml").write_text(original.replace(needle, replacement))
        os.chmod(target / "agent.cordis.yml", 0o600)
        save(target / "preset.yml", {"name": "Geo workflow lab", "description": "Isolated geo-sleuth development"})
        info["standard_preset_sha256"] = hashlib.sha256(original.encode()).hexdigest()
        save(self.marker, info)
        save(self.state / "headless.patch.yml", [{"id": "skill-filesystem", "config": {
            "includeDefaultRoots": False, "customSkillDirs": [str(REPO / "skills")]}}])
        save(self.state / "web.patch.yml", [{"id": "agent-presets", "config": {
            "default": PRESET, "includeShippedRoot": False, "includeUserRoot": False,
            "roots": [{"path": str(self.state / "presets"), "trust": "user"}]}}])
        for mode in PROFILES:
            result = subprocess.run(self.command(mode) + ["--dump-config"], cwd=REPO, env=self.env(), capture_output=True, text=True, timeout=60)
            expected = str(REPO / "skills") if mode == "headless" else str(self.state / "presets")
            if result.returncode or expected not in result.stdout:
                raise RuntimeError(f"Private composition check failed: {mode}")
        return {**info, "status": "ok", "composition_checked": True, "model_requests_sent": 0}

    def running(self):
        path = self.state / "web-process.json"
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        try:
            argv = Path(f"/proc/{record['pid']}/cmdline").read_bytes().split(b"\0")
            if creation_id(record["pid"]) != record["start_id"] or PROFILES["web"].encode() not in argv:
                return None
        except OSError:
            return None
        return record

    def launch_url(self, record):
        text = Path(record["log"]).read_text(errors="replace")
        match = re.search(re.escape(record["origin"]) + r"/\?token=[A-Za-z0-9_-]+", text)
        return match.group(0) if match else None

    def start(self, port):
        self.owned()
        record = self.running()
        if record:
            return {"status": "already_running", "origin": record["origin"], "pid": record["pid"]}
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
            selected = sock.getsockname()[1]
        log = self.state / "web.private.log"
        with log.open("w") as stream:
            os.chmod(log, 0o600)
            proc = subprocess.Popen(self.command("web") + ["--no-open", "--host", "127.0.0.1", "--port", str(selected)],
                cwd=self.workspace, env=self.env(), stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
        record = {"pid": proc.pid, "start_id": creation_id(proc.pid), "origin": f"http://127.0.0.1:{selected}", "log": str(log)}
        save(self.state / "web-process.json", record)
        for _ in range(180):
            if proc.poll() is not None:
                raise RuntimeError(f"Web startup failed ({proc.returncode}); inspect the private local log")
            if self.launch_url(record):
                return {"status": "started", "origin": record["origin"], "pid": proc.pid, "verification": "pending"}
            time.sleep(0.25)
        proc.terminate()
        raise RuntimeError("Web startup timed out; inspect the private local log")

    def verify(self):
        self.owned()
        record = self.running()
        if not record:
            raise RuntimeError("Lab web is not running")
        url = self.launch_url(record)
        if not url:
            raise RuntimeError("No local launch URL")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(url, timeout=20) as response:
            if response.status != 200 or b"<html" not in response.read().lower():
                raise RuntimeError("Authenticated page did not return HTML")
        def rpc(endpoint, args):
            data = {"type": "client-request", "rpcId": uuid.uuid4().hex, "method": endpoint, "payload": {"args": args}}
            request = urllib.request.Request(record["origin"] + "/api/" + endpoint, data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json", "Origin": record["origin"]})
            with opener.open(request, timeout=30) as response:
                result = json.load(response)["result"]
            if not result.get("ok"):
                raise RuntimeError(endpoint + " failed: " + result.get("error", {}).get("code", "unknown"))
            return result.get("value", {})
        created = rpc("session/create", {"request": {"cwd": str(self.workspace), "agentPreset": PRESET}})
        session_id = created["sessionId"]
        catalog = rpc("skills/list", {"request": {"sessionId": session_id}})
        skills = catalog.get("skills", [])
        if [entry["name"] for entry in skills] != ["geo-sleuth"]:
            raise RuntimeError("Catalog does not contain exactly geo-sleuth")
        if not skills[0].get("modelInvocable") or created.get("agentPreset") != PRESET:
            raise RuntimeError("Skill is not model-invocable or session preset is wrong")
        summary = {"schema_version": 1, "status": "ok", "origin": record["origin"], "workspace": str(self.workspace),
            "dsh_home": str(self.home), "preset": PRESET, "session_id": session_id,
            "skills": [{"name": entry["name"], "model_invocable": entry["modelInvocable"]} for entry in skills],
            "authenticated_html": True, "model_requests_sent": 0, "model_authentication": "not_checked",
            "note": "Blank session and catalog only; no prompt submitted."}
        save(self.state / "verification.json", summary)
        return summary

    def stop(self):
        self.owned()
        record = self.running()
        if not record:
            return {"status": "not_running"}
        os.kill(record["pid"], signal.SIGTERM)
        return {"status": "stop_requested", "pid": record["pid"]}

    def open(self):
        self.owned()
        record = self.running()
        if not record:
            raise RuntimeError("Lab web is not running")
        url = self.launch_url(record)
        if not url or not webbrowser.open(url):
            raise RuntimeError("No desktop browser is configured; use the local launch URL in web.private.log")
        return {"status": "opened", "origin": record["origin"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--dsh", default=shutil.which("dsh") or str(Path.home() / ".local/bin/dsh"))
    sub = parser.add_subparsers(dest="action", required=True)
    for command in ("init", "verify", "stop-web", "open-web"):
        sub.add_parser(command)
    web = sub.add_parser("start-web")
    web.add_argument("--port", type=int, default=0)
    sub.add_parser("headless", help="Send an actual model task").add_argument("task")
    args = parser.parse_args()
    try:
        lab = Lab(args.state_root, args.dsh)
        if args.action == "headless":
            lab.owned()
            return subprocess.run(lab.command("headless") + [args.task], cwd=lab.workspace, env=lab.env()).returncode
        result = {"init": lab.init, "verify": lab.verify, "stop-web": lab.stop, "open-web": lab.open,
                  "start-web": lambda: lab.start(args.port)}[args.action]()
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(json.dumps({"status": "error", "error": detail}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
