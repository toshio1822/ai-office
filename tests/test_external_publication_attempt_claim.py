"""Focused Phase 280 tests for durable external-publication claims."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import random
import socket
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as module
from ai_office.engine.external_publication import (
    ExternalPublicationAttemptAlreadyConsumedError,
    ExternalPublicationAttemptClaim,
    ExternalPublicationAttemptClaimError,
    ExternalPublicationAttemptClaimLoadError,
    ExternalPublicationAttemptClaimPersistenceError,
    ExternalPublicationPlan,
    approve_external_publication,
    build_external_publication_attempt_claim,
    claim_external_publication_attempt,
    external_publication_attempt_claim_canonical_bytes,
    external_publication_attempt_claim_digest,
    external_publication_attempt_claim_path,
    external_publication_consumption_key,
    load_external_publication_attempt_claim,
    serialize_external_publication_attempt_claim_canonical,
)


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-280",
        reconciliation_evidence_sha256="a" * 64,
        receipt_sha256="b" * 64,
        business_output_sha256="c" * 64,
        output_byte_length=17,
        provider="future-provider",
        publication_target_sha256="d" * 64,
    )


def _approval(plan: ExternalPublicationPlan | None = None, ident: str = "a-280"):
    return approve_external_publication(
        plan or _plan(), approved_by="reviewer", approval_id=ident
    )


def _forge(source: object, **changes: object) -> object:
    value = object.__new__(type(source))
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in changes.items():
        object.__setattr__(value, name, replacement)
    return value


def test_claim_shape_consumption_key_and_canonical_identity() -> None:
    plan = _plan()
    approval = _approval(plan, "承認-280")
    claim = build_external_publication_attempt_claim(plan, approval)
    assert type(claim) is ExternalPublicationAttemptClaim
    assert len(dataclasses.fields(claim)) == 14
    assert claim.state == "claimed"
    assert claim.publication_plan_sha256 == approval.publication_plan_sha256
    expected_key = hashlib.sha256("承認-280".encode()).hexdigest()
    assert claim.consumption_key == expected_key
    canonical = serialize_external_publication_attempt_claim_canonical(claim)
    assert len(json.loads(canonical)) == 14
    raw = canonical.encode()
    assert external_publication_attempt_claim_canonical_bytes(claim) == raw
    assert external_publication_attempt_claim_digest(claim) == (
        hashlib.sha256(raw).hexdigest()
    )
    assert claim.digest == external_publication_attempt_claim_digest(claim)
    assert "承認-280" in canonical and not raw.endswith(b"\n")
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.state = "changed"  # type: ignore[misc]


def test_exact_types_binding_and_malformed_helpers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    for args in ((SimpleNamespace(), approval), (plan, SimpleNamespace())):
        with pytest.raises(ExternalPublicationAttemptClaimError):
            build_external_publication_attempt_claim(*args)  # type: ignore[arg-type]
    other = dataclasses.replace(plan, regeneration_id="regen-other")
    with pytest.raises(ExternalPublicationAttemptClaimError):
        build_external_publication_attempt_claim(plan, _approval(other))
    with pytest.raises(ExternalPublicationAttemptClaimError):
        external_publication_consumption_key(  # type: ignore[arg-type]
            SimpleNamespace()
        )
    monkeypatch.setattr(
        module, "external_publication_approval_digest", lambda _: "A" * 64
    )
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(plan, approval)
    assert raised.value.detail.classification == "approval_binding"


@pytest.mark.parametrize(
    ("field", "value", "classification"),
    [
        ("business_output_sha256", "e" * 64, "plan_binding"),
        ("publication_approval_sha256", "e" * 64, "approval_binding"),
        ("consumption_key", "e" * 64, "consumption_key"),
    ],
)
def test_claim_internal_bindings_are_revalidated(
    field: str, value: str, classification: str
) -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    forged = _forge(claim, **{field: value})
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        serialize_external_publication_attempt_claim_canonical(
            forged  # type: ignore[arg-type]
        )
    assert raised.value.detail.classification == classification


def test_ledger_path_requires_explicit_existing_non_symlink_directory(
    tmp_path: Path,
) -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    expected = tmp_path / f"{claim.consumption_key}.json"
    assert external_publication_attempt_claim_path(
        tmp_path, claim.consumption_key
    ) == expected
    missing = tmp_path / "missing"
    file_path = tmp_path / "file"
    file_path.write_text("x")
    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "link"
    symlink.symlink_to(target, target_is_directory=True)
    for invalid in (missing, file_path, symlink):
        with pytest.raises(ExternalPublicationAttemptClaimPersistenceError):
            external_publication_attempt_claim_path(
                invalid, claim.consumption_key
            )


def test_first_claim_is_exact_and_second_claim_never_reads_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    claim = claim_external_publication_attempt(tmp_path, plan, approval)
    path = external_publication_attempt_claim_path(tmp_path, claim.consumption_key)
    assert path.read_bytes() == (
        external_publication_attempt_claim_canonical_bytes(claim)
    )
    assert load_external_publication_attempt_claim(path) == claim

    def forbidden_read(self: Path) -> bytes:
        raise AssertionError("must not read existing marker")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        claim_external_publication_attempt(tmp_path, plan, approval)


def test_different_approval_ids_create_separate_markers(tmp_path: Path) -> None:
    plan = _plan()
    one = claim_external_publication_attempt(tmp_path, plan, _approval(plan, "one"))
    two = claim_external_publication_attempt(tmp_path, plan, _approval(plan, "two"))
    assert one.consumption_key != two.consumption_key
    assert len(list(tmp_path.iterdir())) == 2


class _FailHandle:
    def __init__(self, handle: object, stage: str) -> None:
        self.handle = handle
        self.stage = stage

    def __enter__(self) -> _FailHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.handle.close()  # type: ignore[attr-defined]
        if self.stage == "close" and exc_type is None:
            raise OSError("close")
        return False

    def write(self, data: bytes) -> int:
        count = self.handle.write(data)  # type: ignore[attr-defined]
        return count - 1 if self.stage == "short" else count

    def flush(self) -> None:
        if self.stage == "flush":
            raise OSError("flush")
        self.handle.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self.handle.fileno()  # type: ignore[attr-defined]


@pytest.mark.parametrize("stage", ["short", "flush", "close"])
def test_post_create_stream_failure_is_ambiguous_and_retry_is_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    plan = _plan()
    approval = _approval(plan, f"fail-{stage}")
    marker = tmp_path / f"{external_publication_consumption_key(approval)}.json"
    real_open = Path.open

    def wrapped_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_open(self, *args, **kwargs)
        if self == marker and args and args[0] == "xb":
            return _FailHandle(handle, stage)
        return handle

    monkeypatch.setattr(Path, "open", wrapped_open)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert raised.value.detail.classification == "ambiguous" and marker.exists()
    monkeypatch.undo()
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        claim_external_publication_attempt(tmp_path, plan, approval)


@pytest.mark.parametrize("directory", [False, True])
def test_fsync_failure_is_ambiguous_retained_and_retry_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    plan = _plan()
    approval = _approval(plan, "dir-fsync" if directory else "file-fsync")
    marker = tmp_path / f"{external_publication_consumption_key(approval)}.json"
    real_fsync = os.fsync
    calls = 0

    def fail(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == (2 if directory else 1):
            raise OSError("fsync")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert raised.value.detail.classification == "ambiguous" and marker.exists()
    monkeypatch.undo()
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        claim_external_publication_attempt(tmp_path, plan, approval)


def test_create_failure_happens_before_marker_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    approval = _approval(plan, "create-fail")
    marker = tmp_path / f"{external_publication_consumption_key(approval)}.json"
    real_open = Path.open

    def fail(self: Path, *args: object, **kwargs: object) -> object:
        if self == marker and args and args[0] == "xb":
            raise OSError("create")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert raised.value.detail.classification == "create" and not marker.exists()


def _persist_raw(tmp_path: Path) -> tuple[ExternalPublicationAttemptClaim, Path]:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    path = tmp_path / "claim.json"
    path.write_bytes(external_publication_attempt_claim_canonical_bytes(claim))
    return claim, path


def test_loader_rejects_bad_targets_and_reads_valid_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for target in (tmp_path / "missing", tmp_path):
        with pytest.raises(ExternalPublicationAttemptClaimLoadError):
            load_external_publication_attempt_claim(target)
    claim, path = _persist_raw(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(link)
    reads = 0
    real_read = Path.read_bytes

    def counted(self: Path) -> bytes:
        nonlocal reads
        reads += self == path
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", counted)
    assert load_external_publication_attempt_claim(path) == claim
    assert reads == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"{",
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"{}",
    ],
)
def test_loader_rejects_utf8_json_duplicates_constants_and_wrong_keys(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(path)


def test_loader_rejects_forged_binding_and_noncanonical_bytes(tmp_path: Path) -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    payload = json.loads(serialize_external_publication_attempt_claim_canonical(claim))
    payload["business_output_sha256"] = "e" * 64
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(forged)
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(
        external_publication_attempt_claim_canonical_bytes(claim) + b"\n"
    )
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(noncanonical)
    assert raised.value.detail.classification == "noncanonical"


def test_no_predecessor_environment_clock_random_uuid_or_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    approval = _approval(plan, "audit")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden access")

    for name in (
        "load_publication_regeneration_export_reconciliation",
        "build_external_publication_plan",
        "validate_external_publication_plan",
    ):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    for owner, names in (
        (time, ("time", "monotonic", "sleep")),
        (random, ("random", "getrandbits")),
        (uuid, ("uuid1", "uuid4")),
        (socket, ("socket", "create_connection", "getaddrinfo")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    claim = claim_external_publication_attempt(tmp_path, plan, approval)
    path = external_publication_attempt_claim_path(tmp_path, claim.consumption_key)
    assert load_external_publication_attempt_claim(path) == claim


def test_fixed_errors_do_not_leak_approval_id_or_path(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan, "private-id")
    claim_external_publication_attempt(tmp_path, plan, approval)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert "private-id" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
