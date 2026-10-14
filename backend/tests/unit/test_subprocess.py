import sys

import pytest

from app.core.exceptions import ProcessOutputLimitError, RepositoryTooLargeError, ScanTimeoutError
from app.utils.subprocess import ProcessRunner, isolated_environment


def test_process_argument_is_not_a_shell(tmp_path):
    runner = ProcessRunner()
    value = "$(touch injected); echo bad"
    result = runner.run(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", value],
        cwd=tmp_path,
        timeout=5,
        env=isolated_environment(tmp_path),
        output_limit=10000,
    )
    assert result.stdout.strip() == value
    assert not (tmp_path / "injected").exists()


def test_process_timeout(tmp_path):
    with pytest.raises(ScanTimeoutError):
        ProcessRunner().run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout=1,
            env=isolated_environment(tmp_path),
            output_limit=10000,
        )


def test_process_output_limit(tmp_path):
    with pytest.raises(ProcessOutputLimitError):
        ProcessRunner().run(
            [sys.executable, "-c", "print('x' * 10000)"],
            cwd=tmp_path,
            timeout=5,
            env=isolated_environment(tmp_path),
            output_limit=100,
        )


def test_process_repository_limit(tmp_path):
    with pytest.raises(RepositoryTooLargeError):
        ProcessRunner().run(
            [sys.executable, "-c", "open('large', 'w').write('x' * 10000)"],
            cwd=tmp_path,
            timeout=5,
            env=isolated_environment(tmp_path),
            output_limit=1000,
            monitored_path=tmp_path,
            repository_limit=100,
        )
