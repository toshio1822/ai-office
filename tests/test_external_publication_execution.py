"""Focused provider-free tests for the Phase 281 publication execution boundary."""

from __future__ import annotations

import dataclasses
import hashlib
import os
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication_execution as execution_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptAlreadyConsumedError,
    ExternalPublicationAttemptClaim,
    ExternalPublicationExecutionAmbiguousError,
    ExternalPublicationExecutionError,
    ExternalPublicationExecutionResult,
    ExternalPublicationPlan,
    ExternalPublicationTarget,
    ExternalPublicationTransportReceipt,
    approve_external_publication,
    build_external_publication_attempt_claim,
    execute_approved_external_publication,
    external_publication_attempt_claim_digest,
    external_publication_attempt_claim_path,
)

_PLAN_SCHEMA = "external-publication-plan.v1"
_TARGET_SCHEMA = "external-publication-target.v1"
_EVIDENCE_DIGEST = "c" * 64
_RECEIPT_DIGEST = "b" * 64
_TARGET_DIGEST = "e" * 64
_OUTPUT = "exact business output 日本語".encode()
_OUTPUT_DIGEST = hashlib.sha256(_OUTPUT).hexdigest()


def _plan(
    *, output: bytes = _OUTPUT, output_length: int | None = None
) -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version=_PLAN_SCHEMA,
        regeneration_id="regen-281-execution",
        reconciliation_evidence_sha256=_EVIDENCE_DIGEST,
        receipt_sha256=_RECEIPT_DIGEST,
        business_output_sha256=hashlib.sha256(output).hexdigest(),
        output_byte_length=len(output) if output_length is None else output_length,
        provider="future-provider",
        publication_target_sha256=_TARGET_DIGEST,
    )


def _target() -> ExternalPublicationTarget:
    return ExternalPublicationTarget(
        schema_version=_TARGET_SCHEMA,
        provider="future-provider",
        destination_id="destination-281",
    )


def _approval(
    plan: ExternalPublicationPlan | None = None,
) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan or _plan(),
        approved_by="human-reviewer",
        approval_id="approval-281-1",
    )


def _expected(
    plan: ExternalPublicationPlan, approval: ExternalPublicationApproval
) -> tuple[ExternalPublicationAttemptClaim, str]:
    claim = build_external_publication_attempt_claim(plan, approval)
    return claim, external_publication_attempt_claim_digest(claim)


def _install_preflight_stubs(
    monkeypatch: pytest.MonkeyPatch,
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
    expected_claim: ExternalPublicationAttemptClaim,
    expected_digest: str,
) -> dict[str, list[object]]:
    calls: dict[str, list[object]] = {
        "plan": [],
        "approval": [],
        "builder": [],
        "digest": [],
        "claim": [],
    }

    def validate_plan(
        plan_value: object,
        *,
        reconciliation_evidence_path: Path,
        target: ExternalPublicationTarget,
    ) -> None:
        calls["plan"].append((plan_value, reconciliation_evidence_path, target))

    def validate_approval(plan_value: object, approval_value: object) -> None:
        calls["approval"].append((plan_value, approval_value))

    def build_claim(
        plan_value: object, approval_value: object
    ) -> ExternalPublicationAttemptClaim:
        calls["builder"].append((plan_value, approval_value))
        return expected_claim

    def claim_digest(claim_value: object) -> str:
        calls["digest"].append(claim_value)
        return expected_digest

    monkeypatch.setattr(
        execution_module,
        "validate_external_publication_plan",
        validate_plan,
    )
    monkeypatch.setattr(
        execution_module,
        "validate_external_publication_approval",
        validate_approval,
    )
    monkeypatch.setattr(
        execution_module,
        "build_external_publication_attempt_claim",
        build_claim,
    )
    monkeypatch.setattr(
        execution_module,
        "external_publication_attempt_claim_digest",
        claim_digest,
    )
    return calls


def _patch_output_read(
    monkeypatch: pytest.MonkeyPatch,
    output_path: Path,
    output: bytes,
    reads: list[Path],
) -> None:
    original = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == output_path:
            reads.append(path)
            return output
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def _execute_args(
    tmp_path: Path,
    plan: ExternalPublicationPlan,
    approval: ExternalPublicationApproval,
    target: ExternalPublicationTarget,
) -> dict[str, object]:
    output_path = tmp_path / "business-output.bin"
    output_path.write_bytes(_OUTPUT)
    ledger_directory = tmp_path / "ledger"
    ledger_directory.mkdir()
    return {
        "reconciliation_evidence_path": tmp_path / "reconciliation.json",
        "output_path": output_path,
        "ledger_directory": ledger_directory,
        "plan": plan,
        "approval": approval,
        "target": target,
    }


def _assert_execution_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionError
    assert str(error) == "external publication execution is blocked"
    assert type(error.detail.classification) is str
    assert error.detail.classification == classification


def _assert_ambiguous_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionAmbiguousError
    assert str(error) == "external publication execution outcome is ambiguous"
    assert error.detail.classification == classification


def test_valid_execution_has_exact_frozen_result_and_single_boundary_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    target = _target()
    expected_claim, expected_digest = _expected(plan, approval)
    calls = _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    claim_calls: list[tuple[object, object, object]] = []
    real_claim = execution_module.claim_external_publication_attempt

    def claim(
        ledger_directory: Path,
        plan_value: ExternalPublicationPlan,
        approval_value: ExternalPublicationApproval,
    ) -> ExternalPublicationAttemptClaim:
        claim_calls.append((ledger_directory, plan_value, approval_value))
        return real_claim(ledger_directory, plan_value, approval_value)

    monkeypatch.setattr(execution_module, "claim_external_publication_attempt", claim)
    args = _execute_args(tmp_path, plan, approval, target)
    reads: list[Path] = []
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, reads)  # type: ignore[arg-type]
    seen_transport: list[tuple[object, object]] = []

    def transport(
        target_value: object, output_value: bytes
    ) -> ExternalPublicationTransportReceipt:
        seen_transport.append((target_value, output_value))
        return ExternalPublicationTransportReceipt("publication-281-1")

    result = execute_approved_external_publication(
        **args,  # type: ignore[arg-type]
        transport=transport,
    )

    assert type(result) is ExternalPublicationExecutionResult
    assert tuple(field.name for field in fields(result)) == (
        "schema_version",
        "regeneration_id",
        "publication_attempt_claim_sha256",
        "publication_plan_sha256",
        "publication_approval_sha256",
        "business_output_sha256",
        "output_byte_length",
        "provider",
        "publication_target_sha256",
        "publication_id",
        "status",
    )
    assert result == ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id=plan.regeneration_id,
        publication_attempt_claim_sha256=expected_digest,
        publication_plan_sha256=plan.digest,
        publication_approval_sha256=expected_claim.publication_approval_sha256,
        business_output_sha256=plan.business_output_sha256,
        output_byte_length=len(_OUTPUT),
        provider=plan.provider,
        publication_target_sha256=plan.publication_target_sha256,
        publication_id="publication-281-1",
        status="published",
    )
    assert result.status == "published"
    assert calls["plan"] == [
        (args["plan"], args["reconciliation_evidence_path"], args["target"])
    ]
    assert calls["plan"][0][0] is plan
    assert calls["plan"][0][1] is args["reconciliation_evidence_path"]
    assert calls["plan"][0][2] is target
    assert calls["approval"] == [(plan, approval)]
    assert calls["approval"][0][0] is plan
    assert calls["approval"][0][1] is approval
    assert calls["builder"] == [(plan, approval)]
    assert calls["builder"][0][0] is plan
    assert calls["builder"][0][1] is approval
    assert calls["digest"] == [expected_claim]
    assert calls["digest"][0] is expected_claim
    assert len(claim_calls) == 1
    assert claim_calls[0][0] is args["ledger_directory"]
    assert claim_calls[0][1] is plan
    assert claim_calls[0][2] is approval
    assert reads == [args["output_path"]]
    assert len(seen_transport) == 1
    assert seen_transport[0][0] is target
    assert seen_transport[0][1] is _OUTPUT
    marker = external_publication_attempt_claim_path(
        args["ledger_directory"],  # type: ignore[arg-type]
        expected_claim.consumption_key,
    )
    assert marker.exists()
    with pytest.raises(FrozenInstanceError):
        result.status = "other"  # type: ignore[misc]


def test_result_contains_no_raw_output_paths_destination_or_approval_metadata() -> None:
    plan = _plan()
    result = ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id=plan.regeneration_id,
        publication_attempt_claim_sha256="f" * 64,
        publication_plan_sha256="a" * 64,
        publication_approval_sha256="b" * 64,
        business_output_sha256=plan.business_output_sha256,
        output_byte_length=plan.output_byte_length,
        provider=plan.provider,
        publication_target_sha256=plan.publication_target_sha256,
        publication_id="provider-publication-id",
        status="published",
    )
    values = dataclasses.asdict(result)
    assert "output_path" not in values
    assert "reconciliation_evidence_path" not in values
    assert "ledger_directory" not in values
    assert "destination_id" not in values
    assert "approval_id" not in values
    assert "approved_by" not in values
    assert _OUTPUT not in values.values()


@pytest.mark.parametrize(
    "bad_path_factory, classification",
    [
        (lambda path: path.parent / "missing.bin", "output_target"),
        (lambda path: path.parent / "directory.bin", "output_target"),
    ],
)
def test_output_missing_or_directory_fails_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_path_factory,
    classification: str,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    target = _target()
    expected_claim, expected_digest = _expected(plan, approval)
    calls = _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, target)
    if classification == "output_target":
        bad_path = bad_path_factory(args["output_path"])
        if bad_path.name == "directory.bin":
            bad_path.mkdir()
        args["output_path"] = bad_path
    claim_calls: list[object] = []
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *values: claim_calls.append(values),
    )

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: pytest.fail("transport must not run"),
        )
    _assert_execution_error(raised.value, classification)
    assert claim_calls == []
    assert calls["builder"] == [(plan, approval)]


def test_output_symlink_and_nonregular_file_fail_without_read_or_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    output_path = args["output_path"]
    output_path.unlink()  # type: ignore[union-attr]
    real_output = tmp_path / "real-output.bin"
    real_output.write_bytes(_OUTPUT)
    output_path.symlink_to(real_output)  # type: ignore[union-attr]
    claim_calls: list[object] = []
    reads: list[object] = []
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *values: claim_calls.append(values),
    )
    original = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: reads.append(path) or original(path)
    )

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: pytest.fail("transport must not run"),
        )
    _assert_execution_error(raised.value, "output_target")
    assert claim_calls == []
    assert reads == []


def test_output_fifo_is_rejected_without_read_or_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    output_path = args["output_path"]
    output_path.unlink()  # type: ignore[union-attr]
    os.mkfifo(output_path)  # type: ignore[arg-type]
    claim_calls: list[object] = []
    reads: list[object] = []
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *values: claim_calls.append(values),
    )
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: reads.append(path) or original(path),
    )

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: pytest.fail("transport must not run"),
        )
    _assert_execution_error(raised.value, "output_target")
    assert claim_calls == []
    assert reads == []


def test_output_path_requires_exact_concrete_path_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    output_path = args["output_path"]
    args["output_path"] = str(output_path)
    claim_calls: list[object] = []
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *values: claim_calls.append(values),
    )

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: pytest.fail("transport must not run"),
        )
    _assert_execution_error(raised.value, "output_path_type")
    assert claim_calls == []


@pytest.mark.parametrize(
    "bad_output, bad_length, classification",
    [
        (b"different bytes", None, "output_binding"),
        (_OUTPUT, len(_OUTPUT) + 1, "output_binding"),
    ],
)
def test_output_digest_or_length_mismatch_stops_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_output: bytes,
    bad_length: int | None,
    classification: str,
) -> None:
    plan = _plan(output_length=bad_length) if bad_length is not None else _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    reads: list[Path] = []
    _patch_output_read(monkeypatch, args["output_path"], bad_output, reads)  # type: ignore[arg-type]
    claim_calls: list[object] = []
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *values: claim_calls.append(values),
    )

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: pytest.fail("transport must not run"),
        )
    _assert_execution_error(raised.value, classification)
    assert len(reads) == 1
    assert claim_calls == []


def test_non_callable_transport_fails_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    reads: list[Path] = []
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, reads)  # type: ignore[arg-type]
    claim_calls: list[object] = []
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *values: claim_calls.append(values),
    )

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=None,  # type: ignore[arg-type]
        )
    _assert_execution_error(raised.value, "transport")
    assert len(reads) == 1
    assert claim_calls == []


@pytest.mark.parametrize("malformed", [SimpleNamespace(), object()])
def test_malformed_claim_return_stops_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed: object,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, [])  # type: ignore[arg-type]
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *_: malformed,
    )
    transport_calls: list[object] = []

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *values: transport_calls.append(values),
        )
    _assert_execution_error(raised.value, "claim")
    assert transport_calls == []


def test_mismatched_claim_return_stops_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    changed = object.__new__(ExternalPublicationAttemptClaim)
    for field in fields(expected_claim):
        object.__setattr__(changed, field.name, getattr(expected_claim, field.name))
    object.__setattr__(changed, "provider", "other-provider")
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, [])  # type: ignore[arg-type]
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *_: changed,
    )
    transport_calls: list[object] = []

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *values: transport_calls.append(values),
        )
    _assert_execution_error(raised.value, "claim")
    assert transport_calls == []


def test_phase_280_claim_errors_propagate_unchanged_and_transport_is_zero_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, [])  # type: ignore[arg-type]
    original = ExternalPublicationAttemptAlreadyConsumedError("already_consumed")
    monkeypatch.setattr(
        execution_module,
        "claim_external_publication_attempt",
        lambda *_: (_ for _ in ()).throw(original),
    )
    transport_calls: list[object] = []

    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *values: transport_calls.append(values),
        )
    assert raised.value is original
    assert transport_calls == []


def test_transport_exception_is_ambiguous_claim_is_retained_and_retry_is_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    reads: list[Path] = []
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, reads)  # type: ignore[arg-type]
    transport_calls = 0

    def transport(*_: object) -> ExternalPublicationTransportReceipt:
        nonlocal transport_calls
        transport_calls += 1
        raise RuntimeError("provider secret and response must not leak")

    with pytest.raises(ExternalPublicationExecutionAmbiguousError) as raised:
        execute_approved_external_publication(**args, transport=transport)  # type: ignore[arg-type]
    _assert_ambiguous_error(raised.value, "transport_execution")
    assert transport_calls == 1
    assert expected_claim.consumption_key
    marker = external_publication_attempt_claim_path(
        args["ledger_directory"],  # type: ignore[arg-type]
        expected_claim.consumption_key,
    )
    assert marker.exists()

    with pytest.raises(ExternalPublicationAttemptAlreadyConsumedError):
        execute_approved_external_publication(**args, transport=transport)  # type: ignore[arg-type]
    assert transport_calls == 1
    assert len(reads) == 2


def _forged_receipt(publication_id: object) -> ExternalPublicationTransportReceipt:
    receipt = object.__new__(ExternalPublicationTransportReceipt)
    object.__setattr__(receipt, "publication_id", publication_id)
    return receipt


@pytest.mark.parametrize(
    "receipt",
    [
        SimpleNamespace(publication_id="publication-281"),
        _forged_receipt(""),
        _forged_receipt(" publication-281"),
        _forged_receipt("publication-281 "),
        _forged_receipt("x" * 513),
        _forged_receipt("publication\n281"),
        _forged_receipt("publication\x00281"),
        _forged_receipt("publication\ud800"),
    ],
)
def test_invalid_or_attribute_compatible_receipt_is_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt: object,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    expected_claim, expected_digest = _expected(plan, approval)
    _install_preflight_stubs(
        monkeypatch, plan, approval, expected_claim, expected_digest
    )
    args = _execute_args(tmp_path, plan, approval, _target())
    _patch_output_read(monkeypatch, args["output_path"], _OUTPUT, [])  # type: ignore[arg-type]

    with pytest.raises(ExternalPublicationExecutionAmbiguousError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: receipt,
        )
    _assert_ambiguous_error(raised.value, "transport_receipt")
    marker = external_publication_attempt_claim_path(
        args["ledger_directory"],  # type: ignore[arg-type]
        expected_claim.consumption_key,
    )
    assert marker.exists()


def test_transport_receipt_rejects_subclass_and_constructor_is_detail_safe() -> None:
    class ReceiptChild(ExternalPublicationTransportReceipt):
        pass

    with pytest.raises(ExternalPublicationExecutionError) as raised:
        ExternalPublicationTransportReceipt(" publication")
    _assert_execution_error(raised.value, "transport_receipt")

    forged = object.__new__(ReceiptChild)
    object.__setattr__(forged, "publication_id", "publication")
    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execution_module._validate_transport_receipt(forged)
    _assert_execution_error(raised.value, "transport_receipt")


def test_execution_result_constructor_rejects_subclass_and_invalid_identity() -> None:
    class ResultChild(ExternalPublicationExecutionResult):
        pass

    kwargs = {
        "schema_version": "external-publication-execution-result.v1",
        "regeneration_id": "regen-281-execution",
        "publication_attempt_claim_sha256": "f" * 64,
        "publication_plan_sha256": "a" * 64,
        "publication_approval_sha256": "b" * 64,
        "business_output_sha256": _OUTPUT_DIGEST,
        "output_byte_length": len(_OUTPUT),
        "provider": "future-provider",
        "publication_target_sha256": _TARGET_DIGEST,
        "publication_id": "publication-281",
        "status": "published",
    }
    with pytest.raises(ExternalPublicationExecutionError) as raised:
        ResultChild(**kwargs)
    _assert_execution_error(raised.value, "result")

    kwargs["publication_plan_sha256"] = "A" * 64
    with pytest.raises(ExternalPublicationExecutionError) as raised:
        ExternalPublicationExecutionResult(**kwargs)
    _assert_execution_error(raised.value, "result")


def test_preflight_validator_failures_are_fixed_and_transport_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    approval = _approval(plan)
    args = _execute_args(tmp_path, plan, approval, _target())
    calls = {"approval": 0, "claim": 0, "transport": 0}

    def fail_plan(*_: object, **__: object) -> None:
        raise RuntimeError("evidence path and secret must not leak")

    monkeypatch.setattr(
        execution_module, "validate_external_publication_plan", fail_plan
    )
    with pytest.raises(ExternalPublicationExecutionError) as raised:
        execute_approved_external_publication(
            **args,  # type: ignore[arg-type]
            transport=lambda *_: calls.__setitem__("transport", 1),
        )
    _assert_execution_error(raised.value, "plan")
    assert calls == {"approval": 0, "claim": 0, "transport": 0}


def test_public_exports_are_available_without_cli_changes() -> None:
    assert callable(execute_approved_external_publication)
    assert ExternalPublicationTransportReceipt.__name__ == (
        "ExternalPublicationTransportReceipt"
    )
