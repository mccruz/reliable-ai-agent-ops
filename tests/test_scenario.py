from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from reliable_agent_ops.demo_service import HealthHandler, SyntheticHTTPServer
from reliable_agent_ops.restore import verify_restore
from reliable_agent_ops.scenario import run_finish, run_prepare, summarize


class ScenarioIntegrationTests(unittest.TestCase):
    def test_full_synthetic_scenario_produces_readiness_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_file = root / "state" / "health.json"
            artifacts = root / "artifacts"
            restore_root = root / "restore"
            manifest = root / "update-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "project": "synthetic-agent",
                        "latest_version": "1.1.0",
                        "release_url": "https://example.invalid/releases/1.1.0",
                    }
                ),
                encoding="utf-8",
            )
            previous_state = os.environ.get("AGENT_STATE_FILE")
            os.environ["AGENT_STATE_FILE"] = str(state_file)
            server = SyntheticHTTPServer(("127.0.0.1", 0), HealthHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            service_url = f"http://127.0.0.1:{server.server_port}/health"
            try:
                run_prepare(
                    service_url=service_url,
                    state_file=state_file,
                    artifacts_dir=artifacts,
                    update_manifest=manifest,
                )
                archive = sorted((artifacts / "backups").glob("agent-ops-*.tar.gz"))[-1]
                verify_restore(
                    archive,
                    restore_root,
                    artifacts / "receipts" / "restore.json",
                    network_disabled=True,
                )
                run_finish(
                    service_url=service_url,
                    state_file=state_file,
                    artifacts_dir=artifacts,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                if previous_state is None:
                    os.environ.pop("AGENT_STATE_FILE", None)
                else:
                    os.environ["AGENT_STATE_FILE"] = previous_state

            readiness = json.loads(
                (artifacts / "receipts" / "readiness.json").read_text(encoding="utf-8")
            )
            self.assertEqual(readiness["status"], "passed")
            self.assertIn("ready-for-human-review", summarize(artifacts))


if __name__ == "__main__":
    unittest.main()
