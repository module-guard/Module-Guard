"""Re-apply severity assignments and module/example classification to stored findings."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analyzers.module_interface import UTILITY_PROVIDERS
from app.models import Finding, Module, ModuleVersion, ParsedEntity, Repository, Scan
from app.models.enums import Severity
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

    @property
    def changed(self) -> int:
        return self.severity_changed + self.scope_changed


@dataclass
class _Classified:
    """What scope_summary() needs, without loading ORM rows."""

    scanner: str
    severity: Severity
    scope: str


def reapply(session: Session, dry_run: bool = True) -> list[ScanChange]:
    """Bring stored findings in line with the current rules and fix each scan's counters.

    Severity: only Checkov findings without a scanner-provided severity are touched.
    Scope: every finding is classified as module body or example/test by its location.
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
        if severity != row.severity:
            change.severity_changed += 1
            data = dict(raw)
            if severity == Severity.UNKNOWN:
                data.pop("severity_source", None)
            else:
                data["severity_source"] = SOURCE
            values.update(severity=severity, raw_data=data)
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
