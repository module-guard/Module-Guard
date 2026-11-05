"""Lineage API (#26). Every node and edge returned is one the viewer may see.

A parent that is private, deleted or otherwise out of reach is reported exactly like no parent
at all (parent=None, comparison NOT_COMPARABLE / NO_VISIBLE_PARENT), so a response never tells
whether something hidden exists.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Relation = Literal["ORIGINAL", "VERSION", "FORK"]
ComparisonStatus = Literal["IDENTICAL", "CHANGED", "NOT_COMPARABLE"]
# Why two versions could not be compared. NO_VISIBLE_PARENT covers "no parent", "parent
# deleted" and "parent not visible to you" alike, on purpose.
NotComparableReason = Literal["NO_VISIBLE_PARENT", "MISSING_MANIFEST", "FORMAT_MISMATCH"]
FileChangeKind = Literal["ADDED", "REMOVED", "MODIFIED"]


class LineageRef(BaseModel):
    """A visible node, enough to link to it."""

    id: UUID
    owner: str
    name: str
    version: str | None


class Publisher(BaseModel):
    """The account that published the version on this service.

    It is not asserted to be the author of the code, and nothing here establishes legal
    responsibility for it: see the declaration this account published under.
    """

    id: UUID | None
    login: str | None
    system: bool = False


class LineageSource(BaseModel):
    url: str | None
    commit_hash: str | None
    module_path: str


class ComparisonSummary(BaseModel):
    """This version against its parent, from the #9 manifests recorded for both."""

    status: ComparisonStatus
    reason: NotComparableReason | None = None
    added: int = 0
    removed: int = 0
    modified: int = 0
    unchanged: int = 0


class LineageNode(BaseModel):
    ref: LineageRef
    relation: Relation
    parent: LineageRef | None
    comparison: ComparisonSummary
    manifest_sha256: str | None
    file_count: int | None
    source: LineageSource
    publisher: Publisher
    published_at: datetime
    # The publisher declaration text version (docs/module-lineage.md). Null for versions
    # published before declarations existed (backfilled by migration 0011).
    declaration_version: str | None
    backfilled: bool = False
    # Always false: the service records who published, never who wrote the code.
    author_asserted: bool = False


class LineageEdge(BaseModel):
    parent: UUID
    child: UUID
    relation: Relation
    comparison: ComparisonStatus


class LineageGraph(BaseModel):
    """One page of a walk from `root` in one direction, in walk order (nearest first).

    `total` counts only visible nodes on the walk; hidden nodes end the walk and are never
    counted. `edges` connect nodes on this page, plus the edge to `root` from the first.
    """

    root: LineageRef
    direction: Literal["ancestors", "descendants"]
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    total: int
    limit: int
    offset: int
    next_offset: int | None
    # The walk stopped at its depth or size bound; more may exist beyond it.
    truncated: bool = False


class FileSide(BaseModel):
    sha256: str
    size: int
    mode: int


class FileChange(BaseModel):
    path: str
    change: FileChangeKind
    before: FileSide | None = None
    after: FileSide | None = None


class LineageDiff(BaseModel):
    ref: LineageRef
    parent: LineageRef | None
    comparison: ComparisonSummary
    files: list[FileChange] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int
    next_offset: int | None = None
