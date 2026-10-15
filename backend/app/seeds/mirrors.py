"""Mirrors of public modules, published under a system account through the normal path.

Every module goes through PublishService.prepare_github -> register -> ScanService.run: the
same file policy, secret rules, size limits and scan as any upload. Nothing here bypasses a
rule; a module that is refused is reported and left out. Running it again changes nothing
for modules that are already stored at the pinned commit. Stars and fork counts are never
written.
"""

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DomainError
from app.models import ModuleVersion, Repository, Scan, User
from app.models.enums import Visibility
from app.schemas.publish import RepositoryCreate, VersionCreate
from app.services.publish_service import PublishService
from app.services.scan_service import ScanService

logger = logging.getLogger(__name__)
# Licenses under which the files may be redistributed as they are, with LICENSE kept.
REDISTRIBUTABLE = ("Apache-2.0", "MIT", "BSD-3-Clause")
# Staging needs an owner id; a preview may run before the account exists.
PREVIEW_NAMESPACE = UUID("6f0f3f0e-7c1d-4d59-9a57-1b6c0f1e2a10")


class MirrorAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
    name: str = Field(max_length=255)


class MirrorModule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$")
    tag: str = Field(pattern=r"^v?[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    description: str | None = Field(default=None, max_length=500)
    # Set by a person after looking at the reported locations; never set automatically.
    acknowledge_warnings: bool = False
    # Who looked at what, and when: required whenever warnings are acknowledged.
    reviewed: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _acknowledged_with_a_note(self):
        if self.acknowledge_warnings and not (self.reviewed or "").strip():
            raise ValueError("acknowledge_warnings needs a 'reviewed' note.")
        return self

    @property
    def name(self) -> str:
        return re.sub(r"[^a-z0-9_-]", "-", self.repository.split("/", 1)[1].lower())

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}"


class MirrorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account: MirrorAccount
    modules: list[MirrorModule] = Field(max_length=100)


class SeedAborted(Exception):
    """The run cannot continue safely (for example the account name belongs to a person)."""


@dataclass
class Outcome:
    name: str
    tag: str
    # unchanged | add | update | added | updated | skipped | failed
    action: str
    reason: str = ""
    license: str | None = None
    files: int | None = None
    notes: list[str] = field(default_factory=list)


class MirrorSeeder:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        publish: PublishService,
        scans: ScanService,
    ):
        self.session_factory = session_factory
        self.publish = publish
        self.scans = scans

    def _account(self, session: Session, account: MirrorAccount, create: bool) -> User | None:
        user = session.scalar(select(User).where(func.lower(User.login) == account.login.lower()))
        if user is not None and not user.is_system:
            raise SeedAborted(
                f"'{account.login}' already belongs to a signed-in user; nothing was changed."
            )
        if user is None and create:
            user = User(login=account.login, name=account.name, github_id=None, is_system=True)
            session.add(user)
            session.commit()
            logger.info("action=mirror_account_created login=%s", account.login)
        return user

    @staticmethod
    def _stored_commits(session: Session, user: User | None, name: str) -> set[str] | None:
        """Commits of the stored versions; None when the repository does not exist."""
        if user is None:
            return None
        repository = session.scalar(
            select(Repository).where(Repository.owner_id == user.id, Repository.name == name)
        )
        if repository is None:
            return None
        return set(
            session.scalars(
                select(ModuleVersion.commit_hash).where(
                    ModuleVersion.repository_id == repository.id,
                    ModuleVersion.snapshot_sha256.is_not(None),
                )
            )
        )

    def _drop(self, user_id: UUID) -> None:
        self.publish.staging.discard_all(user_id)  # One prepared source per owner at a time.

    def _one(self, user: User | None, module: MirrorModule, apply: bool) -> Outcome:
        outcome = Outcome(module.name, module.tag, "skipped")
        with self.session_factory() as session:
            stored = self._stored_commits(session, user, module.name)
        if stored is not None and module.commit in stored:
            outcome.action = "unchanged"
            return outcome
        owner_id = user.id if user else uuid5(PREVIEW_NAMESPACE, module.name)
        try:
            report = self.publish.prepare_github(owner_id, module.url, module.tag, module.commit)
        except DomainError as error:
            outcome.reason = f"{getattr(error, 'code', type(error).__name__)}: {error}"
            return outcome
        outcome.license, outcome.files = report.license, report.file_count
        if report.excluded_count:
            outcome.notes.append(f"excluded {report.excluded_count} files not on the allowlist")
        # Locations only; the short previews stay out of the output.
        outcome.notes += [f"warning {w.file}:{w.line} {w.rule}" for w in report.warnings]
        if report.license not in REDISTRIBUTABLE:
            outcome.reason = f"license: {report.license or 'no recognised LICENSE file'}"
        elif report.warnings and not module.acknowledge_warnings:
            outcome.reason = (
                f"{len(report.warnings)} possible secret(s) need review "
                "(set acknowledge_warnings in the manifest after checking)"
            )
        if outcome.reason or not apply:
            self._drop(owner_id)
            if not outcome.reason:
                outcome.action = "update" if stored else "add"
            return outcome
        try:
            if stored:
                created = self.publish.register_version(
                    user,
                    module.name,
                    VersionCreate(
                        upload_id=report.upload_id, acknowledge_warnings=module.acknowledge_warnings
                    ),
                    self.scans.scanner.name,
                )
            else:
                created = self.publish.register(
                    user,
                    RepositoryCreate(
                        upload_id=report.upload_id,
                        name=module.name,
                        description=module.description,
                        visibility=Visibility.PUBLIC,
                        acknowledge_warnings=module.acknowledge_warnings,
                    ),
                    self.scans.scanner.name,
                )
        except DomainError as error:
            self._drop(owner_id)
            outcome.reason = f"{getattr(error, 'code', type(error).__name__)}: {error}"
            return outcome
        self.scans.run(created.scan_id)
        with self.session_factory() as session:
            scan = session.get(Scan, created.scan_id)
            kept = self._stored_commits(session, user, module.name) or set()
            if module.commit in kept:
                outcome.action = "updated" if stored else "added"
                outcome.notes.append(f"{created.version}, scan {scan.status.value.lower()}")
            else:
                outcome.action = "failed"
                outcome.reason = f"scan {scan.status.value.lower()}: {scan.error_message or ''}"
        return outcome

    def run(self, manifest: MirrorManifest, apply: bool) -> list[Outcome]:
        names = [module.name for module in manifest.modules]
        if len(set(names)) != len(names):
            raise SeedAborted("The manifest lists the same module twice.")
        with self.session_factory() as session:
            user = self._account(session, manifest.account, create=apply)
            if user is not None:
                session.expunge(user)
        return [self._one(user, module, apply) for module in manifest.modules]
