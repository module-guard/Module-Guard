"""Fail when a Playwright JSON report has a skipped test that is not on the allow-list.

live-smoke runs opt-in specs that skip themselves when RUN_LIVE_SCAN is off or a tool is
missing. A skip nobody expected would look like a pass; this makes it a failure.

    python .github/scripts/require-no-new-skips.py <report.json> <allowed-skips.txt> [minimum passed]
"""

import json
import sys


def tests(suite):
    for spec in suite.get("specs", []):
        for test in spec["tests"]:
            yield spec["title"], test["status"]
    for child in suite.get("suites", []):
        yield from tests(child)


def check(report, allowed, minimum=0):
    """Return a list of problems; empty means the run is clean."""
    results = [t for suite in report["suites"] for t in tests(suite)]
    skipped = [title for title, status in results if status == "skipped"]
    passed = [title for title, status in results if status == "expected"]
    problems = []
    if not results:
        problems.append("the report has no tests at all")
    unexpected = [t for t in skipped if not any(a in t for a in allowed)]
    if unexpected:
        problems.append(f"{len(unexpected)} unexpected skip(s): " + "; ".join(unexpected))
    if len(passed) < minimum:
        problems.append(f"only {len(passed)} passed, expected at least {minimum}")
    return problems


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print("usage: require-no-new-skips.py <report.json> <allowed.txt> [minimum]", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            report = json.load(f)
        with open(sys.argv[2], encoding="utf-8") as f:
            allowed = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except (OSError, ValueError) as error:
        # Playwright died before writing the report: say so instead of a traceback.
        print(f"::error::cannot read the report or allow-list: {error}")
        return 1
    problems = check(report, allowed, int(sys.argv[3]) if len(sys.argv) == 4 else 0)
    if problems:
        print("::error::" + "; ".join(problems))
        return 1
    print("No skips outside the allow-list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
