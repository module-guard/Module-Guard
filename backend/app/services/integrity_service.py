from sqlalchemy import update

from app.models import ModuleVersion
from app.models.common import utcnow
from app.storage.integrity import IntegrityError


def verify_version(session, version, store):
    """Never replace a baseline. MISMATCH is monotonic, including concurrent requests."""
    try:
        if version.integrity_status == "MISMATCH":
            raise IntegrityError(paths=version.mismatched_files)
        if not version.snapshot_sha256 or not version.manifest or not version.manifest_sha256:
            raise IntegrityError("UNVERIFIED")
        data = store.verified_bytes(
            version.snapshot_sha256, version.manifest, version.manifest_sha256
        )
    except IntegrityError as error:
        query = update(ModuleVersion).where(ModuleVersion.id == version.id)
        if error.status != "MISMATCH":
            query = query.where(ModuleVersion.integrity_status != "MISMATCH")
        session.execute(
            query.values(
                integrity_status=error.status, mismatched_files=[d["path"] for d in error.details]
            )
        )
        session.commit()
        raise
    result = session.execute(
        update(ModuleVersion)
        .where(ModuleVersion.id == version.id, ModuleVersion.integrity_status != "MISMATCH")
        .values(integrity_status="VERIFIED", verified_at=utcnow())
    )
    session.commit()
    session.refresh(version)
    if not result.rowcount:
        raise IntegrityError(paths=version.mismatched_files)
    return data
