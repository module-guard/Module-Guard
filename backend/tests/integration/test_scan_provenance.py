"""Scan provenance is durable and never inferred for legacy scans."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_graph_api import service_for

from app.main import create_app
from app.models import Scan
from app.scanners.reapply import reapply
from app.schemas.scan import ScanResponse


def test_rule_digest_changes_when_a_deployed_rule_changes(tmp_path, monkeypatch):
    from app.scanners import provenance

    for name in provenance._RULE_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((provenance._RULE_ROOT / name).read_bytes())
    monkeypatch.setattr(provenance, "_RULE_ROOT", tmp_path)
    initial = provenance.rule_versions()
    assert all(initial.values())
    severity = tmp_path / "scanners/severity_overrides.py"
    severity.write_bytes(severity.read_bytes() + b"\n# changed rule\n")
    revised = provenance.rule_versions()
    assert revised["severity_rules_version"] != initial["severity_rules_version"]
    assert revised["rules_version"] != initial["rules_version"]
    assert revised["remediation_rules_version"] == initial["remediation_rules_version"]


def test_missing_rule_file_yields_unknown_not_fake_version(tmp_path, monkeypatch):
    from app.scanners import provenance

    monkeypatch.setattr(provenance, "_RULE_ROOT", tmp_path)
    assert all(value is None for value in provenance.rule_versions().values())


def test_fixture_scan_records_and_exposes_nonsecret_provenance(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        stored = scan.analysis["provenance"]
        assert stored["checkov_version"] is None  # A fixture is not real Checkov.
        assert stored["moduleguard_version"]
        assert stored["severity_rules_version"]
        assert stored["remediation_rules_version"]
        assert stored["rules_version"]
        assert stored["analysis_options"]["max_analysis_entities"] == settings.max_analysis_entities
        assert stored["checkov_options"]["skip_path"] == (
            r"(^|/)(\.git|\.terraform|node_modules|vendor|\.external_modules)(/|$)"
        )
        assert "session_secret" not in str(stored).lower()
    with TestClient(create_app(settings, sessions, service)) as client:
        detail = client.get(f"/api/scans/{accepted.scan_id}")
        assert detail.status_code == 200
        assert detail.json()["provenance"] == stored


def test_legacy_scan_has_unknown_provenance(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        assert ScanResponse.from_scan(scan).provenance is None


def test_same_source_with_changed_rules_keeps_distinct_scan_provenance(
    sessions, settings, report, monkeypatch
):
    import app.services.scan_service as scan_module

    first = service_for(sessions, settings, report)
    a = first.create("https://github.com/org/repo")
    first.run(a.scan_id)
    original = scan_module.capture_provenance

    def changed_rules(*args):
        return {**original(*args), "rules_version": "a" * 64}

    monkeypatch.setattr(scan_module, "capture_provenance", changed_rules)
    second = service_for(sessions, settings, report)
    b = second.create("https://github.com/org/repo")
    second.run(b.scan_id)
    with sessions() as session:
        old = session.get(Scan, a.scan_id)
        new = session.get(Scan, b.scan_id)
        assert old.commit_hash == new.commit_hash
        assert (
            old.analysis["provenance"]["rules_version"]
            != new.analysis["provenance"]["rules_version"]
        )


def test_reapply_preserves_scan_provenance_and_tracks_processing(sessions, settings, report):
    from app.models import Finding
    from app.models.enums import Severity

    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        original = scan.analysis["provenance"]
        finding = session.scalar(select(Finding).where(Finding.scan_id == accepted.scan_id))
        finding.rule_id = "CKV_AWS_109"
        finding.severity = Severity.UNKNOWN
        finding.guideline = None
        finding.raw_data = {"check_id": "CKV_AWS_109"}
        session.commit()
    with sessions() as session:
        preview = reapply(session, dry_run=True)
        assert any(c.changed for c in preview)
        assert session.get(Scan, accepted.scan_id).analysis["provenance"] == original
    with sessions() as session:
        reapply(session, dry_run=False)
        analysis = session.get(Scan, accepted.scan_id).analysis
        assert analysis["provenance"] == original
        processed = analysis["postprocessing"]
        assert processed["applied_at"]
        assert processed["severity_rules_version"]
        assert processed["remediation_rules_version"]
        reapply(session, dry_run=False)
        assert session.get(Scan, accepted.scan_id).analysis["postprocessing"] == processed


def test_reapply_records_counter_only_correction_once(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        # Establish a clean baseline even if the fixture needs an initial reapply.
        reapply(session, dry_run=False)
        scan = session.get(Scan, accepted.scan_id)
        scan.high_count += 1
        session.commit()
    with sessions() as session:
        changes = reapply(session, dry_run=False)
        assert all(change.changed == 0 for change in changes)
        processed = session.get(Scan, accepted.scan_id).analysis["postprocessing"]
        assert processed["applied_at"]
        assert processed["rules_version"]
        reapply(session, dry_run=False)
        assert session.get(Scan, accepted.scan_id).analysis["postprocessing"] == processed


def test_noop_reapply_keeps_legacy_null_analysis(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        reapply(session, dry_run=False)  # Normalize all counters/scopes first.
        scan = session.get(Scan, accepted.scan_id)
        scan.analysis = None  # Existing scans can predate the analysis JSON.
        session.commit()
    with sessions() as session:
        assert all(c.changed == 0 for c in reapply(session, dry_run=False))
        assert session.get(Scan, accepted.scan_id).analysis is None
