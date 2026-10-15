from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Finding, ModuleVersion, Repository, Scan
from app.models.enums import Severity
from app.scanners.scope import MODULE


class VersionRepository:
    def __init__(self, session: Session):
        self.session = session

    def record(self, scan: Scan, artifacts: dict[str, Any]) -> ModuleVersion:
        """Fill the version this scan targets, or create a repository-less one for it."""
        version = (
            self.session.get(ModuleVersion, scan.module_version_id)
            if scan.module_version_id
            else None
        )
        if version is None:
            version = ModuleVersion()
            self.session.add(version)
        version.scan_id = scan.id
        version.commit_hash = scan.commit_hash
        artifacts = dict(artifacts)
        # The provider describes the repository (as of its newest version), not one version.
        provider = artifacts.pop("provider", None)
        for key, value in artifacts.items():
            setattr(version, key, value)
        if provider:
            if version.repository_id is not None:
                self.session.get(Repository, version.repository_id).provider = provider
            if scan.module.provider is None:
                scan.module.provider = provider
        self.session.flush()
        scan.module_version_id = version.id
        version.critical_rules, version.high_rules = rule_kinds(self.session, scan.id)
        return version


def rule_kinds(session: Session, scan_id) -> tuple[int, int]:
    """Distinct CRITICAL and HIGH rules among a scan's module-body findings."""
    counts = dict(
        session.execute(
            select(Finding.severity, func.count(func.distinct(Finding.rule_id)))
            .where(
                Finding.scan_id == scan_id,
                Finding.scope == MODULE,
                Finding.severity.in_([Severity.CRITICAL, Severity.HIGH]),
            )
            .group_by(Finding.severity)
        ).all()
    )
    return counts.get(Severity.CRITICAL, 0), counts.get(Severity.HIGH, 0)
