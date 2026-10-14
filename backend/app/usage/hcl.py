"""Turns form values into a Terraform `module` block.

Values are data, never code: every string a caller sends ends up inside a quoted HCL string
with quotes, backslashes, control characters and the template openers `${` / `%{` escaped.
Lists and maps arrive as JSON and are written back out as HCL literals, so there is no path
by which caller text lands outside quotes. Identifiers (module label, variable names) come
only from what the server stored about the module.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_VALUE_CHARS = 64 * 1024
MAX_DEPTH = 32
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$")
# A type we are willing to copy into the generated variables.tf: names, parentheses, commas.
SIMPLE_TYPE = re.compile(r"^[a-z]+(\([a-z(), ]*\))?$")
ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class UsageValueError(ValueError):
    """A value does not fit its variable. Carries the variable name, never the value."""

    def __init__(self, variable: str, reason: str):
        super().__init__(reason)
        self.variable = variable
        self.reason = reason


def quote(text: str) -> str:
    """An HCL quoted string that evaluates to exactly `text`."""
    out = []
    for index, char in enumerate(text):
        if char in ESCAPES:
            out.append(ESCAPES[char])
        elif char in "$%" and text[index + 1 : index + 2] == "{":
            out.append(char * 2)  # "${" -> "$${", "%{" -> "%%{": no interpolation, no directive.
        elif ord(char) < 0x20 or ord(char) == 0x7F or char in "  ":
            out.append(f"\\u{ord(char):04x}")
        elif 0xD800 <= ord(char) <= 0xDFFF:
            raise ValueError("Lone surrogate.")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _literal(value: Any, depth: int = 0) -> str:
    if depth > MAX_DEPTH:
        raise ValueError("너무 깊게 중첩되어 있습니다.")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if not NUMBER.match(text):
            raise ValueError("숫자가 아닙니다.")
        return text
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item, depth + 1) for item in value) + "]"
    if isinstance(value, dict):
        pairs = (f"{quote(key)} = {_literal(item, depth + 1)}" for key, item in value.items())
        return "{" + ", ".join(pairs) + "}"
    raise ValueError("지원하지 않는 값입니다.")


def _refuse_constant(_name: str):
    raise ValueError("NaN과 Infinity는 쓸 수 없습니다.")


def kind_of(type_text: str | None) -> str:
    """Which input a variable gets: string, number, bool or json."""
    head = (type_text or "").strip()
    return head if head in ("string", "number", "bool") else "json"


def render_value(variable: dict[str, Any], raw: Any) -> str:
    name = variable["name"]
    kind = kind_of(variable.get("type"))
    try:
        if kind == "bool":
            if isinstance(raw, bool):
                return _literal(raw)
            raise ValueError("true 또는 false여야 합니다.")
        if not isinstance(raw, str):
            raise ValueError("문자열로 보내야 합니다.")
        if len(raw) > MAX_VALUE_CHARS:
            raise ValueError("값이 너무 깁니다.")
        if kind == "string":
            return quote(raw)
        if kind == "number":
            if not NUMBER.match(raw.strip()):
                raise ValueError("숫자가 아닙니다.")
            return raw.strip()
        try:
            parsed = json.loads(raw, parse_constant=_refuse_constant)
        except RecursionError:
            raise ValueError("너무 깊게 중첩되어 있습니다.") from None
        except json.JSONDecodeError:
            raise ValueError("JSON 형식이 아닙니다.") from None
        return _literal(parsed)
    except RecursionError:
        raise UsageValueError(name, "너무 깊게 중첩되어 있습니다.") from None
    except ValueError as error:
        raise UsageValueError(name, str(error)) from None


def module_label(name: str) -> str:
    """Module label and directory name, from the stored repository name only."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name) or "module"
    return cleaned if re.match(r"[A-Za-z_]", cleaned) else f"m_{cleaned}"


@dataclass
class Usage:
    main_tf: str
    variables_tf: str | None
    missing_required: list[str] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)


def _is_blank(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def render_usage(
    name: str,
    variables: list[dict[str, Any]],
    values: dict[str, Any],
    pass_sensitive: list[str] | None = None,
) -> Usage:
    """main.tf (and variables.tf when sensitive inputs are passed through) for one module.

    Sensitive variables never take a value here: they become `name = var.name`, declared in
    variables.tf with sensitive = true, to be supplied as TF_VAR_name.
    """
    known = {v["name"]: v for v in variables if IDENTIFIER.match(str(v.get("name", "")))}
    for sent in values:
        if sent not in known:
            raise UsageValueError("", "이 모듈에 없는 변수가 있습니다.")
        if known[sent].get("sensitive"):
            raise UsageValueError(sent, "민감한 변수는 값을 받지 않습니다. TF_VAR로 넣어 주세요.")
    wanted = set(pass_sensitive or [])
    if not wanted <= {n for n, v in known.items() if v.get("sensitive")}:
        raise UsageValueError("", "이 모듈에 없는 변수가 있습니다.")

    label = module_label(name)
    assignments: list[tuple[str, str]] = []
    missing, sensitive = [], []
    for variable_name in sorted(known, key=lambda n: (not known[n].get("required"), n)):
        variable = known[variable_name]
        if variable.get("sensitive"):
            if variable.get("required") or variable_name in wanted:
                assignments.append((variable_name, f"var.{variable_name}"))
                sensitive.append(variable_name)
            continue
        raw = values.get(variable_name)
        if _is_blank(raw):
            if variable.get("required"):
                missing.append(variable_name)
            continue
        assignments.append((variable_name, render_value(variable, raw)))

    width = max([len("source"), *(len(n) for n, _ in assignments)])
    lines = [f'module "{label}" {{', f'  {"source".ljust(width)} = "./modules/{label}"']
    if assignments or missing:
        lines.append("")
    lines += [f"  {n.ljust(width)} = {text}" for n, text in assignments]
    lines += [f"  # {n} = ...  (필수: 값을 넣어 주세요)" for n in missing]
    lines.append("}")

    declarations = None
    if sensitive:
        blocks = []
        for variable_name in sensitive:
            type_text = (known[variable_name].get("type") or "").strip()
            body = [f"  type      = {type_text}"] if SIMPLE_TYPE.match(type_text) else []
            body.append("  sensitive = true")
            blocks.append(f'variable "{variable_name}" {{\n' + "\n".join(body) + "\n}\n")
        header = "# 값은 코드에 쓰지 말고 환경 변수 TF_VAR_<이름> 으로 넣습니다.\n\n"
        declarations = header + "\n".join(blocks)
    return Usage("\n".join(lines) + "\n", declarations, missing, sensitive)
