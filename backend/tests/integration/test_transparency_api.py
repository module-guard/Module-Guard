from uuid import UUID

import pytest
from conftest import publish, sign_in
from sqlalchemy import select

from app.models import ModuleVersion
from app.services.attestation_service import AttestationService, result_export_digest


@pytest.fixture
def published(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client)
    scan_id = UUID(created["scan_id"])
    with sessions() as session:
        version = session.scalar(select(ModuleVersion).where(ModuleVersion.scan_id == scan_id))
        version_id = version.id
    return client, scan_id, version_id


def test_result_export_digest_is_the_result_subject_of_the_attestation_binding(published, sessions):
    client, scan_id, version_id = published
    with sessions() as session:
        service = AttestationService(session, client.scans.snapshot_store)
        scan, version = service.target(scan_id, version_id, None)
        subjects = service.binding(scan, version)["subjects"]
        [result] = [s for s in subjects if s["name"] == f"result:{scan_id}"]
        assert result_export_digest(session, scan) == result["digest"]["sha256"]
