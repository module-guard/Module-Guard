import io
import logging
import zipfile
from unittest.mock import Mock

from conftest import ORIGIN, prepare, publish, register, sign_in, wait_terminal

MARKER = "VALUE-THAT-MUST-NOT-LEAK-7f3a"
USE = {
    "main.tf": 'resource "aws_security_group" "g" {\n  name = var.name\n}\n',
    "variables.tf": (
        'variable "name" {\n  type = string\n}\n'
        'variable "count_of" {\n  type    = number\n  default = 1\n}\n'
        'variable "tags" {\n  type    = map(string)\n  default = {}\n}\n'
        'variable "password" {\n  type      = string\n  sensitive = true\n}\n'
    ),
    "scripts/run.sh": "#!/bin/sh\necho hi\n",
    "README.md": "# use me",
}


def call(client, kind, owner="alice", name="vpc", **body):
    return client.post(f"/api/repositories/{owner}/{name}/{kind}", json=body)


def unzip(response):
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return {info.filename: archive.read(info).decode() for info in archive.infolist()}


def test_preview_and_download_share_one_generated_file(stack, sessions):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    publish(client, "vpc", USE)
    values = {"name": 'web "${var.x}"\n%{evil}', "count_of": "3", "tags": '{"a b": "c"}'}

    preview = call(client, "usage", values=values)
    assert preview.status_code == 200, preview.text
    assert preview.headers["cache-control"] == "no-store"
    body = preview.json()
    assert body["main_tf"] == (
        'module "vpc" {\n'
        '  source   = "./modules/vpc"\n'
        "\n"
        '  name     = "web \\"$${var.x}\\"\\n%%{evil}"\n'
        "  password = var.password\n"
        "  count_of = 3\n"
        '  tags     = {"a b" = "c"}\n'
        "}\n"
    )
    assert body["sensitive"] == ["password"] and body["missing_required"] == []
    assert (
        'variable "password" {\n  type      = string\n  sensitive = true\n}' in body["variables_tf"]
    )
    assert body["filename"] == "vpc-v1.zip" and body["version"] == "v1"
    assert body["git_source"] is None

    download = call(client, "download", values=values)
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-disposition"] == 'attachment; filename="vpc-v1.zip"'
    assert download.headers["x-content-type-options"] == "nosniff"
    files = unzip(download)
    assert files["main.tf"] == body["main_tf"]
    assert files["variables.tf"] == body["variables_tf"]
    # The module is exactly what was stored at publish time.
    assert {n for n in files if n.startswith("modules/")} == {f"modules/vpc/{n}" for n in USE}
    assert all(files[f"modules/vpc/{n}"] == text for n, text in USE.items())
    modes = {
        i.filename: (i.external_attr >> 16) & 0o777
        for i in zipfile.ZipFile(io.BytesIO(download.content)).infolist()
    }
    assert modes["main.tf"] == 0o644 and all(".." not in n and not n.startswith("/") for n in modes)


def test_only_people_who_can_see_the_module_get_it(stack, sessions):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    publish(client, "vpc", USE)
    publish(client, "secret", USE, visibility="PRIVATE")
    assert call(client, "download", name="secret").status_code == 200
    assert call(client, "usage", owner="ALICE", name="secret").status_code == 200

    sign_in(client, sessions, "bob", 2)
    for kind in ("usage", "download"):
        assert call(client, kind, name="secret").status_code == 404
        assert call(client, kind, name="missing").status_code == 404
        assert call(client, kind, version="v9").status_code == 404
        assert call(client, kind).status_code == 200
    # A fork is the forker's own copy, private by default, and downloadable by them.
    assert client.post("/api/repositories/alice/vpc/fork", json={}).status_code == 201
    assert "modules/vpc/main.tf" in unzip(call(client, "download", owner="bob"))

    client.cookies.clear()
    assert call(client, "download").status_code == 200  # Public: no login needed.
    assert call(client, "download", name="secret").status_code == 404
    assert call(client, "download", owner="bob").status_code == 404
    # Without our Origin the request is refused before anything is read.
    foreign = client.post(
        "/api/repositories/alice/vpc/download", json={}, headers={"Origin": "https://evil.test"}
    )
    assert foreign.status_code == 403
    assert client.headers.get("origin") == ORIGIN


def test_an_unfinished_version_cannot_be_downloaded(stack, sessions):
    parked = stack(dispatcher=Mock())  # The scan never runs, so nothing is stored.
    sign_in(parked, sessions, "alice", 1)
    assert register(parked, prepare(parked, USE)["upload_id"], "pending").status_code == 201
    assert parked.get("/api/repositories/alice/pending").status_code == 200  # The owner sees it.
    for kind in ("usage", "download"):
        assert call(parked, kind, name="pending").status_code == 404


def test_bad_values_are_refused_without_repeating_them(stack, sessions, caplog):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    publish(client, "vpc", USE)
    caplog.set_level(logging.DEBUG)
    cases = [
        ({"values": {"count_of": MARKER}}, "count_of"),
        ({"values": {"tags": "{" + MARKER}}, "tags"),
        ({"values": {"tags": f"[var.{MARKER}]"}}, "tags"),
        ({"values": {"password": MARKER}}, "password"),
        ({"values": {MARKER: "1"}}, None),
        ({"pass_sensitive": [MARKER]}, None),
        ({"values": {"name": "x" * 70000}}, "name"),
    ]
    for body, variable in cases:
        for kind in ("usage", "download"):
            refused = call(client, kind, **body)
            assert refused.status_code == 422, (body.keys(), refused.text[:200])
            assert MARKER not in refused.text
            error = refused.json()["error"]
            assert error["code"] == "InvalidValue"
            assert error.get("details", {}).get("variable") == variable
    # Shape errors come from request validation; they must not echo the input either.
    for body in ({"values": MARKER}, {"values": {}, MARKER: 1}, {"version": [MARKER]}):
        shaped = call(client, "usage", **body)
        assert shaped.status_code == 422 and MARKER not in shaped.text
        assert all("input" not in item for item in shaped.json()["detail"])
    assert (
        call(
            client, "download", values={"name": MARKER, "tags": f'{{"k": "{MARKER}"}}'}
        ).status_code
        == 200
    )
    assert MARKER not in caplog.text
    assert "action=module_downloaded" in caplog.text and "inputs=2" in caplog.text
    too_big = client.post(
        "/api/repositories/alice/vpc/usage", json={"values": {"name": "x" * 300000}}
    )
    assert too_big.status_code == 413


def test_downloads_are_limited_per_hour_per_caller(stack, sessions):
    client = stack(downloads_per_hour=2, usage_previews_per_hour=3)
    sign_in(client, sessions, "alice", 1)
    publish(client, "vpc", USE)
    assert [call(client, "download").status_code for _ in range(3)] == [200, 200, 429]
    # Refused requests do not use up the allowance, and previews are counted apart.
    assert call(client, "download", name="missing").status_code == 404
    assert [call(client, "usage").status_code for _ in range(4)] == [200, 200, 200, 429]
    # Someone else (here: signed out, counted by address) has their own allowance.
    client.cookies.clear()
    assert [call(client, "download").status_code for _ in range(3)] == [200, 200, 429]
    sign_in(client, sessions, "bob", 2)
    assert call(client, "download").status_code == 200


def test_github_origin_offers_a_pinned_git_source(stack, sessions):
    client = stack(files=USE)
    sign_in(client, sessions, "alice", 1)
    prepared = client.post(
        "/api/uploads/github", json={"url": "https://github.com/org/terraform-aws-demo"}
    )
    assert prepared.status_code == 200, prepared.text
    created = client.post(
        "/api/repositories", json={"upload_id": prepared.json()["upload_id"], "name": "demo"}
    )
    assert created.status_code == 201
    wait_terminal(client, created.json()["scan_id"])
    body = call(client, "usage", name="demo").json()
    assert (
        body["git_source"] == "git::https://github.com/org/terraform-aws-demo.git?ref=" + "c" * 40
    )
    assert body["missing_required"] == ["name"]
    assert "  # name = ...  (필수: 값을 넣어 주세요)\n" in body["main_tf"]
