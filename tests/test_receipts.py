from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reliable_agent_ops.receipts import (
    Receipt,
    ReceiptError,
    read_receipt,
    receipt_digest,
    require_fresh,
    write_receipt,
)


class ReceiptTests(unittest.TestCase):
    def test_atomic_round_trip_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "health.json"
            receipt = Receipt.create("health", "passed", {"count": 1})
            write_receipt(path, receipt)
            self.assertEqual(read_receipt(path), receipt)
            self.assertEqual(len(receipt_digest(receipt)), 64)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_rejects_unknown_fields(self) -> None:
        payload = {
            "schema_version": "1.0",
            "kind": "health",
            "status": "passed",
            "checked_at": "2026-01-01T00:00:00Z",
            "details": {},
            "surprise": True,
        }
        with self.assertRaisesRegex(ReceiptError, "unknown fields"):
            Receipt.from_dict(payload)

    def test_rejects_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(ReceiptError, "timezone"):
            Receipt.create(
                "health",
                "passed",
                {},
                checked_at=datetime(2026, 1, 1),
            )

    def test_stale_receipt_fails_closed(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        receipt = Receipt.create(
            "backup",
            "passed",
            {},
            checked_at=now - timedelta(minutes=16),
        )
        with self.assertRaisesRegex(ReceiptError, "stale"):
            require_fresh(receipt, max_age=timedelta(minutes=15), now=now)

    def test_future_receipt_fails_closed(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        receipt = Receipt.create(
            "backup",
            "passed",
            {},
            checked_at=now + timedelta(minutes=1),
        )
        with self.assertRaisesRegex(ReceiptError, "future"):
            require_fresh(receipt, max_age=timedelta(minutes=15), now=now)

    def test_malformed_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ReceiptError, "valid JSON"):
                read_receipt(path)

    def test_wrong_field_type_is_rejected_as_receipt_error(self) -> None:
        payload = {
            "schema_version": "1.0",
            "kind": ["health"],
            "status": "passed",
            "checked_at": "2026-01-01T00:00:00Z",
            "details": {},
        }
        with self.assertRaisesRegex(ReceiptError, "kind must be a string"):
            Receipt.from_dict(payload)

    def test_nested_object_keys_must_be_strings(self) -> None:
        with self.assertRaisesRegex(ReceiptError, "keys must be strings"):
            Receipt.create("health", "passed", {"nested": {1: "value"}})

    def test_reference_time_must_include_timezone(self) -> None:
        receipt = Receipt.create("health", "passed", {})
        with self.assertRaisesRegex(ReceiptError, "reference time"):
            require_fresh(
                receipt,
                max_age=timedelta(minutes=15),
                now=datetime(2026, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
