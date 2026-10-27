#!/usr/bin/env python3
"""Run on the application server as root. Preserve full releases and serialize changes."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path("/opt/devsync")
KEYS = {
    "backend": ["BACKEND_IMAGE", "BACKEND_MIGRATION_IMAGE"],
    "chat": ["CHAT_IMAGE"],
    "frontend": ["FRONTEND_IMAGE"],
}
IMAGES = {
    "BACKEND_IMAGE": "devsync-main", "BACKEND_MIGRATION_IMAGE": "devsync-backend-migrate",
    "CHAT_IMAGE": "devsync-chat", "FRONTEND_IMAGE": "devsync-frontend",
}


def merge(previous, candidate):
    result = json.loads(json.dumps(previous))
    result.setdefault("images", {})
    result.setdefault("releases", {})
    if not candidate.get("releases") or set(candidate["releases"]) - KEYS.keys():
        raise ValueError("Invalid release service selection")
    expected = {key for service in candidate["releases"] for key in KEYS[service]}
    if set(candidate.get("images", {})) != expected:
        raise ValueError("Candidate must contain exactly the selected service images")
    changed = []
    for service, release in candidate["releases"].items():
        current = result["releases"].get(service)
        order = (int(release["id"]), int(release["run_attempt"]))
        if current:
            old_order = (int(current["id"]), int(current["run_attempt"]))
            if order < old_order:
                raise ValueError(f"Refusing stale release for {service}")
            if order == old_order:
                if any(candidate["images"][key] != result["images"][key] for key in KEYS[service]):
                    raise ValueError("Same release identity has different images")
                continue
        for key in KEYS[service]:
            digest = candidate["images"][key]
            if not re.fullmatch(r"docker\.io/ankit42098/" + IMAGES[key] + r"@sha256:[0-9a-f]{64}", digest):
                raise ValueError(f"Invalid image digest: {key}")
            result["images"][key] = digest
        result["releases"][service] = release
        changed.append(service)
    if set(result["images"]) != set(IMAGES):
        raise ValueError("First deployment requires SERVICE=all")
    return result, changed


def compose(directory, *args):
    subprocess.run([
        "docker", "compose", "--project-name", "devsync",
        "--env-file", str(ROOT / "runtime.env"),
        "--env-file", str(directory / "release.env"),
        "-f", str(directory / "compose.prod.yml"), *args,
    ], check=True, timeout=900)


def health():
    # Application endpoints also verify that Nginx resolved the replaced containers.
    for path in ["/healthz", "/api/", "/chat/"]:
        for attempt in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1" + path, timeout=5) as response:
                    if response.status == 200:
                        break
            except (OSError, TimeoutError):
                pass
            time.sleep(2)
        else:
            raise RuntimeError(f"Health check failed: {path}")


def main():
    staging = Path(sys.argv[1]).resolve()
    os.umask(0o077)
    with (ROOT / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pointer = ROOT / "current"
        previous_dir = pointer.resolve() if pointer.exists() else None
        previous = json.loads((previous_dir / "release.json").read_text()) if previous_dir else {}
        candidate = json.loads((staging / "candidate.json").read_text())
        release, changed = merge(previous, candidate)
        if not changed:
            print("UNCHANGED: all selected releases are already deployed")
            return
        target = ROOT / "releases" / str(time.time_ns())
        target.mkdir(mode=0o700)
        for filename in ["compose.prod.yml", "postgres-init.sql"]:
            shutil.copyfile(staging / filename, target / filename)
        (target / "release.json").write_text(json.dumps(release, indent=2))
        (target / "release.env").write_text("".join(f"{key}={value}\n" for key, value in sorted(release["images"].items())))
        # Validate and pull before changing any running application containers.
        compose(target, "config", "--quiet")
        services = list(changed)
        if "backend" in changed:
            services.append("backend-migrate")
        compose(target, "pull", *services)
        try:
            if not previous_dir:
                compose(target, "up", "-d", "--wait", "--wait-timeout", "180", "postgres", "redis", "mongo")
            if "backend" in changed:
                compose(target, "run", "--rm", "--no-deps", "backend-migrate")
            application_services = [s for s in changed if s != "frontend"]
            if application_services:
                compose(target, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", *application_services)
            # Nginx resolves upstream addresses at startup; refresh after container replacement.
            compose(target, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "frontend")
            health()
        except Exception:
            if previous_dir:
                print("Deployment failed; restoring previous application images. Database migrations are NOT reversed.", flush=True)
                compose(previous_dir, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "backend", "chat")
                compose(previous_dir, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "frontend")
                health()
            else:
                print("Initial deployment failed. No previous release exists; inspect containers. Data volumes are preserved.", flush=True)
            raise
        temporary = ROOT / "current.next"
        if temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(target)
        temporary.replace(pointer)
        print("DEPLOYED: " + ", ".join(changed))


if __name__ == "__main__":
    main()
