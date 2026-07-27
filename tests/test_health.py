from __future__ import annotations

import unittest

from reliable_agent_ops.health import (
    HealthConfigError,
    ProbeResult,
    ServiceSpec,
    check_services,
)


class HealthTests(unittest.TestCase):
    def test_faults_are_isolated_and_order_is_stable(self) -> None:
        services = [
            ServiceSpec("alpha-agent", "https://example.invalid/alpha"),
            ServiceSpec("beta-agent", "https://example.invalid/beta"),
            ServiceSpec("gamma-agent", "https://example.invalid/gamma"),
        ]

        def probe(spec: ServiceSpec) -> ProbeResult:
            if spec.name == "beta-agent":
                raise RuntimeError("synthetic failure")
            return ProbeResult(spec.name, True, "status matched", "healthy")

        receipt = check_services(services, probe=probe)
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(
            [result["name"] for result in receipt.details["services"]],
            ["alpha-agent", "beta-agent", "gamma-agent"],
        )
        self.assertEqual(receipt.details["summary"], {"checked": 3, "passed": 2, "failed": 1})

    def test_all_services_must_pass(self) -> None:
        receipt = check_services(
            [ServiceSpec("synthetic-agent", "https://example.invalid/health")],
            probe=lambda spec: ProbeResult(spec.name, True, "ok", "healthy"),
        )
        self.assertEqual(receipt.status, "passed")

    def test_credentials_in_url_are_rejected(self) -> None:
        with self.assertRaisesRegex(HealthConfigError, "credentials"):
            ServiceSpec("synthetic-agent", "https://user:pass@example.invalid/health")

    def test_duplicate_service_names_are_rejected(self) -> None:
        spec = ServiceSpec("synthetic-agent", "https://example.invalid/health")
        with self.assertRaisesRegex(HealthConfigError, "unique"):
            check_services([spec, spec])

    def test_mismatched_probe_name_is_a_failure(self) -> None:
        receipt = check_services(
            [ServiceSpec("synthetic-agent", "https://example.invalid/health")],
            probe=lambda spec: ProbeResult("other-agent", True, "ok", "healthy"),
        )
        self.assertEqual(receipt.status, "failed")
        self.assertIn("mismatched", receipt.details["services"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
