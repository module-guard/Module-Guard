import tarfile
import time
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from conftest import FixtureCollector, FixtureScanner
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.exceptions import ScanQueueFullError, ScanTimeoutError
from app.main import create_app
from app.models import Finding, Module, ModuleVersion, Scan
from app.models.enums import ScanStatus
from app.scanners.base import ScanResult
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper
from app.scanners.severity_overrides import SEVERITY_OVERRIDES
from app.services.scan_service import ScanService


def make_service(sessions, settings, report):
    return ScanService(sessions, settings, FixtureCollector(), FixtureScanner(report))


def wait_terminal(client, scan_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/scans/{scan_id}")
        assert response.status_code == 200
        scan = response.json()
        if scan["status"] in ("COMPLETED", "FAILED"):
            return scan
        time.sleep(0.02)
    pytest.fail("Scan did not reach a terminal status")


def test_async_api_end_to_end(sessions, settings, report):
    service = make_service(sessions, settings, report)
    with TestClient(create_app(settings, sessions, service)) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/api/scans", json={"source": "https://github.com/org/repo.git"})
        assert response.status_code == 202
        assert response.json()["status"] == "QUEUED"
        scan_id = response.json()["scan_id"]
        assert response.headers["location"] == f"/api/scans/{scan_id}"
        scan = wait_terminal(client, scan_id)
        assert scan["status"] == "COMPLETED", scan
        assert scan["commit_hash"] == "a" * 40
        assert scan["summary"]["total"] == 3 and scan["summary"]["unknown"] == 1
        assert scan["started_at"] and scan["finished_at"] and scan["duration_ms"] >= 0
        assert not (settings.workspace_root / scan_id).exists()
        module_id = scan["module_id"]
        repeated = client.post("/api/scans", json={"source": "https://github.com/ORG/REPO"}).json()
        assert wait_terminal(client, repeated["scan_id"])["module_id"] == module_id
        latest = wait_terminal(client, repeated["scan_id"])
        assert latest["summary"]["total"] == 3 and latest["summary"]["critical"] == 1
        assert latest["module"]["default_branch"] == "main"
        findings = client.get(
            f"/api/scans/{scan_id}/findings",
            params={
                "severity": "HIGH",
                "rule_id": "CKV_AWS_24",
                "resource_type": "aws_security_group",
            },
        ).json()
        assert len(findings) == 1
        assert client.get(f"/api/scans/{scan_id}/findings", params={"offset": 3}).json() == []
        assert client.get(f"/api/scans/{uuid4()}").status_code == 404
        assert client.get(f"/api/scans/{uuid4()}/findings").status_code == 404
        assert client.get("/api/scans/not-a-uuid").status_code == 422
        assert client.get(f"/api/scans/{scan_id}/findings?limit=101").status_code == 422
        assert client.get(f"/api/scans/{scan_id}/findings?severity=bad").status_code == 422
    with sessions() as session:
        assert session.get(Scan, UUID(scan_id)).raw_result == report


def run_scan(client, source="https://github.com/org/repo"):
    scan_id = client.post("/api/scans", json={"source": source}).json()["scan_id"]
    return wait_terminal(client, scan_id)


def test_scan_records_version_snapshot_and_interface(sessions, settings, report):
    service = make_service(sessions, settings, report)
    original = service.collector.collect

    def with_interface(source, destination):
        result = original(source, destination)
        (destination / "variables.tf").write_text(
            'variable "name" {\n  description = "Group name"\n  type = string\n}\n'
            'variable "port" {\n  default = 22\n}\noutput "id" {\n  value = "x"\n}\n'
        )
        (destination / "README.md").write_text("# Example\n")
        (destination / "LICENSE").write_text("Apache License\nVersion 2.0, January 2004\n")
        return result

    service.collector.collect = with_interface
    with TestClient(create_app(settings, sessions, service)) as client:
        first = run_scan(client)
        second = run_scan(client)
    assert first["status"] == second["status"] == "COMPLETED"
    with sessions() as session:
        versions = list(session.scalars(select(ModuleVersion).order_by(ModuleVersion.created_at)))
        assert [str(v.scan_id) for v in versions] == [first["id"], second["id"]]
        for scan, version in zip((first, second), versions, strict=True):
            assert session.get(Scan, UUID(scan["id"])).module_version_id == version.id
        version = versions[0]
        assert version.repository_id is None and version.commit_hash == "a" * 40
        assert version.readme == "# Example\n" and version.license == "Apache-2.0"
        assert [(v["name"], v["required"]) for v in version.variables] == [
            ("name", True),
            ("port", False),
        ]
        assert version.variables[0]["description"] == "Group name"
        assert version.outputs == [{"name": "id", "description": None, "sensitive": False}]
        assert version.file_count == 4 and version.size_bytes > 0
    # Identical content is stored once, however many scans reference it.
    assert versions[0].snapshot_sha256 == versions[1].snapshot_sha256
    stored = list(settings.snapshot_root.iterdir())
    assert [p.name for p in stored] == [f"{version.snapshot_sha256}.tar.gz"]
    with tarfile.open(stored[0]) as archive:
        assert archive.getnames() == ["LICENSE", "README.md", "main.tf", "variables.tf"]


@pytest.mark.parametrize("broken", ["snapshot", "too_large", "record"])
def test_snapshot_or_version_failure_fails_the_scan(
    sessions, settings, report, broken, monkeypatch
):
    service = make_service(sessions, settings, report)
    if broken == "snapshot":
        service.snapshot_store.save = Mock(side_effect=OSError("read-only file system /secret"))
    elif broken == "too_large":
        service.snapshot_store.max_bytes = 1
    else:
        monkeypatch.setattr(
            "app.services.scan_service.VersionRepository.record",
            Mock(side_effect=RuntimeError("boom")),
        )
    with TestClient(create_app(settings, sessions, service)) as client:
        scan = run_scan(client)
        assert scan["status"] == "FAILED"
        assert (
            scan["error_message"]
            == {
                "snapshot": "Source snapshot could not be stored.",
                "too_large": "Repository exceeds the snapshot size limit.",
                "record": "Internal scan error. See server event logs.",
            }[broken]
        )
        assert "secret" not in scan["error_message"]
        assert client.get(f"/api/scans/{scan['id']}/findings").json() == []
        assert scan["summary"]["total"] == 0
    with sessions() as session:
        assert session.scalar(select(func.count(ModuleVersion.id))) == 0
        assert session.get(Scan, UUID(scan["id"])).module_version_id is None
    assert not list(settings.workspace_root.iterdir())
    if broken != "record":
        assert not settings.snapshot_root.exists() or not list(settings.snapshot_root.iterdir())


@pytest.mark.parametrize(
    "source,code",
    [
        ("file:///etc", 422),
        ("terraform-aws-modules/vpc/aws", 501),
        ("https://token@github.com/org/repo", 422),
    ],
)
def test_reject_before_persist(sessions, settings, report, source, code):
    service = make_service(sessions, settings, report)
    with TestClient(create_app(settings, sessions, service)) as client:
        response = client.post("/api/scans", json={"source": source})
        assert response.status_code == code
        assert "token@" not in response.text
    with sessions() as session:
        assert session.scalar(select(func.count(Scan.id))) == 0
        assert session.scalar(select(func.count(Module.id))) == 0


@pytest.mark.parametrize("failure", ["collect", "empty", "timeout", "normalize", "database"])
def test_failed_pipeline_cleanup(sessions, settings, report, failure, monkeypatch):
    service = make_service(sessions, settings, report)
    if failure == "collect":
        service.collector.collect = Mock(side_effect=RuntimeError("secret-token"))
    elif failure == "empty":
        original = service.collector.collect

        def empty(source, destination):
            result = original(source, destination)
            (destination / "main.tf").unlink()
            return result

        service.collector.collect = empty
    elif failure == "timeout":
        service.scanner.scan = Mock(side_effect=ScanTimeoutError("Scanner timed out."))
    elif failure == "normalize":
        service.scanner.scan = Mock(return_value=ScanResult({}, 0))
    elif failure == "database":

        def broken(self, scan_id, findings):
            self.session.add(Finding(scan_id=scan_id, rule_id=None))

        monkeypatch.setattr("app.services.scan_service.FindingRepository.add_many", broken)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        assert scan.status == ScanStatus.FAILED
        assert scan.finished_at and scan.duration_ms >= 0
        assert "secret-token" not in scan.error_message
        assert session.scalar(select(func.count(Finding.id))) == 0
        if failure == "normalize":
            assert scan.raw_result == {}
    assert not (settings.workspace_root / str(accepted.scan_id)).exists()


def test_partial_persisted_and_duplicate_delivery(sessions, settings, report):
    report["results"]["parsing_errors"] = ["bad.tf"]
    service = make_service(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        assert scan.status == ScanStatus.COMPLETED and scan.partial_result
        assert scan.warnings
        assert session.scalar(select(func.count(Finding.id))) == 3


def test_queue_full_marked_failed(sessions, settings, report):
    service = make_service(sessions, settings, report)
    dispatcher = Mock()
    dispatcher.submit.side_effect = ScanQueueFullError("Queue full")
    with TestClient(create_app(settings, sessions, service, dispatcher)) as client:
        response = client.post("/api/scans", json={"source": "https://github.com/org/repo"})
        assert response.status_code == 503
    with sessions() as session:
        assert session.scalar(select(Scan.status)) == ScanStatus.FAILED


def test_keep_workspace(sessions, settings, report):
    settings.keep_scan_workspace = True
    service = make_service(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    assert (settings.workspace_root / str(accepted.scan_id) / "module" / "main.tf").exists()


def test_reapplying_severity_updates_findings_and_scan_counters(sessions, settings, report):
    from app.scanners.reapply import reapply
    from app.scanners.severity_overrides import SOURCE

    report["results"]["failed_checks"] += [
        {
            "check_id": "CKV_AWS_109",
            "check_name": "x",
            "resource": "aws_iam_policy.a",
            "file_path": "/main.tf",
        },
        {
            "check_id": "CKV_AWS_356",
            "check_name": "x",
            "resource": "aws_iam_policy.a",
            "file_path": "/main.tf",
        },
        {
            "check_id": "CKV_AWS_999",
            "check_name": "x",
            "resource": "aws_iam_policy.a",
            "file_path": "/main.tf",
        },
    ]
    report["summary"]["failed"] = 6
    # Stored as an older release did: nothing assigned.
    service = ScanService(
        sessions, settings, FixtureCollector(), FixtureScanner(report), CheckovNormalizer()
    )
    with TestClient(create_app(settings, sessions, service)) as client:
        scan = run_scan(client)
        assert scan["summary"] == {
            "total": 6,
            "critical": 1,
            "high": 1,
            "medium": 0,
            "low": 0,
            "info": 0,
            "unknown": 4,
        }
        with sessions() as session:
            preview = reapply(session, dry_run=True)
        assert [
            (c.severity_changed, c.body["high"], c.body["medium"], c.body["unknown"])
            for c in preview
        ] == [(2, 2, 1, 2)]
        # A dry run writes nothing.
        assert client.get(f"/api/scans/{scan['id']}").json()["summary"] == scan["summary"]
        with sessions() as session:
            assert [c.changed for c in reapply(session, dry_run=False)] == [2]
        after = client.get(f"/api/scans/{scan['id']}").json()["summary"]
        assert after == {
            "total": 6,
            "critical": 1,
            "high": 2,
            "medium": 1,
            "low": 0,
            "info": 0,
            "unknown": 2,
        }
        findings = client.get(f"/api/scans/{scan['id']}/findings", params={"limit": 50}).json()
        marked = {
            f["rule_id"]: (f["severity"], f["raw_data"].get("severity_source")) for f in findings
        }
        assert marked["CKV_AWS_109"] == ("HIGH", SOURCE) and marked["CKV_AWS_356"] == (
            "MEDIUM",
            SOURCE,
        )
        # Scanner-provided severities and unlisted rules are untouched.
        assert marked["CKV_AWS_24"] == ("HIGH", None) and marked["CKV_AWS_999"] == ("UNKNOWN", None)
        with sessions() as session:
            assert [c.changed for c in reapply(session, dry_run=False)] == [0]


def scoped_report(report):
    base = {"check_name": "x", "resource": "aws_x.y"}
    report["results"]["failed_checks"] = [
        {**base, "check_id": "CKV_AWS_109", "file_path": "/main.tf"},
        {**base, "check_id": "CKV_AWS_109", "file_path": "/modules/a/main.tf"},
        {**base, "check_id": "CKV_AWS_109", "file_path": "/examples/complete/main.tf"},
        {**base, "check_id": "CKV_AWS_79", "file_path": "/tests/unit/main.tf"},
        {**base, "check_id": "CKV_AWS_79", "file_path": "/example/main.tf"},
        {**base, "check_id": "CKV_AWS_356", "file_path": "/test/main.tf"},
        # Declared in the body, but only instantiated from an example.
        {
            **base,
            "check_id": "CKV_AWS_38",
            "file_path": "/main.tf",
            "caller_file_path": "/examples/complete/main.tf",
        },
        # Not one of the example folders.
        {
            **base,
            "check_id": "CKV_AWS_39",
            "file_path": "/main.tf",
            "caller_file_path": "/wrappers/main.tf",
        },
        {**base, "check_id": "CKV_AWS_356", "file_path": "/examples-extra/main.tf"},
    ]
    report["summary"]["failed"] = 9
    return report


def test_findings_are_split_into_module_body_and_examples(sessions, settings, report):
    service = make_service(sessions, settings, scoped_report(report))
    service.normalizer = CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES))
    with TestClient(create_app(settings, sessions, service)) as client:
        scan = run_scan(client)
        # The headline numbers describe the module body only.
        assert scan["summary"] == {
            "total": 4,
            "critical": 0,
            "high": 3,
            "medium": 1,
            "low": 0,
            "info": 0,
            "unknown": 0,
        }
        assert scan["scope_summary"]["example"] == {
            "total": 5,
            "critical": 0,
            "high": 4,
            "medium": 1,
            "low": 0,
            "info": 0,
            "unknown": 0,
        }
        assert scan["scope_summary"]["module_by_scanner"] == {"checkov": 4}
        assert scan["scope_summary"]["example_by_scanner"] == {"checkov": 5}

        def listed(**params):
            return client.get(
                f"/api/scans/{scan['id']}/findings", params={"limit": 50, **params}
            ).json()

        assert len(listed()) == 9
        assert {f["scope"] for f in listed(scope="MODULE")} == {"MODULE"}
        assert len(listed(scope="MODULE")) == 4 and len(listed(scope="EXAMPLE")) == 5
        assert client.get(f"/api/scans/{scan['id']}/findings?scope=bad").status_code == 422
        assert scan["summary"]["total"] == 4 and scan["summary"]["high"] == 3


def test_reapply_classifies_scope_of_findings_stored_before_the_split(sessions, settings, report):
    from sqlalchemy import update

    from app.scanners.reapply import reapply

    service = make_service(sessions, settings, scoped_report(report))
    service.normalizer = CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES))
    with TestClient(create_app(settings, sessions, service)) as client:
        scan = run_scan(client)
        # Put the database back to how it looked before findings were classified.
        with sessions() as session:
            session.execute(update(Finding).values(scope="MODULE"))
            session.execute(
                update(Scan).values(
                    total_findings=9, high_count=7, medium_count=2, scope_summary=None
                )
            )
            session.commit()
        assert client.get(f"/api/scans/{scan['id']}").json()["summary"]["high"] == 7
        with sessions() as session:
            (preview,) = reapply(session, dry_run=True)
        assert (preview.before["high"], preview.body["high"], preview.example["high"]) == (7, 3, 4)
        assert preview.scope_changed == 5 and preview.example_by_caller == 1
        assert client.get(f"/api/scans/{scan['id']}").json()["summary"]["high"] == 7
        with sessions() as session:
            reapply(session, dry_run=False)
        after = client.get(f"/api/scans/{scan['id']}").json()
        assert after["summary"]["total"] == 4 and after["summary"]["high"] == 3
        assert after["scope_summary"]["example"]["high"] == 4
        with sessions() as session:
            assert [c.changed for c in reapply(session, dry_run=False)] == [0]
