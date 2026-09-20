# ruff: noqa: E501

"""Focused provider-free regressions for the Phase 303 boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_start_authorization as authorization_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError,
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeStartAuthorization,
    decide_and_persist_external_publication_recovery_resume,
    external_publication_operation_intent_digest,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_digest,
    external_publication_recovery_resume_decision_preparation_digest,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    external_publication_recovery_resume_decision_preparation_start_authorization_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_intent,
    load_external_publication_recovery_resume_decision_preparation_intent_binding,
    load_external_publication_recovery_resume_decision_preparation_start_authorization,
    materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent,
    persist_external_publication_operation_intent,
    persist_external_publication_recovery_resume_decision_preparation_intent_binding,
    persist_external_publication_recovery_resume_decision_preparation_start_authorization,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    prepare_and_persist_external_publication_recovery_resume_decision_lineage,
    serialize_external_publication_recovery_resume_decision_preparation_start_authorization_canonical,
)

_AUTHORIZATION_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-start-authorization.v1"
)
_BINDING_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
)
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_AUTHORIZATION_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-start-authorization-"
)
_INTENT_PREFIX = "external-publication-operation-intent-"
_START_PREFIX = "external-publication-operation-start-"
_DECISION_PREFIX = "external-publication-recovery-resume-decision-"
_PREPARATION_PREFIX = "external-publication-recovery-resume-decision-preparation-"
_BINDING_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-intent-binding-"
)
_SUFFIX = ".json"
_AUTHORIZATION_KEYS = frozenset(
    {
        "decision_preparation_intent_binding_sha256",
        "decision_preparation_sha256",
        "expected_operation_start_sha256",
        "operation",
        "operation_intent_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_decision_sha256",
        "result_kind",
        "result_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_CompatibilityError = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError
_PersistenceError = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError
_ConflictError = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError
)
_Detail = (
    ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationFailureDetail
)


class _Recorder:
    def __init__(
        self,
        result: object = None,
        *,
        fault: BaseException | None = None,
        delegate: object | None = None,
    ) -> None:
        self.result = result
        self.fault = fault
        self.delegate = delegate
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if self.fault is not None:
            raise self.fault
        result = (
            self.delegate(*args, **kwargs)  # type: ignore[operator]
            if self.delegate is not None
            else self.result
        )
        self.results.append(result)
        return result

    @property
    def call_count(self) -> int:
        return len(self.calls)


class _StringChild(str):
    pass


class _PathChild(type(Path())):
    pass


class _FaultyHandle:
    def __init__(self, real: object, stage: str) -> None:
        self._real = real
        self._stage = stage

    def __enter__(self) -> _FaultyHandle:
        return self

    def __exit__(self, *exc: object) -> bool:
        self._real.close()  # type: ignore[attr-defined]
        return False

    def write(self, data: bytes) -> int:
        if self._stage == "write_error":
            raise OSError("write failed")
        if self._stage == "short_write":
            self._real.write(data[:-1])  # type: ignore[attr-defined]
            return len(data) - 1
        self._real.write(data)  # type: ignore[attr-defined]
        return len(data)

    def flush(self) -> None:
        if self._stage == "flush_error":
            raise OSError("flush failed")
        self._real.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        if self._stage == "file_fsync":
            return -1
        return self._real.fileno()  # type: ignore[attr-defined]


class _CloseFaultHandle:
    def __init__(self, real: object) -> None:
        self._real = real

    def write(self, data: bytes) -> int:
        self._real.write(data)  # type: ignore[attr-defined]
        return len(data)

    def flush(self) -> None:
        self._real.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self._real.fileno()  # type: ignore[attr-defined]

    def close(self) -> None:
        raise OSError("close failed")


class _OsShim:
    def __init__(self, stage: str) -> None:
        self._stage = stage

    def __getattr__(self, name: str) -> object:
        if name == "fsync" and self._stage == "file_fsync":

            def _fail(*args: object, **kwargs: object) -> None:
                raise OSError("fsync failed")

            return _fail
        return getattr(os, name)


def _forged(source: object, **overrides: object) -> object:
    value = object.__new__(type(source))
    names = {field.name for field in dataclasses.fields(source)}  # type: ignore[arg-type]
    for name in overrides:
        assert name in names, name
    for name in names:
        object.__setattr__(value, name, getattr(source, name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _intent(
    *,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
    operation: str = "resume",
) -> ExternalPublicationOperationIntent:
    return ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
        operation=operation,  # type: ignore[arg-type]
    )


def _binding(
    *,
    intent_digest: str = "e" * 64,
    preparation_digest: str = "a" * 64,
    decision_digest: str = "b" * 64,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
    source: str = "resume",
    previous: str = "already_acquired",
    recovery: str = "reconciliation_mismatch",
    result_kind: str = "reconciliation",
    result_digest: str | None = "f" * 64,
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    return ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
        schema_version=_BINDING_SCHEMA,
        decision_preparation_sha256=preparation_digest,
        recovery_resume_decision_sha256=decision_digest,
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
        operation_intent_sha256=intent_digest,
        source_operation=source,  # type: ignore[arg-type]
        previous_recovery_kind=previous,  # type: ignore[arg-type]
        recovery_kind=recovery,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_digest,
        operation="resume",
        state="authorized",
    )


def _expected_start(
    intent: ExternalPublicationOperationIntent, intent_digest: str
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=intent.publication_approval_sha256,
        publication_plan_sha256=intent.publication_plan_sha256,
        operation="resume",
        state="started",
    )


def _valid_lineage(
    root: Path,
    *,
    previous: str = "already_acquired",
    recovery: str = "reconciliation_mismatch",
    result_kind: str = "reconciliation",
    result_digest: str | None = "f" * 64,
) -> tuple[
    Path,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationOperationIntent,
    str,
    str,
    ExternalPublicationOperationStart,
    str,
    str,
]:
    intent = _intent()
    intent_digest = external_publication_operation_intent_digest(intent)
    binding = _binding(
        intent_digest=intent_digest,
        previous=previous,
        recovery=recovery,
        result_kind=result_kind,
        result_digest=result_digest,
    )
    binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            binding
        )
    )
    start = _expected_start(intent, intent_digest)
    start_digest = external_publication_operation_start_digest(start)
    return (
        root / "phase-302-binding.json",
        binding,
        intent,
        binding_digest,
        intent_digest,
        start,
        start_digest,
        root / f"{_AUTHORIZATION_PREFIX}{binding_digest}.json",
    )


def _seed_real_phase295_lineage(
    root: Path,
    *,
    previous_recovery_kind: str,
    result_kind: str,
    result_sha256: str | None,
) -> Path:
    """Build only local Phase295/296/298 artifacts for integration."""
    root.mkdir()
    anchor = root / "phase-295-binding.json"
    phase295_binding = ExternalPublicationRecoveryResumeIntentBinding(
        schema_version="external-publication-recovery-resume-intent-binding.v1",
        resume_preparation_sha256="a" * 64,
        recovery_decision_sha256="b" * 64,
        publication_approval_sha256="c" * 64,
        publication_plan_sha256="d" * 64,
        operation_intent_sha256="e" * 64,
        source_operation="resume",
        recovery_kind=previous_recovery_kind,  # type: ignore[arg-type]
        operation="resume",
        state="authorized",
    )
    phase295_digest = external_publication_recovery_resume_intent_binding_digest(
        phase295_binding
    )
    start = ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,
        operation_intent_sha256=phase295_binding.operation_intent_sha256,
        publication_approval_sha256=phase295_binding.publication_approval_sha256,
        publication_plan_sha256=phase295_binding.publication_plan_sha256,
        operation="resume",
        state="started",
    )
    start_digest = external_publication_operation_start_digest(start)
    authorization = ExternalPublicationRecoveryResumeStartAuthorization(
        schema_version="external-publication-recovery-resume-start-authorization.v1",
        resume_intent_binding_sha256=phase295_digest,
        resume_preparation_sha256=phase295_binding.resume_preparation_sha256,
        recovery_decision_sha256=phase295_binding.recovery_decision_sha256,
        operation_intent_sha256=phase295_binding.operation_intent_sha256,
        expected_operation_start_sha256=start_digest,
        publication_approval_sha256=phase295_binding.publication_approval_sha256,
        publication_plan_sha256=phase295_binding.publication_plan_sha256,
        source_operation=phase295_binding.source_operation,
        recovery_kind=phase295_binding.recovery_kind,
        operation="resume",
        state="authorized",
    )
    authorization_digest = (
        external_publication_recovery_resume_start_authorization_digest(authorization)
    )
    authorization_path = root / (
        "external-publication-recovery-resume-start-authorization-"
        f"{phase295_digest}.json"
    )
    start_path = root / (
        f"external-publication-recovery-resume-start-{authorization_digest}.json"
    )
    outcome = ExternalPublicationRecoveryResumeOutcome(
        schema_version="external-publication-recovery-resume-outcome.v1",
        resume_start_authorization_sha256=authorization_digest,
        resume_intent_binding_sha256=phase295_digest,
        operation_intent_sha256=phase295_binding.operation_intent_sha256,
        operation_start_sha256=start_digest,
        publication_approval_sha256=phase295_binding.publication_approval_sha256,
        publication_plan_sha256=phase295_binding.publication_plan_sha256,
        source_operation=phase295_binding.source_operation,
        recovery_kind=phase295_binding.recovery_kind,
        operation="resume",
        state="recovery_required",
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,
    )
    outcome_path = root / (
        f"external-publication-recovery-resume-outcome-{authorization_digest}.json"
    )
    persist_external_publication_recovery_resume_intent_binding(
        anchor, phase295_binding
    )
    persist_external_publication_recovery_resume_start_authorization(
        authorization_path, authorization
    )
    start_path.write_bytes(external_publication_operation_start_canonical_bytes(start))
    persist_external_publication_recovery_resume_outcome(outcome_path, outcome)
    return anchor


def _run_injected(
    root: Path,
    *,
    binding: object | None = None,
    intent: object | None = None,
    binding_digest: object | None = None,
    intent_digest: object | None = None,
    start_digest: object | None = None,
    **overrides: object,
) -> tuple[object, dict[str, _Recorder]]:
    (
        binding_path,
        expected_binding,
        expected_intent,
        expected_binding_digest,
        expected_intent_digest,
        expected_start,
        expected_start_digest,
        _authorization_path,
    ) = _valid_lineage(root)
    binding_value = expected_binding if binding is None else binding
    intent_value = expected_intent if intent is None else intent
    binding_digest_value = (
        expected_binding_digest if binding_digest is None else binding_digest
    )
    intent_digest_value = (
        expected_intent_digest if intent_digest is None else intent_digest
    )
    start_digest_value = expected_start_digest if start_digest is None else start_digest
    recorders = {
        "binding_loader": _Recorder(binding_value),
        "binding_digest": _Recorder(binding_digest_value),
        "intent_loader": _Recorder(intent_value),
        "intent_digest": _Recorder(intent_digest_value),
        "start_digest": _Recorder(start_digest_value),
    }
    result = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path,
        binding_loader=recorders["binding_loader"],
        binding_digest_function=recorders["binding_digest"],
        intent_loader=recorders["intent_loader"],
        intent_digest_function=recorders["intent_digest"],
        start_digest_function=recorders["start_digest"],
        **overrides,
    )
    return result, recorders


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is _CompatibilityError
    assert isinstance(
        error,
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationError,
    )
    assert isinstance(error, ValueError)
    assert str(error) == authorization_module._AUTHORIZATION_ERROR_MESSAGE
    assert type(error.detail) is _Detail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is _PersistenceError
    assert str(error) == authorization_module._PERSISTENCE_ERROR_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError
    )
    assert str(error) == authorization_module._LOAD_ERROR_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict(error: ValueError) -> None:
    assert type(error) is _ConflictError
    assert str(error) == authorization_module._PERSISTENCE_ERROR_MESSAGE
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


def test_public_exports_signature_and_default_dependencies() -> None:
    expected = set(authorization_module.__all__)
    import ai_office.engine as engine

    assert expected <= set(engine.__all__)
    for name in expected:
        assert getattr(engine, name) is getattr(authorization_module, name)

    signature = inspect.signature(
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert list(signature.parameters) == [
        "decision_preparation_intent_binding_path",
        "binding_loader",
        "binding_digest_function",
        "intent_loader",
        "intent_digest_function",
        "start_digest_function",
    ]
    assert (
        signature.parameters["binding_loader"].default
        is authorization_module.load_external_publication_recovery_resume_decision_preparation_intent_binding
    )
    assert (
        signature.parameters["binding_digest_function"].default
        is external_publication_recovery_resume_decision_preparation_intent_binding_digest
    )
    assert (
        signature.parameters["intent_loader"].default
        is authorization_module.load_external_publication_operation_intent
    )
    assert (
        signature.parameters["intent_digest_function"].default
        is external_publication_operation_intent_digest
    )
    assert (
        signature.parameters["start_digest_function"].default
        is external_publication_operation_start_digest
    )
    forbidden_parameters = {
        "operation_intent_path",
        "authorization_path",
        "start_path",
        "expected_start",
        "binding",
        "intent",
    }
    assert not forbidden_parameters.intersection(signature.parameters)


def test_model_has_exact_frozen_fields_and_invariants() -> None:
    assert [
        field.name
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
        )
    ] == [
        "schema_version",
        "decision_preparation_intent_binding_sha256",
        "decision_preparation_sha256",
        "recovery_resume_decision_sha256",
        "operation_intent_sha256",
        "expected_operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "result_kind",
        "result_sha256",
        "operation",
        "state",
    ]
    value = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
        schema_version=_AUTHORIZATION_SCHEMA,
        decision_preparation_intent_binding_sha256="0" * 64,
        decision_preparation_sha256="1" * 64,
        recovery_resume_decision_sha256="2" * 64,
        operation_intent_sha256="3" * 64,
        expected_operation_start_sha256="4" * 64,
        publication_approval_sha256="5" * 64,
        publication_plan_sha256="6" * 64,
        source_operation="resume",
        previous_recovery_kind="already_acquired",
        recovery_kind="reconciliation_mismatch",
        result_kind="reconciliation",
        result_sha256="7" * 64,
        operation="resume",
        state="authorized",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.state = "authorized"  # type: ignore[misc]

    invalid = (
        {"operation": "fresh"},
        {"state": "started"},
        {
            "previous_recovery_kind": "reconciliation_mismatch",
            "source_operation": "fresh",
        },
        {"result_kind": "none", "result_sha256": "f" * 64},
        {
            "result_kind": "none",
            "result_sha256": None,
            "recovery_kind": "reconciliation_mismatch",
        },
        {
            "result_kind": "reconciliation",
            "result_sha256": "f" * 64,
            "recovery_kind": "already_acquired",
        },
        {"decision_preparation_sha256": "A" * 64},
        {"source_operation": _StringChild("resume")},
    )
    for overrides in invalid:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError
        ):
            ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
                **{
                    field.name: overrides.get(field.name, getattr(value, field.name))
                    for field in dataclasses.fields(value)
                }  # type: ignore[arg-type]
            )


def test_canonical_json_has_exact_keys_and_stable_digest() -> None:
    value = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
        schema_version=_AUTHORIZATION_SCHEMA,
        decision_preparation_intent_binding_sha256="0" * 64,
        decision_preparation_sha256="1" * 64,
        recovery_resume_decision_sha256="2" * 64,
        operation_intent_sha256="3" * 64,
        expected_operation_start_sha256="4" * 64,
        publication_approval_sha256="5" * 64,
        publication_plan_sha256="6" * 64,
        source_operation="resume",
        previous_recovery_kind="already_acquired",
        recovery_kind="reconciliation_mismatch",
        result_kind="reconciliation",
        result_sha256="7" * 64,
        operation="resume",
        state="authorized",
    )
    encoded = serialize_external_publication_recovery_resume_decision_preparation_start_authorization_canonical(
        value
    )
    parsed = json.loads(encoded)
    assert frozenset(parsed) == _AUTHORIZATION_KEYS
    assert encoded == json.dumps(
        parsed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    assert (
        authorization_module.external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(
            value
        )
        == encoded.encode()
    )
    digest = external_publication_recovery_resume_decision_preparation_start_authorization_digest(
        value
    )
    assert (
        digest
        == external_publication_recovery_resume_decision_preparation_start_authorization_digest(
            value
        )
    )
    assert len(digest) == 64 and digest == digest.lower()


def test_loader_rejects_duplicate_constants_keys_and_noncanonical(
    tmp_path: Path,
) -> None:
    value = ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
        schema_version=_AUTHORIZATION_SCHEMA,
        decision_preparation_intent_binding_sha256="0" * 64,
        decision_preparation_sha256="1" * 64,
        recovery_resume_decision_sha256="2" * 64,
        operation_intent_sha256="3" * 64,
        expected_operation_start_sha256="4" * 64,
        publication_approval_sha256="5" * 64,
        publication_plan_sha256="6" * 64,
        source_operation="resume",
        previous_recovery_kind="already_acquired",
        recovery_kind="reconciliation_mismatch",
        result_kind="reconciliation",
        result_sha256="7" * 64,
        operation="resume",
        state="authorized",
    )
    path = tmp_path / "authorization.json"
    canonical = authorization_module.external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(
        value
    )
    path.write_bytes(canonical)
    assert (
        load_external_publication_recovery_resume_decision_preparation_start_authorization(
            path
        )
        == value
    )

    cases = {
        "duplicate": b'{"state":"authorized","state":"authorized"}',
        "constant": canonical.replace(b'"result_sha256":"7', b'"result_sha256":NaN'),
        "extra": canonical[:-1] + b',"extra":1}',
        "missing": canonical.replace(b',"state":"authorized"', b""),
        "noncanonical": json.dumps(json.loads(canonical), indent=2).encode(),
    }
    for name, contents in cases.items():
        path.write_bytes(contents)
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationLoadError
        ) as caught:
            load_external_publication_recovery_resume_decision_preparation_start_authorization(
                path
            )
        _assert_load_error(
            caught.value,
            "noncanonical"
            if name == "noncanonical"
            else ("keys" if name in {"extra", "missing"} else "parse"),
        )


def test_append_only_persistence_is_idempotent_and_conflict_unchanged(
    tmp_path: Path,
) -> None:
    _, _, _, _, _, _, _, path = _valid_lineage(tmp_path)
    _, binding, intent, binding_digest, intent_digest, start, start_digest, _ = (
        _valid_lineage(tmp_path)
    )
    authorization = (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
            schema_version=_AUTHORIZATION_SCHEMA,
            decision_preparation_intent_binding_sha256=binding_digest,
            decision_preparation_sha256=binding.decision_preparation_sha256,
            recovery_resume_decision_sha256=binding.recovery_resume_decision_sha256,
            operation_intent_sha256=intent_digest,
            expected_operation_start_sha256=start_digest,
            publication_approval_sha256=intent.publication_approval_sha256,
            publication_plan_sha256=intent.publication_plan_sha256,
            source_operation=binding.source_operation,
            previous_recovery_kind=binding.previous_recovery_kind,
            recovery_kind=binding.recovery_kind,
            result_kind=binding.result_kind,
            result_sha256=binding.result_sha256,
            operation="resume",
            state="authorized",
        )
    )
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        path, authorization
    )
    original = path.read_bytes()
    persist_external_publication_recovery_resume_decision_preparation_start_authorization(
        path, authorization
    )
    assert path.read_bytes() == original

    different = dataclasses.replace(authorization, decision_preparation_sha256="9" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_start_authorization(
            path, different
        )
    _assert_conflict(caught.value)
    assert path.read_bytes() == original

    path.write_bytes(b"partial")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_start_authorization(
            path, authorization
        )
    _assert_conflict(caught.value)
    assert path.read_bytes() == b"partial"
    del intent, start


@pytest.mark.parametrize(
    "stage",
    [
        "write_error",
        "short_write",
        "flush_error",
        "file_fsync",
        "close_failure",
        "dir_fsync",
    ],
)
def test_ambiguous_persistence_retains_artifact_without_retry_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    _, binding, intent, binding_digest, intent_digest, _, start_digest, path = (
        _valid_lineage(tmp_path)
    )
    authorization = (
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization(
            schema_version=_AUTHORIZATION_SCHEMA,
            decision_preparation_intent_binding_sha256=binding_digest,
            decision_preparation_sha256=binding.decision_preparation_sha256,
            recovery_resume_decision_sha256=binding.recovery_resume_decision_sha256,
            operation_intent_sha256=intent_digest,
            expected_operation_start_sha256=start_digest,
            publication_approval_sha256=intent.publication_approval_sha256,
            publication_plan_sha256=intent.publication_plan_sha256,
            source_operation=binding.source_operation,
            previous_recovery_kind=binding.previous_recovery_kind,
            recovery_kind=binding.recovery_kind,
            result_kind=binding.result_kind,
            result_sha256=binding.result_sha256,
            operation="resume",
            state="authorized",
        )
    )
    exclusive_open_calls: list[Path] = []
    if stage == "file_fsync":
        monkeypatch.setattr(authorization_module, "os", _OsShim(stage))
    elif stage == "dir_fsync":

        def _fail(*args: object, **kwargs: object) -> None:
            raise OSError("directory fsync failed")

        monkeypatch.setattr(
            authorization_module, "_fsync_authorization_directory", _fail
        )
    else:
        real_open = Path.open

        def _fake_open(
            candidate: Path, mode: str = "r", *args: object, **kwargs: object
        ) -> object:
            if mode == "xb":
                exclusive_open_calls.append(candidate)
                real = real_open(candidate, mode, buffering=0)
                if stage == "close_failure":
                    return _CloseFaultHandle(real)
                return _FaultyHandle(real, stage)
            return real_open(candidate, mode, *args, **kwargs)

        monkeypatch.setattr(authorization_module.Path, "open", _fake_open)

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_start_authorization(
            path, authorization
        )
    _assert_persistence_error(caught.value, "ambiguous")
    assert path.exists()
    assert isinstance(path.read_bytes(), bytes)
    if stage not in {"file_fsync", "dir_fsync"}:
        assert exclusive_open_calls == [path]


def test_preflight_rejects_noncallable_dependencies_before_loader_or_mutation(
    tmp_path: Path,
) -> None:
    loader = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError
    ) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=tmp_path / "anchor.json",
            binding_loader=loader,
            intent_loader=None,  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "configuration")
    assert loader.call_count == 0
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError
    ) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=_PathChild(
                tmp_path / "anchor.json"
            )
        )
    _assert_error(caught.value, "path_type")
    assert list(tmp_path.iterdir()) == []


def test_boundary_calls_loaders_and_digests_exactly_once_with_exact_identities(
    tmp_path: Path,
) -> None:
    (
        binding_path,
        binding,
        intent,
        binding_digest,
        intent_digest,
        start,
        start_digest,
        authorization_path,
    ) = _valid_lineage(tmp_path)
    binding_loader = _Recorder(binding)
    binding_digest_function = _Recorder(binding_digest)
    intent_loader = _Recorder(intent)
    intent_digest_function = _Recorder(intent_digest)
    start_digest_function = _Recorder(start_digest)

    result = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path,
        binding_loader=binding_loader,
        binding_digest_function=binding_digest_function,
        intent_loader=intent_loader,
        intent_digest_function=intent_digest_function,
        start_digest_function=start_digest_function,
    )
    assert (
        type(result)
        is ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorization
    )
    assert binding_loader.call_count == 1
    assert binding_loader.calls[0][0] == (binding_path,)
    assert binding_digest_function.call_count == 1
    assert binding_digest_function.calls[0][0][0] is binding
    assert intent_loader.call_count == 1
    expected_intent_path = tmp_path / f"{_INTENT_PREFIX}{intent_digest}.json"
    assert intent_loader.calls[0][0] == (expected_intent_path,)
    assert intent_digest_function.call_count == 1
    assert intent_digest_function.calls[0][0][0] is intent
    assert start_digest_function.call_count == 1
    expected_start = _expected_start(intent, intent_digest)
    assert start_digest_function.calls[0][0][0] == expected_start
    assert start_digest_function.calls[0][0][0] is not start
    assert result.decision_preparation_intent_binding_sha256 == binding_digest
    assert result.decision_preparation_sha256 == binding.decision_preparation_sha256
    assert (
        result.recovery_resume_decision_sha256
        == binding.recovery_resume_decision_sha256
    )
    assert result.operation_intent_sha256 == intent_digest
    assert result.expected_operation_start_sha256 == start_digest
    assert result.publication_approval_sha256 == binding.publication_approval_sha256
    assert result.publication_plan_sha256 == binding.publication_plan_sha256
    assert result.previous_recovery_kind == binding.previous_recovery_kind
    assert result.recovery_kind == binding.recovery_kind
    assert result.result_kind == binding.result_kind
    assert result.result_sha256 == binding.result_sha256
    assert authorization_path.exists()
    assert not list(tmp_path.glob(f"{_START_PREFIX}*.json"))


def test_existing_exact_authorization_returns_loaded_object_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = _run_injected(tmp_path)
    (
        binding_path,
        binding,
        intent,
        binding_digest,
        intent_digest,
        _,
        start_digest,
        authorization_path,
    ) = _valid_lineage(tmp_path)
    real_loader = authorization_module.load_external_publication_recovery_resume_decision_preparation_start_authorization
    authorization_loader = _Recorder(delegate=real_loader)
    monkeypatch.setattr(
        authorization_module,
        "load_external_publication_recovery_resume_decision_preparation_start_authorization",
        authorization_loader,
    )
    result, recorders = _run_injected(tmp_path)
    assert result is authorization_loader.results[0]
    assert result is not first
    assert authorization_loader.call_count == 1
    assert authorization_loader.calls[0][0] == (authorization_path,)
    assert recorders["binding_loader"].calls[0][0] == (binding_path,)
    assert recorders["intent_loader"].calls[0][0] == (
        tmp_path / f"{_INTENT_PREFIX}{intent_digest}.json",
    )
    assert binding_digest == result.decision_preparation_intent_binding_sha256
    assert intent_digest == result.operation_intent_sha256
    assert start_digest == result.expected_operation_start_sha256
    del binding, intent


@pytest.mark.parametrize("mismatch", ["digest", "approval", "plan", "operation"])
def test_intent_lineage_mismatch_precedes_start_digest_and_persistence(
    tmp_path: Path, mismatch: str
) -> None:
    (
        _,
        binding,
        expected_intent,
        binding_digest,
        expected_intent_digest,
        _,
        _,
        authorization_path,
    ) = _valid_lineage(tmp_path)
    if mismatch == "digest":
        intent = expected_intent
        intent_digest: object = "9" * 64
    elif mismatch == "approval":
        intent = _intent(approval="8" * 64)
        intent_digest = binding.operation_intent_sha256
    elif mismatch == "plan":
        intent = _intent(plan="8" * 64)
        intent_digest = binding.operation_intent_sha256
    else:
        intent = _intent(operation="fresh")
        intent_digest = binding.operation_intent_sha256

    binding_loader = _Recorder(binding)
    intent_loader = _Recorder(intent)
    start_digest = _Recorder("7" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError
    ) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=tmp_path
            / "phase-302-binding.json",
            binding_loader=binding_loader,
            binding_digest_function=_Recorder(binding_digest),
            intent_loader=intent_loader,
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=start_digest,
        )
    _assert_error(
        caught.value,
        "intent_contract" if mismatch == "operation" else "intent_lineage",
    )
    assert binding_loader.call_count == 1
    assert intent_loader.call_count == 1
    assert start_digest.call_count == 0
    assert not authorization_path.exists()
    del expected_intent, expected_intent_digest


@pytest.mark.parametrize(
    "dependency,expected_error",
    [
        (
            "binding_loader",
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError(
                "load"
            ),
        ),
        (
            "binding_digest",
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError(
                "digest"
            ),
        ),
    ],
)
def test_known_binding_errors_preserve_identity_and_stop_downstream(
    tmp_path: Path,
    dependency: str,
    expected_error: BaseException,
) -> None:
    _, binding, _, binding_digest, _, _, _, _ = _valid_lineage(tmp_path)
    kwargs: dict[str, object] = {
        "binding_loader": _Recorder(binding),
        "binding_digest_function": _Recorder(binding_digest),
        "intent_loader": _Recorder(),
        "intent_digest_function": _Recorder(),
        "start_digest_function": _Recorder(),
    }
    dependency_key = (
        "binding_digest_function" if dependency == "binding_digest" else dependency
    )
    kwargs[dependency_key] = _Recorder(fault=expected_error)
    with pytest.raises(type(expected_error)) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=tmp_path
            / "phase-302-binding.json",
            **kwargs,
        )
    assert caught.value is expected_error
    assert kwargs["intent_loader"].call_count == 0  # type: ignore[union-attr]
    assert kwargs["start_digest_function"].call_count == 0  # type: ignore[union-attr]


def test_known_intent_and_start_errors_preserve_identity(
    tmp_path: Path,
) -> None:
    _, binding, intent, binding_digest, intent_digest, _, _, _ = _valid_lineage(
        tmp_path
    )
    intent_error = ExternalPublicationOperationIntentError("load")
    intent_loader = _Recorder(fault=intent_error)
    with pytest.raises(ExternalPublicationOperationIntentError) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=tmp_path
            / "phase-302-binding.json",
            binding_loader=_Recorder(binding),
            binding_digest_function=_Recorder(binding_digest),
            intent_loader=intent_loader,
            intent_digest_function=_Recorder(),
            start_digest_function=_Recorder(),
        )
    assert caught.value is intent_error

    start_error = ExternalPublicationOperationStartError("digest")
    start_digest = _Recorder(fault=start_error)
    with pytest.raises(ExternalPublicationOperationStartError) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=tmp_path
            / "phase-302-binding.json",
            binding_loader=_Recorder(binding),
            binding_digest_function=_Recorder(binding_digest),
            intent_loader=_Recorder(intent),
            intent_digest_function=_Recorder(intent_digest),
            start_digest_function=start_digest,
        )
    assert caught.value is start_error


def test_unexpected_dependency_errors_are_sanitized_and_downstream_zero(
    tmp_path: Path,
) -> None:
    _, binding, _, binding_digest, _, _, _, _ = _valid_lineage(tmp_path)
    intent_loader = _Recorder(fault=RuntimeError("sensitive dependency detail"))
    start_digest = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationStartAuthorizationCompatibilityError
    ) as caught:
        authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=tmp_path
            / "phase-302-binding.json",
            binding_loader=_Recorder(binding),
            binding_digest_function=_Recorder(binding_digest),
            intent_loader=intent_loader,
            intent_digest_function=_Recorder(),
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "dependency_error")
    assert "sensitive" not in str(caught.value)
    assert start_digest.call_count == 0


def test_existing_partial_noncanonical_and_different_authorization_stay_unchanged(
    tmp_path: Path,
) -> None:
    first, _ = _run_injected(tmp_path)
    _, _, _, _, _, _, _, path = _valid_lineage(tmp_path)
    original = path.read_bytes()
    cases = [
        (b"partial", "parse"),
        (json.dumps(json.loads(original), indent=2).encode(), "noncanonical"),
    ]
    different = dataclasses.replace(first, decision_preparation_sha256="9" * 64)
    cases.append(
        (
            authorization_module.external_publication_recovery_resume_decision_preparation_start_authorization_canonical_bytes(
                different
            ),
            "conflict",
        )
    )
    for contents, classification in cases:
        path.write_bytes(contents)
        with pytest.raises(ValueError) as caught:
            _run_injected(tmp_path)
        if classification == "conflict":
            _assert_conflict(caught.value)
        else:
            _assert_load_error(caught.value, classification)
        assert path.read_bytes() == contents
    path.write_bytes(original)


def test_cycle_separation_allows_repeated_intent_and_start_digests(
    tmp_path: Path,
) -> None:
    intent = _intent()
    intent_digest = external_publication_operation_intent_digest(intent)
    binding_a = _binding(
        intent_digest=intent_digest,
        preparation_digest="a" * 64,
        decision_digest="b" * 64,
    )
    binding_b = _binding(
        intent_digest=intent_digest,
        preparation_digest="8" * 64,
        decision_digest="9" * 64,
    )
    binding_a_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            binding_a
        )
    )
    binding_b_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            binding_b
        )
    )
    start = _expected_start(intent, intent_digest)
    start_digest = external_publication_operation_start_digest(start)
    first = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=tmp_path / "cycle-a-binding.json",
        binding_loader=_Recorder(binding_a),
        binding_digest_function=_Recorder(binding_a_digest),
        intent_loader=_Recorder(intent),
        intent_digest_function=_Recorder(intent_digest),
        start_digest_function=_Recorder(start_digest),
    )
    second = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=tmp_path / "cycle-b-binding.json",
        binding_loader=_Recorder(binding_b),
        binding_digest_function=_Recorder(binding_b_digest),
        intent_loader=_Recorder(intent),
        intent_digest_function=_Recorder(intent_digest),
        start_digest_function=_Recorder(start_digest),
    )
    assert (
        first.operation_intent_sha256 == second.operation_intent_sha256 == intent_digest
    )
    assert (
        first.expected_operation_start_sha256
        == second.expected_operation_start_sha256
        == start_digest
    )
    assert first.decision_preparation_intent_binding_sha256 == binding_a_digest
    assert second.decision_preparation_intent_binding_sha256 == binding_b_digest
    assert first.decision_preparation_sha256 != second.decision_preparation_sha256
    assert len(list(tmp_path.glob(f"{_AUTHORIZATION_PREFIX}*.json"))) == 2


@pytest.mark.parametrize(
    "previous,recovery,result_kind,result_digest",
    [
        ("already_acquired", "reconciliation_mismatch", "reconciliation", "f" * 64),
        ("reconciliation_mismatch", "already_acquired", "none", None),
    ],
)
def test_cross_cycle_recovery_result_provenance_is_preserved(
    tmp_path: Path,
    previous: str,
    recovery: str,
    result_kind: str,
    result_digest: str | None,
) -> None:
    (
        binding_path,
        binding,
        intent,
        binding_digest,
        intent_digest,
        start,
        start_digest,
        _,
    ) = _valid_lineage(
        tmp_path,
        previous=previous,
        recovery=recovery,
        result_kind=result_kind,
        result_digest=result_digest,
    )
    result = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path,
        binding_loader=_Recorder(binding),
        binding_digest_function=_Recorder(binding_digest),
        intent_loader=_Recorder(intent),
        intent_digest_function=_Recorder(intent_digest),
        start_digest_function=_Recorder(start_digest),
    )
    assert result.previous_recovery_kind == previous
    assert result.recovery_kind == recovery
    assert result.result_kind == result_kind
    assert result.result_sha256 == result_digest


def test_real_local_phase302_binding_and_phase289_intent_integration(
    tmp_path: Path,
) -> None:
    (
        binding_path,
        binding,
        intent,
        binding_digest,
        intent_digest,
        start,
        start_digest,
        authorization_path,
    ) = _valid_lineage(tmp_path)
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        binding_path, binding
    )
    intent_path = tmp_path / f"{_INTENT_PREFIX}{intent_digest}.json"
    persist_external_publication_operation_intent(intent_path, intent)

    result = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
        decision_preparation_intent_binding_path=binding_path
    )
    assert result.decision_preparation_intent_binding_sha256 == binding_digest
    assert result.decision_preparation_sha256 == binding.decision_preparation_sha256
    assert (
        result.recovery_resume_decision_sha256
        == binding.recovery_resume_decision_sha256
    )
    assert result.operation_intent_sha256 == intent_digest
    assert result.expected_operation_start_sha256 == start_digest
    assert result.source_operation == binding.source_operation
    assert result.previous_recovery_kind == binding.previous_recovery_kind
    assert result.recovery_kind == binding.recovery_kind
    assert result.result_kind == binding.result_kind
    assert result.result_sha256 == binding.result_sha256
    assert authorization_path.exists()
    assert not (tmp_path / f"{_START_PREFIX}{start_digest}.json").exists()
    del start


def test_real_phase295_to_phase303_integration_preserves_lineage_and_start_boundary(
    tmp_path: Path,
) -> None:
    """Run the real local Phase295 -> Phase303 provider-free lineage twice."""
    cases = (
        (
            "already_acquired",
            "reconciliation",
            "7" * 64,
            "reconciliation_mismatch",
        ),
        ("reconciliation_mismatch", "none", None, "already_acquired"),
    )
    for previous, result_kind, result_sha256, current in cases:
        root = tmp_path / f"{previous}-{result_kind}"
        anchor = _seed_real_phase295_lineage(
            root,
            previous_recovery_kind=previous,
            result_kind=result_kind,
            result_sha256=result_sha256,
        )
        decision = decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=anchor,
            decision="authorize_resume_preparation",
            decided_by="operator@example.test",
            decision_id=f"{previous}-{result_kind}",
        )
        preparation = (
            prepare_and_persist_external_publication_recovery_resume_decision_lineage(
                resume_intent_binding_path=anchor
            )
        )
        phase302_binding = materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(
            resume_intent_binding_path=anchor
        )

        decision_digest = external_publication_recovery_resume_decision_digest(decision)
        preparation_digest = (
            external_publication_recovery_resume_decision_preparation_digest(
                preparation
            )
        )
        binding_digest = external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            phase302_binding
        )
        binding_path = root / f"{_BINDING_PREFIX}{preparation_digest}.json"
        intent_digest = phase302_binding.operation_intent_sha256
        intent_path = root / f"{_INTENT_PREFIX}{intent_digest}.json"
        expected_intent = load_external_publication_operation_intent(intent_path)
        expected_start = _expected_start(expected_intent, intent_digest)
        start_digest = external_publication_operation_start_digest(expected_start)

        before_phase303 = {path.name for path in root.iterdir()}
        before_start_markers = {
            path.name
            for path in root.iterdir()
            if path.name.startswith("external-publication-recovery-resume-start-")
        }
        authorization = authorization_module.authorize_and_persist_external_publication_recovery_resume_decision_preparation_start(
            decision_preparation_intent_binding_path=binding_path
        )
        after_phase303 = {path.name for path in root.iterdir()}
        after_start_markers = {
            path.name
            for path in root.iterdir()
            if path.name.startswith("external-publication-recovery-resume-start-")
        }
        authorization_path = root / (
            f"{_AUTHORIZATION_PREFIX}{binding_digest}{_SUFFIX}"
        )

        assert (
            authorization.decision_preparation_intent_binding_sha256 == binding_digest
        )
        assert authorization.decision_preparation_sha256 == preparation_digest
        assert authorization.recovery_resume_decision_sha256 == decision_digest
        assert authorization.operation_intent_sha256 == intent_digest
        assert authorization.expected_operation_start_sha256 == start_digest
        assert (
            authorization.publication_approval_sha256
            == phase302_binding.publication_approval_sha256
        )
        assert (
            authorization.publication_plan_sha256
            == phase302_binding.publication_plan_sha256
        )
        assert authorization.source_operation == phase302_binding.source_operation
        assert authorization.previous_recovery_kind == previous
        assert authorization.recovery_kind == current
        assert authorization.result_kind == result_kind
        assert authorization.result_sha256 == result_sha256
        assert (
            load_external_publication_recovery_resume_decision_preparation_intent_binding(
                binding_path
            )
            == phase302_binding
        )
        assert authorization_path.exists()
        assert after_phase303 == before_phase303 | {authorization_path.name}
        assert after_start_markers == before_start_markers


def test_source_audit_excludes_acquisition_orchestration_and_ambient_state() -> None:
    source = Path(authorization_module.__file__).read_text()
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    forbidden_imports = {
        "time",
        "random",
        "uuid",
        "socket",
        "subprocess",
        "load_external_publication_operation_start",
        "acquire_external_publication_operation_start",
        "persist_external_publication_operation_start",
        "build_external_publication_operation_intent",
        "materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent",
    }
    assert not forbidden_imports.intersection(imported | imported_from)
    forbidden_source_fragments = (
        ".resolve(",
        ".absolute(",
        "realpath(",
        "normpath(",
        "abspath(",
        "samefile(",
        "readlink(",
        "os.environ",
        "os.getenv",
        "socket.",
        "subprocess.",
        "uuid.",
        "time.",
        "random.",
    )
    assert not any(fragment in source for fragment in forbidden_source_fragments)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "acquire_external_publication_operation_start" not in called_names
    assert "load_external_publication_operation_start" not in called_names
    assert "persist_external_publication_operation_start" not in called_names
