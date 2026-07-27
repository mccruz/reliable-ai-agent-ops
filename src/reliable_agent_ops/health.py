from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .receipts import Receipt

SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
MAX_RESPONSE_BYTES = 4096


class HealthConfigError(ValueError):
    """Raised when a health-check target is unsafe or ambiguous."""


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    url: str
    expected_status: str = "healthy"
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not SERVICE_NAME.fullmatch(self.name):
            raise HealthConfigError(
                "service names must start with a letter and contain only lowercase "
                "letters, numbers, and hyphens"
            )
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HealthConfigError("health URLs must use http or https with a hostname")
        if parsed.username or parsed.password:
            raise HealthConfigError("credentials are not allowed in health URLs")
        if parsed.fragment:
            raise HealthConfigError("health URLs must not contain fragments")
        if not (0 < self.timeout_seconds <= 30):
            raise HealthConfigError("timeout_seconds must be between 0 and 30")
        if not self.expected_status or len(self.expected_status) > 64:
            raise HealthConfigError("expected_status must be 1 to 64 characters")


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    reason: str
    observed_status: str | None = None


Probe = Callable[[ServiceSpec], ProbeResult]


def probe_http(spec: ServiceSpec) -> ProbeResult:
    request = Request(
        spec.url,
        headers={"Accept": "application/json", "User-Agent": "reliable-ai-agent-ops/0.1"},
    )
    try:
        with urlopen(request, timeout=spec.timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return ProbeResult(spec.name, False, "response exceeded 4096 bytes")
            if response.status < 200 or response.status >= 300:
                return ProbeResult(spec.name, False, f"HTTP {response.status}")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        return ProbeResult(spec.name, False, f"HTTP {code}")
    except (TimeoutError, URLError, OSError) as exc:
        return ProbeResult(spec.name, False, type(exc).__name__)

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ProbeResult(spec.name, False, "response was not valid JSON")
    if not isinstance(payload, dict):
        return ProbeResult(spec.name, False, "response root was not an object")
    observed = payload.get("status")
    if not isinstance(observed, str):
        return ProbeResult(spec.name, False, "response omitted string status")
    if observed != spec.expected_status:
        return ProbeResult(
            spec.name,
            False,
            f"expected {spec.expected_status!r}, observed {observed!r}",
            observed,
        )
    return ProbeResult(spec.name, True, "status matched", observed)


def check_services(
    services: Iterable[ServiceSpec],
    *,
    probe: Probe = probe_http,
    checked_at: datetime | None = None,
) -> Receipt:
    specs = list(services)
    if not specs:
        raise HealthConfigError("at least one service is required")
    if len(specs) > 32:
        raise HealthConfigError("no more than 32 services may be checked at once")
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise HealthConfigError("service names must be unique")

    results: dict[str, ProbeResult] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(specs))) as executor:
        future_map = {executor.submit(probe, spec): spec for spec in specs}
        for future in as_completed(future_map):
            spec = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # Fault isolation is intentional.
                result = ProbeResult(spec.name, False, f"probe raised {type(exc).__name__}")
            if result.name != spec.name:
                result = ProbeResult(spec.name, False, "probe returned a mismatched name")
            results[spec.name] = result

    ordered = [results[spec.name] for spec in specs]
    passed = all(result.passed for result in ordered)
    return Receipt.create(
        "health",
        "passed" if passed else "failed",
        {
            "services": [asdict(result) for result in ordered],
            "summary": {
                "checked": len(ordered),
                "passed": sum(result.passed for result in ordered),
                "failed": sum(not result.passed for result in ordered),
            },
        },
        checked_at=checked_at,
    )
