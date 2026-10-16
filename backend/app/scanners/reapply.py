"""Re-apply severity assignments, remediation text and module/example classification to
stored findings.

Only Checkov findings' severity and guideline (plus scope and the scan counters) change.
Correlated findings (TFCR*) are not re-evaluated: a stored TFCR001 that stayed HIGH because
CKV_AWS_62/63 were UNKNOWN at scan time is promoted only by scanning again.

    python -m app.scanners.reapply            # dry run: report what would change
    python -m app.scanners.reapply --apply    # write the changes

See docs/severity-mapping.md.
"""

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analyzers.module_interface import UTILITY_PROVIDERS
from app.models import Finding, Module, ModuleVersion, ParsedEntity, Repository, Scan
from app.models.enums import Severity
from app.scanners.remediation import GUIDELINE_SOURCE, REMEDIATIONS, has_guideline
from app.scanners.scope import EXAMPLE, EXAMPLE_DIRECTORIES, MODULE, finding_scope, scope_summary
from app.scanners.severity_overrides import SEVERITY_OVERRIDES, SOURCE

LEVELS = ("critical", "high", "medium", "low", "info", "unknown")


@dataclass
class ScanChange:
    scan_id: UUID
    module: str
    created_at: object
    # Stored counters before, and the module-body / example split after.
    before: Counter = field(default_factory=Counter)
    body: Counter = field(default_factory=Counter)
    example: Counter = field(default_factory=Counter)
    # Example findings whose own file is in the module body but whose caller is an example.
    example_by_caller: int = 0
    severity_changed: int = 0
    scope_changed: int = 0
    guideline_changed: int = 0

    @property
    def changed(self) -> int:
        """Number of changes; one finding can add to more than one counter."""
        return self.severity_changed + self.scope_changed + self.guideline_changed


@dataclass
class _Classified:
    """What scope_summary() needs, without loading ORM rows."""

    scanner: str
    severity: Severity
    scope: str


def reapply(session: Session, dry_run: bool = True) -> list[ScanChange]:
    """Bring stored findings in line with the current rules and fix each scan's counters.

    Severity: only Checkov findings without a scanner-provided severity are touched.
    Guideline: only Checkov findings whose guideline is empty or was filled by ModuleGuard.
    Scope: every finding is classified as module body or example/test by its location.
    Correlated findings are left as they are (see the module docstring).
    Reads only columns that exist before migration 0007, so a dry run works on an older
    database; applying requires the migration. Safe to run repeatedly.
    """
    rows = session.execute(
        select(
            Finding.id,
            Finding.scan_id,
            Finding.rule_id,
            Finding.scanner,
            Finding.severity,
            Finding.file_path,
            Finding.guideline,
            Finding.raw_data,
            Scan.created_at,
            Module.name,
        )
        .join(Scan, Scan.id == Finding.scan_id)
        .join(Module, Module.id == Scan.module_id)
        .order_by(Scan.created_at)
    ).all()
    # Before migration 0007 there is no scope column: everything counts as MODULE.
    try:
        with session.begin_nested():
            stored_scope = dict(session.execute(select(Finding.id, Finding.scope)).all())
    except Exception:
        stored_scope = {}
    changes: dict[UUID, ScanChange] = {}
    classified: dict[UUID, list[_Classified]] = defaultdict(list)
    for row in rows:
        change = changes.setdefault(row.scan_id, ScanChange(row.scan_id, row.name, row.created_at))
        raw = row.raw_data or {}
        change.before[row.severity.value.lower()] += 1
        severity = row.severity
        if row.scanner == "checkov" and (
            row.severity == Severity.UNKNOWN or raw.get("severity_source") == SOURCE
        ):
            severity = SEVERITY_OVERRIDES.get(row.rule_id, Severity.UNKNOWN)
        scope = finding_scope(row.file_path, raw)
        if scope == EXAMPLE:
            change.example[severity.value.lower()] += 1
            own = (row.file_path or "").lstrip("/").split("/", 1)[0].lower()
            change.example_by_caller += own not in EXAMPLE_DIRECTORIES
        else:
            change.body[severity.value.lower()] += 1
        classified[row.scan_id].append(_Classified(row.scanner, severity, scope))
        values = {}
        data = dict(raw)
        if severity != row.severity:
            change.severity_changed += 1
            if severity == Severity.UNKNOWN:
                data.pop("severity_source", None)
            else:
                data["severity_source"] = SOURCE
            values.update(severity=severity, raw_data=data)
        guideline = _guideline(row.scanner, row.rule_id, row.guideline, raw)
        if guideline != row.guideline:
            change.guideline_changed += 1
            if guideline is not None and guideline == REMEDIATIONS.get(row.rule_id):
                data["guideline_source"] = GUIDELINE_SOURCE
            else:
                data.pop("guideline_source", None)
            values.update(guideline=guideline, raw_data=data)
        if scope != stored_scope.get(row.id, MODULE):
            change.scope_changed += 1
            values["scope"] = scope
        if values and not dry_run:
            session.execute(update(Finding).where(Finding.id == row.id).values(**values))
    if not dry_run:
        for scan_id, change in changes.items():
            session.execute(
                update(Scan)
                .where(Scan.id == scan_id)
                .values(
                    total_findings=sum(change.body.values()),
                    scope_summary=scope_summary(classified[scan_id]),
                    **{f"{level}_count": change.body[level] for level in LEVELS},
                )
            )
        session.commit()
    return list(changes.values())


def _guideline(scanner: str, rule_id: str, stored: str | None, raw: dict) -> str | None:
    """The guideline a stored finding should carry under the current remediation table.

    Checkov's own guideline is never replaced; text ModuleGuard filled is refreshed, or
    cleared when the rule left the table.
    """
    ours = raw.get("guideline_source") == GUIDELINE_SOURCE
    if scanner != "checkov" or (has_guideline(stored) and not ours):
        return stored
    if rule_id in REMEDIATIONS:
        return REMEDIATIONS[rule_id]
    return None if ours else stored


def refresh_rule_counts(session: Session, dry_run: bool = True) -> int:
    """Recompute every version's badge numbers from its scan. Returns how many differ."""
    from app.repositories.version_repository import rule_kinds

    differing = 0
    for version in session.scalars(select(ModuleVersion).where(ModuleVersion.scan_id.is_not(None))):
        counts = rule_kinds(session, version.scan_id)
        if counts != (version.critical_rules, version.high_rules):
            differing += 1
            if not dry_run:
                version.critical_rules, version.high_rules = counts
    if not dry_run:
        session.commit()
    return differing


def backfill_providers(session: Session, dry_run: bool = True) -> list[tuple[str, str]]:
    """Fill repository.provider from the root required_providers of its newest scanned version."""
    filled = []
    for repository in session.scalars(select(Repository).where(Repository.provider.is_(None))):
        scan_id = session.scalar(
            select(ModuleVersion.scan_id)
            .where(
                ModuleVersion.repository_id == repository.id,
                ModuleVersion.scan_id.is_not(None),
                ModuleVersion.snapshot_sha256.is_not(None),
            )
            .order_by(ModuleVersion.created_at.desc())
            .limit(1)
        )
        if scan_id is None:
            continue
        names = sorted(
            entity.name.lower()
            for entity in session.scalars(
                select(ParsedEntity).where(
                    ParsedEntity.scan_id == scan_id,
                    ParsedEntity.entity_type == "REQUIRED_PROVIDER",
                )
            )
            if not (entity.entity_metadata or {}).get("module_path")
        )
        provider = ([n for n in names if n not in UTILITY_PROVIDERS] or names or [None])[0]
        if provider:
            filled.append((repository.name, provider))
            if not dry_run:
                repository.provider = provider
    if not dry_run:
        session.commit()
    return filled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.scanners.reapply",
        description="Re-apply ModuleGuard severity, remediation text and scope to stored scans.",
    )
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--database-url", help="defaults to DATABASE_URL / .env")
    args = parser.parse_args(argv)

    from app.core.config import Settings
    from app.core.database import create_session_factory

    url = args.database_url or Settings().database_url
    dry_run = not args.apply
    with create_session_factory(url)() as session:
        changes = reapply(session, dry_run=dry_run)
        for change in changes:
            if change.changed:
                print(
                    f"{change.scan_id} {change.module}: severity {change.severity_changed}, "
                    f"guideline {change.guideline_changed}, scope {change.scope_changed}; "
                    f"module body now {dict(+change.body)}"
                )
        badges = refresh_rule_counts(session, dry_run=dry_run)
    touched = sum(1 for change in changes if change.changed)
    print(
        f"{'Would update' if dry_run else 'Updated'} {touched} of {len(changes)} scans "
        f"(severity {sum(c.severity_changed for c in changes)}, "
        f"guideline {sum(c.guideline_changed for c in changes)}, "
        f"scope {sum(c.scope_changed for c in changes)}); "
        f"version badges {'differing' if dry_run else 'refreshed'}: {badges}."
    )
    if dry_run:
        print("Dry run: nothing written. Re-run with --apply. Correlations are not recomputed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
