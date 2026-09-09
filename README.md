# Reliable AI Agent Ops

A Python and Docker demo that helps an operator check whether an AI service
can be recovered from its backup. It produces evidence for a recovery decision
using fictional data and stops for human review.

## Example result

**Synthetic scenario:** a service starts healthy, a failure is introduced,
and a backup is restored into separate temporary storage.

| Report item | Expected result |
| --- | --- |
| Backup and restored files | Checksums match |
| Recovery evidence | Complete, current, and linked to the failure |
| Final decision | `ready-for-human-review` |
| Automatic production recovery | Not executed |

The [scenario integration test](tests/test_scenario.py) verifies the linked
recovery result. Docker provides the network isolation in the full demo; this
is not a production uptime or recovery-time claim.

## My contribution

I built the health probes, backup and restore verification, linked evidence
records, and recovery decision checks. The fictional service exercises
operational controls; it does not call an LLM.

![Architecture showing health checks, backup, isolated restore testing, and human review](assets/architecture.svg)

<a id="review-this-project-in-3-minutes"></a>

## Explore the project

Start with the example above, then follow the diagram and the
[engineering evidence](#engineering-evidence). Setup is optional for review.

## The problem

An AI service is not reliable simply because it can produce an answer. Operators
also need to know whether the service is healthy, whether its state can be
restored, and whether recovery evidence is complete and current.

This project turns those questions into a small, repeatable workflow that ends
with a person—not an automatic production action.

## How it works

1. Check a fictional AI service and record when the check occurred.
2. Create a locked backup with checksums for its files and archive.
3. Introduce a fictional service failure.
4. Restore the backup into temporary storage with networking disabled.
5. Compare every restored file with the backup record.
6. Recheck health and connect the failure, backup, restore, and recovery records.
7. Stop at `ready-for-human-review`.

## Engineering evidence

| Capability | Implementation | Check |
| --- | --- | --- |
| Detect incomplete or altered backups | [Backup](src/reliable_agent_ops/backup.py), [restore](src/reliable_agent_ops/restore.py) | [Archive and restore tests](tests/test_backup_restore.py) |
| Reject stale or inconsistent recovery evidence | [Recovery](src/reliable_agent_ops/recovery.py) | [Recovery tests](tests/test_recovery.py) |
| Join the stages into a review decision | [Scenario](src/reliable_agent_ops/scenario.py) | [Integration test](tests/test_scenario.py) |

## Optional Docker demo

The demonstration uses fictional data and local containers. It requires no API
keys, external services, or production access.

```bash
git clone https://github.com/mccruz/reliable-ai-agent-ops.git
cd reliable-ai-agent-ops
export DEMO_UID="$(id -u)"
export DEMO_GID="$(id -g)"
docker compose up --build
```

On Windows with Docker Desktop, run `docker compose up --build` without the two
environment-variable commands. The containers exit when the scenario finishes.

Open `demo-output/summary.json`. A successful run reports that the scenario
passed, human review is required, and automatic recovery was not executed. See
the [demo guide](docs/demo.md) for expected stages, cleanup, and troubleshooting.

## Safety and limits

- All scenario data is fictional, and the repository contains no credentials or
  production configuration.
- Scenario services run as non-root users with restricted container
  capabilities.
- The Docker restore check uses separate temporary storage and enforces no
  network access. The standalone CLI flag is an operator attestation, not a
  network switch.
- Archive extraction rejects unsafe paths, links, unexpected files, and checksum
  mismatches.
- Recovery stops when evidence is missing, outdated, invalid, or inconsistent.
- External commands are allowlisted, run without a shell, and have time limits.
- Notifications are dry-run previews; no chat or incident service is contacted.

The included health checks support HTTP, command, and process probes but do not
cover every production platform. The update check uses a local fictional
manifest, and recovery never restarts a real service. Integrating a production
orchestrator, alerting platform, or secret manager would require a separate
review.

## Verification

The automated suite covers health checks, backup locking and checksums, safe
restore extraction, evidence freshness, recovery decisions, command limits, and
the full Docker scenario.

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

GitHub Actions repeats the checks across supported Python versions and runs the
complete Docker demonstration.

## Project guide

- [Architecture and safety model](docs/architecture.md)
- [Docker demo](docs/demo.md)
- [Public/private boundary](docs/provenance.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## License

Copyright © 2026 Mark Cruz. Released under the [MIT License](LICENSE).
