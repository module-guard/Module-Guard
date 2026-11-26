"""Anchor adapters: put a checkpoint's Merkle root where the server cannot rewrite it.

Only the 32 root bytes ever leave the process: no scan id, digest, URL or credential. Default
`none` makes no call at all. Failures are stored as `failed` with a fixed code and retried by the
next `checkpoint` run; no other calendar or TSA is ever tried instead. External error text is
discarded.

OpenTimestamps: the calendar's raw answer is stored as-is. It is a pending timestamp, not a
complete `.ots` file, and Bitcoin confirmation is outside this module. No `.ots` is produced here
(the container format was not checked against the official implementation).
"""

import base64
import os
from urllib.parse import urlsplit

import httpx

from app.attestations import signing
from app.services.attestation_service import load_policy
from app.transparency.log import ANCHOR_TYPES, _now, receipt_sha256

TYPES = ("none", *ANCHOR_TYPES)
MAX_RESPONSE = 64 * 1024
MAX_CALENDARS = 8
TIMEOUT = 10
SHA256_OID = "2.16.840.1.101.3.4.2.1"


class AnchorConfigError(Exception):
    """Misconfiguration. Nothing was sent and nothing is recorded; fix and run again."""


class AnchorFailed(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def configured_type() -> str:
    value = os.environ.get("MODULEGUARD_TRANSPARENCY_ANCHOR", "none").strip() or "none"
    if value not in TYPES:
        raise AnchorConfigError(
            "MODULEGUARD_TRANSPARENCY_ANCHOR is not none/opentimestamps/rfc3161"
        )
    return value


def https_url(url: str) -> str:
    """HTTPS to a host, with no credentials, query or fragment."""
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and url.isprintable()
            and " " not in url
        )
        _ = parts.port  # Raises on a malformed port.
    except ValueError:
        valid = False
    if not valid:
        raise AnchorConfigError("anchor endpoint must be a plain https URL")
    return url


def calendars() -> list[str]:
    urls = list(
        dict.fromkeys(
            https_url(item.strip())
            for item in os.environ.get("MODULEGUARD_OTS_CALENDARS", "").split(",")
            if item.strip()
        )
    )
    if not urls or len(urls) > MAX_CALENDARS:
        raise AnchorConfigError("MODULEGUARD_OTS_CALENDARS needs 1 to 8 approved https calendars")
    return urls


def _post(client, url, body, headers) -> bytes:
    try:
        with client.stream("POST", url, content=body, headers=headers) as response:
            if response.status_code != 200:  # Redirects are not followed, so 3xx lands here.
                raise AnchorFailed("ANCHOR_UNAVAILABLE")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise AnchorFailed("ANCHOR_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
    except (httpx.HTTPError, OSError, ValueError):
        raise AnchorFailed("ANCHOR_UNAVAILABLE") from None
    if not chunks:
        raise AnchorFailed("ANCHOR_RESPONSE_INVALID")
    return b"".join(chunks)


def _client():
    return httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False)


def _opentimestamps(root: bytes) -> list[bytes]:
    urls = calendars()
    receipts = []
    with _client() as client:
        for url in urls:
            try:
                receipts.append(
                    _post(
                        client,
                        url.rstrip("/") + "/digest",
                        root,
                        {"Accept": "application/vnd.opentimestamps.v1"},
                    )
                )
            except AnchorFailed:
                continue  # Another approved calendar may still answer; none is substituted.
    if not receipts:
        raise AnchorFailed("ANCHOR_UNAVAILABLE")
    return receipts


def _rfc3161(root: bytes) -> list[bytes]:
    from rfc3161_client import HashAlgorithm, TimestampRequestBuilder

    policy = load_policy()
    if policy is None:
        raise AnchorConfigError("MODULEGUARD_ATTESTATION_POLICY_FILE is needed for rfc3161")
    url = https_url(policy.tsa_url)
    request = TimestampRequestBuilder().data(root).hash_algorithm(HashAlgorithm.SHA256).build()
    with _client() as client:
        answer = _post(
            client,
            url,
            request.as_bytes(),
            {"Content-Type": "application/timestamp-query"},
        )
    try:
        # The imprint is SHA-256 of the root bytes; the TSA must be one the policy trusts.
        signing._timestamp(
            root, base64.b64encode(answer).decode(), policy.tsa_roots_pem, request.nonce
        )
    except Exception:
        raise AnchorFailed("ANCHOR_RESPONSE_INVALID") from None
    return [answer]


_SUBMIT = {"opentimestamps": _opentimestamps, "rfc3161": _rfc3161}


def _record(checkpoint, kind, receipts=None, code=None):
    encoded = [base64.b64encode(r).decode() for r in receipts or []]
    record = {
        "type": kind,
        "status": "submitted" if receipts else "failed",
        "tree_size": checkpoint["tree_size"],
        "root_hash": checkpoint["root_hash"],
        "receipts_b64": encoded,
        "created_at": _now(),
    }
    if receipts:
        record["receipt_sha256"] = receipt_sha256(encoded)
    else:
        record["code"] = code
    return record


def anchor_pending(writer, kind) -> list[dict]:
    """Anchor the newest checkpoint and retry earlier failures. Returns what happened."""
    if kind == "none":
        return []
    if kind not in _SUBMIT:
        raise AnchorConfigError("unknown anchor type")
    state = writer.state
    targets = [
        c
        for c in state.checkpoints
        if state.anchors.get((c["tree_size"], kind), {}).get("status") == "failed"
    ]
    head = state.tree_head
    if head is not None and (head["tree_size"], kind) not in state.anchors:
        targets.append(head)
    results = []
    for checkpoint in targets:
        try:
            record = _record(
                checkpoint, kind, _SUBMIT[kind](bytes.fromhex(checkpoint["root_hash"]))
            )
        except AnchorFailed as error:
            record = _record(checkpoint, kind, code=error.code)
        writer.put_anchor(record)
        results.append(
            {
                "tree_size": record["tree_size"],
                "type": kind,
                "status": record["status"],
                "code": record.get("code"),
            }
        )
    return results


def verify_anchors(state, policy) -> tuple[list[str], list[str]]:
    """(findings, warnings). Only a TSA receipt can be checked here; OpenTimestamps cannot."""
    findings, warnings = [], []
    for (size, kind), record in sorted(state.anchors.items()):
        if record["status"] != "submitted":
            warnings.append("ANCHOR_FAILED")
        elif kind == "opentimestamps":
            warnings.append("OTS_NOT_VERIFIED")  # Needs the official `ots` tool and Bitcoin.
        elif policy is None:
            warnings.append("ANCHOR_UNVERIFIED")
        else:
            try:
                signing._timestamp(
                    bytes.fromhex(record["root_hash"]),
                    record["receipts_b64"][0],
                    policy.tsa_roots_pem,
                )
            except Exception:
                findings.append("ANCHOR_MISMATCH")
    return list(dict.fromkeys(findings)), list(dict.fromkeys(warnings))
