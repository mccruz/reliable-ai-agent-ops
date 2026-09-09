# Synthetic recovery demo

This is an optional hands-on guide for technical reviewers. To review the project without installing anything, use the [main README](../README.md), [architecture diagram](../assets/architecture.svg), and [architecture and safety model](architecture.md).

## Prerequisites

- Docker Desktop on macOS or Windows, or Docker Engine on Linux
- Docker Compose v2 (`docker compose`, with a space)
- Git for downloading the repository
- No credentials, API keys, external services, or production access

Make sure Docker is running:

```bash
git --version
docker --version
docker compose version
```

## Run

Download the repository if needed:

```bash
git clone https://github.com/mccruz/reliable-ai-agent-ops.git
cd reliable-ai-agent-ops
```

Run all later commands from this directory. On macOS or Linux:

```bash
export DEMO_UID="$(id -u)"
export DEMO_GID="$(id -g)"
docker compose up --build
```

On Windows PowerShell, Docker Desktop can use the Compose defaults:

```powershell
docker compose up --build
```

All containers exit after the scenario completes. The fictional service has a three-minute safety timeout so a failed orchestration does not run indefinitely.
The short-lived `storage-init` service first validates and assigns only the three
dedicated synthetic mounts to `DEMO_UID:DEMO_GID`; all scenario services then run
as that non-root identity.

## What happens

| Stage | Expected evidence |
| --- | --- |
| Storage initialization | The Compose log reports three validated mounts owned by the requested non-root UID/GID |
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

Review the concise outcome by opening `demo-output/summary.json` in a text editor. On macOS or Linux:

```bash
cat demo-output/summary.json
```

The top-level `status` should be `passed`, `human_review_required` should be `true`, and `automatic_recovery_executed` should be `false`.

## Failure experiments

These tests are safe because all data and services are fictional.

### Prove checksum enforcement

Run the demo once, append text to the newest `.tar.gz`, and rerun only the restore verifier. It exits non-zero before extraction because the archive no longer matches its checksum.

### Prove freshness enforcement

Change a receipt's `checked_at` to an old UTC timestamp and run the readiness command. The decision becomes `blocked`.

### Check the isolation prerequisite

Omit `--network-disabled` from the restore command in a disposable copy of the
demo. Restore verification refuses to issue a passing receipt. This checks the
required operator attestation; it does not test the runtime's network access.

The CLI flag **does not disable networking or inspect the container**. Only use
it when the containing runtime provides that isolation. In the supplied demo,
Compose enforces `network_mode: none` on `restore-verifier`, mounts the backup
read-only, and provides separate scratch storage. Removing that Compose setting
while retaining the flag would make the attestation inaccurate.

The tests automate these scenarios without changing the committed example.

## Cleanup

```bash
docker compose down --volumes
rm -rf ./demo-output
```

The generated bind-mounted files are owned by the invoking user, so deleting
`demo-output/` removes only synthetic local artifacts and does not require `sudo`.

## Troubleshooting

| Message | Likely cause | Resolution |
| --- | --- | --- |
| `command not found: docker` | Docker is not installed. | Install Docker Desktop or Docker Engine, then reopen the terminal. |
| `Cannot connect to the Docker daemon` | Docker is installed but its engine is stopped. | Open Docker Desktop and wait for it to finish starting. |
| `no configuration file provided` | The terminal is outside the cloned repository. | Run `cd reliable-ai-agent-ops` and retry. |
| A generated receipt is not readable | The current UID/GID was not supplied on macOS or Linux. | Run the two `export DEMO_...` commands, clean up, and rerun. |
