import json
import logging
import os
import shutil
import sys
from pathlib import Path

import pytest

from app.core.exceptions import UploadRejectedError
from app.uploads.secrets import (
    SecretInspector,
    SecretScanUnavailableError,
    refuse_confirmed,
)
from app.utils.subprocess import ProcessResult

BINARY = os.getenv("CHECKOV_BINARY") or str(
    Path(__file__).parents[2] / ".venv-checkov" / "bin" / "checkov"
)
if not Path(BINARY).is_file():
    BINARY = shutil.which("checkov") or str(Path(sys.executable).parent / "checkov")

# Credential-shaped values are assembled at runtime so none is committed verbatim.
AWS = "AKIA" + "Z7QHB3K2" + "LMNOP4RT"
GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6q7R8"
SLACK = "xoxb-" + "123456789012-1234567890123-" + "AbCdEfGhIjKlMnOpQrStUvWx"
STRIPE = "sk_live_" + "4eC39HqLyjWDarjtT1" + "zdp7dcABCDEF"
PRIVATE_KEY = "-----BEGIN RSA " + "PRIVATE KEY-----"
ENTROPY = "9f86d081884c7d659a2feaa0c55ad015" + "a3bf4f1b2b0b822cd15d6c15b0f00a08"
ALL_VALUES = (AWS, GITHUB, SLACK, STRIPE, ENTROPY)


class FakeRunner:
    def __init__(self, checks=(), returncode=1, stdout=None, error=None):
        self.checks, self.returncode, self.stdout, self.error = checks, returncode, stdout, error
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        report = {"check_type": "secrets", "results": {"failed_checks": list(self.checks)}}
        output = self.stdout if self.stdout is not None else json.dumps(report)
        return ProcessResult(self.returncode, output)


def check(rule, name, file, line):
    return {
        "check_id": rule,
        "check_name": name,
        "file_path": f"/{file}",
        "file_line_range": [line, line + 1],
        "resource": "sha1-of-the-secret",
    }


def module(tmp_path, files):
    root = tmp_path / "module"
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root, sorted(files)


def inspect(settings, tmp_path, files, runner=None, real=False):
    root, names = module(tmp_path, files)
    runner = None if real else (runner or FakeRunner())
    return SecretInspector(settings, runner).inspect(root, names)


@pytest.mark.parametrize(
    "value,rule",
    [
        (AWS, "AWS_ACCESS_KEY"),
        ("ASIA" + AWS[4:], "AWS_ACCESS_KEY"),
        (GITHUB, "GITHUB_TOKEN"),
        (SLACK, "SLACK_TOKEN"),
        (STRIPE, "STRIPE_KEY"),
        (PRIVATE_KEY, "PRIVATE_KEY"),
        ("-----BEGIN OPENSSH " + "PRIVATE KEY-----", "PRIVATE_KEY"),
    ],
)
def test_known_formats_are_definite_in_any_file_type(settings, tmp_path, value, rule):
    findings = inspect(
        settings, tmp_path, {"main.tf": "# ok\n", "docs/notes.md": f"line one\nkey: {value}\n"}
    )
    assert [(f.file, f.line, f.rule, f.confirmed) for f in findings] == [
        ("docs/notes.md", 2, rule, True)
    ]
    assert findings[0].preview == value[:4] + "••••"
    assert value not in json.dumps([f.public() for f in findings], ensure_ascii=False)


def test_documented_aws_examples_are_not_secrets(settings, tmp_path):
    text = (
        'access_key = "AKIAIOSFODNN7EXAMPLE"\n'
        'secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
    )
    runner = FakeRunner(
        [
            check("CKV_SECRET_2", "AWS Access Key", "main.tf", 1),
            check("CKV_SECRET_6", "Base64 High Entropy String", "main.tf", 2),
        ]
    )
    assert inspect(settings, tmp_path, {"main.tf": text}, runner) == []


def test_checkov_rules_split_into_definite_and_entropy(settings, tmp_path):
    text = f'a = "{ENTROPY}"\nb = "plain"\nc = "twilio-shaped-value-1234567890"\n'
    runner = FakeRunner(
        [
            check("CKV_SECRET_19", "Hex High Entropy String", "main.tf", 1),
            check("CKV_SECRET_18", "Twilio API Key", "main.tf", 3),
        ]
    )
    findings = inspect(settings, tmp_path, {"main.tf": text}, runner)
    assert [(f.line, f.rule, f.confirmed, f.preview) for f in findings] == [
        (3, "CKV_SECRET_18", True, "twil••••"),
        (1, "CKV_SECRET_19", False, "9f86••••"),
    ]
    with pytest.raises(UploadRejectedError) as caught:
        refuse_confirmed(findings)
    assert caught.value.code == "SecretsDetected"
    assert caught.value.details == [
        {
            "path": "main.tf",
            "line": 3,
            "rule": "CKV_SECRET_18",
            "reason": "Twilio API Key",
            "preview": "twil••••",
        }
    ]
    warnings = refuse_confirmed([f for f in findings if not f.confirmed])
    assert [f.rule for f in warnings] == ["CKV_SECRET_19"]


def test_definite_match_supersedes_entropy_on_the_same_line(settings, tmp_path):
    runner = FakeRunner([check("CKV_SECRET_6", "Base64 High Entropy String", "main.tf", 1)])
    findings = inspect(settings, tmp_path, {"main.tf": f'token = "{GITHUB}"\n'}, runner)
    assert [(f.rule, f.confirmed) for f in findings] == [("GITHUB_TOKEN", True)]


def test_checkov_runs_isolated_and_scans_every_file_type(settings, tmp_path):
    runner = FakeRunner()
    root, names = module(tmp_path, {"main.tf": "x", ".checkov.yml": "skip-check: [CKV_SECRET_2]"})
    SecretInspector(settings, runner).inspect(root, names)
    args, kwargs = runner.calls[0]
    assert args[args.index("--framework") + 1] == "secrets"
    assert "--enable-secret-scan-all-files" in args and "--skip-download" in args
    assert kwargs["cwd"] == root.parent and kwargs["timeout"] == 60
    assert set(kwargs["env"]) >= {"HOME", "PATH"} and "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
    # The repository's own Checkov config is restored and our temporary config removed.
    assert (root / ".checkov.yml").exists()
    assert not (root.parent / "checkov-secrets-config.yml").exists()


@pytest.mark.parametrize(
    "runner",
    [
        FakeRunner(error=FileNotFoundError("checkov")),
        FakeRunner(error=TimeoutError()),
        FakeRunner(returncode=2),
        FakeRunner(stdout="Traceback: secret_key=" + AWS),
        FakeRunner(stdout="[1, 2"),
    ],
)
def test_scan_that_cannot_run_blocks_the_upload_without_leaking(settings, tmp_path, runner, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SecretScanUnavailableError) as caught:
        inspect(settings, tmp_path, {"main.tf": "x"}, runner)
    assert AWS not in str(caught.value) and AWS not in caplog.text
    assert caught.value.__cause__ is None and "Traceback" not in caplog.text


def test_values_never_reach_logs_errors_or_findings(settings, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    text = "\n".join(f'v{i} = "{value}"' for i, value in enumerate(ALL_VALUES)) + "\n"
    runner = FakeRunner([check("CKV_SECRET_19", "Hex High Entropy String", "main.tf", 5)])
    findings = inspect(settings, tmp_path, {"main.tf": text}, runner)
    assert len(findings) == 5 and sum(f.confirmed for f in findings) == 4
    with pytest.raises(UploadRejectedError) as caught:
        refuse_confirmed(findings)
    haystack = "\n".join(
        [
            caplog.text,
            str(caught.value),
            json.dumps(caught.value.details, ensure_ascii=False),
            repr(findings),
        ]
    )
    for value in ALL_VALUES:
        assert value not in haystack and value[:8] not in haystack
    assert "count=4" in caplog.text


def test_malformed_checkov_entries_are_skipped(settings, tmp_path):
    runner = FakeRunner(
        [
            {"check_id": "CKV_SECRET_2"},
            check("CKV_SECRET_2", "AWS Access Key", "../../etc/passwd", 1),
            check("CKV_SECRET_2", "AWS Access Key", "main.tf", 99),
        ]
    )
    findings = inspect(settings, tmp_path, {"main.tf": "x\n"}, runner)
    # Locations are still reported, but nothing outside the module is read for a preview.
    assert [(f.file, f.preview) for f in findings] == [
        ("../../etc/passwd", None),
        ("main.tf", None),
    ]


@pytest.mark.checkov
@pytest.mark.skipif(not Path(BINARY).is_file(), reason="Checkov is not installed")
def test_real_checkov_secrets(settings, tmp_path):
    settings = settings.model_copy(update={"checkov_binary": str(Path(BINARY).resolve())})
    files = {
        "main.tf": f'provider "aws" {{\n  access_key = "{AWS}"\n}}\n'
        f'locals {{\n  digest = "{ENTROPY}"\n}}\n',
        "docs/key.md": f"{PRIVATE_KEY}\nMIIEpAIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----\n",
        "clean.tf": 'variable "name" {}\n',
    }
    findings = inspect(settings, tmp_path, files, real=True)
    located = {(f.file, f.confirmed) for f in findings}
    assert ("main.tf", True) in located and ("docs/key.md", True) in located
    assert not any(f.file == "clean.tf" for f in findings)
    for value in (AWS, ENTROPY):
        assert value not in repr(findings)
