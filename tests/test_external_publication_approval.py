"""Focused provider-free tests for Phase 279 external publication approval."""

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
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as external_module
import ai_office.engine.publication_regeneration_export as export_module
import ai_office.engine.publication_regeneration_export_receipt as receipt_module
import ai_office.engine.publication_regeneration_projection as projection_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationApprovalError,
    ExternalPublicationFailureDetail,
    ExternalPublicationPlan,
    approve_external_publication,
    external_publication_approval_canonical_bytes,
    external_publication_approval_digest,
    external_publication_plan_digest,
    serialize_external_publication_approval_canonical,
    validate_external_publication_approval,
)
from ai_office.engine import (
    publication_regeneration_export_reconciliation as reconciliation_module,
)

_PLAN_SCHEMA = "external-publication-plan.v1"
_PLAN_DIGEST = "d" * 64
_RECEIPT_DIGEST = "b" * 64
_OUTPUT_DIGEST = "a" * 64
_TARGET_DIGEST = "e" * 64


class StringChild(str):
    """A string subclass used to prove exact metadata typing."""


class TruthyApproval:
    """A non-bool value whose truthiness must not be coerced."""

    def __bool__(self) -> bool:
        return True


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id="regen-279-approval",
        reconciliation_evidence_sha256="c" * 64,
        receipt_sha256=_RECEIPT_DIGEST,
        business_output_sha256=_OUTPUT_DIGEST,
        output_byte_length=17,
        provider="future-provider",
        publication_target_sha256=_TARGET_DIGEST,
    )


def _approval(
    plan: ExternalPublicationPlan | None = None,
    *,
    approved_by: str = "人間レビュー者",
    approval_id: str = "approval-279-1",
) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan or _plan(),
        approved_by=approved_by,
        approval_id=approval_id,
    )


def _assert_approval_error(
    error: ValueError,
    classification: str,
) -> None:
    assert type(error) is ExternalPublicationApprovalError
    assert str(error) == "external publication approval is invalid"
    assert type(error.detail) is ExternalPublicationFailureDetail
    assert error.detail.classification == classification


def _forged_instance(
    cls: type[object],
    source: object,
) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def test_valid_approval_has_exact_frozen_fields_and_exact_plan_binding() -> None:
    plan = _plan()
    approval = _approval(plan)

    assert type(approval) is ExternalPublicationApproval
    assert tuple(field.name for field in fields(ExternalPublicationApproval)) == (
        "approved",
        "publication_plan_sha256",
        "approved_by",
        "approval_id",
    )
    assert approval.approved is True
    assert type(approval.approved) is bool
    assert approval.publication_plan_sha256 == external_publication_plan_digest(plan)
    assert approval.approved_by == "人間レビュー者"
    assert approval.approval_id == "approval-279-1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        approval.approved_by = "changed"  # type: ignore[misc]


def test_approval_creation_calls_plan_digest_once_with_exact_plan_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    expected_digest = external_publication_plan_digest(plan)
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        return expected_digest

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    approval = approve_external_publication(
        plan,
        approved_by="reviewer",
        approval_id="approval-1",
    )

    assert calls == [plan]
    assert calls[0] is plan
    assert approval.publication_plan_sha256 == expected_digest


def test_approval_creation_does_not_reload_or_rebuild_phase278_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Phase 279 must not reload or rebuild Phase 278 inputs")

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        forbidden,
    )
    monkeypatch.setattr(external_module, "build_external_publication_plan", forbidden)
    monkeypatch.setattr(
        external_module,
        "validate_external_publication_plan",
        forbidden,
    )

    approval = approve_external_publication(
        plan,
        approved_by="reviewer",
        approval_id="approval-1",
    )

    assert approval.approved is True


@pytest.mark.parametrize("approved", [False, 1, TruthyApproval()])
def test_approval_requires_exact_builtin_true(approved: object) -> None:
    with pytest.raises(ExternalPublicationApprovalError) as raised:
        ExternalPublicationApproval(
            approved=approved,  # type: ignore[arg-type]
            publication_plan_sha256=_PLAN_DIGEST,
            approved_by="reviewer",
            approval_id="approval-1",
        )

    _assert_approval_error(raised.value, "approval_metadata")


@pytest.mark.parametrize(
    "approved_by, classification",
    [
        ("", "approved_by"),
        (" reviewer", "approved_by"),
        ("reviewer ", "approved_by"),
        (StringChild("reviewer"), "approved_by"),
        (1, "approved_by"),
        ("r" * 257, "approved_by"),
        ("reviewer\nsecret", "approval_metadata"),
        ("reviewer\x00secret", "approval_metadata"),
        ("reviewer\ud800", "approval_metadata"),
    ],
)
def test_approval_metadata_approved_by_is_strict_and_uncoerced(
    approved_by: object,
    classification: str,
) -> None:
    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            _plan(),
            approved_by=approved_by,  # type: ignore[arg-type]
            approval_id="approval-1",
        )

    _assert_approval_error(raised.value, classification)


@pytest.mark.parametrize(
    "approval_id, classification",
    [
        ("", "approval_id"),
        (" approval-1", "approval_id"),
        ("approval-1 ", "approval_id"),
        (StringChild("approval-1"), "approval_id"),
        (1, "approval_id"),
        ("a" * 257, "approval_id"),
        ("approval-1\nsecret", "approval_metadata"),
        ("approval-1\x00secret", "approval_metadata"),
        ("approval-1\ud800", "approval_metadata"),
    ],
)
def test_approval_metadata_approval_id_is_strict_and_uncoerced(
    approval_id: object,
    classification: str,
) -> None:
    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            _plan(),
            approved_by="reviewer",
            approval_id=approval_id,  # type: ignore[arg-type]
        )

    _assert_approval_error(raised.value, classification)


def test_invalid_metadata_fails_before_plan_digest_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def digest(value: object) -> str:
        nonlocal calls
        calls += 1
        return _PLAN_DIGEST

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            _plan(),
            approved_by=" reviewer",
            approval_id="approval-1",
        )

    _assert_approval_error(raised.value, "approved_by")
    assert calls == 0


def test_invalid_plan_fails_before_plan_digest_and_without_rebuilding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    forged_plan = _forged_instance(ExternalPublicationPlan, plan)
    object.__setattr__(forged_plan, "provider", "invalid provider")
    calls = 0

    def digest(value: object) -> str:
        nonlocal calls
        calls += 1
        return _PLAN_DIGEST

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            forged_plan,  # type: ignore[arg-type]
            approved_by="reviewer",
            approval_id="approval-1",
        )

    _assert_approval_error(raised.value, "plan")
    assert calls == 0


@pytest.mark.parametrize("candidate", [SimpleNamespace(), "not-a-plan"])
def test_approval_creation_requires_exact_plan_type(candidate: object) -> None:
    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            candidate,  # type: ignore[arg-type]
            approved_by="reviewer",
            approval_id="approval-1",
        )

    _assert_approval_error(raised.value, "plan_type")


def test_subclass_and_attribute_compatible_plan_are_rejected_at_public_boundaries(
) -> None:
    class PlanSubclass(ExternalPublicationPlan):
        pass

    valid = _plan()
    forged_subclass = _forged_instance(PlanSubclass, valid)
    substitute = SimpleNamespace(**dataclasses.asdict(valid))

    for candidate in (forged_subclass, substitute):
        with pytest.raises(ExternalPublicationApprovalError) as raised:
            approve_external_publication(
                candidate,  # type: ignore[arg-type]
                approved_by="reviewer",
                approval_id="approval-1",
            )
        _assert_approval_error(raised.value, "plan_type")

        with pytest.raises(ExternalPublicationApprovalError) as raised:
            validate_external_publication_approval(
                candidate,  # type: ignore[arg-type]
                _approval(valid),
            )
        _assert_approval_error(raised.value, "plan_type")


def test_malformed_plan_digest_result_fails_closed_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        return "A" * 64

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            plan,
            approved_by="reviewer",
            approval_id="approval-1",
        )

    _assert_approval_error(raised.value, "digest")
    assert calls == [plan]
    assert calls[0] is plan


def test_plan_digest_exception_is_sanitized_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        raise RuntimeError(f"private plan {value!r} digest={'a' * 64}")

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        approve_external_publication(
            plan,
            approved_by="private-reviewer",
            approval_id="private-approval",
        )

    _assert_approval_error(raised.value, "digest")
    assert calls == [plan]
    assert calls[0] is plan
    assert "private-reviewer" not in str(raised.value)
    assert "private-approval" not in str(raised.value)
    assert "a" * 64 not in str(raised.value)


def test_approval_canonical_json_is_exact_unicode_four_keys_and_sha256() -> None:
    plan = _plan()
    approval = _approval(
        plan,
        approved_by="人間レビュー者",
        approval_id="承認-279-1",
    )
    expected = json.dumps(
        {
            "approved": True,
            "approved_by": "人間レビュー者",
            "approval_id": "承認-279-1",
            "publication_plan_sha256": plan.digest,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    expected_bytes = expected.encode("utf-8")

    assert serialize_external_publication_approval_canonical(approval) == expected
    assert external_publication_approval_canonical_bytes(approval) == expected_bytes
    assert external_publication_approval_digest(approval) == hashlib.sha256(
        expected_bytes
    ).hexdigest()
    assert json.loads(expected) == {
        "approved": True,
        "approved_by": "人間レビュー者",
        "approval_id": "承認-279-1",
        "publication_plan_sha256": plan.digest,
    }
    assert not expected_bytes.endswith(b"\n")
    assert b"\\u4eba" not in expected_bytes
    assert "destination/primary" not in expected


def test_approval_digest_property_uses_canonical_approval_identity() -> None:
    approval = _approval()

    assert approval.digest == external_publication_approval_digest(approval)


@pytest.mark.parametrize("candidate", [SimpleNamespace(), "not-an-approval"])
def test_serializer_and_digest_require_exact_approval_type(candidate: object) -> None:
    for helper in (
        serialize_external_publication_approval_canonical,
        external_publication_approval_canonical_bytes,
        external_publication_approval_digest,
    ):
        with pytest.raises(ExternalPublicationApprovalError) as raised:
            helper(candidate)  # type: ignore[arg-type]
        _assert_approval_error(raised.value, "approval_type")


def test_forged_approval_subclass_and_attribute_substitute_are_rejected() -> None:
    valid = _approval()

    class ApprovalSubclass(ExternalPublicationApproval):
        pass

    forged_subclass = _forged_instance(ApprovalSubclass, valid)
    substitute = SimpleNamespace(**dataclasses.asdict(valid))
    for candidate in (forged_subclass, substitute):
        with pytest.raises(ExternalPublicationApprovalError) as raised:
            serialize_external_publication_approval_canonical(candidate)  # type: ignore[arg-type]
        _assert_approval_error(raised.value, "approval_type")


def test_validator_calls_plan_digest_once_with_exact_plan_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_digest = external_publication_plan_digest(plan)
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        return expected_digest

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    assert validate_external_publication_approval(plan, approval) is None
    assert calls == [plan]
    assert calls[0] is plan


def test_validator_requires_exact_approval_type_before_plan_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    calls = 0

    def digest(value: object) -> str:
        nonlocal calls
        calls += 1
        return _PLAN_DIGEST

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        validate_external_publication_approval(plan, SimpleNamespace())  # type: ignore[arg-type]

    _assert_approval_error(raised.value, "approval_type")
    assert calls == 0


def test_validator_rejects_invalid_approval_invariants_before_plan_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    valid = _approval(plan)
    forged = _forged_instance(ExternalPublicationApproval, valid)
    object.__setattr__(forged, "approved_by", " invalid")
    calls = 0

    def digest(value: object) -> str:
        nonlocal calls
        calls += 1
        return _PLAN_DIGEST

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        validate_external_publication_approval(
            plan,
            forged,  # type: ignore[arg-type]
        )

    _assert_approval_error(raised.value, "approved_by")
    assert calls == 0


def test_validator_rejects_malformed_digest_result_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    calls: list[object] = []

    def digest(value: object) -> str:
        calls.append(value)
        return "not-a-digest"

    monkeypatch.setattr(external_module, "external_publication_plan_digest", digest)

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        validate_external_publication_approval(plan, approval)

    _assert_approval_error(raised.value, "digest")
    assert calls == [plan]
    assert calls[0] is plan


def test_validator_rejects_plan_binding_mismatch_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = ExternalPublicationApproval(
        approved=True,
        publication_plan_sha256="f" * 64,
        approved_by="private-reviewer",
        approval_id="private-approval",
    )
    expected_digest = external_publication_plan_digest(plan)
    monkeypatch.setattr(
        external_module,
        "external_publication_plan_digest",
        lambda value: expected_digest,
    )

    with pytest.raises(ExternalPublicationApprovalError) as raised:
        validate_external_publication_approval(plan, approval)

    _assert_approval_error(raised.value, "plan_binding")
    assert "private-reviewer" not in str(raised.value)
    assert "private-approval" not in str(raised.value)
    assert "f" * 64 not in str(raised.value)
    assert expected_digest not in str(raised.value)


def test_validator_requires_exact_plan_and_approval_invariants_without_reloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    forbidden_calls = 0

    def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal forbidden_calls
        forbidden_calls += 1
        raise AssertionError("Phase 279 must not access Phase 276 or rebuild Phase 278")

    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        forbidden,
    )
    monkeypatch.setattr(external_module, "build_external_publication_plan", forbidden)
    monkeypatch.setattr(
        external_module,
        "validate_external_publication_plan",
        forbidden,
    )

    validate_external_publication_approval(plan, approval)
    assert forbidden_calls == 0


def test_approval_helpers_do_not_mutate_filesystem_or_use_output_receipt_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan()
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> object:
        calls.append("forbidden")
        raise AssertionError("filesystem or predecessor access is forbidden")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(
        external_module,
        "load_publication_regeneration_export_reconciliation",
        forbidden,
    )
    monkeypatch.setattr(external_module, "build_external_publication_plan", forbidden)
    before = sorted(path.name for path in tmp_path.iterdir())

    approval = _approval(plan)
    serialize_external_publication_approval_canonical(approval)
    external_publication_approval_canonical_bytes(approval)
    external_publication_approval_digest(approval)
    validate_external_publication_approval(plan, approval)

    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert calls == []


def test_approval_boundaries_do_not_call_forbidden_predecessor_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    forbidden_boundaries = (
        (reconciliation_module, "reconcile_publication_regeneration_export"),
        (
            receipt_module,
            "load_publication_regeneration_export_receipt",
        ),
        (
            receipt_module,
            "publication_regeneration_export_receipt_digest",
        ),
        (export_module, "export_publication_regeneration_output"),
        (projection_module, "project_publication_regeneration_output"),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden predecessor boundary was called")

    for module, name in forbidden_boundaries:
        monkeypatch.setattr(module, name, forbidden)
        monkeypatch.setattr(external_module, name, forbidden, raising=False)

    approval = approve_external_publication(
        plan,
        approved_by="reviewer",
        approval_id="approval-predecessor-audit",
    )
    validate_external_publication_approval(plan, approval)
    serialize_external_publication_approval_canonical(approval)
    external_publication_approval_canonical_bytes(approval)
    external_publication_approval_digest(approval)


def test_approval_boundaries_do_not_access_environment_clock_random_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("forbidden runtime access was called")

    with monkeypatch.context() as runtime_guard:
        class ForbiddenEnvironment:
            def __getitem__(self, key: object) -> object:
                forbidden(key)
                return None

            def __contains__(self, key: object) -> bool:
                forbidden(key)
                return False

            def get(self, key: object, default: object = None) -> object:
                forbidden(key, default)
                return None

            def __getattr__(self, name: str) -> object:
                forbidden(name)
                return None

        runtime_guard.setattr(os, "environ", ForbiddenEnvironment())
        runtime_guard.setattr(os, "getenv", forbidden)
        for name in ("putenv", "unsetenv", "urandom"):
            if hasattr(os, name):
                runtime_guard.setattr(os, name, forbidden)

        for name in (
            "time",
            "time_ns",
            "monotonic",
            "monotonic_ns",
            "perf_counter",
            "perf_counter_ns",
            "process_time",
            "process_time_ns",
            "thread_time",
            "thread_time_ns",
            "sleep",
        ):
            if hasattr(time, name):
                runtime_guard.setattr(time, name, forbidden)

        for name in (
            "choice",
            "choices",
            "getrandbits",
            "randint",
            "random",
            "randrange",
            "sample",
            "seed",
            "shuffle",
            "uniform",
            "Random",
            "SystemRandom",
        ):
            if hasattr(random, name):
                runtime_guard.setattr(random, name, forbidden)
        for name in (
            "choice",
            "randbelow",
            "randbits",
            "token_bytes",
            "token_hex",
        ):
            if hasattr(secrets, name):
                runtime_guard.setattr(secrets, name, forbidden)
        for name in ("uuid1", "uuid3", "uuid4", "uuid5"):
            runtime_guard.setattr(uuid, name, forbidden)
        for name in (
            "socket",
            "create_connection",
            "getaddrinfo",
            "gethostbyaddr",
            "gethostbyname",
            "gethostbyname_ex",
            "getnameinfo",
        ):
            if hasattr(socket, name):
                runtime_guard.setattr(socket, name, forbidden)

        approval = approve_external_publication(
            plan,
            approved_by="reviewer",
            approval_id="approval-runtime-audit",
        )
        validate_external_publication_approval(plan, approval)
        canonical = serialize_external_publication_approval_canonical(approval)
        canonical_bytes = external_publication_approval_canonical_bytes(approval)
        digest = external_publication_approval_digest(approval)
        property_digest = approval.digest

    assert canonical_bytes == canonical.encode("utf-8")
    assert digest == hashlib.sha256(canonical_bytes).hexdigest()
    assert property_digest == digest


def test_public_engine_exports_are_available() -> None:
    assert callable(approve_external_publication)
    assert callable(validate_external_publication_approval)
    assert callable(serialize_external_publication_approval_canonical)
    assert callable(external_publication_approval_canonical_bytes)
    assert callable(external_publication_approval_digest)
    assert ExternalPublicationApprovalError.__name__ == (
        "ExternalPublicationApprovalError"
    )
