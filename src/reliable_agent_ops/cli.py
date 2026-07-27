from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from .backup import ARCHIVE_PREFIX, ARCHIVE_SUFFIX, create_backup
from .health import ServiceSpec, check_services
from .notifications import DryRunNotifier, NotificationEvent
from .receipts import read_receipt, write_receipt
from .recovery import evaluate_readiness, evaluate_recovery
from .restore import verify_restore
from .scenario import run_finish, run_prepare, summarize
from .storage import prepare_demo_storage
from .updates import check_update


def _service(value: str) -> ServiceSpec:
    name, separator, url = value.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError("service must use NAME=URL")
    try:
        return ServiceSpec(name, url)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _required_receipt(value: str) -> tuple[str, str, Path]:
    parts = value.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError("receipt must use LABEL:KIND:PATH")
    return parts[0], parts[1], Path(parts[2])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-ops",
        description="Credential-free reliability evidence for AI-agent services.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="check one or more HTTP health endpoints")
    health.add_argument("--service", action="append", required=True, type=_service)
    health.add_argument("--receipt", required=True, type=Path)

    backup = subparsers.add_parser("backup", help="create an atomic checksummed backup")
    backup.add_argument("--source", required=True, type=Path)
    backup.add_argument("--output-dir", required=True, type=Path)
    backup.add_argument("--receipt", required=True, type=Path)
    backup.add_argument("--retention", type=int, default=3)

    restore = subparsers.add_parser(
        "verify-restore",
        help="verify and restore the newest backup into an ephemeral target",
    )
    archive_group = restore.add_mutually_exclusive_group(required=True)
    archive_group.add_argument("--archive", type=Path)
    archive_group.add_argument("--backup-dir", type=Path)
    restore.add_argument("--restore-root", required=True, type=Path)
    restore.add_argument("--receipt", required=True, type=Path)
    restore.add_argument(
        "--network-disabled",
        action="store_true",
        help="attest that the containing runtime has networking disabled",
    )

    update = subparsers.add_parser("check-update", help="check a local release manifest")
    update.add_argument("--current-version", required=True)
    update.add_argument("--manifest", required=True, type=Path)
    update.add_argument("--receipt", required=True, type=Path)

    notify = subparsers.add_parser("notify", help="emit a credential-free dry-run notification")
    notify.add_argument("--event", required=True)
    notify.add_argument("--message", required=True)
    notify.add_argument("--output", type=Path)
    notify.add_argument("--receipt", required=True, type=Path)

    recovery = subparsers.add_parser("recovery", help="evaluate recovery evidence")
    recovery.add_argument("--failed-health", required=True, type=Path)
    recovery.add_argument("--backup", required=True, type=Path)
    recovery.add_argument("--restore", required=True, type=Path)
    recovery.add_argument("--recovered-health", required=True, type=Path)
    recovery.add_argument("--max-age-seconds", type=int, default=900)
    recovery.add_argument("--receipt", required=True, type=Path)

    readiness = subparsers.add_parser("readiness", help="evaluate fail-closed readiness controls")
    readiness.add_argument(
        "--require",
        action="append",
        required=True,
        type=_required_receipt,
        metavar="LABEL:KIND:PATH",
    )
    readiness.add_argument("--max-age-seconds", type=int, default=900)
    readiness.add_argument("--receipt", required=True, type=Path)

    prepare = subparsers.add_parser("scenario-prepare", help="run the demo's pre-restore stages")
    prepare.add_argument("--service-url", required=True)
    prepare.add_argument("--state-file", required=True, type=Path)
    prepare.add_argument("--artifacts-dir", required=True, type=Path)
    prepare.add_argument("--update-manifest", required=True, type=Path)

    finish = subparsers.add_parser("scenario-finish", help="run the demo's recovery gate")
    finish.add_argument("--service-url", required=True)
    finish.add_argument("--state-file", required=True, type=Path)
    finish.add_argument("--artifacts-dir", required=True, type=Path)

    storage = subparsers.add_parser(
        "prepare-demo-storage",
        help="assign dedicated synthetic mounts to the non-root Compose user",
    )
    storage.add_argument("--artifacts-dir", required=True, type=Path)
    storage.add_argument("--state-dir", required=True, type=Path)
    storage.add_argument("--restore-dir", required=True, type=Path)
    storage.add_argument("--uid", required=True, type=int)
    storage.add_argument("--gid", required=True, type=int)

    summary = subparsers.add_parser("summary", help="show the final demo decision")
    summary.add_argument("--artifacts-dir", required=True, type=Path)
    return parser


def _newest_archive(directory: Path) -> Path:
    archives = sorted(Path(directory).glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"))
    if not archives:
        raise FileNotFoundError("no backup archive was found")
    return archives[-1]


def execute(args: argparse.Namespace) -> int:
    if args.command == "health":
        receipt = check_services(args.service)
        write_receipt(args.receipt, receipt)
        return 0 if receipt.status == "passed" else 1
    if args.command == "backup":
        create_backup(
            args.source,
            args.output_dir,
            args.receipt,
            retention=args.retention,
        )
        return 0
    if args.command == "verify-restore":
        archive = args.archive or _newest_archive(args.backup_dir)
        verify_restore(
            archive,
            args.restore_root,
            args.receipt,
            network_disabled=args.network_disabled,
        )
        return 0
    if args.command == "check-update":
        receipt = check_update(args.current_version, args.manifest)
        write_receipt(args.receipt, receipt)
        return 0
    if args.command == "notify":
        receipt = DryRunNotifier(args.output).send(
            NotificationEvent(args.event, args.message)
        )
        write_receipt(args.receipt, receipt)
        return 0
    if args.command == "recovery":
        receipt = evaluate_recovery(
            failed_health=read_receipt(args.failed_health),
            backup=read_receipt(args.backup),
            restore=read_receipt(args.restore),
            recovered_health=read_receipt(args.recovered_health),
            max_age=timedelta(seconds=args.max_age_seconds),
        )
        write_receipt(args.receipt, receipt)
        return 0 if receipt.status == "passed" else 1
    if args.command == "readiness":
        required = {
            label: (kind, read_receipt(path)) for label, kind, path in args.require
        }
        receipt = evaluate_readiness(
            required,
            max_age=timedelta(seconds=args.max_age_seconds),
        )
        write_receipt(args.receipt, receipt)
        return 0 if receipt.status == "passed" else 1
    if args.command == "scenario-prepare":
        run_prepare(
            service_url=args.service_url,
            state_file=args.state_file,
            artifacts_dir=args.artifacts_dir,
            update_manifest=args.update_manifest,
        )
        return 0
    if args.command == "scenario-finish":
        run_finish(
            service_url=args.service_url,
            state_file=args.state_file,
            artifacts_dir=args.artifacts_dir,
        )
        return 0
    if args.command == "prepare-demo-storage":
        result = prepare_demo_storage(
            (args.artifacts_dir, args.state_dir, args.restore_dir),
            owner_uid=args.uid,
            owner_gid=args.gid,
        )
        print(
            json.dumps(
                {
                    "status": "prepared",
                    "entry_count": result.entry_count,
                    "owner_uid": result.owner_uid,
                    "owner_gid": result.owner_gid,
                    "roots": result.roots,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "summary":
        print(summarize(args.artifacts_dir))
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return execute(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
