import json
from pathlib import Path

import hcl2
from hcl2.utils import SerializationOptions

from app.core.exceptions import UnsafeRepositoryError


def validate_local_module_paths(files: list[Path], root: Path) -> None:
    """Prevent Checkov's local-module loader from reading outside the collected repository.

    This is an input boundary check, not dependency graph or flow analysis.
    """
    root = root.resolve()
    for path in files:
        try:
            with path.open() as handle:
                document = (
                    json.load(handle)
                    if path.name.endswith(".json")
                    else hcl2.load(
                        handle,
                        serialization_options=SerializationOptions(
                            strip_string_quotes=True,
                            with_comments=False,
                            explicit_blocks=False,
                            preserve_heredocs=False,
                        ),
                    )
                )
            modules = document.get("module", [])
            if isinstance(modules, dict):
                modules = [modules]
            for block in modules:
                for module in block.values():
                    source = module.get("source", "")
                    if not isinstance(source, str) or "${" in source or "%{" in source:
                        raise UnsafeRepositoryError("Module sources must be literal strings.")
                    if source.startswith(("/", "~", "file:", "\\")):
                        raise UnsafeRepositoryError(
                            "Absolute and file module sources are not supported."
                        )
                    if source.startswith("."):
                        resolved = (path.parent / source).resolve()
                        if not resolved.is_relative_to(root):
                            raise UnsafeRepositoryError(
                                "Local module source escapes the repository."
                            )
        except UnsafeRepositoryError:
            raise
        except Exception:
            raise UnsafeRepositoryError("Terraform input could not be safely parsed.") from None
