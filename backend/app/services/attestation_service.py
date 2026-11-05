"""Bind signatures to the visible version, immutable lineage and current scan revision."""

import os
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.inspection import inspect

from app.attestations import signing
from app.attestations.store import ProofStore
from app.auth.visibility import repository_visible_to
from app.core.exceptions import ConflictError, NotFoundError
from app.models import (
    DownloadArtifact,
    Finding,
    GraphEdge,
    ModuleVersion,
    ParsedEntity,
    Repository,
    Scan,
)
from app.models.enums import ScanStatus
from app.schemas.analysis import AnalysisSummary
from app.schemas.attestation import (
    AttestationEntry,
    AttestationPolicy,
    AttestationProof,
    AttestationResponse,
    Verification,
)
from app.services.graph_service import GraphService
from app.services.lineage_service import record_of
from app.storage.artifacts import wrapper
from app.storage.integrity import IntegrityError, digest


def load_policy() -> AttestationPolicy | None:
    path = os.environ.get("MODULEGUARD_ATTESTATION_POLICY_FILE")
    if not path:
        return None
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(131073)
        if len(data) > 131072:
            return None
        return AttestationPolicy.model_validate(signing.strict_json(data))
    except (OSError, ValueError, RecursionError):
        return None


def _json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rows(session, model, scan_id):
    rows = session.scalars(
        select(model).where(model.scan_id == scan_id).order_by(model.id).limit(50001)
    )
    result = [
        {item.key: _json_value(getattr(row, item.key)) for item in inspect(model).column_attrs}
        for row in rows
    ]
    if len(result) > 50000:
        raise ConflictError("검사 결과가 증명 크기 상한을 초과했습니다.")
    return result


class AttestationService:
    def __init__(self, session, store, policy=None):
        self.session, self.store, self.policy = session, store, policy
        self.proofs = ProofStore(store.root / "attestations")

    def target(self, scan_id, version_id, viewer_id, *, owner=False):
        version = self.session.scalar(
            select(ModuleVersion)
            .join(Repository, Repository.id == ModuleVersion.repository_id)
            .where(
                ModuleVersion.id == version_id,
                ModuleVersion.scan_id == scan_id,
                repository_visible_to(viewer_id),
                *([Repository.owner_id == viewer_id] if owner else []),
            )
        )
        if version is None:
            raise NotFoundError("Version not found.")
        scan = self.session.get(Scan, scan_id)
        if scan is None:
            raise NotFoundError("Scan not found.")
        return scan, version

    def binding(self, scan, version):
        if scan.status not in (ScanStatus.COMPLETED, ScanStatus.FAILED):
            raise ConflictError("완료된 검사에만 증명을 발급할 수 있습니다.")
        record = record_of(self.session, version.id)
        if record is None:
            raise ConflictError("불변 계보 기록이 없습니다.")
        if not version.manifest_sha256 or not version.snapshot_sha256:
            raise IntegrityError("UNVERIFIED")
        if version.integrity_status == "MISMATCH" or (
            record.manifest_sha256 != version.manifest_sha256
            or record.manifest != version.manifest
            or (record.commit_hash is not None and record.commit_hash != version.commit_hash)
            or (record.source_url is not None and record.source_url != version.source_url)
            or record.module_path != version.module_path
            or record.repository_id != version.repository_id
        ):
            raise IntegrityError()
        self.store.verified_bytes(
            version.snapshot_sha256, version.manifest, version.manifest_sha256
        )
        _, artifact_key = wrapper(version.repository.name, version.variables)
        artifact = self.session.scalar(
            select(DownloadArtifact).where(
                DownloadArtifact.version_id == version.id,
                DownloadArtifact.request_sha256 == artifact_key,
            )
        )
        if artifact is not None and (
            artifact.integrity_status == "MISMATCH"
            or digest(artifact.content) != artifact.zip_sha256
            or len(artifact.content) != artifact.artifact_size
        ):
            raise IntegrityError()
        analysis = scan.analysis or {}
        # Hash stored result bytes without returning raw findings, expressions or stderr.
        export = {
            "format": 1,
            "scan_id": str(scan.id),
            "status": scan.status.value,
            "partial_result": scan.partial_result,
            "counts": {
                name: getattr(scan, name)
                for name in (
                    "total_findings",
                    "critical_count",
                    "high_count",
                    "medium_count",
                    "low_count",
                    "info_count",
                    "unknown_count",
                )
            },
            "analysis": AnalysisSummary.model_validate(analysis).model_dump(mode="json"),
            "scope_summary": scan.scope_summary,
            "postprocessing": analysis.get("postprocessing"),
            "findings": _rows(self.session, Finding, scan.id),
            "entities": _rows(self.session, ParsedEntity, scan.id),
            "edges": _rows(self.session, GraphEdge, scan.id),
        }
        exported = signing.canonical(export)
        if len(exported) > 16 * 1024 * 1024:
            raise ConflictError("검사 결과가 증명 크기 상한을 초과했습니다.")
        result_digest = digest(exported)
        subjects = [
            {"name": f"manifest:{version.id}", "digest": {"sha256": version.manifest_sha256}},
            {"name": f"snapshot:{version.id}", "digest": {"sha256": version.snapshot_sha256}},
            {"name": f"result:{scan.id}", "digest": {"sha256": result_digest}},
        ]
        if artifact is not None:
            subjects.append(
                {"name": f"zip:{artifact.id}", "digest": {"sha256": artifact.zip_sha256}}
            )
        stored = analysis.get("provenance") or {}
        provenance = {
            key: stored.get(key)
            for key in (
                "moduleguard_version",
                "build_commit",
                "scanner_name",
                "checkov_version",
                "severity_rules_version",
                "remediation_rules_version",
                "rules_version",
                "analysis_options",
                "checkov_options",
            )
        }
        # Commit to provenance values, but do not disclose arbitrary stored metadata in a proof.
        dependencies = (
            GraphService(self.session)
            .dependencies(scan.id)
            .model_dump(mode="json", exclude={"analysis"})
        )
        lockfile = next(
            (f["sha256"] for f in version.manifest["files"] if f["path"] == ".terraform.lock.hcl"),
            None,
        )
        return {
            "subjects": subjects,
            "module_id": str(scan.module_id),
            "repository_id": str(version.repository_id),
            "version_id": str(version.id),
            "scan_id": str(scan.id),
            "lineage_id": str(record.id),
            # Parent information is deliberately absent: a parent may be private/deleted.
            "source_commit": version.commit_hash,
            "source_path": version.module_path,
            "source_url_sha256": digest((version.source_url or "").encode()),
            "result_format": 1,
            "provenance_sha256": digest(signing.canonical(provenance)),
            "dependencies": {
                "declared_sha256": digest(signing.canonical(dependencies)),
                "lockfile_sha256": lockfile,
                "resolved_versions_recorded": False,
            },
            "execution_profile": {"mode": "static", "environment_recorded": False},
            "metadata_complete": False,
        }

    def describe(self, scan, version):
        try:
            saved = self.proofs.read(version.id)
        except signing.AttestationUnavailable:
            return AttestationResponse(
                scan_id=scan.id,
                version_id=version.id,
                status="unknown",
                reasons=["PROOF_STORE_UNAVAILABLE"],
            )
        if not saved:
            return AttestationResponse(
                scan_id=scan.id, version_id=version.id, status="unsigned", reasons=["NO_SIGNATURE"]
            )
        try:
            binding, problem = self.binding(scan, version), None
        except IntegrityError as error:
            binding = None
            problem = Verification(
                status="invalid" if error.status == "MISMATCH" else "unknown",
                reasons=[
                    "CONTENT_MISMATCH" if error.status == "MISMATCH" else "CONTENT_UNAVAILABLE"
                ],
            )
        except (ConflictError, ValueError):
            binding, problem = None, Verification(status="unknown", reasons=["TARGET_UNAVAILABLE"])
        entries = []
        try:
            for item in saved:
                proof = AttestationProof.model_validate(item["proof"])
                checked = problem or signing.verify(proof, binding, self.policy)
                entries.append(AttestationEntry(id=item["id"], proof=proof, verification=checked))
        except (ValueError, KeyError, TypeError):
            return AttestationResponse(
                scan_id=scan.id,
                version_id=version.id,
                status="unknown",
                reasons=["PROOF_STORE_INVALID"],
            )
        return AttestationResponse(
            scan_id=scan.id,
            version_id=version.id,
            status=entries[-1].verification.status,
            reasons=entries[-1].verification.reasons,
            entries=entries,
        )

    def issue(self, scan, version):
        if self.policy is None:
            raise signing.AttestationUnavailable("서명 정책이 설정되지 않았습니다.")
        with self.proofs.issuance(version.id) as fd:
            expected = self.binding(scan, version)
            try:
                proof = signing.issue(expected, self.policy)
            except signing.AttestationUnavailable:
                raise
            except Exception:
                raise signing.AttestationUnavailable(
                    "외부 서명 서비스를 사용할 수 없습니다."
                ) from None
            scan_id, version_id = scan.id, version.id
            self.session.expire_all()
            scan, version = (
                self.session.get(Scan, scan_id),
                self.session.get(ModuleVersion, version_id),
            )
            if (
                scan is None
                or version is None
                or version.scan_id != scan_id
                or self.binding(scan, version) != expected
            ):
                raise ConflictError("서명 중 검사 결과가 변경되었습니다. 다시 시도해 주세요.")
            self.proofs.append(fd, proof)
        return self.describe(scan, version)
