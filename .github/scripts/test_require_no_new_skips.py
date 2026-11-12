"""Run: python .github/scripts/test_require_no_new_skips.py"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

here = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("guard", here / "require-no-new-skips.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def report(*pairs):
    # nested on purpose: Playwright nests describe blocks as child suites
    specs = [{"title": t, "tests": [{"status": s}]} for t, s in pairs]
    return {"suites": [{"specs": [], "suites": [{"specs": specs}]}]}


allowed = ["terraform is not installed"]
assert guard.check(report(("a", "expected"), ("x terraform is not installed", "skipped")), allowed) == []
assert "unexpected skip" in guard.check(report(("a", "expected"), ("b", "skipped")), allowed)[0]
assert "only 1 passed" in guard.check(report(("a", "expected")), allowed, 2)[0]
assert "no tests at all" in guard.check({"suites": []}, allowed)[0]  # nothing ran is never clean
assert guard.check(report(("a", "expected")), ["a title nobody has"]) == []  # a stale allow-list line is harmless

# A report written by a real Playwright run (trimmed): one pass, one allowed skip, one surprise skip,
# one failure. The failure is Playwright's job to report; the surprise skip is ours.
real = json.loads((here / "fixtures/playwright-real-mixed.json").read_text(encoding="utf-8"))
problems = guard.check(real, ["expected skip (terraform is not installed)"], 1)
assert len(problems) == 1 and "surprise skip" in problems[0], problems
assert guard.check(real, ["expected skip", "surprise skip"], 1) == []


def cli(report_path, allow_text, *extra):
    with tempfile.TemporaryDirectory() as tmp:
        allow = pathlib.Path(tmp, "allow.txt")
        allow.write_text(allow_text, encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(here / "require-no-new-skips.py"), str(report_path), str(allow), *extra],
            capture_output=True,
            text=True,
        )
    return done.returncode, done.stdout + done.stderr


fixture = here / "fixtures/playwright-real-mixed.json"
code, out = cli(fixture, "expected skip\n", "1")
assert code == 1 and "surprise skip" in out, out
code, out = cli(fixture, "expected skip\nsurprise skip\n", "1")
assert code == 0, out
code, out = cli(here / "fixtures/does-not-exist.json", "")  # Playwright died before writing it
assert code == 1 and "cannot read" in out and "Traceback" not in out, out
with tempfile.TemporaryDirectory() as tmp:
    broken = pathlib.Path(tmp, "broken.json")
    broken.write_text("{not json", encoding="utf-8")
    code, out = cli(broken, "")
    assert code == 1 and "cannot read" in out and "Traceback" not in out, out
print("ok")
