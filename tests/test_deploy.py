import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_release import candidate, deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "releases").mkdir()
        self.staging = self.root / "staging"
        self.staging.mkdir()
        for file in ["compose.prod.yml", "postgres-init.sql"]:
            (self.staging / file).write_text("test fixture")
        (self.staging / "candidate.json").write_text(json.dumps(candidate()))
        self.root_patch = patch.object(deploy, "ROOT", self.root)
        self.argv_patch = patch("sys.argv", ["deploy.py", str(self.staging)])
        self.root_patch.start()
        self.argv_patch.start()

    def tearDown(self):
        self.argv_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_initial_deployment_orders_migrations_before_application(self):
        with patch.object(deploy, "compose") as compose, patch.object(deploy, "health"):
            deploy.main()
        calls = [call.args[1:] for call in compose.call_args_list]
        migration = next(i for i, call in enumerate(calls) if call[0] == "run")
        app = next(i for i, call in enumerate(calls) if call[0] == "up" and "backend" in call)
        self.assertLess(migration, app)
        self.assertTrue((self.root / "current" / "release.json").exists())

    def test_failed_health_restores_old_images_and_keeps_release_record(self):
        old = self.root / "releases" / "old"
        old.mkdir()
        (old / "release.json").write_text(json.dumps(candidate()))
        (self.root / "current").symlink_to(old)
        (self.staging / "candidate.json").write_text(json.dumps(candidate(("backend",), 11)))
        with patch.object(deploy, "compose") as compose, patch.object(deploy, "health", side_effect=[RuntimeError("unhealthy"), None]):
            with self.assertRaises(RuntimeError):
                deploy.main()
        self.assertEqual((self.root / "current").resolve(), old)
        calls = [call.args for call in compose.call_args_list]
        self.assertTrue(any(call[0] == old and "backend" in call for call in calls))
        self.assertEqual(sum(call[1] == "run" for call in calls), 1)

    def test_duplicate_release_does_not_touch_containers(self):
        old = self.root / "releases" / "old"
        old.mkdir()
        (old / "release.json").write_text(json.dumps(candidate()))
        (self.root / "current").symlink_to(old)
        with patch.object(deploy, "compose") as compose:
            deploy.main()
        compose.assert_not_called()
