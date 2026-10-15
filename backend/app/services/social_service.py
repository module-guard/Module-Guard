"""Explore, stars, forks and profiles: everything about other people's repositories."""

from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.auth.visibility import repository_visible_to
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, RateLimitedError
from app.models import ModuleVersion, Repository, Scan, Star, User
from app.models.enums import OriginType, ScanStatus, Visibility
from app.schemas.social import (
    BadgeInfo,
    ForkCreated,
    ForkRequest,
    RepositoryCard,
    RepositoryRef,
    StarState,
    UserProfile,
)
from app.uploads.limits import HourlyLimiter

Sort = Literal["popular", "recent", "name"]
Kind = Literal["owned", "forked", "starred"]


def find_user(session: Session, login: str) -> User:
    """GitHub logins are case-insensitive, so lookups are too."""
    user = session.scalar(select(User).where(func.lower(User.login) == login.lower()))
    if user is None:
        raise NotFoundError("User not found.")
    return user


def find_visible_repository(
    session: Session, owner: str, name: str, viewer_id: UUID | None
) -> Repository:
    """A repository the viewer may see; hidden ones are indistinguishable from missing."""
    repository = session.scalar(
        select(Repository)
        .join(User, User.id == Repository.owner_id)
        .where(
            func.lower(User.login) == owner.lower(),
            Repository.name == name,
            repository_visible_to(viewer_id),
        )
    )
    if repository is None:
        raise NotFoundError("Repository not found.")
    return repository


class SocialService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        fork_limiter: HourlyLimiter | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.fork_limiter = fork_limiter or HourlyLimiter(settings.forks_per_hour)

    # ------------------------------------------------------------------ cards

    def cards(
        self, session: Session, repositories: list[Repository], viewer_id: UUID | None
    ) -> list[RepositoryCard]:
        """Cards for a page of repositories using a fixed number of queries."""
        if not repositories:
            return []
        ids = [r.id for r in repositories]
        versions: dict[UUID, list[ModuleVersion]] = {i: [] for i in ids}
        for version in session.scalars(
            select(ModuleVersion)
            .where(ModuleVersion.repository_id.in_(ids))
            .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
        ):
            versions[version.repository_id].append(version)
        newest = {
            i: next((v for v in rows if v.snapshot_sha256), None) for i, rows in versions.items()
        }
        attempts = {i: rows[0] if rows else None for i, rows in versions.items()}
        scan_ids = {v.scan_id for v in newest.values() if v and v.scan_id}
        scans = {
            scan.id: scan for scan in session.scalars(select(Scan).where(Scan.id.in_(scan_ids)))
        }
        # State of an unpublished repository comes from its latest attempt's scan.
        pending = [v.id for i, v in attempts.items() if v and newest[i] is None]
        attempt_scans = {
            scan.module_version_id: scan
            for scan in session.scalars(select(Scan).where(Scan.module_version_id.in_(pending)))
        }
        origin_ids = {r.forked_from_id for r in repositories if r.forked_from_id}
        origins = {
            origin.id: origin
            for origin in session.scalars(
                select(Repository).where(
                    Repository.id.in_(origin_ids), repository_visible_to(viewer_id)
                )
            )
        }
        cards = []
        for repository in repositories:
            version = newest[repository.id]
            scan = scans.get(version.scan_id) if version else None
            attempt = attempts[repository.id]
            attempt_scan = attempt_scans.get(attempt.id) if attempt else None
            failed = (
                version is None
                and attempt_scan is not None
                and attempt_scan.status == ScanStatus.FAILED
            )
            origin = origins.get(repository.forked_from_id)
            cards.append(
                RepositoryCard(
                    owner=repository.owner.login,
                    mirror=repository.owner.is_system,
                    owner_avatar_url=repository.owner.avatar_url,
                    name=repository.name,
                    description=repository.description,
                    provider=repository.provider,
                    visibility=repository.visibility,
                    origin_type=repository.origin_type,
                    forked_from=(
                        RepositoryRef(owner=origin.owner.login, name=origin.name)
                        if origin
                        else None
                    ),
                    star_count=repository.star_count,
                    fork_count=repository.fork_count,
                    updated_at=repository.updated_at,
                    state="PUBLISHED" if version else "FAILED" if failed else "PENDING",
                    failure=attempt_scan.error_message if failed else None,
                    badge=(
                        BadgeInfo(
                            version=version.version,
                            critical_rules=version.critical_rules,
                            high_rules=version.high_rules,
                            unknown=scan.unknown_count if scan else 0,
                            total=scan.total_findings if scan else 0,
                            scanned_at=(scan.finished_at or scan.created_at) if scan else None,
                        )
                        if version
                        else None
                    ),
                )
            )
        return cards

    # ---------------------------------------------------------------- explore

    def explore(
        self,
        viewer_id: UUID | None,
        limit: int,
        offset: int,
        search: str | None = None,
        provider: str | None = None,
        sort: Sort = "popular",
    ) -> list[RepositoryCard]:
        """Public repositories with a stored version. Nobody's private work appears here."""
        with self.session_factory() as session:
            query = select(Repository).where(repository_visible_to(None))
            if search:
                query = query.where(
                    or_(
                        Repository.name.icontains(search, autoescape=True),
                        Repository.description.icontains(search, autoescape=True),
                    )
                )
            if provider:
                query = query.where(func.lower(Repository.provider) == provider.lower())
            order = {
                "popular": (
                    (Repository.star_count + Repository.fork_count).desc(),
                    Repository.updated_at.desc(),
                ),
                "recent": (Repository.updated_at.desc(),),
                "name": (Repository.name.asc(),),
            }[sort]
            rows = list(
                session.scalars(query.order_by(*order, Repository.id).limit(limit).offset(offset))
            )
            return self.cards(session, rows, viewer_id)

    def providers(self) -> list[str]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(func.lower(Repository.provider))
                    .where(repository_visible_to(None), Repository.provider.is_not(None))
                    .distinct()
                    .order_by(func.lower(Repository.provider))
                )
            )

    # ------------------------------------------------------------------ stars

    def set_star(self, viewer_id: UUID, owner: str, name: str, starred: bool) -> StarState:
        """Idempotent. star_count is a cache kept in step within the same transaction."""
        with self.session_factory() as session:
            repository = find_visible_repository(session, owner, name, viewer_id)
            existing = session.get(Star, (viewer_id, repository.id))
            if starred and existing is None:
                try:
                    with session.begin_nested():
                        session.add(Star(user_id=viewer_id, repository_id=repository.id))
                        session.flush()
                except IntegrityError:
                    pass  # A concurrent request starred it first: nothing to count.
                else:
                    self._bump(session, repository.id, Repository.star_count, 1)
            elif not starred and existing is not None:
                session.delete(existing)
                session.flush()
                self._bump(session, repository.id, Repository.star_count, -1)
            session.commit()
            session.refresh(repository)
            return StarState(starred=starred, star_count=repository.star_count)

    @staticmethod
    def _bump(session: Session, repository_id: UUID, column, delta: int) -> None:
        condition = [Repository.id == repository_id]
        if delta < 0:
            condition.append(column > 0)
        session.execute(update(Repository).where(*condition).values({column.key: column + delta}))

    @staticmethod
    def is_starred(session: Session, viewer_id: UUID | None, repository_id: UUID) -> bool:
        return viewer_id is not None and session.get(Star, (viewer_id, repository_id)) is not None

    # ------------------------------------------------------------------- fork

    def fork(self, viewer: User, owner: str, name: str, payload: ForkRequest) -> ForkCreated:
        """Copy rows only: the fork's first version points at the origin's snapshot and scan."""
        self.fork_limiter.check(viewer.id, "가져오기가 너무 많습니다. 잠시 뒤 다시 시도해 주세요.")
        with self.session_factory() as session:
            origin = find_visible_repository(session, owner, name, viewer.id)
            if origin.owner_id == viewer.id:
                raise ConflictError(
                    "자신의 모듈은 가져올 수 없습니다. 새 버전 올리기를 사용하세요."
                )
            source = session.scalar(
                select(ModuleVersion)
                .where(
                    ModuleVersion.repository_id == origin.id,
                    ModuleVersion.snapshot_sha256.is_not(None),
                )
                .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
                .limit(1)
            )
            if source is None:
                raise NotFoundError("Repository not found.")
            owned = session.scalar(
                select(func.count(Repository.id)).where(Repository.owner_id == viewer.id)
            )
            if owned >= self.settings.max_repositories_per_user:
                raise RateLimitedError(
                    "모듈 수가 상한에 도달했습니다. 쓰지 않는 모듈을 지워 주세요."
                )
            taken = set(
                session.scalars(select(Repository.name).where(Repository.owner_id == viewer.id))
            )
            if payload.name is not None:
                if payload.name in taken:
                    raise ConflictError("같은 이름의 모듈이 이미 있습니다.")
                chosen = payload.name
            else:
                chosen, suffix = origin.name, 0
                while chosen in taken:
                    suffix += 1
                    chosen = f"{origin.name[:95]}-{suffix}"
            scan_id = source.scan_id or session.scalar(
                select(Scan.id).where(Scan.module_version_id == source.id).limit(1)
            )
            fork = Repository(
                owner_id=viewer.id,
                name=chosen,
                description=origin.description,
                provider=origin.provider,
                visibility=payload.visibility,
                forked_from_id=origin.id,
                origin_type=OriginType.FORK,
                origin_url=origin.origin_url,
            )
            session.add(fork)
            try:
                session.flush()
            except IntegrityError:
                raise ConflictError("같은 이름의 모듈이 이미 있습니다.") from None
            session.add(
                ModuleVersion(
                    repository_id=fork.id,
                    version="v1",
                    # Same digest as the origin: storage/retention.py keeps the file alive
                    # for as long as any version, the origin's or a fork's, references it.
                    snapshot_sha256=source.snapshot_sha256,
                    size_bytes=source.size_bytes,
                    file_count=source.file_count,
                    commit_hash=source.commit_hash,
                    readme=source.readme,
                    variables=source.variables,
                    outputs=source.outputs,
                    license=source.license,
                    critical_rules=source.critical_rules,
                    high_rules=source.high_rules,
                    scan_id=scan_id,
                )
            )
            self._bump(session, origin.id, Repository.fork_count, 1)
            session.commit()
            return ForkCreated(
                owner=viewer.login,
                name=chosen,
                visibility=fork.visibility,
                forked_from=RepositoryRef(owner=origin.owner.login, name=origin.name),
            )

    # ---------------------------------------------------------------- profile

    def profile(self, login: str, viewer_id: UUID | None) -> UserProfile:
        with self.session_factory() as session:
            user = find_user(session, login)
            counts = {
                kind: session.scalar(
                    select(func.count()).select_from(
                        self._listing(user, kind, viewer_id).subquery()
                    )
                )
                for kind in ("owned", "forked", "starred")
            }
            return UserProfile(
                login=user.login,
                mirror=user.is_system,
                name=user.name,
                avatar_url=user.avatar_url,
                is_self=user.id == viewer_id,
                repository_count=counts["owned"],
                fork_count=counts["forked"],
                star_count=counts["starred"],
            )

    @staticmethod
    def _listing(user: User, kind: Kind, viewer_id: UUID | None):
        """Repositories of one profile tab, limited to what the viewer may see."""
        query = select(Repository).where(repository_visible_to(viewer_id))
        if kind == "starred":
            star = aliased(Star)
            return query.join(star, star.repository_id == Repository.id).where(
                star.user_id == user.id
            )
        query = query.where(Repository.owner_id == user.id)
        forked = Repository.origin_type == OriginType.FORK
        return query.where(forked if kind == "forked" else ~forked)

    def profile_repositories(
        self, login: str, kind: Kind, viewer_id: UUID | None, limit: int, offset: int
    ) -> list[RepositoryCard]:
        with self.session_factory() as session:
            user = find_user(session, login)
            rows = list(
                session.scalars(
                    self._listing(user, kind, viewer_id)
                    .order_by(Repository.updated_at.desc(), Repository.id)
                    .limit(limit)
                    .offset(offset)
                )
            )
            return self.cards(session, rows, viewer_id)


__all__ = ["SocialService", "Visibility", "find_user", "find_visible_repository"]
