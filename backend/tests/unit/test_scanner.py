import json
from unittest.mock import Mock

import pytest

from app.collectors.github import GitHubCollector
from app.core.exceptions import (
    CheckovExecutionError,
    CheckovNotInstalledError,
    InvalidCheckovResultError,
    RepositoryCloneError,
    ScanTimeoutError,
    UnsupportedSourceError,
)
from app.scanners.checkov import CheckovScanner
from app.utils.subprocess import ProcessResult


@pytest.mark.parametrize("code", [0, 1])
def test_scanner_flags_and_json(settings, report, tmp_path, code):
    runner = Mock()
    runner.run.return_value = ProcessResult(code, json.dumps(report))
    scanner = CheckovScanner(settings, runner)
    assert scanner.scan(tmp_path / "module").raw_data == report
    args, kwargs = runner.run.call_args
    assert args[0][args[0].index("--download-external-modules") + 1] == "false"
    assert args[0][args[0].index("--evaluate-variables") + 1] == "false"
    assert "--config-file" in args[0] and "--skip-download" in args[0]
    assert kwargs["timeout"] == settings.checkov_timeout_seconds
    assert "DATABASE_URL" not in kwargs["env"]


@pytest.mark.parametrize(
    "code,output,error",
    [
        (2, "{}", CheckovExecutionError),
        (0, "broken", InvalidCheckovResultError),
        (0, "", InvalidCheckovResultError),
        (0, "null", InvalidCheckovResultError),
    ],
)
def test_scanner_failure(settings, tmp_path, code, output, error):
    runner = Mock()
    runner.run.return_value = ProcessResult(code, output)
    with pytest.raises(error):
        CheckovScanner(settings, runner).scan(tmp_path / "module")


@pytest.mark.parametrize(
    "error,expected",
    [
        (FileNotFoundError(), CheckovNotInstalledError),
        (ScanTimeoutError("timeout"), ScanTimeoutError),
    ],
)
def test_scanner_exception(settings, tmp_path, error, expected):
    runner = Mock()
    runner.run.side_effect = error
    with pytest.raises(expected):
        CheckovScanner(settings, runner).scan(tmp_path / "module")


def test_git_arguments(settings, tmp_path):
    runner = Mock()
    runner.run.side_effect = [
        ProcessResult(0, ""),
        ProcessResult(0, "a" * 40),
        ProcessResult(0, "main"),
    ]
    collected = GitHubCollector(settings, runner).collect(
        "https://github.com/Org/Repo.git", tmp_path / "module"
    )
    assert collected.commit_hash == "a" * 40
    args, kwargs = runner.run.call_args_list[0]
    assert "--depth=1" in args[0] and "http.followRedirects=false" in args[0]
    assert args[0][-2] == "https://github.com/org/repo"
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["repository_limit"] == 200 * 1024**2


def test_git_arguments_for_a_pinned_tag_or_commit(settings, tmp_path):
    for ref, wanted in (("v5.21.0", "refs/tags/v5.21.0"), ("b" * 40, "b" * 40)):
        runner = Mock()
        runner.run.side_effect = [ProcessResult(0, "")] * 3 + [ProcessResult(0, "a" * 40)]
        target = tmp_path / ref
        collected = GitHubCollector(settings, runner).collect(
            "https://github.com/Org/Repo", target, ref
        )
        assert (collected.commit_hash, collected.branch) == ("a" * 40, ref)
        commands = [call.args[0] for call in runner.run.call_args_list]
        assert commands[0][-4:] == ["init", "--quiet", "--", str(target)]
        fetch = commands[1]
        assert fetch[-6:] == [
            "fetch",
            "--depth=1",
            "--no-tags",
            "--",
            "https://github.com/org/repo",
            wanted,
        ]
        assert commands[2][-4:] == ["checkout", "--quiet", "--detach", "FETCH_HEAD"]
        assert all("protocol.allow=never" in command for command in commands)
        assert not any("symbolic-ref" in command for command in commands)


@pytest.mark.parametrize(
    "ref",
    ["main", "--upload-pack=x", "v1", "v1.2.3;x", "refs/tags/v1.2.3", "a" * 39, "", "-v1.2.3"],
)
def test_only_release_tags_and_full_commits_can_be_pinned(settings, tmp_path, ref):
    runner = Mock()
    with pytest.raises(UnsupportedSourceError):
        GitHubCollector(settings, runner).collect(
            "https://github.com/org/repo", tmp_path / "m", ref
        )
    runner.run.assert_not_called()


def test_git_stderr_not_exposed(settings, tmp_path):
    runner = Mock()
    runner.run.return_value = ProcessResult(1, "secret")
    with pytest.raises(RepositoryCloneError, match="Unable to clone") as error:
        GitHubCollector(settings, runner).collect(
            "https://github.com/org/repo", tmp_path / "module"
        )
    assert "secret" not in str(error.value)


def test_repository_config_restored_after_timeout(settings, tmp_path):
    target = tmp_path / "module"
    target.mkdir()
    config = target / ".checkov.yaml"
    config.write_text("skip-check: [CKV_AWS_24]\n")
    runner = Mock()

    def timed_out(*args, **kwargs):
        assert not config.exists()
        raise ScanTimeoutError("timeout")

    runner.run.side_effect = timed_out
    with pytest.raises(ScanTimeoutError):
        CheckovScanner(settings, runner).scan(target)
    assert config.read_text() == "skip-check: [CKV_AWS_24]\n"
    assert not list(tmp_path.glob("ignored-config-*"))
