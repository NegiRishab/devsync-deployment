#!/usr/bin/env python3
"""Resolve only completed successful main-push CI releases; never execute artifacts."""
import argparse
import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

OWNER = "NegiRishab"
NAMESPACE = "ankit42098"
SERVICES = {
    "backend": ("TempSyncBackend1", {"BACKEND_IMAGE": "devsync-main", "BACKEND_MIGRATION_IMAGE": "devsync-backend-migrate"}),
    "chat": ("ChatService", {"CHAT_IMAGE": "devsync-chat"}),
    "frontend": ("DevSyncFrontend", {"FRONTEND_IMAGE": "devsync-frontend"}),
}


def api(path):
    request = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(artifact_id, repo):
    # Obtain GitHub's signed storage URL without forwarding the GitHub token to storage.
    url = f"https://api.github.com/repos/{OWNER}/{repo}/actions/artifacts/{artifact_id}/zip"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"]})
    try:
        response = urllib.request.build_opener(NoRedirect).open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers["Location"]
        if not location.startswith("https://"):
            raise ValueError("Artifact storage URL must use HTTPS")
        response = urllib.request.urlopen(location, timeout=60)
    with response:
        data = response.read(65537)
    if len(data) > 65536:
        raise ValueError("Release artifact is unexpectedly large")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo("release.env")
        if info.file_size > 8192:
            raise ValueError("Release manifest is unexpectedly large")
        return archive.read(info).decode("utf-8")


def parse_manifest(text, service, sha):
    values = {}
    allowed = set(SERVICES[service][1]) | {"COMMIT_SHA", "IMAGE_TAG"}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in values:
            raise ValueError("Invalid or duplicate release field")
        values[key] = value
    if values.get("COMMIT_SHA") != sha:
        raise ValueError("Artifact commit does not match the successful CI run")
    images = {}
    for key, image in SERVICES[service][1].items():
        value = values.get(key, "")
        pattern = rf"(?:docker\.io/)?{re.escape(NAMESPACE)}/{image}@sha256:[0-9a-f]{{64}}"
        if not re.fullmatch(pattern, value):
            raise ValueError(f"Unexpected image digest for {key}")
        images[key] = "docker.io/" + value.removeprefix("docker.io/")
    return images


def resolve(service, run_id="", current=None):
    repo = SERVICES[service][0]
    base = f"/repos/{OWNER}/{repo}/actions"
    if run_id:
        if not run_id.isdigit():
            raise ValueError("RUN_ID must be numeric")
        run = api(f"{base}/runs/{run_id}")
    else:
        runs = api(f"{base}/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=1")["workflow_runs"]
        if not runs:
            raise ValueError(f"No successful main-push CI release for {repo}")
        run = runs[0]
    if not (run["status"] == "completed" and run["conclusion"] == "success"
            and run["event"] == "push" and run["head_branch"] == "main"
            and run["path"] == ".github/workflows/ci.yml"
            and run["repository"]["full_name"] == f"{OWNER}/{repo}"):
        raise ValueError("Selected run is not an eligible CI release")
    metadata = {k: run[k] for k in ["id", "run_attempt", "head_sha"]}
    if current and current.get("releases", {}).get(service) == metadata:
        # An unchanged deployment does not require an artifact that may have expired.
        text = "".join(f"{key}={current['images'][key]}\n" for key in SERVICES[service][1])
        text += f"COMMIT_SHA={run['head_sha']}\n"
        return parse_manifest(text, service, run["head_sha"]), metadata
    artifacts = api(f"{base}/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
    name = f"release-{service}-{run['head_sha']}"
    matches = [a for a in artifacts if a["name"] == name and not a["expired"]]
    if len(matches) != 1:
        raise ValueError(f"Expected one unexpired artifact {name}")
    images = parse_manifest(download(matches[0]["id"], repo), service, run["head_sha"])
    metadata = {k: run[k] for k in ["id", "run_attempt", "head_sha"]}
    return images, metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=["all", *SERVICES], required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--current")
    args = parser.parse_args()
    if args.service == "all" and args.run_id:
        parser.error("RUN_ID is only supported with a single service")
    current = json.loads(Path(args.current).read_text()) if args.current and Path(args.current).exists() else {}
    result = {"images": {}, "releases": {}}
    for service in SERVICES if args.service == "all" else [args.service]:
        images, metadata = resolve(service, args.run_id.strip(), current)
        result["images"].update(images)
        result["releases"][service] = metadata
        print(f"Verified {service}: run {metadata['id']}, attempt {metadata['run_attempt']}")
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
