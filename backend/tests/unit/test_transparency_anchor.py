"""Anchor adapters with only the HTTP transport replaced: storage, receipts and state changes
are the real code. No calendar or TSA is ever contacted."""

import base64

import httpx
import pytest

from app.transparency import anchors, merkle
from app.transparency import log as tlog
from app.transparency.log import TransparencyLog, check_log
from tests.attestation_helpers import crypto_material

CALENDARS = "https://alice.calendar.test,https://bob.calendar.test/"


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    return crypto_material(tmp_path_factory)


@pytest.fixture
def log(tmp_path):
    log = TransparencyLog(tmp_path / "transparency")
    with log.writing() as writer:
        for i in range(3):
            writer.add(
                "SCAN",
                {
                    "scan_id": f"00000000-0000-0000-0000-{i + 1:012d}",
                    "module_version_id": None,
                    "status": "COMPLETED",
                    "result_sha256": f"{i:064x}",
                    "manifest_sha256": None,
                    "snapshot_sha256": None,
                },
            )
        writer.checkpoint()
    return log


class Requests(list):
    box: dict


@pytest.fixture
def network(monkeypatch):
    """Replace only the transport. `handler` is set per test; every request is recorded."""
    seen, original = Requests(), httpx.Client
    box = {"handler": lambda request: httpx.Response(200, content=b"\x01calendar-answer")}

    def respond(request):
        seen.append(request)
        return box["handler"](request)

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    monkeypatch.setenv("MODULEGUARD_OTS_CALENDARS", CALENDARS)
    seen.box = box
    return seen


def run(log, kind="opentimestamps"):
    with log.writing() as writer:
        return anchors.anchor_pending(writer, kind)


def test_none_is_the_default_and_makes_no_external_call(log, network, monkeypatch):
    monkeypatch.delenv("MODULEGUARD_TRANSPARENCY_ANCHOR", raising=False)
    assert anchors.configured_type() == "none"
    assert run(log, "none") == []
    assert network == [] and log.read().anchors == {}


def test_unknown_anchor_type_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("MODULEGUARD_TRANSPARENCY_ANCHOR", "ethereum")
    with pytest.raises(anchors.AnchorConfigError):
        anchors.configured_type()


def test_opentimestamps_sends_only_the_root_and_stores_the_raw_answers(log, network):
    state = log.read()
    root = bytes.fromhex(state.tree_head["root_hash"])
    assert run(log) == [
        {"tree_size": 3, "type": "opentimestamps", "status": "submitted", "code": None}
    ]
    assert [r.url.host for r in network] == ["alice.calendar.test", "bob.calendar.test"]
    for request in network:
        assert request.method == "POST" and request.url.path == "/digest"
        assert request.content == root and len(request.content) == 32
        assert request.url.query == b"" and "authorization" not in request.headers
        assert "cookie" not in request.headers
    record = log.read().anchors[(3, "opentimestamps")]
    assert record["status"] == "submitted" and record["root_hash"] == state.tree_head["root_hash"]
    assert [base64.b64decode(r) for r in record["receipts_b64"]] == [b"\x01calendar-answer"] * 2
    assert record["receipt_sha256"] == tlog.receipt_sha256(record["receipts_b64"])
    assert check_log(log.read()) == []
    assert anchors.verify_anchors(log.read(), None) == ([], ["OTS_NOT_VERIFIED"])
    network.clear()
    assert run(log) == [] and network == []  # Submitted is final: nothing is sent again.


def test_one_calendar_failing_still_submits_and_none_is_substituted(log, network):
    network.box["handler"] = lambda r: (
        httpx.Response(200, content=b"ok")
        if r.url.host == "alice.calendar.test"
        else httpx.Response(500, text="SECRET_CALENDAR_ERROR")
    )
    run(log)
    assert [r.url.host for r in network] == ["alice.calendar.test", "bob.calendar.test"]
    assert len(log.read().anchors[(3, "opentimestamps")]["receipts_b64"]) == 1


@pytest.mark.parametrize(
    "handler,code",
    [
        (lambda r: httpx.Response(500, text="SECRET_CALENDAR_ERROR"), "ANCHOR_UNAVAILABLE"),
        (
            lambda r: httpx.Response(302, headers={"Location": "https://evil.test/"}),
            "ANCHOR_UNAVAILABLE",
        ),
        (
            lambda r: httpx.Response(200, content=b"x" * (anchors.MAX_RESPONSE + 1)),
            "ANCHOR_UNAVAILABLE",
        ),
        (lambda r: httpx.Response(200, content=b""), "ANCHOR_UNAVAILABLE"),
    ],
)
def test_failures_are_stored_as_failed_with_a_fixed_code(log, network, handler, code):
    network.box["handler"] = handler
    [result] = run(log)
    assert result["status"] == "failed" and result["code"] == code
    stored = (log.root / "anchors" / f"{3:020d}-opentimestamps.json").read_text()
    assert "SECRET_CALENDAR_ERROR" not in stored and "evil.test" not in stored
    assert [r.url.host for r in network] == ["alice.calendar.test", "bob.calendar.test"]


def test_a_transport_error_is_a_failure_without_its_text(log, network):
    def broken(request):
        raise httpx.ConnectError("SECRET_NETWORK_DETAIL")

    network.box["handler"] = broken
    [result] = run(log)
    assert result["status"] == "failed"
    assert "SECRET_NETWORK_DETAIL" not in str(log.read().anchors)


def test_a_failed_anchor_is_retried_by_the_next_run_then_left_alone(log, network):
    network.box["handler"] = lambda r: httpx.Response(503)
    assert run(log)[0]["status"] == "failed"
    network.box["handler"] = lambda r: httpx.Response(200, content=b"answer")
    network.clear()
    assert run(log)[0]["status"] == "submitted"
    assert len(network) == 2
    network.clear()
    assert run(log) == [] and network == []
    assert log.read().anchors[(3, "opentimestamps")]["status"] == "submitted"


def test_older_failures_are_retried_and_the_new_head_is_anchored(log, network):
    network.box["handler"] = lambda r: httpx.Response(503)
    run(log)
    with log.writing() as writer:
        writer.add(
            "SCAN",
            {
                "scan_id": "00000000-0000-0000-0000-000000000009",
                "module_version_id": None,
                "status": "FAILED",
                "result_sha256": "9" * 64,
                "manifest_sha256": None,
                "snapshot_sha256": None,
            },
        )
        writer.checkpoint()
    network.box["handler"] = lambda r: httpx.Response(200, content=b"answer")
    assert sorted(r["tree_size"] for r in run(log)) == [3, 4]
    assert {k: v["status"] for k, v in log.read().anchors.items()} == {
        (3, "opentimestamps"): "submitted",
        (4, "opentimestamps"): "submitted",
    }


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://calendar.test",
        "https://user:pass@calendar.test",
        "https://calendar.test/?key=secret",
        "https://calendar.test/#fragment",
        "calendar.test",
        "https:///path",
        "https://calendar.test:99999",
        ",".join(f"https://c{i}.test" for i in range(9)),
    ],
)
def test_calendar_urls_are_https_without_credentials_and_bounded(log, network, monkeypatch, value):
    monkeypatch.setenv("MODULEGUARD_OTS_CALENDARS", value)
    with pytest.raises(anchors.AnchorConfigError):
        run(log)
    assert network == [] and log.read().anchors == {}


# ------------------------------------------------------------------ RFC 3161


@pytest.fixture
def tsa(material, network, monkeypatch):
    _, timestamp, policy = material
    monkeypatch.setattr(anchors, "load_policy", lambda: policy)

    def answer(request):
        assert str(request.url) == policy.tsa_url
        assert request.headers["content-type"] == "application/timestamp-query"
        return httpx.Response(
            200, content=base64.b64decode(timestamp(b"unused", request_bytes=request.content))
        )

    network.box["handler"] = answer
    return policy


def test_rfc3161_timestamps_the_root_and_verifies_against_policy_roots(log, network, tsa):
    assert run(log, "rfc3161")[0]["status"] == "submitted"
    state = log.read()
    record = state.anchors[(3, "rfc3161")]
    assert len(network) == 1 and len(record["receipts_b64"]) == 1
    assert check_log(state) == []
    assert anchors.verify_anchors(state, tsa) == ([], [])
    assert anchors.verify_anchors(state, None) == ([], ["ANCHOR_UNVERIFIED"])
    other = tsa.model_copy(update={"tsa_roots_pem": []})
    assert anchors.verify_anchors(state, other)[0] == ["ANCHOR_MISMATCH"]


def test_a_receipt_for_another_root_is_a_mismatch(log, network, tsa):
    run(log, "rfc3161")
    state = log.read()
    state.anchors[(3, "rfc3161")]["root_hash"] = merkle.root([merkle.leaf_hash(b"x")]).hex()
    assert anchors.verify_anchors(state, tsa)[0] == ["ANCHOR_MISMATCH"]


def test_rfc3161_needs_a_policy_and_rejects_an_untrusted_answer(log, network, tsa, monkeypatch):
    other = tsa.model_copy(update={"tsa_roots_pem": []})
    monkeypatch.setattr(anchors, "load_policy", lambda: other)
    [result] = run(log, "rfc3161")
    assert result["status"] == "failed" and result["code"] == "ANCHOR_RESPONSE_INVALID"
    monkeypatch.setattr(anchors, "load_policy", lambda: None)
    with pytest.raises(anchors.AnchorConfigError):
        run(log, "rfc3161")


def test_rfc3161_endpoint_must_be_https(log, network, tsa, monkeypatch):
    monkeypatch.setattr(
        anchors, "load_policy", lambda: tsa.model_copy(update={"tsa_url": "http://tsa.test"})
    )
    with pytest.raises(anchors.AnchorConfigError):
        run(log, "rfc3161")
    assert network == []
