"""Shared deterministic identity for the ZIP's generated wrapper, independent of source hash."""

from app.storage.integrity import digest, manifest_bytes
from app.usage.hcl import module_label, render_usage


def wrapper(name, variables, values=None, pass_sensitive=None):
    usage = render_usage(name, variables or [], values or {}, pass_sensitive)
    files = {"main.tf": usage.main_tf}
    if usage.variables_tf:
        files["variables.tf"] = usage.variables_tf
    key = digest(manifest_bytes({"format": 1, "label": module_label(name), "files": files}))
    return files, key
