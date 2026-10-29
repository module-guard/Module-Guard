"""Fail when a pytest JUnit report shows that marked tests were skipped instead of run.

The PostgreSQL and Checkov tests skip themselves when their dependency is missing
(TEST_DATABASE_URL, the Checkov binary). That is right on a laptop and wrong in CI,
where a silent skip would look like a pass. CI calls this after those runs.

    python .github/scripts/require-tests-ran.py <junit.xml> <minimum passed>
"""

import sys
import xml.etree.ElementTree as ET


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: require-tests-ran.py <junit.xml> <minimum passed>", file=sys.stderr)
        return 2
    path, minimum = sys.argv[1], int(sys.argv[2])
    passed, skipped, failed = [], [], []
    for case in ET.parse(path).getroot().iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        if case.find("skipped") is not None:
            skipped.append(name)
        elif case.find("failure") is not None or case.find("error") is not None:
            failed.append(name)
        else:
            passed.append(name)
    for name in passed:
        print(f"ran: {name}")
    problems = []
    if skipped:
        problems.append(f"{len(skipped)} skipped: " + ", ".join(skipped))
    if failed:
        problems.append(f"{len(failed)} failed: " + ", ".join(failed))
    if len(passed) < minimum:
        problems.append(f"only {len(passed)} passed, expected at least {minimum}")
    if problems:
        print("::error::" + "; ".join(problems))
        return 1
    print(f"{len(passed)} tests ran and passed (minimum {minimum}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
