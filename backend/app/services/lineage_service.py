"""Lineage of module versions (#26): recording on publish/fork, and the read API.

Writing: `record_version` is called once per repository version, from the code paths that
create versions (PublishService._start_version for uploads, GitHub and mirrors, and
SocialService.fork). The parent is always chosen by the server, never by the caller, and is
validated (relation rules, no cycles) before the immutable row is written.

Reading: a record is visible only while its version exists, is stored, still belongs to the
same repository, and that repository is visible to the viewer. Walks stop at the first hidden
record, and a hidden parent is reported exactly like no parent, so nothing about private,
deleted or unreachable versions (not even their existence) leaks. There is deliberately no
lookup by digest.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.visibility import repository_visible_to
from app.core.exceptions import ConflictError, NotFoundError
from app.models import LineageRecord, ModuleVersion, Repository, User
from app.models.lineage import DECLARATION_VERSION, FORK, ORIGINAL, RELATIONS, VERSION
from app.schemas.lineage import (
    ComparisonSummary,
    FileChange,
    FileSide,
    LineageDiff,
    LineageEdge,
    LineageGraph,
    LineageNode,
    LineageRef,
    LineageSource,
    Publisher,
)

logger = logging.getLogger(__name__)

# Bounds on one walk. Real chains are a few versions deep; these only stop runaway walks.
MAX_DEPTH = 200
MAX_NODES = 1000


class InvalidLineageError(ConflictError):
    code = "InvalidLineage"


# ------------------------------------------------------------------------------ writing


def latest_stored_record(
    session: Session, repository_id: UUID, exclude_version: UUID | None = None
) -> LineageRecord | None:
    """The record of a repository's newest stored version (the parent of its next version)."""
    query = (
        select(LineageRecord)
        .join(ModuleVersion, ModuleVersion.id == LineageRecord.version_id)
        .where(
            ModuleVersion.repository_id == repository_id,
            ModuleVersion.snapshot_sha256.is_not(None),
        )
        .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
        .limit(1)
    )
    if exclude_version is not None:
        query = query.where(ModuleVersion.id != exclude_version)
    return session.scalar(query)


def record_of(session: Session, version_id: UUID) -> LineageRecord | None:
    return session.scalar(select(LineageRecord).where(LineageRecord.version_id == version_id))


def record_version(
    session: Session,
    version: ModuleVersion,
    relation: str,
    parent: LineageRecord | None,
    publisher_id: UUID | None,
) -> LineageRecord:
    """Write the one immutable lineage row of `version`. Refuses anything inconsistent."""
    if version.id is None or version.repository_id is None:
        raise InvalidLineageError("계보는 저장소에 속한 버전에만 기록합니다.")
    if relation not in RELATIONS:
        raise InvalidLineageError("알 수 없는 계보 관계입니다.")
    if (relation == ORIGINAL) != (parent is None):
        raise InvalidLineageError("원본은 부모가 없고, 파생 버전은 부모가 있어야 합니다.")
    if parent is not None:
        same_repository = parent.repository_id == version.repository_id
        if relation == VERSION and not same_repository:
            raise InvalidLineageError("다음 버전의 부모는 같은 모듈의 이전 버전이어야 합니다.")
        if relation == FORK and same_repository:
            raise InvalidLineageError("가져온 모듈의 부모는 다른 모듈의 버전이어야 합니다.")
        _refuse_cycle(session, parent, version.id)
    if record_of(session, version.id) is not None:
        raise InvalidLineageError("이 버전의 계보는 이미 기록되었습니다.")
    record = LineageRecord(
        version_id=version.id,
        repository_id=version.repository_id,
        parent_id=parent.id if parent else None,
        relation=relation,
        manifest=version.manifest,
        manifest_sha256=version.manifest_sha256,
        source_url=version.source_url,
        commit_hash=version.commit_hash,
        module_path=version.module_path or ".",
        publisher_id=publisher_id,
        declaration_version=DECLARATION_VERSION,
    )
    session.add(record)
    session.flush()
    return record


def _refuse_cycle(session: Session, parent: LineageRecord, version_id: UUID) -> None:
    """Walk up from the proposed parent: it must end at a root without meeting itself again
    or the version being recorded."""
    seen: set[UUID] = set()
    node: LineageRecord | None = parent
    while node is not None:
        if node.id in seen or node.version_id == version_id:
            raise InvalidLineageError("계보에 순환이 생기는 부모는 사용할 수 없습니다.")
        seen.add(node.id)
        if len(seen) > MAX_DEPTH * 10:
            raise InvalidLineageError("계보가 너무 깊어 확인할 수 없습니다.")
        node = session.get(LineageRecord, node.parent_id) if node.parent_id else None


def record_new_version(session: Session, version: ModuleVersion, publisher_id: UUID) -> None:
    """Upload, GitHub or mirror: ORIGINAL for a repository's first stored version, otherwise a
    VERSION of the newest stored one."""
    parent = latest_stored_record(session, version.repository_id, exclude_version=version.id)
    record_version(session, version, VERSION if parent else ORIGINAL, parent, publisher_id)


def record_fork(
    session: Session, version: ModuleVersion, source: ModuleVersion, publisher_id: UUID
) -> None:
    parent = record_of(session, source.id)
    if parent is None:
        # Every stored version has a record (0011 backfilled the old ones). Without one the
        # fork is left without lineage rather than being called an original.
        logger.warning("action=lineage_missing_parent version_id=%s", source.id)
        return
    record_version(session, version, FORK, parent, publisher_id)


# ------------------------------------------------------------------------------ reading


def compare(child: LineageRecord, parent: LineageRecord | None):
    """Summary and per-file changes of child against parent, from the recorded manifests."""
    if parent is None:
        return ComparisonSummary(status="NOT_COMPARABLE", reason="NO_VISIBLE_PARENT"), []
    if not child.manifest or not parent.manifest:
        return ComparisonSummary(status="NOT_COMPARABLE", reason="MISSING_MANIFEST"), []
    if child.manifest.get("format") != parent.manifest.get("format"):
        return ComparisonSummary(status="NOT_COMPARABLE", reason="FORMAT_MISMATCH"), []
    before = {f["path"]: f for f in parent.manifest.get("files", [])}
    after = {f["path"]: f for f in child.manifest.get("files", [])}

    def side(entry):
        return FileSide(sha256=entry["sha256"], size=entry["size"], mode=entry["mode"])

    changes, unchanged = [], 0
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old is None:
            changes.append(FileChange(path=path, change="ADDED", after=side(new)))
        elif new is None:
            changes.append(FileChange(path=path, change="REMOVED", before=side(old)))
        elif (old["sha256"], old["size"], old["mode"]) != (new["sha256"], new["size"], new["mode"]):
            changes.append(
                FileChange(path=path, change="MODIFIED", before=side(old), after=side(new))
            )
        else:
            unchanged += 1
    counts = {kind: sum(c.change == kind for c in changes) for kind in ("ADDED", "REMOVED")}
    summary = ComparisonSummary(
        status="CHANGED" if changes else "IDENTICAL",
        added=counts["ADDED"],
        removed=counts["REMOVED"],
        modified=len(changes) - counts["ADDED"] - counts["REMOVED"],
        unchanged=unchanged,
    )
    return summary, changes


class LineageService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    # -------------------------------------------------------------- visibility helpers

    @staticmethod
    def _visible(
        session: Session, ids: set[UUID], viewer_id: UUID | None
    ) -> dict[UUID, LineageRef]:
        """Refs of the records among `ids` that the viewer may see; the rest are absent."""
        if not ids:
            return {}
        rows = session.execute(
            select(LineageRecord.id, User.login, Repository.name, ModuleVersion.version)
            .join(ModuleVersion, ModuleVersion.id == LineageRecord.version_id)
            .join(Repository, Repository.id == ModuleVersion.repository_id)
            .join(User, User.id == Repository.owner_id)
            .where(
                LineageRecord.id.in_(ids),
                ModuleVersion.repository_id == LineageRecord.repository_id,
                ModuleVersion.snapshot_sha256.is_not(None),
                repository_visible_to(viewer_id),
            )
        ).all()
        return {
            row.id: LineageRef(id=row.id, owner=row.login, name=row.name, version=row.version)
            for row in rows
        }

    def _root(
        self, session: Session, owner: str, name: str, version: str | None, viewer_id
    ) -> LineageRecord:
        # Imported here: social_service records forks through this module.
        from app.services.social_service import find_visible_repository

        repository = find_visible_repository(session, owner, name, viewer_id)
        query = select(ModuleVersion).where(
            ModuleVersion.repository_id == repository.id,
            ModuleVersion.snapshot_sha256.is_not(None),
        )
        if version is not None:
            query = query.where(ModuleVersion.version == version)
        row = session.scalar(
            query.order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc()).limit(1)
        )
        record = record_of(session, row.id) if row is not None else None
        if record is None:
            raise NotFoundError("Lineage not found.")
        return record

    def _nodes(
        self, session: Session, records: list[LineageRecord], refs: dict[UUID, LineageRef], viewer
    ) -> list[LineageNode]:
        parent_ids = {r.parent_id for r in records if r.parent_id}
        parents = {
            p.id: p
            for p in session.scalars(select(LineageRecord).where(LineageRecord.id.in_(parent_ids)))
        }
        parent_refs = self._visible(session, parent_ids, viewer)
        publisher_ids = {r.publisher_id for r in records if r.publisher_id}
        users = {u.id: u for u in session.scalars(select(User).where(User.id.in_(publisher_ids)))}
        nodes = []
        for record in records:
            parent_ref = parent_refs.get(record.parent_id) if record.parent_id else None
            parent = parents.get(record.parent_id) if parent_ref else None
            summary, _ = compare(record, parent)
            user = users.get(record.publisher_id) if record.publisher_id else None
            nodes.append(
                LineageNode(
                    ref=refs[record.id],
                    relation=record.relation,
                    parent=parent_ref,
                    comparison=summary,
                    manifest_sha256=record.manifest_sha256,
                    file_count=len(record.manifest["files"]) if record.manifest else None,
                    source=LineageSource(
                        url=record.source_url,
                        commit_hash=record.commit_hash,
                        module_path=record.module_path,
                    ),
                    publisher=Publisher(
                        id=record.publisher_id,
                        login=user.login if user else None,
                        system=bool(user and user.is_system),
                    ),
                    published_at=record.published_at,
                    declaration_version=record.declaration_version,
                    backfilled=record.backfilled,
                )
            )
        return nodes

    # ---------------------------------------------------------------------- endpoints

    def node(self, owner, name, version, viewer_id) -> LineageNode:
        with self.session_factory() as session:
            root = self._root(session, owner, name, version, viewer_id)
            refs = self._visible(session, {root.id}, viewer_id)
            return self._nodes(session, [root], refs, viewer_id)[0]

    def graph(self, owner, name, version, viewer_id, direction, limit, offset) -> LineageGraph:
        with self.session_factory() as session:
            root = self._root(session, owner, name, version, viewer_id)
            root_ref = self._visible(session, {root.id}, viewer_id)[root.id]
            if direction == "ancestors":
                walk, refs, truncated = self._ancestors(session, root, viewer_id)
            else:
                walk, refs, truncated = self._descendants(session, root, viewer_id)
            page = walk[offset : offset + limit]
            # Every node on a walk hangs off root or an earlier node of the same walk, so each
            # node on the page brings exactly one edge, to a node the viewer can see.
            by_id = {r.id: r for r in walk} | {root.id: root}
            edges = []
            for index, record in enumerate(walk[offset : offset + limit], start=offset):
                if direction == "ancestors":
                    child, parent = (walk[index - 1] if index else root), record
                else:
                    child, parent = record, by_id[record.parent_id]
                summary, _ = compare(child, parent)
                edges.append(
                    LineageEdge(
                        parent=parent.id,
                        child=child.id,
                        relation=child.relation,
                        comparison=summary.status,
                    )
                )
            end = offset + len(page)
            return LineageGraph(
                root=root_ref,
                direction=direction,
                nodes=self._nodes(session, page, refs, viewer_id),
                edges=edges,
                total=len(walk),
                limit=limit,
                offset=offset,
                next_offset=end if end < len(walk) else None,
                truncated=truncated,
            )

    def _ancestors(self, session, root, viewer_id):
        walk, refs, node, truncated = [], {}, root, False
        while node.parent_id is not None:
            if len(walk) >= MAX_DEPTH:
                truncated = True
                break
            visible = self._visible(session, {node.parent_id}, viewer_id)
            if node.parent_id not in visible:
                break  # hidden or deleted: the walk ends exactly as it would at a root
            node = session.get(LineageRecord, node.parent_id)
            refs.update(visible)
            walk.append(node)
        return walk, refs, truncated

    def _descendants(self, session, root, viewer_id):
        walk, refs, frontier, depth, truncated = [], {}, [root.id], 0, False
        seen = {root.id}
        while frontier:
            depth += 1
            children = list(
                session.scalars(
                    select(LineageRecord)
                    .where(LineageRecord.parent_id.in_(frontier))
                    .order_by(LineageRecord.published_at, LineageRecord.id)
                )
            )
            visible = self._visible(session, {c.id for c in children}, viewer_id)
            frontier = []
            for child in children:
                if child.id not in visible or child.id in seen:
                    continue  # hidden children are skipped, and so is everything below them
                if len(walk) >= MAX_NODES or depth > MAX_DEPTH:
                    truncated = True
                    break
                seen.add(child.id)
                walk.append(child)
                refs[child.id] = visible[child.id]
                frontier.append(child.id)
        return walk, refs, truncated

    def diff(self, owner, name, version, viewer_id, limit, offset) -> LineageDiff:
        with self.session_factory() as session:
            root = self._root(session, owner, name, version, viewer_id)
            ref = self._visible(session, {root.id}, viewer_id)[root.id]
            parent_ref = None
            parent = None
            if root.parent_id is not None:
                parent_ref = self._visible(session, {root.parent_id}, viewer_id).get(root.parent_id)
                parent = session.get(LineageRecord, root.parent_id) if parent_ref else None
            summary, changes = compare(root, parent)
            page = changes[offset : offset + limit]
            end = offset + len(page)
            return LineageDiff(
                ref=ref,
                parent=parent_ref,
                comparison=summary,
                files=page,
                total=len(changes),
                limit=limit,
                offset=offset,
                next_offset=end if end < len(changes) else None,
            )
