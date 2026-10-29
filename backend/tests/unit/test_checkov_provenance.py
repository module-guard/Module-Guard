"""Executable version queries use bounded ProcessRunner, not package metadata."""

from unittest.mock import Mock

import pytest

from app.scanners.checkov import CheckovScanner
from app.utils.subprocess import ProcessResult


def test_checkov_version_is_from_selected_binary(settings, tmp_path):
    settings.checkov_binary = "/opt/scan/bin/checkov"
    runner = Mock()
    runner.run.return_value = ProcessResult(0, "3.3.26\n")
    assert CheckovScanner(settings, runner).version(tmp_path / "module") == "3.3.26"
    args, kwargs = runner.run.call_args
    assert args[0] == ["/opt/scan/bin/checkov", "--version"]
    assert kwargs["timeout"] <= 10
    assert kwargs["output_limit"] <= 4096
    assert "DATABASE_URL" not in kwargs["env"]


@pytest.mark.parametrize("result", [ProcessResult(1, "3.3.26"), ProcessResult(0, "secret=abc")])
def test_checkov_version_failures_remain_unknown(settings, tmp_path, result):
    runner = Mock()
    runner.run.return_value = result
    assert CheckovScanner(settings, runner).version(tmp_path / "module") is None


def test_checkov_version_missing_executable_remains_unknown(settings, tmp_path):
    runner = Mock()
    runner.run.side_effect = FileNotFoundError("private path")
    assert CheckovScanner(settings, runner).version(tmp_path / "module") is None
