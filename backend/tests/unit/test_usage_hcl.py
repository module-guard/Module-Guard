import json
import random
import shutil
import subprocess

import pytest

from app.usage.hcl import UsageValueError, module_label, quote, render_usage, render_value

STRING = {"name": "name", "type": "string", "required": True}
HOSTILE = [
    'plain "quoted" text',
    "back\\slash and trailing \\",
    "line one\nline two\r\n\ttabbed",
    "${var.secret}",
    "%{ if true }yes%{ endif }",
    "$${already} %%{escaped}",
    "$$${triple} $ { spaced } $",
    '"\n}\nresource "null_resource" "pwned" {\n  triggers = { a = "',
    '" + file("/etc/passwd") + "',
    "<<EOT\nheredoc\nEOT",
    "nul\x00 bell\x07 esc\x1b del\x7f sep  ",
    "한글 ✓ 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 🙂",
    "# comment // also /* block */",
    "",
]


def unquote(text: str) -> str:
    """Reads a string back the way HCL does (the escapes quote() may emit)."""
    assert text.startswith('"') and text.endswith('"')
    body, out, index = text[1:-1], [], 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            code = body[index + 1]
            if code == "u":
                out.append(chr(int(body[index + 2 : index + 6], 16)))
                index += 6
                continue
            out.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}[code])
            index += 2
        elif char in "$%" and body[index + 1 : index + 3] == char + "{":
            out.append(char + "{")
            index += 3
        else:
            assert char != '"' and char not in "\n\r", "raw quote or newline escaped the string"
            assert not (char in "$%" and body[index + 1 : index + 2] == "{"), "live template"
            out.append(char)
            index += 1
    return "".join(out)


@pytest.mark.parametrize("text", HOSTILE)
def test_strings_stay_strings(text):
    quoted = quote(text)
    assert "\n" not in quoted and unquote(quoted) == text


def test_required_escapes_are_exact():
    assert quote('a"b') == '"a\\"b"'
    assert quote("a\\b") == '"a\\\\b"'
    assert quote("a\nb") == '"a\\nb"'
    assert quote("${x}") == '"$${x}"'
    assert quote("%{x}") == '"%%{x}"'
    assert quote("$ %") == '"$ %"'


def test_values_follow_the_variable_type():
    number = {"name": "n", "type": "number"}
    assert render_value(number, " 10 ") == "10" and render_value(number, "-1.5e3") == "-1.5e3"
    for bad in ("1; x", "0x10", "1 + 1", "${1}", "١٢", "1\n2", "NaN", "01"):
        with pytest.raises(UsageValueError):
            render_value(number, bad)
    flag = {"name": "b", "type": "bool"}
    assert render_value(flag, True) == "true" and render_value(flag, False) == "false"
    for bad in ("true", 1, "true || file(x)"):
        with pytest.raises(UsageValueError):
            render_value(flag, bad)
    items = {"name": "l", "type": "list(string)"}
    assert render_value(items, '["a", "${b}"]') == '["a", "$${b}"]'
    # HCL is not accepted for collections: it would be code.
    for bad in (
        "[var.x]",
        '["a", file("x")]',
        "{a = 1}",
        "[NaN]",
        "[Infinity]",
        "[" * 40 + "]" * 40,
    ):
        with pytest.raises(UsageValueError):
            render_value(items, bad)
    tags = {"name": "m", "type": "map(any)"}
    rendered = render_value(
        tags, json.dumps({'k"} evil = "${x}': [1, 2.5, None, True, {"a": "b\n"}]})
    )
    assert rendered == '{"k\\"} evil = \\"$${x}" = [1, 2.5, null, true, {"a" = "b\\n"}]}'
    with pytest.raises(UsageValueError):
        render_value(STRING, "x" * (64 * 1024 + 1))
    with pytest.raises(UsageValueError):
        render_value(tags, "[" * 100000)


def test_error_names_the_variable_but_never_the_value():
    with pytest.raises(UsageValueError) as caught:
        render_value({"name": "n", "type": "number"}, "SECRET-MARKER")
    assert caught.value.variable == "n" and "SECRET-MARKER" not in str(caught.value)


VARIABLES = [
    {"name": "cidr", "type": "string", "required": True},
    {"name": "name", "type": "string", "required": True},
    {"name": "azs", "type": "list(string)", "required": False, "default": "[]"},
    {"name": "enable_nat", "type": "bool", "required": False, "default": "false"},
    {"name": "password", "type": "string", "required": True, "sensitive": True},
    {"name": "token", "type": 'object({ a = optional(string, file("x")) })', "sensitive": True},
]


def test_module_block_layout():
    usage = render_usage(
        "vpc", VARIABLES, {"name": "my-vpc", "azs": '["a"]', "enable_nat": True, "cidr": ""}
    )
    assert usage.main_tf == (
        'module "vpc" {\n'
        '  source     = "./modules/vpc"\n'
        "\n"
        '  name       = "my-vpc"\n'
        "  password   = var.password\n"
        '  azs        = ["a"]\n'
        "  enable_nat = true\n"
        "  # cidr = ...  (필수: 값을 넣어 주세요)\n"
        "}\n"
    )
    assert usage.missing_required == ["cidr"] and usage.sensitive == ["password"]
    assert (
        'variable "password" {\n  type      = string\n  sensitive = true\n}' in usage.variables_tf
    )
    assert "TF_VAR_" in usage.variables_tf


def test_sensitive_values_are_refused_and_optional_ones_are_opt_in():
    with pytest.raises(UsageValueError) as caught:
        render_usage("vpc", VARIABLES, {"password": "hunter2"})
    assert caught.value.variable == "password"
    usage = render_usage("vpc", VARIABLES, {}, pass_sensitive=["token"])
    assert "  token    = var.token\n" in usage.main_tf
    # A type that is more than names and parentheses is not copied into generated code.
    assert 'variable "token" {\n  sensitive = true\n}' in usage.variables_tf
    assert "file(" not in usage.variables_tf
    with pytest.raises(UsageValueError):
        render_usage("vpc", VARIABLES, {}, pass_sensitive=["name"])


def test_unknown_variables_and_odd_names():
    with pytest.raises(UsageValueError):
        render_usage("vpc", VARIABLES, {'x" }\nresource "a" "b" {': "1"})
    # A stored name that is not an identifier is never written into code.
    odd = [{"name": 'a" {}\n', "type": "string", "required": True}]
    assert render_usage("vpc", odd, {}).main_tf == 'module "vpc" {\n  source = "./modules/vpc"\n}\n'
    assert module_label("1password") == "m_1password"
    assert module_label("terraform-aws-vpc") == "terraform-aws-vpc"
    assert module_label('x"/../y') == "x_____y"
    assert render_usage("vpc", [], {}).variables_tf is None


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform is not installed")
def test_terraform_reads_back_exactly_what_was_sent(tmp_path):
    """Round trip through the real parser: every value must come back character for character."""
    rng = random.Random(20261014)
    alphabet = [
        '"',
        "\\",
        "\n",
        "\r",
        "\t",
        "$",
        "%",
        "{",
        "}",
        "$${",
        "%%{",
        "${",
        "%{",
        "a",
        " ",
        "한",
        "#",
        "<<EOT\n",
        "\x01",
        " ",
        "🙂",
        "'",
        "/*",
        "=",
    ]
    samples = [s for s in HOSTILE if "\x00" not in s]
    samples += [
        "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 14))) for _ in range(300)
    ]
    nested = {'key ${a} %{b} "q"': ["x\ny", {"deep": "$${z}"}], "n": [1, -2.5, None, True]}
    lines = [f"  s{i} = {quote(s)}" for i, s in enumerate(samples)]
    lines.append(
        "  nested = " + render_value({"name": "nested", "type": "any"}, json.dumps(nested))
    )
    (tmp_path / "main.tf").write_text(
        "locals {\n" + "\n".join(lines) + "\n}\n"
        'output "all" {\n  value = jsonencode({ strings = ['
        + ", ".join(f"local.s{i}" for i in range(len(samples)))
        + "], nested = local.nested })\n}\n",
        encoding="utf-8",
    )
    run = lambda *args: subprocess.run(  # noqa: E731
        ["terraform", *args], cwd=tmp_path, capture_output=True, text=True, timeout=120
    )
    assert run("init", "-backend=false", "-input=false").returncode == 0
    applied = run("apply", "-auto-approve", "-input=false")
    assert applied.returncode == 0, applied.stderr[-2000:]
    result = json.loads(json.loads(run("output", "-json", "all").stdout))
    assert result["strings"] == samples
    assert result["nested"] == nested
