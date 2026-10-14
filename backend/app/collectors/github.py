import re
from pathlib import Path

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.source_detector import detect_source, normalize_github_url
from app.core.config import Settings
from app.core.exceptions import RepositoryCloneError, UnsupportedSourceError
from app.models.enums import SourceType
from app.utils.subprocess import ProcessRunner, isolated_environment

RELEASE_TAG = re.compile(r"v?[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")


class GitHubCollector(SourceCollector):
    def __init__(self, settings: Settings, runner: ProcessRunner | None = None):
        self.settings = settings
        self.runner = runner or ProcessRunner()

    def supports(self, source: str) -> bool:
        try:
            normalize_github_url(source)
            return True
        except UnsupportedSourceError:
            return False

    def collect(self, source: str, destination: Path, ref: str | None = None) -> CollectedModule:
        identity = detect_source(normalize_github_url(source))
        if ref is not None and not (RELEASE_TAG.fullmatch(ref) or FULL_COMMIT.fullmatch(ref)):
            raise UnsupportedSourceError("Pin a release tag like v1.2.3 or a full commit hash.")
        if destination.exists() or destination.is_symlink():
            raise RepositoryCloneError("Clone destination must not already exist.")
        env = isolated_environment(destination.parent)
        base = [
            "git",
            "-c",
            "http.followRedirects=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.https.allow=always",
        ]

        def run(arguments: list[str]) -> str:
            try:
                result = self.runner.run(
                    base + arguments,
                    cwd=destination.parent,
                    timeout=self.settings.git_timeout_seconds,
                    env=env,
                    output_limit=self.settings.max_process_output_mb * 1024**2,
                    monitored_path=destination,
                    repository_limit=self.settings.max_repository_size_mb * 1024**2,
                )
            except FileNotFoundError:
                raise RepositoryCloneError("Git binary is not installed.") from None
            if result.returncode != 0:
                # Never expose stderr: remote output can contain credentials or malicious text.
                raise RepositoryCloneError(
                    "Unable to clone or inspect the public GitHub repository."
                )
            return result.stdout.strip()

        if ref is None:
            run(
                [
                    "clone",
                    "--depth=1",
                    "--single-branch",
                    "--no-tags",
                    "--",
                    identity.source_url,
                    str(destination),
                ]
            )
            branch = None
        else:
            # Exactly one ref, by its full name: a tag cannot be mistaken for a branch.
            wanted = ref if FULL_COMMIT.fullmatch(ref) else f"refs/tags/{ref}"
            run(["init", "--quiet", "--", str(destination)])
            run(
                [
                    "-C",
                    str(destination),
                    "fetch",
                    "--depth=1",
                    "--no-tags",
                    "--",
                    identity.source_url,
                    wanted,
                ]
            )
            run(["-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD"])
            branch = ref
        commit = run(["-C", str(destination), "rev-parse", "HEAD"])
        if branch is None:
            branch = run(["-C", str(destination), "symbolic-ref", "--short", "HEAD"])
        return CollectedModule(
            SourceType.GITHUB,
            identity.source_url,
            destination,
            identity.repository_name,
            identity.owner,
            branch,
            commit,
        )
