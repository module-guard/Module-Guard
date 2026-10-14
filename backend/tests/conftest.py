import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.collectors.base import CollectedModule, SourceCollector
from app.core.config import Settings
from app.core.database import Base
from app.models import Finding, Module, Scan  # noqa: F401
from app.models.enums import SourceType
from app.scanners.base import ScanResult, SecurityScanner

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        workspace_root=tmp_path / "workspaces",
        snapshot_root=tmp_path / "snapshots",
        upload_root=tmp_path / "uploads",
        # Direct scans (POST /api/scans) and the demo routes exist only in development.
        app_environment="development",
        _env_file=None,
    )


@pytest.fixture
def sessions(settings):
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def report():
    return {
        "check_type": "terraform",
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_24",
                    "check_name": "Restrict SSH ingress",
                    "severity": "HIGH",
                    "resource": "aws_security_group.bad_example",
                    "file_path": "/main.tf",
                    "file_line_range": [1, 9],
                    "check_result": {"result": "FAILED"},
                    "guideline": "https://example.com/rules/24",
                },
                {
                    "check_id": "CKV_TEST_CRITICAL",
                    "check_name": "Test critical",
                    "severity": "CRITICAL",
                    "resource": "aws_security_group.bad_example",
                    "file_path": "/main.tf",
                },
                {
                    "check_id": "CKV_TEST_UNKNOWN",
                    "check_name": "Unknown severity",
                    "resource": "module.child.aws_s3_bucket.insecure",
                    "file_path": "/s3/main.tf",
                },
            ]
        },
        "summary": {"failed": 3, "passed": 0, "parsing_errors": 0},
    }


class FixtureCollector(SourceCollector):
    def supports(self, source: str) -> bool:
        return True

    def collect(self, source: str, destination: Path) -> CollectedModule:
        shutil.copytree(FIXTURES / "open_security_group", destination)
        return CollectedModule(
            SourceType.GITHUB, source, destination, "repo", "org", "main", "a" * 40
        )


class FixtureScanner(SecurityScanner):
    name = "checkov"

    def __init__(self, report):
        self.report = report

    def scan(self, target_path):
        return ScanResult(self.report, 1)


ORIGIN = "http://localhost:3000"


def sign_in(client, sessions, login="alice", github_id=1):
    """Create a user and an authenticated session cookie without going through OAuth."""
    from app.auth import sessions as auth_sessions
    from app.auth.oauth import GitHubProfile

    with sessions() as session:
        user = auth_sessions.upsert_user(
            session,
            GitHubProfile(github_id, login, None, None),
            Settings.model_fields["reserved_logins"].default,
        )
        token = auth_sessions.create(session, user, 3600)
        session.commit()
        user_id = user.id
    for cookie in [c for c in client.cookies.jar if c.name == auth_sessions.SESSION_COOKIE]:
        client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    client.cookies.set(auth_sessions.SESSION_COOKIE, token, domain="testserver.local", path="/")
    client.headers["Origin"] = ORIGIN
    return user_id


class FakeCheckovSecrets:
    """Stands in for the Checkov process; known-format detection still runs for real."""

    def __init__(self, checks=(), error=None):
        self.checks, self.error = list(checks), error

    def run(self, args, **kwargs):
        import json

        from app.utils.subprocess import ProcessResult

        if self.error:
            raise self.error
        report = {"check_type": "secrets", "results": {"failed_checks": self.checks}}
        return ProcessResult(1 if self.checks else 0, json.dumps(report))


# ---------------------------------------------------------------- publishing test stack

import io  # noqa: E402
import time  # noqa: E402
import zipfile  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services.publish_service import PublishService  # noqa: E402
from app.services.scan_service import ScanService  # noqa: E402
from app.uploads.secrets import SecretInspector  # noqa: E402

TF = 'resource "aws_security_group" "bad_example" {\n  name = "x"\n}\n'
VARIABLES = 'variable "name" {\n  description = "Group name"\n  type = string\n}\n'
ZIP = {"Content-Type": "application/zip"}
ENTROPY = "9f86d081884c7d659a2feaa0c55ad015" + "a3bf4f1b2b0b822cd15d6c15b0f00a08"
ENTROPY_CHECK = {
    "check_id": "CKV_SECRET_19",
    "check_name": "Hex High Entropy String",
    "file_path": "/main.tf",
    "file_line_range": [1, 2],
}


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


MODULE = {"main.tf": TF, "variables.tf": VARIABLES, "README.md": "# demo", "run.bin": "x"}


class RepoCollector(SourceCollector):
    """A repository per URL. `files` is one tree for every URL, or {url: {ref: (commit, tree)}}."""

    def __init__(self, files, pinned=None):
        self.files, self.sources, self.pinned = dict(files), [], pinned or {}

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.sources.append(source if ref is None else (source, ref))
        commit, files = "c" * 40, self.files
        if ref is not None:
            commit, files = self.pinned[source][ref]
        for name, content in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        name = source.rsplit("/", 1)[-1] if ref is not None else "terraform-aws-demo"
        return CollectedModule(SourceType.GITHUB, source, destination, name, "org", "main", commit)


class NeverCollect(SourceCollector):
    """The scan pipeline must not download a published source again."""

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        raise AssertionError("published source was collected a second time")


@pytest.fixture
def stack(settings, sessions, report):
    opened = []

    def build(checks=(), files=None, dispatcher=None, **overrides):
        configured = settings.model_copy(update=overrides)
        github = RepoCollector(files or {**MODULE, ".git/config": "[core]"})
        publish = PublishService(
            sessions,
            configured,
            collector=github,
            inspector=SecretInspector(configured, FakeCheckovSecrets(checks)),
        )
        scans = ScanService(sessions, configured, NeverCollect(), FixtureScanner(report))
        client = TestClient(
            create_app(configured, sessions, scans, dispatcher=dispatcher, publish_service=publish)
        )
        client.__enter__()
        opened.append(client)
        client.github, client.publish, client.scans = github, publish, scans
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def prepare(client, entries=None):
    response = client.post("/api/uploads/zip", content=zip_bytes(entries or MODULE), headers=ZIP)
    assert response.status_code == 200, response.text
    return response.json()


def register(client, upload_id, name="demo", **extra):
    return client.post("/api/repositories", json={"upload_id": upload_id, "name": name, **extra})


def wait_terminal(client, scan_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        scan = client.get(f"/api/scans/{scan_id}").json()
        if scan["status"] in ("COMPLETED", "FAILED"):
            return scan
        time.sleep(0.02)
    pytest.fail("Scan did not reach a terminal status")


def publish(client, name="demo", entries=None, **extra):
    created = register(client, prepare(client, entries)["upload_id"], name, **extra)
    assert created.status_code == 201, created.text
    return created.json(), wait_terminal(client, created.json()["scan_id"])
