import pytest

from app.scanners.scope import finding_scope


@pytest.mark.parametrize(
    "path",
    [
        "/examples/complete/main.tf",
        "examples/main.tf",
        "/example/main.tf",
        "/test/main.tf",
        "/tests/fixtures/x/main.tf",
        "/Examples/main.tf",
    ],
)
def test_example_and_test_folders(path):
    assert finding_scope(path, {}) == "EXAMPLE"
    # A body file instantiated from one of those folders counts as an example too.
    assert finding_scope("/main.tf", {"caller_file_path": path}) == "EXAMPLE"


@pytest.mark.parametrize(
    "path",
    [
        "/main.tf",
        "/modules/examples/main.tf",
        "/modules/net/tests.tf",
        "/examples.tf",
        "/examples-extra/main.tf",
        "/wrappers/main.tf",
        "/testing/main.tf",
        None,
        "",
    ],
)
def test_module_body(path):
    assert finding_scope(path, {}) == "MODULE"
    assert finding_scope(path, None) == "MODULE"
    assert finding_scope("/main.tf", {"caller_file_path": path}) == "MODULE"
    assert finding_scope("/main.tf", {"caller_file_path": 42}) == "MODULE"
