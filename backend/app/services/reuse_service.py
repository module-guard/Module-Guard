"""Fail-closed, same-owner static reuse. The source proof is always reverified."""

import copy
import platform
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect, select

from app.attestations.signing import canonical
from app.models import (
    Finding,
    GraphEdge,
    ModuleVersion,
    ParsedEntity,
    Repository,
    ReuseRecord,
    Scan,
)
from app.models.common import utcnow
from app.models.enums import ScanStatus
from app.services.attestation_service import AttestationService, load_policy
from app.services.graph_service import GraphService
from app.services.trust_service import complete
from app.storage.integrity import digest

POLICY = "static-reuse-v1"


def execution_context():
    root = Path(__file__).resolve().parents[1]
    try:
        build = digest(
            b"".join(
                str(p.relative_to(root)).encode() + b"\0" + p.read_bytes()
                for p in sorted(root.rglob("*.py"))
            )
        )
        packages = {
            name: package_version(name) for name in ("python-hcl2", "pydantic", "SQLAlchemy")
        }
        return {
            "mode": "static-offline",
            "python": platform.python_version(),
            "platform": platform.system(),
            "machine": platform.machine(),
            "packages": packages,
            "deployed_code_sha256": build,
            "collection_policy": "snapshot-v1",
            "result_schema": 1,
        }
    except (OSError, PackageNotFoundError):
        return None


def reuse_conditions(manifest, snapshot, provenance, context, policy):
    required = (
        "moduleguard_version",
        "checkov_version",
        "rules_version",
        "severity_rules_version",
        "remediation_rules_version",
        "analysis_options",
        "checkov_options",
    )
    if (
        not manifest
        or not snapshot
        or not context
        or not policy
        or not provenance
        or not all(provenance.get(k) for k in required)
    ):
        return None
    if provenance["checkov_options"].get("download_external_modules") is not False:
        return None
    return {
        "manifest": manifest,
        "snapshot": snapshot,
        "provenance": provenance,
        "environment": context,
        "trust_policy_sha256": digest(canonical(policy.model_dump(mode="json"))),
        "reuse_policy": POLICY,
    }


def copy_rows(session, model, source_id, target_id, ids=None):
    rows = list(session.scalars(select(model).where(model.scan_id == source_id)))
    for row in rows:
        values = {
            c.key: copy.deepcopy(getattr(row, c.key))
            for c in inspect(model).column_attrs
            if c.key not in {"id", "scan_id", "created_at"}
        }
        if model is GraphEdge:
            for key in ("source_entity_id", "target_entity_id"):
                values[key] = ids.get(values[key]) if values[key] else None
        new_id = ids[row.id] if model is ParsedEntity else uuid4()
        session.add(model(id=new_id, scan_id=target_id, **values))
    session.flush()


def try_reuse(session, scan, target, artifacts, provenance, store, started):
    began = time.monotonic()
    policy = load_policy()
    context = execution_context()
    conditions = reuse_conditions(
        artifacts["manifest_sha256"], artifacts["snapshot_sha256"], provenance, context, policy
    )
    key = digest(canonical(conditions)) if conditions else None
    reason = "CONDITIONS_UNRECORDED" if not conditions else "NO_ELIGIBLE_SOURCE"
    # A cross-owner public cache would reveal existence/timing across ownership boundaries.
    candidates = []
    if conditions and target.repository_id:
        owner = session.get(Repository, target.repository_id).owner_id
        candidates = session.scalars(
            select(ModuleVersion)
            .join(Repository)
            .where(
                Repository.owner_id == owner,
                ModuleVersion.id != target.id,
                ModuleVersion.manifest_sha256 == artifacts["manifest_sha256"],
                ModuleVersion.snapshot_sha256 == artifacts["snapshot_sha256"],
            )
            .order_by(ModuleVersion.created_at.desc())
            .limit(20)
        ).all()
    for candidate in candidates:
        source = session.get(Scan, candidate.scan_id) if candidate.scan_id else None
        if not complete(source):
            continue
        old = source.analysis or {}
        previous = reuse_conditions(
            candidate.manifest_sha256,
            candidate.snapshot_sha256,
            old.get("provenance"),
            old.get("execution_context"),
            policy,
        )
        if previous != conditions:
            continue
        deps = GraphService(session).dependencies(source.id)
        # External providers/modules cannot be proven resolved by the present static collector.
        if deps.providers or any(m.source_type not in ("LOCAL", "local") for m in deps.modules):
            continue
        service = AttestationService(session, store, policy)
        verified = service.describe(source, candidate)
        if (
            verified.status != "valid"
            or not verified.entries
            or verified.entries[-1].verification.purpose != "platform"
        ):
            continue
        # describe checks source bytes, persisted rows, policy freshness, revocation and TSA.
        ids = {
            row: uuid4()
            for row in session.scalars(
                select(ParsedEntity.id).where(ParsedEntity.scan_id == source.id)
            )
        }
        copy_rows(session, ParsedEntity, source.id, scan.id, ids)
        copy_rows(session, GraphEdge, source.id, scan.id, ids)
        copy_rows(session, Finding, source.id, scan.id)
        runtime = (scan.analysis or {}).get("runtime")
        retry = (scan.analysis or {}).get("retry_of")
        scan.analysis = {
            **copy.deepcopy(old),
            "execution_context": context,
            "reuse_source": str(source.id),
            "runtime": runtime,
        }
        scan.analysis.pop("retried_by", None)
        scan.analysis.pop("retry_of", None)
        if retry:
            scan.analysis = {**scan.analysis, "retry_of": retry}
        for attr in (
            "total_findings",
            "critical_count",
            "high_count",
            "medium_count",
            "low_count",
            "info_count",
            "unknown_count",
            "scope_summary",
            "raw_result",
        ):
            setattr(scan, attr, copy.deepcopy(getattr(source, attr)))
        scan.status, scan.partial_result, scan.warnings = ScanStatus.COMPLETED, False, []
        scan.finished_at, scan.duration_ms = utcnow(), int((time.monotonic() - started) * 1000)
        for attr in ("readme", "variables", "outputs", "license", "critical_rules", "high_rules"):
            setattr(target, attr, copy.deepcopy(getattr(candidate, attr)))
        for attr, value in artifacts.items():
            if hasattr(target, attr):
                setattr(target, attr, value)
        session.add(
            ReuseRecord(
                target_scan_id=scan.id,
                source_scan_id=source.id,
                source_version_id=candidate.id,
                reused=True,
                reason="VERIFIED_IDENTICAL_STATIC_INPUT",
                key=key,
                policy_version=POLICY,
                conditions=conditions,
                verification=verified.entries[-1].verification.model_dump(mode="json"),
                source_finished_at=source.finished_at,
                source_duration_ms=source.duration_ms,
                lookup_duration_ms=int((time.monotonic() - began) * 1000),
            )
        )
        session.commit()
        return True
    session.add(
        ReuseRecord(
            target_scan_id=scan.id,
            reused=False,
            reason=reason,
            key=key,
            policy_version=POLICY,
            conditions=conditions or {},
            verification={},
            lookup_duration_ms=int((time.monotonic() - began) * 1000),
        )
    )
    session.commit()
    return False
