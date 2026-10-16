import pytest
from conftest import ORIGIN, FakeCheckovSecrets, sign_in
from fastapi.testclient import TestClient

from app.collectors.base import CollectedModule, SourceCollector
from app.core.exceptions import RegistryModuleNotFoundError, UnsupportedSourceError
from app.main import create_app
from app.models.enums import SourceType
from app.services.publish_service import PublishService
from app.uploads.secrets import SecretInspector

TF = 'resource "null_resource" "a" {}\n'
ADDRESS = "terraform-aws-modules/vpc/aws"


class RegistryStub(SourceCollector):
    """A fixture collector: writes a fixed tree for a Registry address, records the version."""

    def __init__(self, files, error=None):
        self.files, self.calls, self.error = files, [], error

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.calls.append((source, ref))
        if self.error is not None:
            raise self.error
        for name, content in self.files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return CollectedModule(
            SourceType.TERRAFORM_REGISTRY,
            source,
            destination,
            "terraform-aws-vpc",
            "terraform-aws-modules",
            "main",
            "c" * 40,
        )


@pytest.fixture
def make_client(settings, sessions):
    opened = []

    def build(files=None, error=None, **overrides):
        configured = settings.model_copy(update=overrides)
        collector = RegistryStub(files or {"main.tf": TF, ".git/config": "[core]"}, error)
        service = PublishService(
            sessions,
            configured,
            collector=collector,
            inspector=SecretInspector(configured, FakeCheckovSecrets()),
        )
        client = TestClient(create_app(configured, sessions, publish_service=service))
        client.__enter__()
        opened.append(client)
        client.service, client.collector = service, collector
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def test_registry_import_requires_login_and_an_allowed_origin(make_client, sessions):
    client = make_client()
    body = {"address": ADDRESS, "version": None}
    assert client.post("/api/uploads/registry", json=body).status_code == 403
    assert (
        client.post(
            "/api/uploads/registry", json=body, headers={"Origin": "https://evil.com"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/uploads/registry", json=body, headers={"Origin": ORIGIN}).status_code
        == 401
    )
    sign_in(client, sessions)
    assert client.post("/api/uploads/registry", json=body).status_code == 200


def test_registry_import_returns_an_upload_report(make_client, sessions):
    files = {"main.tf": TF, "README.md": "# demo", "scripts/x.bin": "x", ".git/config": "[core]"}
    client = make_client(files=files)
    sign_in(client, sessions)
    report = client.post("/api/uploads/registry", json={"address": ADDRESS}).json()
    assert report["origin_type"] == "GITHUB"
    assert report["origin_url"] == ADDRESS
    assert report["commit_hash"] == "c" * 40
    assert report["suggested_name"] == "terraform-aws-vpc"
    assert report["file_count"] == 2
    assert [e["path"] for e in report["excluded"]] == ["scripts/x.bin"]
    # The address and (absent) version were passed straight to the collector.
    assert client.collector.calls == [(ADDRESS, None)]


def test_registry_version_is_passed_to_the_collector(make_client, sessions):
    client = make_client()
    sign_in(client, sessions)
    response = client.post("/api/uploads/registry", json={"address": ADDRESS, "version": "5.8.1"})
    assert response.status_code == 200
    assert client.collector.calls == [(ADDRESS, "5.8.1")]


def test_unknown_registry_module_is_404_and_non_github_is_422(make_client, sessions):
    missing = make_client(error=RegistryModuleNotFoundError("Registry module not found."))
    sign_in(missing, sessions)
    gone = missing.post("/api/uploads/registry", json={"address": ADDRESS})
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "RegistryModuleNotFoundError"

    foreign = make_client(error=UnsupportedSourceError("Only GitHub-backed modules are supported."))
    sign_in(foreign, sessions, "bob", 2)
    refused = foreign.post("/api/uploads/registry", json={"address": ADDRESS})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "UnsupportedSourceError"


def test_malformed_registry_request_is_422(make_client, sessions):
    client = make_client()
    sign_in(client, sessions)
    # extra_forbidden and a missing required field both use FastAPI's 422 detail shape.
    assert client.post("/api/uploads/registry", json={}).status_code == 422
    assert (
        client.post("/api/uploads/registry", json={"address": ADDRESS, "unexpected": 1}).status_code
        == 422
    )


def test_one_registry_import_at_a_time(make_client, sessions):
    client = make_client(upload_preflights_per_hour=3)
    user_id = sign_in(client, sessions)
    with client.service.limiter.slot(user_id):
        busy = client.post("/api/uploads/registry", json={"address": ADDRESS})
        assert busy.status_code == 429 and busy.json()["error"]["code"] == "RateLimitedError"
