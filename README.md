# Reliable AI Agent Ops

Reliable AI Agent Ops is a credential-free Python and Docker Compose case study for operating AI-agent services safely. It demonstrates fault-isolated health checks, atomic checksummed backups, network-disabled restore verification, typed evidence receipts, dry-run notifications, and fail-closed recovery gates—without production infrastructure, secrets, or automatic remediation.

![Architecture showing synthetic service health, atomic backup, isolated restore verification, typed receipts, and a human review gate](assets/architecture.svg)

## Why this project exists

AI automation does not end when an agent produces an answer. Implementation teams also need evidence that the service is healthy, state can be recovered, failures remain isolated, and no recovery action proceeds on stale or incomplete evidence.

This project turns those operational requirements into a small, reviewable workflow:

1. Check a fictional AI-agent service and record timestamped health evidence.
2. Publish a locked, atomic backup with per-file and archive SHA-256 checksums.
3. Inject a synthetic service failure without affecting other controls.
4. Restore into an ephemeral volume inside a container with networking disabled.
5. Verify every restored file against the backup manifest.
6. Recheck service health and link the failure, backup, restore, and recovery receipts.
7. Stop at `ready-for-human-review`; never authorize an automatic production action.

## Recruiter-ready implementation evidence

| Capability | What the repository demonstrates |
| --- | --- |
| AI-agent operations | Health contracts, bounded concurrent probes, explicit failure reasons, and per-service fault isolation |
| Backup engineering | Exclusive locking, safe source traversal, deterministic archive content, atomic publication, checksums, and retention |
| Recovery verification | Path-safe extraction, manifest reconciliation, a network-disabled Compose stage, ephemeral storage, and no live-volume writes |
| Reliable automation | Versioned receipt schemas, UTC freshness checks, evidence digests, cross-receipt validation, and fail-closed decisions |
| Integration safety | Credential-free notifications, allowlisted external-command seams, validated identifiers, `shell=False`, and bounded timeouts |
| Delivery quality | Standard-library Python, an offline unit/integration suite, Python 3.11–3.14 CI, and a reproducible synthetic demo |

## Run the Docker recovery demo

Prerequisite: Docker with the Compose plugin.

```bash
docker compose up --build
python3 -m json.tool demo-output/summary.json
```

The stack uses only fictional data and four local services:

- `synthetic-agent` exposes a JSON health contract backed by a synthetic state file.
- `scenario-prepare` captures healthy evidence, creates a backup, checks a local update manifest, emits a dry-run notification, and injects a failure.
- `restore-verifier` has `network_mode: none`, reads the backup, restores only into an ephemeral volume, verifies its manifest, and writes a restore receipt.
- `scenario-finish` simulates a reviewed remediation, confirms recovered health, evaluates recovery evidence, and writes the final readiness decision.

Expected final result:

```json
{
  "automatic_recovery_executed": false,
  "human_review_required": true,
  "scenario": "synthetic-ai-agent-recovery",
  "status": "passed"
}
```

Clean up the synthetic volumes when finished:

```bash
docker compose down --volumes
```

See the [demo walkthrough](docs/demo.md) for the failure stages and receipt files.

## Run the tests locally

Use a virtual environment so the project does not modify a Homebrew- or system-managed Python installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-deps .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

The suite covers malformed and stale receipts, future timestamps, probe isolation, overlapping paths, symlink rejection, archive tampering, path traversal, retention, network-isolation evidence, checksum mismatches, unsafe command inputs, credential-free notifications, and the complete recovery scenario.

## Architecture and decision model

Every control emits a small JSON receipt with:

- `schema_version`
- `kind`
- `status`
- timezone-aware `checked_at`
- control-specific `details`

The recovery evaluator accepts only fresh evidence that proves:

- a service failure was actually observed;
- the backup and isolated restore share the same identifier and SHA-256 checksum;
- the restore used `network_mode: none`, an ephemeral target, and no live volume;
- all services later returned healthy.

The final readiness gate also links the exact health and restore receipt digests recorded by recovery. Missing, malformed, failed, future-dated, stale, or mismatched evidence produces `blocked`.

More detail is available in the [architecture and safety model](docs/architecture.md).

## Security and privacy boundaries

- No authentication keys, environment secrets, real notification routes, or credential-loading code.
- No production hostnames, addresses, service names, paths, schedules, databases, topology, logs, or backup data.
- No private repository history, bundled skills, third-party source, or operational configuration.
- Health URLs reject embedded credentials.
- Backup input rejects symlinks, special files, and overlapping source/output paths.
- Restore extraction rejects absolute paths, parent traversal, links, special files, duplicates, and oversized payloads.
- External-command integration accepts only allowlisted prefixes and validated identifiers, never a shell string.
- Notifications are dry-run only and reject sensitive metadata keys.
- Compose containers use read-only root filesystems, `no-new-privileges`, and a minimal file-write capability for synthetic bind-mounted output.

Please use the [security policy](SECURITY.md) for responsible disclosure.

## Deliberate limitations

This is a portfolio-grade reference implementation, not a production operations system.

- The fictional service is not an AI model and contains no user data.
- The demo verifies a restore but does not copy restored data into the live synthetic volume.
- The CLI's `--network-disabled` flag is an attestation; isolation is actually enforced only by the included Compose service's `network_mode: none`.
- The notification interface ships with a dry-run adapter only. Telegram or another external channel would require a later, separately reviewed release.
- The update check reads a local synthetic manifest and does not contact a vendor.
- File locking uses `fcntl`, targeting Linux and macOS.
- Recovery ends at human review and never restarts services or changes production state.

## Repository map

```text
src/reliable_agent_ops/
├── backup.py          # locked, atomic backup publication and retention
├── health.py          # bounded concurrent health checks and fault isolation
├── receipts.py        # typed schema, atomic writes, digests, and freshness
├── recovery.py        # recovery and readiness evidence gates
├── restore.py         # checksum and path-safe isolated restore verification
├── notifications.py  # notifier protocol and credential-free dry run
├── updates.py         # strict semantic-version manifest check
├── commands.py        # allowlisted, shell-free integration seam
└── scenario.py        # deterministic Compose demo stages
```

## Authorship and provenance

The public implementation is newly written from independently owned reliability concepts and uses synthetic fixtures. It does not copy the private source repository's history, deployment wiring, documentation, infrastructure details, notification routes, or working tree. See the [provenance statement](docs/provenance.md).

Copyright © 2026 Mark Cruz. Released under the [MIT License](LICENSE).
