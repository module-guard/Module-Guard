from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import (
    ConflictError,
    InvalidPolicyEntryError,
    NotFoundError,
    SourceBlockedError,
    UnsupportedSourceError,
)
from app.models import AllowBlockEntry, AllowBlockEvent, Repository, User
from app.models.enums import OriginType
from app.services import allowblock_service as policy

DOC = Path(__file__).parents[2] / "docs" / "allowblock-policy.md"
SECTIONS = (
    "## 1. 대상 단위",
    "## 2. 우선순위",
    "## 3. 만료와 해제",
    "## 4. 적용 지점",
    "## 5. 감사 이력",
    "## 6. 관리자 권한과 호출 제한",
    "## 7. 공개 범위",
)
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
URL = "https://github.com/Evil-Org/Bad-Module"


def test_policy_document_covers_every_rule():
    text = DOC.read_text(encoding="utf-8")
    for heading in SECTIONS:
        assert heading in text
    assert "BLOCK이 ALLOW보다 우선" in text
    assert "소급" in text


@pytest.fixture
def db(sessions):
    with sessions() as session:
        yield session


@pytest.fixture
def admin(db):
    user = User(login="Root", github_id=900)
    db.add(user)
    db.commit()
    return user


def add(db, admin, kind="github_owner", target="evil-org", effect="BLOCK", now=NOW, **extra):
    return policy.create_entry(
        db,
        admin,
        target_kind=kind,
        target=target,
        effect=effect,
        reason=extra.pop("reason", "reported"),
        evidence_urls=extra.pop("evidence_urls", ["https://example.com/report"]),
        now=now,
        **extra,
    )


def actions(db, entry):
    return {
        event.action
        for event in db.scalars(select(AllowBlockEvent).where(AllowBlockEvent.entry_id == entry.id))
    }


def test_source_keys_are_normalized_to_repository_and_owner():
    assert policy.source_keys(URL + ".git") == [
        ("github_repository", "https://github.com/evil-org/bad-module"),
        ("github_owner", "evil-org"),
    ]
    assert policy.source_keys("hashicorp/consul/aws") == []
    assert policy.source_keys("upload://1234") == []


def test_target_normalization(db, admin):
    owner = add(db, admin, target="Evil-Org")
    assert owner.target_key == "evil-org"
    repo = add(db, admin, "github_repository", URL + ".git")
    assert repo.target_key == "https://github.com/evil-org/bad-module"
    for bad_owner in ("evil/org", "-x", "", "a*", "x" * 40):
        with pytest.raises(InvalidPolicyEntryError):
            add(db, admin, target=bad_owner)
    with pytest.raises(UnsupportedSourceError):
        add(db, admin, "github_repository", "https://gitlab.com/a/b")
    with pytest.raises(InvalidPolicyEntryError):
        add(db, admin, kind="glob", target="*")
    with pytest.raises(InvalidPolicyEntryError):
        add(db, admin, effect="DENY")


def test_repository_kind_needs_an_existing_repository(db, admin):
    with pytest.raises(NotFoundError):
        add(db, admin, "repository", str(uuid4()))
    with pytest.raises(InvalidPolicyEntryError):
        add(db, admin, "repository", "not-a-uuid")
    repository = Repository(owner_id=admin.id, name="m", origin_type=OriginType.UPLOAD)
    db.add(repository)
    db.commit()
    entry = add(db, admin, "repository", str(repository.id).upper())
    assert entry.target_key == str(repository.id)
    assert entry.repository_id == repository.id


def test_block_beats_allow_and_allow_alone_blocks_nothing(db, admin):
    keys = policy.source_keys(URL)
    assert policy.decide(db, keys, NOW).effect is None
    add(db, admin, effect="ALLOW")
    assert policy.decide(db, keys, NOW).effect == "ALLOW"
    policy.enforce_source(db, URL, NOW)  # an ALLOW never stops anything
    add(db, admin, "github_repository", URL, effect="BLOCK")
    assert policy.decide(db, keys, NOW).effect == "BLOCK"
    with pytest.raises(SourceBlockedError):
        policy.enforce_source(db, URL, NOW)


def test_owner_block_applies_to_every_repository_of_the_owner(db, admin):
    add(db, admin, target="evil-org")
    with pytest.raises(SourceBlockedError):
        policy.enforce_source(db, "https://github.com/evil-org/anything", NOW)
    policy.enforce_source(db, "https://github.com/good-org/anything", NOW)


def test_blocked_error_has_a_fixed_safe_message(db, admin):
    add(db, admin, reason="secret internal reason", evidence_urls=["https://x.test/leak"])
    with pytest.raises(SourceBlockedError) as caught:
        policy.enforce_source(db, URL, NOW)
    assert caught.value.status_code == 403
    assert caught.value.code == "SourceBlocked"
    assert "secret" not in str(caught.value) and "evil" not in str(caught.value).lower()


def test_expiry_is_exclusive_at_the_boundary(db, admin):
    expires = NOW + timedelta(hours=1)
    add(db, admin, expires_at=expires)
    keys = policy.source_keys(URL)
    assert policy.decide(db, keys, expires - timedelta(seconds=1)).effect == "BLOCK"
    assert policy.decide(db, keys, expires).effect is None
    assert policy.decide(db, keys, expires + timedelta(days=1)).effect is None


def test_expiry_must_be_in_the_future_and_timezone_aware(db, admin):
    with pytest.raises(InvalidPolicyEntryError):
        add(db, admin, expires_at=NOW)
    with pytest.raises(InvalidPolicyEntryError):
        add(db, admin, expires_at=datetime(2027, 1, 1))


def test_create_writes_an_entry_and_a_create_event_together(db, admin):
    entry = add(db, admin, public_message="Blocked for abuse")
    assert (entry.created_by_login, entry.created_by_id) == ("Root", admin.id)
    (event,) = db.scalars(select(AllowBlockEvent).where(AllowBlockEvent.entry_id == entry.id))
    assert event.action == "CREATE" and event.actor_login == "Root"
    assert event.reason == "reported"
    assert event.evidence_urls == ["https://example.com/report"]
    assert event.snapshot["effect"] == "BLOCK" and event.snapshot["target_key"] == "evil-org"


def test_duplicate_active_entry_is_a_conflict_and_leaves_no_trace(db, admin):
    first = add(db, admin)
    with pytest.raises(ConflictError):
        add(db, admin, now=NOW + timedelta(minutes=1))
    assert db.scalar(select(AllowBlockEntry.id).where(AllowBlockEntry.id != first.id)) is None
    assert actions(db, first) == {"CREATE"}


def test_expired_entry_is_closed_when_the_target_is_registered_again(db, admin):
    old = add(db, admin, expires_at=NOW + timedelta(hours=1))
    later = NOW + timedelta(days=2)
    new = add(db, admin, now=later, reason="again")
    assert new.id != old.id
    db.refresh(old)
    assert old.revoked_at is not None and old.revoke_reason == "expired"
    assert actions(db, old) == {"CREATE", "EXPIRE"}
    assert actions(db, new) == {"CREATE"}
    assert policy.decide(db, policy.source_keys(URL), later).entry_id == new.id


def test_revoke_keeps_the_row_and_records_an_event(db, admin):
    entry = add(db, admin)
    later = NOW + timedelta(hours=2)
    policy.revoke_entry(
        db, admin, entry.id, reason="mistake", evidence_urls=["https://e.test/1"], now=later
    )
    db.refresh(entry)
    assert entry.revoked_at is not None and entry.revoked_by_login == "Root"
    assert entry.revoke_reason == "mistake"
    assert entry.revoke_evidence_urls == ["https://e.test/1"]
    assert actions(db, entry) == {"CREATE", "REVOKE"}
    policy.enforce_source(db, URL, later)
    # The same target can be registered again after a revoke.
    assert add(db, admin, now=later).id != entry.id


def test_revoke_rejects_unknown_revoked_and_expired_entries(db, admin):
    with pytest.raises(NotFoundError):
        policy.revoke_entry(db, admin, uuid4(), reason="x", evidence_urls=[], now=NOW)
    entry = add(db, admin, expires_at=NOW + timedelta(hours=1))
    with pytest.raises(ConflictError):
        policy.revoke_entry(
            db, admin, entry.id, reason="x", evidence_urls=[], now=NOW + timedelta(hours=2)
        )
    other = add(db, admin, target="other")
    policy.revoke_entry(db, admin, other.id, reason="x", evidence_urls=[], now=NOW)
    with pytest.raises(ConflictError):
        policy.revoke_entry(db, admin, other.id, reason="y", evidence_urls=[], now=NOW)


def test_events_are_append_only(db, admin):
    entry = add(db, admin)
    event = db.scalar(select(AllowBlockEvent).where(AllowBlockEvent.entry_id == entry.id))
    event.reason = "rewritten"
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()
    db.delete(db.get(AllowBlockEvent, event.id))
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()


def test_repository_block(db, admin):
    repository = Repository(owner_id=admin.id, name="m", origin_type=OriginType.UPLOAD)
    db.add(repository)
    db.commit()
    policy.enforce_repository(db, repository.id, NOW)
    add(db, admin, "repository", str(repository.id))
    with pytest.raises(SourceBlockedError):
        policy.enforce_repository(db, repository.id, NOW)
    policy.enforce_repository(db, uuid4(), NOW)


def test_guard_checks_current_state_with_its_own_session(sessions, db, admin):
    guard = policy.SourceGuard(sessions)
    guard(URL)
    guard("hashicorp/consul/aws")
    add(db, admin, now=datetime.now(UTC))
    with pytest.raises(SourceBlockedError):
        guard(URL)
    with pytest.raises(SourceBlockedError):
        guard.check(URL)
