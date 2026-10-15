from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import ModuleVersion, Repository, Scan
from app.models.common import utcnow
from app.models.enums import OriginType, ScanStatus
from app.schemas.home import Activity, Attention, HomeResponse
from app.schemas.scan import FindingSummary
from app.services.social_service import SocialService

STALE_AFTER_DAYS = 90
ACTIVITY_LIMIT = 5


class HomeService:
    """The signed-in user's own repositories, what needs a look, and recent events."""

    def __init__(self, session_factory: sessionmaker[Session], social: SocialService):
        self.session_factory = session_factory
        self.social = social

    def home(self, user_id: UUID) -> HomeResponse:
        with self.session_factory() as session:
            repositories = list(
                session.scalars(
                    select(Repository)
                    .where(Repository.owner_id == user_id)
                    .order_by(Repository.updated_at.desc(), Repository.id)
                )
            )
            cards = self.social.cards(session, repositories, user_id)
            stale_before = utcnow() - timedelta(days=STALE_AFTER_DAYS)
            attention = []
            for card in cards:
                if card.state == "FAILED":
                    attention.append(Attention(name=card.name, reason="failed", since=None))
                elif card.badge and card.badge.scanned_at:
                    scanned = card.badge.scanned_at
                    if scanned.tzinfo is None:
                        scanned = scanned.replace(tzinfo=stale_before.tzinfo)
                    if scanned < stale_before:
                        attention.append(Attention(name=card.name, reason="stale", since=scanned))

            ids = [r.id for r in repositories]
            names = {r.id: r for r in repositories}
            rows = session.execute(
                select(ModuleVersion, Scan)
                .outerjoin(Scan, Scan.id == ModuleVersion.scan_id)
                .where(ModuleVersion.repository_id.in_(ids))
                .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
                .limit(ACTIVITY_LIMIT)
            ).all()
            # Attempts that were never published keep their scan through module_version_id.
            attempt_scans = {
                scan.module_version_id: scan
                for scan in session.scalars(
                    select(Scan).where(
                        Scan.module_version_id.in_([v.id for v, s in rows if s is None])
                    )
                )
            }
            activity = []
            for version, scan in rows:
                repository = names[version.repository_id]
                scan = scan or attempt_scans.get(version.id)
                if repository.origin_type == OriginType.FORK and version.version == "v1":
                    kind = "forked"
                elif scan is None or scan.status not in (ScanStatus.COMPLETED, ScanStatus.FAILED):
                    kind = "running"
                elif version.snapshot_sha256 and scan.status == ScanStatus.COMPLETED:
                    kind = "published"
                else:
                    kind = "failed"
                at = version.created_at
                if kind in ("published", "failed") and scan.finished_at:
                    at = scan.finished_at
                activity.append(
                    Activity(name=repository.name, version=version.version, kind=kind, at=at)
                )

            # Newest stored version of each repository; two forks may share one scan.
            latest: dict[UUID, UUID] = {}
            for version in session.scalars(
                select(ModuleVersion)
                .where(
                    ModuleVersion.repository_id.in_(ids),
                    ModuleVersion.snapshot_sha256.is_not(None),
                    ModuleVersion.scan_id.is_not(None),
                )
                .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
            ):
                latest.setdefault(version.repository_id, version.scan_id)
            scans = {
                scan.id: scan
                for scan in session.scalars(select(Scan).where(Scan.id.in_(set(latest.values()))))
            }
            totals = dict.fromkeys(("critical", "high", "medium", "low", "info", "unknown"), 0)
            for scan_id in latest.values():
                scan = scans[scan_id]
                for key in totals:
                    totals[key] += getattr(scan, f"{key}_count")
            severity = FindingSummary(total=sum(totals.values()), **totals)
            return HomeResponse(
                repositories=cards,
                attention=attention,
                activity=activity,
                severity=severity,
                stale_after_days=STALE_AFTER_DAYS,
            )
