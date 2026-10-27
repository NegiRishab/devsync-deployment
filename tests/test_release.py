import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch = load("fetch_release")
deploy = load("deploy")
SHA = "a" * 40


def candidate(services=("backend", "chat", "frontend"), run=10):
    images, releases = {}, {}
    for service in services:
        for key, image in fetch.SERVICES[service][1].items():
            images[key] = f"docker.io/ankit42098/{image}@sha256:" + (hex(run)[2:] * 64)[:64]
        releases[service] = {"id": run, "run_attempt": 1, "head_sha": SHA}
    return {"images": images, "releases": releases}


class ReleaseTests(unittest.TestCase):
    def test_unchanged_release_needs_no_artifact_download(self):
        current = candidate()
        run = dict(current["releases"]["chat"], status="completed", conclusion="success",
                   event="push", head_branch="main", path=".github/workflows/ci.yml",
                   repository={"full_name": "NegiRishab/ChatService"})
        with patch.object(fetch, "api", return_value=run) as api, patch.object(fetch, "download") as download:
            images, _ = fetch.resolve("chat", "10", current)
        self.assertEqual(images["CHAT_IMAGE"], current["images"]["CHAT_IMAGE"])
        self.assertEqual(api.call_count, 1)
        download.assert_not_called()

    def test_failed_run_is_never_deployed(self):
        run = {"status": "completed", "conclusion": "failure"}
        with patch.object(fetch, "api", return_value=run), self.assertRaises(ValueError):
            fetch.resolve("chat", "10")

    def test_successful_manual_run_is_not_a_release(self):
        run = {"status": "completed", "conclusion": "success", "event": "workflow_dispatch"}
        with patch.object(fetch, "api", return_value=run), self.assertRaises(ValueError):
            fetch.resolve("chat", "10")

    def test_digest_manifest(self):
        value = "ankit42098/devsync-chat@sha256:" + "a" * 64
        manifest = f"CHAT_IMAGE={value}\nCOMMIT_SHA={SHA}\nIMAGE_TAG=example\n"
        self.assertEqual(fetch.parse_manifest(manifest, "chat", SHA)["CHAT_IMAGE"], "docker.io/" + value)

    def test_reject_tag_instead_of_digest(self):
        with self.assertRaises(ValueError):
            fetch.parse_manifest(f"CHAT_IMAGE=ankit42098/devsync-chat:latest\nCOMMIT_SHA={SHA}\n", "chat", SHA)

    def test_reject_artifact_with_wrong_commit(self):
        with self.assertRaises(ValueError):
            fetch.parse_manifest(f"COMMIT_SHA={'b' * 40}\n", "chat", SHA)

    def test_reject_unexpected_or_duplicate_fields(self):
        for text in ["UNEXPECTED=x\n", f"COMMIT_SHA={SHA}\nCOMMIT_SHA={SHA}\n"]:
            with self.assertRaises(ValueError):
                fetch.parse_manifest(text, "chat", SHA)

    def test_partial_first_deployment_rejected(self):
        with self.assertRaises(ValueError):
            deploy.merge({}, candidate(("chat",)))

    def test_partial_update_preserves_other_services(self):
        previous = candidate()
        result, changed = deploy.merge(previous, candidate(("chat",), 11))
        self.assertEqual(changed, ["chat"])
        self.assertEqual(result["images"]["BACKEND_IMAGE"], previous["images"]["BACKEND_IMAGE"])
        self.assertEqual(result["images"]["FRONTEND_IMAGE"], previous["images"]["FRONTEND_IMAGE"])
        self.assertNotEqual(result["images"]["CHAT_IMAGE"], previous["images"]["CHAT_IMAGE"])

    def test_repeated_release_is_noop(self):
        _, changed = deploy.merge(candidate(), candidate())
        self.assertEqual(changed, [])

    def test_stale_release_rejected(self):
        with self.assertRaises(ValueError):
            deploy.merge(candidate(run=11), candidate(("chat",), 10))

    def test_backend_requires_matching_migration_reference(self):
        release = candidate(("backend",))
        del release["images"]["BACKEND_MIGRATION_IMAGE"]
        with self.assertRaises(ValueError):
            deploy.merge(candidate(), release)

    def test_reject_wrong_registry(self):
        release = candidate()
        release["images"]["CHAT_IMAGE"] = "evil.example/chat@sha256:" + "a" * 64
        with self.assertRaises(ValueError):
            deploy.merge({}, release)


if __name__ == "__main__":
    unittest.main()
