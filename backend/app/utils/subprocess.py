import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import ProcessOutputLimitError, ScanTimeoutError
from app.utils.filesystem import check_repository_size


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str


class ProcessRunner:
    """Bound runtime, captured output, and clone disk use; never invoke a shell."""

    def run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout: int,
        env: dict[str, str],
        output_limit: int,
        monitored_path: Path | None = None,
        repository_limit: int | None = None,
    ) -> ProcessResult:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            started = time.monotonic()
            try:
                while True:
                    finished = process.poll() is not None
                    if (
                        os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                        > output_limit
                    ):
                        raise ProcessOutputLimitError(
                            "Process output exceeds the configured limit."
                        )
                    if monitored_path is not None and repository_limit is not None:
                        check_repository_size(monitored_path, repository_limit)
                    if finished:
                        break
                    if time.monotonic() - started > timeout:
                        raise ScanTimeoutError("Subprocess exceeded its execution timeout.")
                    time.sleep(0.1)
                stdout.seek(0)
                return ProcessResult(
                    process.returncode, stdout.read().decode("utf-8", errors="replace")
                )
            finally:
                # Kill the whole process group, including descendants left by a finished parent.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()


def isolated_environment(home: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "PYTHONUNBUFFERED": "1",
    }
