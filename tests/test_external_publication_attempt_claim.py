"""Focused Phase 280 tests for durable external-publication one-use claims."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import random
import secrets
import socket
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as external_module
import ai_office.engine.publication_regeneration_export as export_module
import ai_office.engine.publication_regeneration_export_receipt as receipt_module
import ai_office.engine.publication_regeneration_projection as projection_module
from ai_office.engine import publication_regeneration_export_reconciliation as reconciliation_module
from ai_office.engine.external_publication import (
    ExternalPublicationApproval,
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


def _approval(
    plan: ExternalPublicationPlan | None = None,
    *,
    approval_id: str = "approval-280-1",
) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan or _plan(),
        approved_by="人間レビュー者",
        approval_id=approval_id,
    )


def _forged(source: object, **changes: object) -> object:
    value = object.__new__(type(source))
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in changes.items():
        object.__setattr__(value, name, replacement)
    return value


def _assert_claim_error(error: Exception, classification: str) -> None:
    assert type(error) is ExternalPublicationAttemptClaimError
    assert str(error) == "external publication attempt claim is invalid"
    assert error.detail.classification == classification  # type: ignore[attr-defined]


def test_valid_plan_and_approval_build_exact_frozen_claim() -> None:
    plan = _plan()
    approval = _approval(plan)
    claim = build_external_publication_attempt_claim(plan, approval)

    assert type(claim) is ExternalPublicationAttemptClaim
    assert tuple(field.name for field in dataclasses.fields(claim)) == (
        "schema_version",
        "consumption_key",
        "regeneration_id",
        "publication_plan_sha256",
        "publication_approval_sha256",
        "approval_id",
        "approved_by",
        "reconciliation_evidence_sha256",
        "receipt_sha256",
        "business_output_sha256",
        "output_byte_length",
        "provider",
        "publication_target_sha256",
        "state",
    )
    assert claim.schema_version == "external-publication-attempt.v1"
    assert claim.state == "claimed"
    assert claim.publication_plan_sha256 == approval.publication_plan_sha256
    assert claim.approval_id == approval.approval_id
    assert claim.approved_by == approval.approved_by
    assert claim.regeneration_id == plan.regeneration_id
    assert claim.reconciliation_evidence_sha256 == plan.reconciliation_evidence_sha256
    assert claim.receipt_sha256 == plan.receipt_sha256
    assert claim.business_output_sha256 == plan.business_output_sha256
    assert claim.output_byte_length == plan.output_byte_length
    assert claim.provider == plan.provider
    assert claim.publication_target_sha256 == plan.publication_target_sha256
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.state = "changed"  # type: ignore[misc]


def test_consumption_key_is_exact_approval_id_utf8_sha256() -> None:
    approval = _approval(approval_id="承認-280")
    expected = hashlib.sha256("承認-280".encode("utf-8")).hexdigest()
    assert external_publication_consumption_key(approval) == expected


def test_different_approval_ids_have_different_consumption_keys() -> None:
    assert external_publication_consumption_key(_approval(approval_id="a-1")) != (
        external_publication_consumption_key(_approval(approval_id="a-2"))
    )


@pytest.mark.parametrize("candidate", [SimpleNamespace(), "approval"])
def test_consumption_key_requires_exact_approval_type(candidate: object) -> None:
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        external_publication_consumption_key(candidate)  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "approval_type")


def test_claim_build_rejects_plan_or_approval_substitutes() -> None:
    plan = _plan()
    approval = _approval(plan)
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(SimpleNamespace(), approval)  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "plan_type")
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(plan, SimpleNamespace())  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "approval_type")


def test_claim_build_rejects_plan_approval_mismatch() -> None:
    plan = _plan()
    other = dataclasses.replace(plan, regeneration_id="regen-other")
    approval = _approval(other)
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(plan, approval)
    _assert_claim_error(raised.value, "plan_binding")


def test_claim_build_fails_closed_on_malformed_helper_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    monkeypatch.setattr(
        external_module,
        "external_publication_approval_digest",
        lambda value: "A" * 64,
    )
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(plan, approval)
    assert raised.value.detail.classification == "approval_binding"


def test_canonical_claim_identity_is_exact_fourteen_keys_unicode_utf8_sha256() -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="承認-280")
    claim = build_external_publication_attempt_claim(plan, approval)
    canonical = serialize_external_publication_attempt_claim_canonical(claim)
    payload = json.loads(canonical)
    assert tuple(sorted(payload)) == tuple(
        sorted(
            {
                "approval_id",
                "approved_by",
                "business_output_sha256",
                "consumption_key",
                "output_byte_length",
                "provider",
                "publication_approval_sha256",
                "publication_plan_sha256",
                "publication_target_sha256",
                "receipt_sha256",
                "reconciliation_evidence_sha256",
                "regeneration_id",
                "schema_version",
                "state",
            }
        )
    )
    raw = canonical.encode("utf-8")
    assert external_publication_attempt_claim_canonical_bytes(claim) == raw
    assert external_publication_attempt_claim_digest(claim) == hashlib.sha256(raw).hexdigest()
    assert claim.digest == external_publication_attempt_claim_digest(claim)
    assert not raw.endswith(b"\n")
    assert "承認-280" in canonical
    assert "\\u627f" not in canonical


def test_forged_claim_internal_plan_binding_fails_closed() -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    forged = _forged(claim, business_output_sha256="e" * 64)
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        serialize_external_publication_attempt_claim_canonical(forged)  # type: ignore[arg-type]
    assert raised.value.detail.classification == "plan_binding"


def test_forged_claim_approval_binding_fails_closed() -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    forged = _forged(claim, publication_approval_sha256="e" * 64)
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        serialize_external_publication_attempt_claim_canonical(forged)  # type: ignore[arg-type]
    assert raised.value.detail.classification == "approval_binding"


def test_forged_claim_consumption_key_fails_closed() -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    forged = _forged(claim, consumption_key="e" * 64)
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        serialize_external_publication_attempt_claim_canonical(forged)  # type: ignore[arg-type]
    assert raised.value.detail.classification == "consumption_key"


def test_serializer_rejects_attribute_compatible_claim() -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    substitute = SimpleNamespace(**dataclasses.asdict(claim))
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        serialize_external_publication_attempt_claim_canonical(substitute)  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "claim_type")


def test_claim_path_requires_exact_existing_non_symlink_directory(tmp_path: Path) -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    expected = tmp_path / f"{claim.consumption_key}.json"
    assert external_publication_attempt_claim_path(tmp_path, claim.consumption_key) == expected

    missing = tmp_path / "missing"
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(missing, claim.consumption_key)
    assert raised.value.detail.classification == "ledger_directory"

    file_path = tmp_path / "file"
    file_path.write_text("x")
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError):
        external_publication_attempt_claim_path(file_path, claim.consumption_key)

    target = tmp_path / "target"
    target.mkdir()
    symlink = tmp_path / "link"
    symlink.symlink_to(target, target_is_directory=True)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError):
        external_publication_attempt_claim_path(symlink, claim.consumption_key)


def test_first_claim_succeeds_and_persisted_bytes_are_exact_canonical(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan)
    claim = claim_external_publication_attempt(tmp_path, plan, approval)
    path = external_publication_attempt_claim_path(tmp_path, claim.consumption_key)
    assert path.read_bytes() == external_publication_attempt_claim_canonical_bytes(claim)
    assert load_external_publication_attempt_claim(path) == claim


def test_second_claim_same_approval_is_always_already_consumed_without_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    first = claim_external_publication_attempt(tmp_path, plan, approval)
    marker = external_publication_attempt_claim_path(tmp_path, first.consumption_key)
    original = marker.read_bytes()

    def forbidden_read(self: Path) -> bytes:
        raise AssertionError("existing marker must not be read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert str(raised.value) == "external publication approval is already consumed"
    assert raised.value.detail.classification == "already_consumed"
    monkeypatch.undo()
    assert marker.read_bytes() == original


def test_different_approval_ids_create_separate_markers(tmp_path: Path) -> None:
    plan = _plan()
    first = claim_external_publication_attempt(
        tmp_path,
        plan,
        _approval(plan, approval_id="one"),
    )
    second = claim_external_publication_attempt(
        tmp_path,
        plan,
        _approval(plan, approval_id="two"),
    )
    assert first.consumption_key != second.consumption_key
    assert len(list(tmp_path.iterdir())) == 2


class _WriteFailureHandle:
    def __init__(self, handle: object, stage: str) -> None:
        self.handle = handle
        self.stage = stage

    def __enter__(self) -> "_WriteFailureHandle":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.handle.close()  # type: ignore[attr-defined]
        if self.stage == "close" and exc_type is None:
            raise OSError("close failed")
        return False

    def write(self, data: bytes) -> int:
        written = self.handle.write(data)  # type: ignore[attr-defined]
        if self.stage == "short_write":
            return max(0, written - 1)
        return written

    def flush(self) -> None:
        if self.stage == "flush":
            raise OSError("flush failed")
        self.handle.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self.handle.fileno()  # type: ignore[attr-defined]


@pytest.mark.parametrize("stage", ["short_write", "flush", "close"])
def test_post_create_write_flush_close_failures_are_ambiguous_and_retain_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id=f"failure-{stage}")
    key = external_publication_consumption_key(approval)
    marker = tmp_path / f"{key}.json"
    original_open = Path.open

    def wrapped_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(self, *args, **kwargs)
        if self == marker and args and args[0] == "xb":
            return _WriteFailureHandle(handle, stage)
        return handle

    monkeypatch.setattr(Path, "open", wrapped_open)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert type(raised.value) is ExternalPublicationAttemptClaimPersistenceError
    assert raised.value.detail.classification == "ambiguous"
    assert marker.exists()
    monkeypatch.undo()
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        claim_external_publication_attempt(tmp_path, plan, approval)


def test_file_fsync_failure_is_ambiguous_and_retry_is_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="file-fsync")
    marker = tmp_path / f"{external_publication_consumption_key(approval)}.json"
    monkeypatch.setattr(
        os,
        "fsync",
        lambda fd: (_ for _ in ()).throw(OSError("fsync")),
    )
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert raised.value.detail.classification == "ambiguous"
    assert marker.exists()
    monkeypatch.undo()
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        claim_external_publication_attempt(tmp_path, plan, approval)


def test_directory_fsync_failure_is_ambiguous_and_retry_is_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="dir-fsync")
    marker = tmp_path / f"{external_publication_consumption_key(approval)}.json"
    real_fsync = os.fsync
    calls = 0

    def fail_second(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("dir fsync")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_second)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert raised.value.detail.classification == "ambiguous"
    assert marker.exists()
    monkeypatch.undo()
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        claim_external_publication_attempt(tmp_path, plan, approval)


def test_open_failure_before_marker_creation_leaves_no_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="create-failure")
    key = external_publication_consumption_key(approval)
    marker = tmp_path / f"{key}.json"
    original_open = Path.open

    def fail_open(self: Path, *args: object, **kwargs: object) -> object:
        if self == marker and args and args[0] == "xb":
            raise OSError("create failed")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert raised.value.detail.classification == "create"
    assert not marker.exists()


def _write_claim(tmp_path: Path) -> tuple[ExternalPublicationAttemptClaim, Path]:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    path = tmp_path / "claim.json"
    path.write_bytes(external_publication_attempt_claim_canonical_bytes(claim))
    return claim, path


def test_loader_rejects_missing_directory_symlink_and_non_regular(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(tmp_path / "missing")
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(tmp_path)
    _, path = _write_claim(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(link)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(fifo)


@pytest.mark.parametrize(
    "contents",
    [
        b"\xff",
        b"{",
        b'{"schema_version":"external-publication-attempt.v1","schema_version":"x"}',
        b'{"schema_version":"external-publication-attempt.v1","output_byte_length":NaN}',
        b'{"schema_version":"external-publication-attempt.v1","output_byte_length":Infinity}',
        b"{}",
    ],
)
def test_loader_rejects_invalid_utf8_json_duplicates_constants_and_wrong_keys(
    tmp_path: Path,
    contents: bytes,
) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(contents)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError):
        load_external_publication_attempt_claim(path)


def test_loader_rejects_forged_binding_and_noncanonical_bytes(tmp_path: Path) -> None:
    claim = build_external_publication_attempt_claim(_plan(), _approval())
    payload = json.loads(serialize_external_publication_attempt_claim_canonical(claim))
    payload["business_output_sha256"] = "e" * 64
    forged = tmp_path / "forged.json"
    forged.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(forged)
    assert raised.value.detail.classification == "record"

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_bytes(external_publication_attempt_claim_canonical_bytes(claim) + b"\n")
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(noncanonical)
    assert raised.value.detail.classification == "noncanonical"


def test_loader_reads_bytes_exactly_once_and_never_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim, path = _write_claim(tmp_path)
    original = path.read_bytes()
    real_read = Path.read_bytes
    reads = 0

    def counted_read(self: Path) -> bytes:
        nonlocal reads
        if self == path:
            reads += 1
        return real_read(self)

    def forbidden_write(self: Path, data: bytes) -> int:
        raise AssertionError("loader must not repair")

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    monkeypatch.setattr(Path, "write_bytes", forbidden_write)
    assert load_external_publication_attempt_claim(path) == claim
    assert reads == 1
    monkeypatch.undo()
    assert path.read_bytes() == original


def test_phase280_boundaries_do_not_call_forbidden_predecessors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="predecessor-audit")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden predecessor boundary")

    boundaries = (
        (reconciliation_module, "reconcile_publication_regeneration_export"),
        (receipt_module, "load_publication_regeneration_export_receipt"),
        (export_module, "export_publication_regeneration_output"),
        (projection_module, "project_publication_regeneration_output"),
    )
    for module, name in boundaries:
        monkeypatch.setattr(module, name, forbidden)
        monkeypatch.setattr(external_module, name, forbidden, raising=False)
    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        forbidden,
    )
    monkeypatch.setattr(external_module, "build_external_publication_plan", forbidden)
    monkeypatch.setattr(external_module, "validate_external_publication_plan", forbidden)

    claim = claim_external_publication_attempt(tmp_path, plan, approval)
    path = external_publication_attempt_claim_path(tmp_path, claim.consumption_key)
    assert load_external_publication_attempt_claim(path) == claim


def test_phase280_has_no_environment_clock_random_uuid_or_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="runtime-audit")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden runtime access")

    with monkeypatch.context() as guard:
        guard.setattr(os, "getenv", forbidden)
        for name in ("time", "time_ns", "monotonic", "perf_counter", "sleep"):
            guard.setattr(time, name, forbidden)
        for name in ("random", "randint", "randrange", "getrandbits"):
            guard.setattr(random, name, forbidden)
        for name in ("token_bytes", "token_hex", "randbelow"):
            guard.setattr(secrets, name, forbidden)
        guard.setattr(uuid, "uuid1", forbidden)
        guard.setattr(uuid, "uuid4", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        guard.setattr(socket, "getaddrinfo", forbidden)

        claim = claim_external_publication_attempt(tmp_path, plan, approval)
        path = external_publication_attempt_claim_path(tmp_path, claim.consumption_key)
        assert load_external_publication_attempt_claim(path) == claim


def test_error_messages_do_not_leak_approval_id_or_path(tmp_path: Path) -> None:
    plan = _plan()
    approval = _approval(plan, approval_id="private-approval-id")
    claim_external_publication_attempt(tmp_path, plan, approval)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        claim_external_publication_attempt(tmp_path, plan, approval)
    assert "private-approval-id" not in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
