"""Bulk collection of the pinned candidates in seeds/candidates.json (docs/collection.md).

    python scripts/collect_modules.py run                  # preview: fetch and check, store nothing
    python scripts/collect_modules.py run --apply          # publish what the preview accepts
    python scripts/collect_modules.py run --apply --resume # continue an interrupted run
    python scripts/collect_modules.py pin list.txt > new.json   # "owner/repo [tag]" per line
    python scripts/collect_modules.py verify-pins          # do the pinned tags still point there?

`run` reuses MirrorSeeder (the same publish path as seed_mirrors.py). Back up the database
before --apply. Reports and the resume checkpoint are written as files under --report-dir.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.seeds.collection import (
    BatchCollector,
    latest_tag,
    resolve_pin,
    run_id,
    summarize,
    write_reports,
)
from app.seeds.mirrors import MirrorManifest, MirrorModule, SeedAborted
from app.utils.subprocess import ProcessRunner

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


def load(path: Path) -> MirrorManifest:
    return MirrorManifest.model_validate_json(path.read_text(encoding="utf-8"))


def collect(args: argparse.Namespace) -> int:
    from app.core.config import Settings
    from app.core.database import create_session_factory
    from app.core.logging import configure_logging
    from app.seeds.mirrors import MirrorSeeder
    from app.services.publish_service import PublishService
    from app.services.scan_service import ScanService

    manifest = load(args.manifest)
    already = load(args.exclude) if args.exclude and args.exclude.exists() else None
    if args.only:
        manifest.modules = [m for m in manifest.modules if m.name in args.only.split(",")]
    if args.limit:
        manifest.modules = manifest.modules[: args.limit]
    settings = Settings()
    configure_logging("WARNING")
    factory = create_session_factory(settings.database_url)
    seeder = MirrorSeeder(
        factory, PublishService(factory, settings), ScanService(factory, settings)
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.report_dir / "checkpoint.json"
    if not args.resume:
        checkpoint.unlink(missing_ok=True)
    batch = BatchCollector(
        seeder,
        retries=args.retries,
        min_interval=args.min_interval,
        workers=args.workers,
        checkpoint=checkpoint,
    )
    mode = "APPLY" if args.apply else "PREVIEW (nothing is stored)"
    print(f"{mode}: @{manifest.account.login}, {len(manifest.modules)} candidates", flush=True)
    print(f"{'module':36} {'tag':10} {'state':20} {'category':16} detail", flush=True)

    def show(r):
        print(
            f"{r.name[:36]:36} {r.tag:10} {r.state:20} {r.category:16} {r.reason[:80]}", flush=True
        )

    try:
        records = batch.run(manifest, args.apply, already, show)
    except SeedAborted as error:
        print(f"ABORTED: {error}")
        return 2
    except KeyboardInterrupt:
        print("INTERRUPTED: progress kept; run again with --resume.")
        return 130
    summary = summarize(records, len(manifest.modules))
    json_path, md_path = write_reports(args.report_dir, run_id(), records, summary, args.apply)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"REPORT: {json_path}\nREPORT: {md_path}")
    return 1 if summary["stored_scan_failed"] else 0


def pin(args: argparse.Namespace) -> int:
    """Resolve each "owner/repo [tag]" line to tag + full commit and print a manifest."""
    runner, modules = ProcessRunner(), []
    with tempfile.TemporaryDirectory() as work:
        for line in args.list.read_text(encoding="utf-8").splitlines():
            parts = line.split("#")[0].split()
            if not parts:
                continue
            try:
                tag = parts[1] if len(parts) > 1 else latest_tag(runner, parts[0], Path(work))
                commit = resolve_pin(runner, parts[0], tag, Path(work))
            except RuntimeError as error:
                print(f"SKIPPED {parts[0]}: {error}", file=sys.stderr)
                continue
            modules.append(MirrorModule(repository=parts[0], tag=tag, commit=commit))
            print(f"pinned {parts[0]} {tag}", file=sys.stderr)
    account = {"login": args.login, "name": "terraform 모듈 수집 미러"}
    result = MirrorManifest.model_validate({"account": account, "modules": modules})
    print(result.model_dump_json(indent=2, exclude_defaults=True))
    return 0


def verify(args: argparse.Namespace) -> int:
    runner, moved = ProcessRunner(), 0
    with tempfile.TemporaryDirectory() as work:
        for module in load(args.manifest).modules:
            try:
                now = resolve_pin(runner, module.repository, module.tag, Path(work))
            except RuntimeError as error:
                now = f"unresolved ({error})"
            if now != module.commit:
                moved += 1
                print(
                    f"MOVED {module.repository} {module.tag}: pinned {module.commit[:12]}, now {now[:40]}"
                )
    print(f"{moved} of {len(load(args.manifest).modules)} pins differ")
    return 1 if moved else 0


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
commands = parser.add_subparsers(dest="command", required=True)
run = commands.add_parser("run", help="preview or publish the candidates")
run.add_argument("manifest", nargs="?", type=Path, default=SEEDS / "candidates.json")
run.add_argument("--apply", action="store_true", help="store; without it nothing is stored")
run.add_argument("--resume", action="store_true", help="skip modules finished by an earlier run")
run.add_argument("--only", help="comma separated module names")
run.add_argument("--limit", type=int, help="only the first N candidates")
run.add_argument("--workers", type=int, default=1, help="preview only; --apply is always 1")
run.add_argument("--min-interval", type=float, default=1.0, help="seconds between module starts")
run.add_argument("--retries", type=int, default=3, help="retries for network/timeout failures")
run.add_argument("--exclude", type=Path, default=SEEDS / "mirrors.json", help="already mirrored")
run.add_argument("--report-dir", type=Path, default=Path("backups/collection"))
run.set_defaults(handler=collect)
pin_cmd = commands.add_parser("pin", help="turn 'owner/repo [tag]' lines into a pinned manifest")
pin_cmd.add_argument("list", type=Path)
pin_cmd.add_argument("--login", default="terraform-collected-mirror")
pin_cmd.set_defaults(handler=pin)
verify_cmd = commands.add_parser("verify-pins", help="check that the pinned tags still match")
verify_cmd.add_argument("manifest", nargs="?", type=Path, default=SEEDS / "candidates.json")
verify_cmd.set_defaults(handler=verify)

arguments = parser.parse_args()
sys.exit(arguments.handler(arguments))
