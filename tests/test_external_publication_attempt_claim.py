"""Focused provider-free tests for the Phase 280 one-use claim ledger."""

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
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as external_module
import ai_office.engine.publication_regeneration_export as export_module
import ai_office.engine.publication_regeneration_export_receipt as receipt_module
import ai_office.engine.publication_regeneration_projection as projection_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptAlreadyConsumedError,
    ExternalPublicationAttemptClaim,
    ExternalPublicationAttemptClaimError,
    ExternalPublicationAttemptClaimLoadError,
    ExternalPublicationAttemptClaimPersistenceError,
    ExternalPublicationFailureDetail,
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
from ai_office.engine import (
    publication_regeneration_export_reconciliation as reconciliation_module,
)

_PLAN_SCHEMA = "external-publication-plan.v1"
_ATTEMPT_SCHEMA = "external-publication-attempt.v1"
_RECEIPT_DIGEST = "b" * 64
_OUTPUT_DIGEST = "a" * 64
_EVIDENCE_DIGEST = "c" * 64
_TARGET_DIGEST = "e" * 64


def _plan(regeneration_id: str = "regen-280-claim") -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id=regeneration_id,
        reconciliation_evidence_sha256=_EVIDENCE_DIGEST,
        receipt_sha256=_RECEIPT_DIGEST,
        business_output_sha256=_OUTPUT_DIGEST,
        output_byte_length=17,
        provider="future-provider",
        publication_target_sha256=_TARGET_DIGEST,
    )


def _approval(
    plan: ExternalPublicationPlan | None = None,
    *,
    approved_by: str = "human-reviewer",
    approval_id: str = "approval-280-1",
) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan or _plan(),
        approved_by=approved_by,
        approval_id=approval_id,
    )


def _claim(
    plan: ExternalPublicationPlan | None = None,
    *,
    approval_id: str = "approval-280-1",
) -> ExternalPublicationAttemptClaim:
    actual_plan = plan or _plan()
    return build_external_publication_attempt_claim(
        actual_plan,
        _approval(actual_plan, approval_id=approval_id),
    )


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_claim_error(
    error: ValueError,
    classification: str,
    *,
    error_type: type[ValueError] = ExternalPublicationAttemptClaimError,
) -> None:
    assert type(error) is error_type
    assert str(error) == "external publication attempt claim is invalid"
    assert type(error.detail) is ExternalPublicationFailureDetail  # type: ignore[attr-defined]
    assert error.detail.classification == classification  # type: ignore[attr-defined]


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationAttemptClaimPersistenceError
    assert str(error) == "external publication attempt claim persistence failed"
    assert type(error.detail) is ExternalPublicationFailureDetail
    assert error.detail.classification == classification


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationAttemptClaimLoadError
    assert str(error) == "external publication attempt claim could not be loaded"
    assert type(error.detail) is ExternalPublicationFailureDetail
    assert error.detail.classification == classification


def _canonical_mapping(claim: ExternalPublicationAttemptClaim) -> dict[str, object]:
    return {
        "approval_id": claim.approval_id,
        "approved_by": claim.approved_by,
        "business_output_sha256": claim.business_output_sha256,
        "consumption_key": claim.consumption_key,
        "output_byte_length": claim.output_byte_length,
        "provider": claim.provider,
        "publication_approval_sha256": claim.publication_approval_sha256,
        "publication_plan_sha256": claim.publication_plan_sha256,
        "publication_target_sha256": claim.publication_target_sha256,
        "receipt_sha256": claim.receipt_sha256,
        "reconciliation_evidence_sha256": (claim.reconciliation_evidence_sha256),
        "regeneration_id": claim.regeneration_id,
        "schema_version": claim.schema_version,
        "state": claim.state,
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _write_claim(path: Path, claim: ExternalPublicationAttemptClaim) -> None:
    path.write_bytes(external_publication_attempt_claim_canonical_bytes(claim))


def test_claim_has_exact_frozen_fourteen_field_identity() -> None:
    claim = _claim()

    assert type(claim) is ExternalPublicationAttemptClaim
    assert tuple(field.name for field in fields(ExternalPublicationAttemptClaim)) == (
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
    assert claim.schema_version == _ATTEMPT_SCHEMA
    assert claim.state == "claimed"
    assert claim.regeneration_id == "regen-280-claim"
    assert claim.provider == "future-provider"
    assert claim.output_byte_length == 17
    assert claim.publication_plan_sha256 == _plan().digest
    assert claim.publication_approval_sha256 == _approval().digest
    assert claim.consumption_key == hashlib.sha256(b"approval-280-1").hexdigest()
    assert "destination" not in claim.__dict__
    assert "credential" not in claim.__dict__
    with pytest.raises(FrozenInstanceError):
        claim.state = "other"  # type: ignore[misc]


def test_consumption_key_is_sha256_of_exact_utf8_approval_id() -> None:
    approval = _approval(approval_id="承認-280-1")

    assert (
        external_publication_consumption_key(approval)
        == hashlib.sha256("承認-280-1".encode()).hexdigest()
    )
    assert external_publication_consumption_key(approval) != (
        external_publication_consumption_key(_approval(approval_id="承認-280-2"))
    )


def test_canonical_claim_json_bytes_and_digest_are_exact() -> None:
    claim = _claim()
    expected = (
        '{"approval_id":"approval-280-1","approved_by":"human-reviewer",'
        f'"business_output_sha256":"{_OUTPUT_DIGEST}",'
        f'"consumption_key":"{claim.consumption_key}",'
        '"output_byte_length":17,"provider":"future-provider",'
        f'"publication_approval_sha256":"{claim.publication_approval_sha256}",'
        f'"publication_plan_sha256":"{claim.publication_plan_sha256}",'
        f'"publication_target_sha256":"{_TARGET_DIGEST}",'
        f'"receipt_sha256":"{_RECEIPT_DIGEST}",'
        f'"reconciliation_evidence_sha256":"{_EVIDENCE_DIGEST}",'
        '"regeneration_id":"regen-280-claim",'
        f'"schema_version":"{_ATTEMPT_SCHEMA}","state":"claimed"}}'
    )

    assert serialize_external_publication_attempt_claim_canonical(claim) == expected
    assert external_publication_attempt_claim_canonical_bytes(claim) == (
        expected.encode("utf-8")
    )
    assert (
        external_publication_attempt_claim_digest(claim)
        == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    )
    assert claim.digest == external_publication_attempt_claim_digest(claim)
    assert _canonical_mapping(claim) == json.loads(expected)
    assert b"\n" not in expected.encode("utf-8")


def test_claim_builder_reuses_exact_plan_approval_values_without_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path
    plan = _plan()
    approval = _approval(plan)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden predecessor or external boundary called")

    for name in (
        "load_publication_regeneration_export_reconciliation",
        "publication_regeneration_export_reconciliation_digest",
        "build_external_publication_plan",
        "validate_external_publication_plan",
    ):
        monkeypatch.setattr(external_module, name, forbidden)
    for owner, name in (
        (Path, "read_bytes"),
        (Path, "write_bytes"),
        (Path, "open"),
        (Path, "resolve"),
        (os, "open"),
        (os, "fsync"),
        (os, "getenv"),
        (socket, "socket"),
        (time, "time"),
        (time, "time_ns"),
        (random, "random"),
        (secrets, "token_bytes"),
        (uuid, "uuid4"),
    ):
        monkeypatch.setattr(owner, name, forbidden)

    claim = build_external_publication_attempt_claim(plan, approval)
    assert claim.regeneration_id == plan.regeneration_id
    assert claim.publication_plan_sha256 == approval.publication_plan_sha256
    assert claim.approval_id == approval.approval_id
    assert claim.approved_by == approval.approved_by


def test_claim_building_does_not_call_predecessor_or_runtime_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden predecessor or runtime boundary called")

    forbidden_boundaries = (
        (external_module, "load_publication_regeneration_export_reconciliation"),
        (external_module, "publication_regeneration_export_reconciliation_digest"),
        (external_module, "build_external_publication_plan"),
        (external_module, "validate_external_publication_plan"),
        (reconciliation_module, "reconcile_publication_regeneration_export"),
        (receipt_module, "load_publication_regeneration_export_receipt"),
        (receipt_module, "publication_regeneration_export_receipt_digest"),
        (export_module, "export_publication_regeneration_output"),
        (projection_module, "project_publication_regeneration_output"),
    )
    for module, name in forbidden_boundaries:
        monkeypatch.setattr(module, name, forbidden)
        monkeypatch.setattr(external_module, name, forbidden, raising=False)

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(external_module.os, "open", forbidden)
    monkeypatch.setattr(external_module.os, "fsync", forbidden)
    monkeypatch.setattr(external_module.os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "time_ns", forbidden)
    monkeypatch.setattr(random, "random", forbidden)
    monkeypatch.setattr(secrets, "token_bytes", forbidden)
    monkeypatch.setattr(uuid, "uuid4", forbidden)

    claim = build_external_publication_attempt_claim(plan, approval)
    assert claim == _claim(plan)


def test_public_boundaries_reject_subclasses_and_attribute_compatible_values() -> None:
    plan = _plan()
    approval = _approval(plan)
    claim = _claim(plan)

    class PlanChild(ExternalPublicationPlan):
        pass

    class ApprovalChild(ExternalPublicationApproval):
        pass

    class ClaimChild(ExternalPublicationAttemptClaim):
        pass

    candidates = (
        (PlanChild, plan),
        (ApprovalChild, approval),
        (ClaimChild, claim),
    )
    for child_type, source in candidates:
        forged = _forged_instance(child_type, source)
        if child_type is PlanChild:
            with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
                build_external_publication_attempt_claim(forged, approval)  # type: ignore[arg-type]
            _assert_claim_error(raised.value, "plan_type")
        elif child_type is ApprovalChild:
            with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
                build_external_publication_attempt_claim(plan, forged)  # type: ignore[arg-type]
            _assert_claim_error(raised.value, "approval_type")
            with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
                external_publication_consumption_key(forged)  # type: ignore[arg-type]
            _assert_claim_error(raised.value, "approval_type")
        else:
            with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
                serialize_external_publication_attempt_claim_canonical(forged)  # type: ignore[arg-type]
            _assert_claim_error(raised.value, "claim_type")

    substitute = SimpleNamespace(**claim.__dict__)
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        serialize_external_publication_attempt_claim_canonical(substitute)  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "claim_type")
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        external_publication_consumption_key(SimpleNamespace(**approval.__dict__))  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "approval_type")


def test_claim_builder_fails_closed_on_malformed_helper_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)

    key_calls = 0

    def malformed_key(_approval: ExternalPublicationApproval) -> str:
        nonlocal key_calls
        key_calls += 1
        return "not-a-consumption-key"

    monkeypatch.setattr(
        external_module,
        "external_publication_consumption_key",
        malformed_key,
    )
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(plan, approval)
    _assert_claim_error(raised.value, "consumption_key")
    assert key_calls == 1

    monkeypatch.undo()
    approval_digest_calls = 0

    def malformed_digest(_approval: ExternalPublicationApproval) -> str:
        nonlocal approval_digest_calls
        approval_digest_calls += 1
        return "not-a-digest"

    monkeypatch.setattr(
        external_module,
        "external_publication_approval_digest",
        malformed_digest,
    )
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        build_external_publication_attempt_claim(plan, approval)
    _assert_claim_error(raised.value, "approval_binding")
    assert approval_digest_calls == 1


def test_claim_path_requires_exact_existing_nonsymlink_caller_directory(
    tmp_path: Path,
) -> None:
    claim = _claim()
    missing = tmp_path / "missing-ledger"
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(missing, claim.consumption_key)
    _assert_persistence_error(raised.value, "ledger_directory")
    assert not missing.exists()

    file_path = tmp_path / "not-a-directory"
    file_path.write_bytes(b"file")
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(file_path, claim.consumption_key)
    _assert_persistence_error(raised.value, "ledger_directory")

    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = external_publication_attempt_claim_path(
        ledger,
        claim.consumption_key,
    )
    assert marker == ledger / f"{claim.consumption_key}.json"
    assert not marker.exists()

    symlink = tmp_path / "ledger-link"
    symlink.symlink_to(ledger, target_is_directory=True)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(symlink, claim.consumption_key)
    _assert_persistence_error(raised.value, "ledger_directory")

    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(ledger, "A" * 64)
    _assert_persistence_error(raised.value, "consumption_key")
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(ledger, "not-a-key")
    _assert_persistence_error(raised.value, "consumption_key")
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(str(ledger), claim.consumption_key)  # type: ignore[arg-type]
    _assert_persistence_error(raised.value, "ledger_directory_type")


def test_first_claim_is_exclusive_durable_and_persists_exact_canonical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    claim = build_external_publication_attempt_claim(plan, approval)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    fsync_calls: list[int] = []
    original_fsync = external_module.os.fsync

    def record_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(external_module.os, "fsync", record_fsync)
    created = claim_external_publication_attempt(ledger, plan, approval)
    marker = ledger / f"{claim.consumption_key}.json"

    assert created == claim
    assert marker.read_bytes() == external_publication_attempt_claim_canonical_bytes(
        claim
    )
    assert len(fsync_calls) == 2
    assert marker.name == f"{claim.consumption_key}.json"
    assert approval.approval_id not in marker.name


def test_successful_claim_orders_write_flush_file_fsync_close_and_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{external_publication_consumption_key(approval)}.json"
    events: list[str] = []
    original_open = Path.open
    original_fsync = external_module.os.fsync

    class HandleSpy:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> HandleSpy:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            events.append("close")
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def write(self, contents: bytes) -> int:
            events.append("write")
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            events.append("flush")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

    def spying_open(
        candidate: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        handle = original_open(candidate, *args, **kwargs)
        if candidate == marker and args and args[0] == "xb":
            return HandleSpy(handle)
        return handle

    fsync_calls = 0

    def spying_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        events.append("file_fsync")
        original_fsync(descriptor)

    monkeypatch.setattr(Path, "open", spying_open)
    monkeypatch.setattr(external_module.os, "fsync", spying_fsync)

    def spying_directory_fsync(_directory: Path) -> None:
        events.append("directory_fsync")

    monkeypatch.setattr(
        external_module,
        "_fsync_claim_directory",
        spying_directory_fsync,
    )
    claim_external_publication_attempt(ledger, plan, approval)

    assert events == [
        "write",
        "flush",
        "file_fsync",
        "close",
        "directory_fsync",
    ]
    assert fsync_calls == 1


def test_second_claim_with_identical_marker_rejects_without_reading_existing_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    first = claim_external_publication_attempt(ledger, plan, approval)
    marker = ledger / f"{first.consumption_key}.json"
    before = marker.read_bytes()

    def forbidden_read(_path: Path) -> bytes:
        raise AssertionError("existing marker was read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        claim_external_publication_attempt(ledger, plan, approval)
    assert type(raised.value) is ExternalPublicationAttemptAlreadyConsumedError
    assert str(raised.value) == "external publication approval is already consumed"
    assert raised.value.detail.classification == "already_consumed"
    monkeypatch.undo()
    assert marker.read_bytes() == before


@pytest.mark.parametrize("existing", (b"", b"corrupt-marker"))
def test_existing_marker_is_already_consumed_without_reading_any_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: bytes,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{external_publication_consumption_key(approval)}.json"
    marker.write_bytes(existing)

    def forbidden_read(_path: Path) -> bytes:
        raise AssertionError("existing marker was read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        claim_external_publication_attempt(ledger, plan, approval)
    assert raised.value.detail.classification == "already_consumed"


def test_same_approval_id_on_different_plan_rejects_without_reading_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_plan = _plan("regen-280-first")
    second_plan = _plan("regen-280-second")
    first_approval = _approval(first_plan, approval_id="same-approval-id")
    second_approval = _approval(second_plan, approval_id="same-approval-id")
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    first = claim_external_publication_attempt(ledger, first_plan, first_approval)
    marker = ledger / f"{first.consumption_key}.json"
    before = marker.read_bytes()

    def forbidden_read(_path: Path) -> bytes:
        raise AssertionError("existing marker was read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        claim_external_publication_attempt(ledger, second_plan, second_approval)
    assert raised.value.detail.classification == "already_consumed"
    monkeypatch.undo()
    assert marker.read_bytes() == before


def test_different_approval_ids_create_separate_markers(tmp_path: Path) -> None:
    plan = _plan()
    first_approval = _approval(plan, approval_id="approval-280-1")
    second_approval = _approval(plan, approval_id="approval-280-2")
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    first = claim_external_publication_attempt(ledger, plan, first_approval)
    second = claim_external_publication_attempt(ledger, plan, second_approval)

    assert first.consumption_key != second.consumption_key
    assert (ledger / f"{first.consumption_key}.json").is_file()
    assert (ledger / f"{second.consumption_key}.json").is_file()


@pytest.mark.parametrize(
    "failure_stage",
    ("short_write", "flush", "file_fsync", "close", "directory_fsync"),
)
def test_post_creation_write_and_durability_failures_are_ambiguous_and_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    claim = build_external_publication_attempt_claim(plan, approval)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    canonical = external_publication_attempt_claim_canonical_bytes(claim)
    original_open = Path.open
    original_fsync = external_module.os.fsync
    fsync_calls = 0

    class FailingHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def write(self, contents: bytes) -> int:
            if failure_stage == "short_write":
                self.handle.write(contents[:1])  # type: ignore[attr-defined]
                return 1
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            if failure_stage == "flush":
                raise OSError("synthetic flush failure")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            self.handle.close()  # type: ignore[attr-defined]
            if failure_stage == "close":
                raise OSError("synthetic close failure")

    def wrapped_open(
        candidate: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        handle = original_open(candidate, *args, **kwargs)
        if candidate == marker and args and args[0] == "xb":
            return FailingHandle(handle)
        return handle

    def failing_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if failure_stage == "file_fsync":
            raise OSError("synthetic file fsync failure")
        if failure_stage == "directory_fsync" and fsync_calls == 2:
            raise OSError("synthetic directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(Path, "open", wrapped_open)
    monkeypatch.setattr(external_module.os, "fsync", failing_fsync)

    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(ledger, plan, approval)
    _assert_persistence_error(raised.value, "ambiguous")
    assert marker.exists()
    assert marker.read_bytes() == (
        canonical[:1] if failure_stage == "short_write" else canonical
    )

    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as consumed:
        claim_external_publication_attempt(ledger, plan, approval)
    assert consumed.value.detail.classification == "already_consumed"


def test_create_failure_before_exclusive_creation_leaves_no_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{external_publication_consumption_key(approval)}.json"

    def fail_open(_path: Path, *_args: object, **_kwargs: object) -> object:
        raise OSError("synthetic create failure")

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        claim_external_publication_attempt(ledger, plan, approval)
    _assert_persistence_error(raised.value, "create")
    assert not marker.exists()


def test_loader_accepts_exact_canonical_record_and_reads_bytes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    marker = tmp_path / "claim.json"
    _write_claim(marker, claim)
    original_read_bytes = Path.read_bytes
    reads = 0

    def record_read(path: Path) -> bytes:
        nonlocal reads
        reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    assert load_external_publication_attempt_claim(marker) == claim
    assert reads == 1


def test_loader_rejects_missing_symlink_directory_and_nonregular_targets(
    tmp_path: Path,
) -> None:
    claim = _claim()
    regular = tmp_path / "regular.json"
    _write_claim(regular, claim)

    missing = tmp_path / "missing.json"
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(missing)
    _assert_load_error(raised.value, "target")

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(directory)
    _assert_load_error(raised.value, "target")

    symlink = tmp_path / "link.json"
    symlink.symlink_to(regular)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(symlink)
    _assert_load_error(raised.value, "target")

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "claim.fifo"
        os.mkfifo(fifo)
        with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
            load_external_publication_attempt_claim(fifo)
        _assert_load_error(raised.value, "target")

    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(str(regular))  # type: ignore[arg-type]
    _assert_load_error(raised.value, "path_type")


def test_loader_rejects_strict_json_and_canonicality_violations_without_repair(
    tmp_path: Path,
) -> None:
    claim = _claim()
    valid = _canonical_mapping(claim)
    canonical = _canonical_json(valid)
    cases = (
        (b"\xff", "parse"),
        (b"{", "parse"),
        (canonical[:-1] + b',"state":"claimed"}', "parse"),
        (
            canonical.replace(b'"output_byte_length":17', b'"output_byte_length":NaN'),
            "parse",
        ),
        (
            canonical.replace(
                b'"output_byte_length":17', b'"output_byte_length":Infinity'
            ),
            "parse",
        ),
        (_canonical_json({**valid, "extra": "field"}), "keys"),
        (
            _canonical_json(
                {key: value for key, value in valid.items() if key != "state"}
            ),
            "keys",
        ),
        (_canonical_json({**valid, "output_byte_length": 17.0}), "claim_binding"),
        (_canonical_json({**valid, "provider": "other-provider"}), "plan_binding"),
        (canonical + b"\n", "noncanonical"),
    )

    for index, (contents, classification) in enumerate(cases):
        marker = tmp_path / f"invalid-{index}.json"
        marker.write_bytes(contents)
        before = marker.read_bytes()
        with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
            load_external_publication_attempt_claim(marker)
        _assert_load_error(raised.value, classification)
        assert marker.read_bytes() == before


def test_loader_rejects_forged_plan_approval_and_consumption_bindings(
    tmp_path: Path,
) -> None:
    claim = _claim()
    valid = _canonical_mapping(claim)
    cases = (
        {**valid, "publication_plan_sha256": "f" * 64},
        {**valid, "publication_approval_sha256": "f" * 64},
        {**valid, "consumption_key": "f" * 64},
        {**valid, "approval_id": "different-approval"},
        {**valid, "approved_by": "different-reviewer"},
        {**valid, "regeneration_id": "different-regeneration"},
        {**valid, "state": "prepared"},
    )

    for index, value in enumerate(cases):
        marker = tmp_path / f"binding-{index}.json"
        marker.write_bytes(_canonical_json(value))
        before = marker.read_bytes()
        with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
            load_external_publication_attempt_claim(marker)
        expected = (
            "plan_binding",
            "approval_binding",
            "consumption_key",
            "consumption_key",
            "approval_binding",
            "plan_binding",
            "state",
        )[index]
        _assert_load_error(raised.value, expected)
        assert marker.read_bytes() == before


def test_loader_rejects_duplicate_keys_and_does_not_read_twice_or_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    canonical = serialize_external_publication_attempt_claim_canonical(claim)
    marker = tmp_path / "duplicate.json"
    duplicate = (canonical[:-1] + ',"state":"claimed"}').encode("utf-8")
    marker.write_bytes(duplicate)
    original = marker.read_bytes()
    reads = 0
    original_read_bytes = Path.read_bytes

    def record_read(path: Path) -> bytes:
        nonlocal reads
        reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(marker)
    _assert_load_error(raised.value, "parse")
    assert reads == 1
    assert original_read_bytes(marker) == original


def test_error_messages_are_fixed_and_detail_safe(tmp_path: Path) -> None:
    claim = _claim()
    secret_id = "private-approval-id"
    approval = _approval(approval_id=secret_id)
    missing = tmp_path / "private-ledger" / "claim.json"

    with pytest.raises(ExternalPublicationAttemptClaimPersistenceError) as raised:
        external_publication_attempt_claim_path(
            missing.parent,
            claim.consumption_key,
        )
    assert str(raised.value) == "external publication attempt claim persistence failed"
    assert str(missing) not in str(raised.value)
    assert secret_id not in str(raised.value)
    assert claim.consumption_key not in str(raised.value)
    assert approval.approval_id == secret_id


def test_public_engine_exports_are_available() -> None:
    assert callable(build_external_publication_attempt_claim)
    assert callable(claim_external_publication_attempt)
    assert callable(load_external_publication_attempt_claim)
    assert callable(external_publication_consumption_key)
    assert callable(external_publication_attempt_claim_path)
    assert callable(external_publication_attempt_claim_digest)
    assert callable(serialize_external_publication_attempt_claim_canonical)


def test_claim_validation_does_not_change_phase_278_plan_or_phase_279_approval() -> (
    None
):
    plan = _plan()
    approval = _approval(plan)
    before_plan = dataclasses.asdict(plan)
    before_approval = dataclasses.asdict(approval)

    claim = build_external_publication_attempt_claim(plan, approval)

    assert dataclasses.asdict(plan) == before_plan
    assert dataclasses.asdict(approval) == before_approval
    assert claim.publication_plan_sha256 == approval.publication_plan_sha256
    assert claim.publication_approval_sha256 == approval.digest


@pytest.mark.parametrize(
    "candidate",
    [SimpleNamespace(), "not-a-claim", 1],
)
def test_claim_serializer_and_digest_require_exact_claim_type(
    candidate: object,
) -> None:
    for function in (
        serialize_external_publication_attempt_claim_canonical,
        external_publication_attempt_claim_canonical_bytes,
        external_publication_attempt_claim_digest,
    ):
        with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
            function(candidate)  # type: ignore[arg-type]
        _assert_claim_error(raised.value, "claim_type")


@pytest.mark.parametrize(
    "candidate",
    [SimpleNamespace(), "not-an-approval", 1],
)
def test_consumption_key_requires_exact_approval_type(candidate: object) -> None:
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        external_publication_consumption_key(candidate)  # type: ignore[arg-type]
    _assert_claim_error(raised.value, "approval_type")


@pytest.mark.parametrize(
    "field,value,classification",
    [
        ("schema_version", "external-publication-attempt.v2", "schema_version"),
        ("consumption_key", "A" * 64, "consumption_key"),
        ("state", "ready", "state"),
        ("provider", "", "claim_binding"),
        ("output_byte_length", -1, "claim_binding"),
    ],
)
def test_claim_model_rejects_invalid_structural_fields(
    field: str,
    value: object,
    classification: str,
) -> None:
    claim = _claim()
    values = claim.__dict__.copy()
    values[field] = value
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        ExternalPublicationAttemptClaim(**values)  # type: ignore[arg-type]
    _assert_claim_error(raised.value, classification)


@pytest.mark.parametrize("constant", (b"NaN", b"Infinity", b"-Infinity"))
def test_loader_rejects_all_nonstandard_json_constants(
    tmp_path: Path,
    constant: bytes,
) -> None:
    claim = _claim()
    canonical = external_publication_attempt_claim_canonical_bytes(claim)
    marker = tmp_path / f"constant-{constant.decode('ascii').replace('-', 'n')}.json"
    marker.write_bytes(
        canonical.replace(
            b'"output_byte_length":17', b'"output_byte_length":' + constant
        )
    )
    with pytest.raises(ExternalPublicationAttemptClaimLoadError) as raised:
        load_external_publication_attempt_claim(marker)
    _assert_load_error(raised.value, "parse")


@pytest.mark.parametrize("bad_key", ["", " " * 64, "g" * 64, "0" * 63])
def test_consumption_key_rejects_noncanonical_keys(
    bad_key: str,
) -> None:
    with pytest.raises(ExternalPublicationAttemptClaimError) as raised:
        external_publication_attempt_claim_path(Path.cwd(), bad_key)
    _assert_persistence_error(raised.value, "consumption_key")
