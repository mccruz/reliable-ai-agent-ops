# Synthetic recovery demo

## Prerequisites

- Docker Engine or Docker Desktop
- Docker Compose v2
- No credentials, API keys, or external services

## Run

From the repository root:

```bash
docker compose up --build
```

All containers exit after the scenario completes. The fictional service has a three-minute safety timeout so a failed orchestration does not run indefinitely.

## What happens

| Stage | Expected evidence |
| --- | --- |
| Healthy baseline | `receipts/health-initial.json` passes |
| Backup | `receipts/backup.json` references an atomic archive and SHA-256 |
| Update check | `receipts/update.json` compares two fictional semantic versions |
| Notification | `receipts/notification.json` confirms dry-run mode and no external delivery |
| Fault injection | `receipts/health-failed.json` records one observed failed service |
| Isolated restore | `receipts/restore.json` records network mode `none`, an ephemeral target, and no live-volume access |
| Recovery check | `receipts/health-recovered.json` and `receipts/recovery.json` link the recovery evidence |
| Readiness | `receipts/readiness.json` ends at `ready-for-human-review` |

Artifacts appear in `demo-output/`, which is excluded from Git:

```text
demo-output/
├── backups/
│   ├── agent-ops-<synthetic-id>.tar.gz
│   └── agent-ops-<synthetic-id>.tar.gz.sha256
├── receipts/
│   ├── backup.json
│   ├── health-failed.json
│   ├── health-initial.json
│   ├── health-recovered.json
│   ├── notification.json
│   ├── readiness.json
│   ├── recovery.json
│   ├── restore.json
│   └── update.json
├── notification-dry-run.json
└── summary.json
```

Review the concise outcome:

```bash
python3 -m json.tool demo-output/summary.json
```

## Failure experiments

These tests are safe because all data and services are fictional.

### Prove checksum enforcement

Run the demo once, append text to the newest `.tar.gz`, and rerun only the restore verifier. It exits non-zero before extraction because the archive no longer matches its checksum.

### Prove freshness enforcement

Change a receipt's `checked_at` to an old UTC timestamp and run the readiness command. The decision becomes `blocked`.

### Prove fail-closed isolation

Remove `network_mode: none` from a local copy of `compose.yaml` and omit `--network-disabled`. Restore verification refuses to issue a passing receipt.

The tests automate these scenarios without changing the committed example.

## Cleanup

```bash
docker compose down --volumes
```

Deleting `demo-output/` removes only synthetic local artifacts.
