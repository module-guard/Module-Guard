"""Publish the mirrored modules listed in seeds/mirrors.json under the mirror account.

    python scripts/seed_mirrors.py            # preview: fetch and check, store nothing
    python scripts/seed_mirrors.py --apply    # publish what the preview accepts
    python scripts/seed_mirrors.py --only=terraform-aws-kms,terraform-aws-sqs

Each module goes through the normal publish path (file policy, secret rules, limits, scan)
pinned to the tag and commit in the manifest. Safe to run again: modules already stored at
the pinned commit are left alone. Back up the database first. Uses DATABASE_URL, UPLOAD_ROOT
and SNAPSHOT_ROOT like the application, so run it where the API runs:

    docker compose exec api python scripts/seed_mirrors.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.core.database import create_session_factory
from app.core.logging import configure_logging
from app.seeds.mirrors import MirrorManifest, MirrorSeeder, SeedAborted
from app.services.publish_service import PublishService
from app.services.scan_service import ScanService

arguments = sys.argv[1:]
apply = "--apply" in arguments
only = next((a.split("=", 1)[1].split(",") for a in arguments if a.startswith("--only=")), None)
paths = [a for a in arguments if not a.startswith("--")]
manifest_path = (
    Path(paths[0]) if paths else Path(__file__).resolve().parents[1] / "seeds/mirrors.json"
)
manifest = MirrorManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
if only is not None:
    manifest.modules = [m for m in manifest.modules if m.name in only]
settings = Settings()
configure_logging("WARNING")
factory = create_session_factory(settings.database_url)
seeder = MirrorSeeder(factory, PublishService(factory, settings), ScanService(factory, settings))
print(("APPLY" if apply else "PREVIEW (nothing is stored)") + f": @{manifest.account.login}")
print(f"{'module':32} {'tag':10} {'result':10} {'license':12} {'files':>5}  detail")
counts: dict[str, int] = {}
try:
    for module in manifest.modules:
        # One at a time, printed as it finishes: a full run takes many minutes.
        (outcome,) = seeder.run(MirrorManifest(account=manifest.account, modules=[module]), apply)
        counts[outcome.action] = counts.get(outcome.action, 0) + 1
        print(
            f"{outcome.name[:32]:32} {outcome.tag:10} {outcome.action:10}"
            f" {outcome.license or '-':12} {outcome.files if outcome.files is not None else '-':>5}"
            f"  {outcome.reason}",
            flush=True,
        )
        for note in outcome.notes:
            print(f"{'':32} - {note}", flush=True)
except SeedAborted as error:
    print(f"ABORTED: {error}")
    sys.exit(2)
print("TOTAL: " + ", ".join(f"{action} {count}" for action, count in sorted(counts.items())))
sys.exit(1 if counts.get("failed") else 0)
