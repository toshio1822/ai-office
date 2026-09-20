"""Focused provider-free regressions for the Phase 302 boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation_intent_binding as binding_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationIntentPersistenceError,
    ExternalPublicationOperationStart,
    ExternalPublicationRecoveryResumeDecision,
    ExternalPublicationRecoveryResumeDecisionError,
    ExternalPublicationRecoveryResumeDecisionPreparation,
    ExternalPublicationRecoveryResumeDecisionPreparationError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingFailureDetail,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError,
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError,
    ExternalPublicationRecoveryResumeDecisionRequired,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    decide_and_persist_external_publication_recovery_resume,
    external_publication_operation_intent_digest,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_digest,
    external_publication_recovery_resume_decision_preparation_digest,
    external_publication_recovery_resume_decision_preparation_intent_binding_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_intent,
    load_external_publication_recovery_resume_decision_preparation_intent_binding,
    materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent,
    persist_external_publication_operation_intent,
    persist_external_publication_recovery_resume_decision_preparation_intent_binding,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    prepare_and_persist_external_publication_recovery_resume_decision_lineage,
    serialize_external_publication_recovery_resume_decision_preparation_intent_binding_canonical,
)

_BINDING_SCHEMA = (
    "external-publication-recovery-resume-decision-preparation-intent-binding.v1"
)
_DECISION_SCHEMA = "external-publication-recovery-resume-decision.v1"
_DECISION_REQUIRED_SCHEMA = "external-publication-recovery-resume-decision-required.v1"
_PREPARATION_SCHEMA = "external-publication-recovery-resume-decision-preparation.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_DECISION_PREFIX = "external-publication-recovery-resume-decision-"
_PREPARATION_PREFIX = "external-publication-recovery-resume-decision-preparation-"
_BINDING_PREFIX = (
    "external-publication-recovery-resume-decision-preparation-intent-binding-"
)
_INTENT_PREFIX = "external-publication-operation-intent-"
_SUFFIX = ".json"
_MESSAGE = (
    "external publication recovery resume decision preparation intent binding "
    "is blocked"
)
_PERSISTENCE_MESSAGE = (
    "external publication recovery resume decision preparation intent binding "
    "persistence failed"
)
_LOAD_MESSAGE = (
    "external publication recovery resume decision preparation intent binding "
    "could not be loaded"
)
_BINDING_KEYS = frozenset(
    {
        "decision_preparation_sha256",
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
_CompatibilityError = (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
)
_PersistenceError = (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
)
_ConflictError = (
    ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError
)
_Detail = ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingFailureDetail


class _Recorder:
    def __init__(
        self,
        result: object = None,
        *,
        fault: BaseException | None = None,
        delegate: object | None = None,
        events: list[str] | None = None,
        event: str | None = None,
    ) -> None:
        self.result = result
        self.fault = fault
        self.delegate = delegate
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []
        self.events = events
        self.event = event

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if self.events is not None and self.event is not None:
            self.events.append(self.event)
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
    """Wrap one real file and inject one post-create persistence fault."""

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


def _install_binding_persistence_fault(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    if stage == "file_fsync":
        monkeypatch.setattr(binding_module, "os", _OsShim(stage))
        return
    if stage == "dir_fsync":

        def _fail(*args: object, **kwargs: object) -> None:
            raise OSError("directory fsync failed")

        monkeypatch.setattr(binding_module, "_fsync_binding_directory", _fail)
        return

    real_open = Path.open

    def _fake_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode == "xb":
            real = real_open(path, mode, buffering=0)
            if stage == "close_failure":
                return _CloseFaultHandle(real)
            return _FaultyHandle(real, stage)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _fake_open)


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


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is _CompatibilityError
    assert isinstance(
        error, ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError
    )
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert type(error.detail) is _Detail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is _PersistenceError
    assert str(error) == _PERSISTENCE_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError
    )
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict(error: ValueError) -> None:
    assert type(error) is _ConflictError
    assert str(error) == _PERSISTENCE_MESSAGE
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


def _required(**overrides: object) -> ExternalPublicationRecoveryResumeDecisionRequired:
    values: dict[str, object] = {
        "schema_version": _DECISION_REQUIRED_SCHEMA,
        "recovery_resume_outcome_sha256": "0" * 64,
        "resume_start_authorization_sha256": "1" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "operation_intent_sha256": "3" * 64,
        "operation_start_sha256": "4" * 64,
        "publication_approval_sha256": "5" * 64,
        "publication_plan_sha256": "6" * 64,
        "source_operation": "resume",
        "previous_recovery_kind": "already_acquired",
        "recovery_kind": "reconciliation_mismatch",
        "operation": "resume",
        "result_kind": "reconciliation",
        "result_sha256": "7" * 64,
        "state": "decision_required",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecisionRequired(**values)  # type: ignore[arg-type]


def _decision(
    required: ExternalPublicationRecoveryResumeDecisionRequired,
    **overrides: object,
) -> ExternalPublicationRecoveryResumeDecision:
    values: dict[str, object] = {
        field.name: getattr(required, field.name)
        for field in dataclasses.fields(required)
        if field.name != "schema_version" and field.name != "state"
    }
    values.update(
        {
            "schema_version": _DECISION_SCHEMA,
            "decision": "authorize_resume_preparation",
            "decided_by": "operator@example.test",
            "decision_id": "decision-1",
            "state": "decided",
        }
    )
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecision(**values)  # type: ignore[arg-type]


def _preparation(
    decision: ExternalPublicationRecoveryResumeDecision,
    *,
    decision_digest: str = "a" * 64,
    **overrides: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparation:
    values: dict[str, object] = {
        field.name: getattr(decision, field.name)
        for field in dataclasses.fields(decision)
        if field.name
        in {
            "recovery_resume_outcome_sha256",
            "resume_start_authorization_sha256",
            "resume_intent_binding_sha256",
            "operation_intent_sha256",
            "operation_start_sha256",
            "publication_approval_sha256",
            "publication_plan_sha256",
            "source_operation",
            "previous_recovery_kind",
            "recovery_kind",
            "result_kind",
            "result_sha256",
        }
    }
    values.update(
        {
            "schema_version": _PREPARATION_SCHEMA,
            "recovery_resume_decision_sha256": decision_digest,
            "decision": "authorize_resume_preparation",
            "target_operation": "resume",
            "state": "prepared",
        }
    )
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecisionPreparation(**values)  # type: ignore[arg-type]


def _binding(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding:
    values: dict[str, object] = {
        "schema_version": _BINDING_SCHEMA,
        "decision_preparation_sha256": "a" * 64,
        "recovery_resume_decision_sha256": "b" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation_intent_sha256": "e" * 64,
        "source_operation": "resume",
        "previous_recovery_kind": "already_acquired",
        "recovery_kind": "reconciliation_mismatch",
        "result_kind": "reconciliation",
        "result_sha256": "f" * 64,
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
        **values  # type: ignore[arg-type]
    )


def _paths(
    root: Path, *, outcome: str, decision: str, preparation: str, intent: str
) -> dict[str, Path]:
    anchor = root / "phase-295-binding.json"
    return {
        "anchor": anchor,
        "decision": root / f"{_DECISION_PREFIX}{outcome}.json",
        "preparation": root / f"{_PREPARATION_PREFIX}{decision}.json",
        "binding": root / f"{_BINDING_PREFIX}{preparation}.json",
        "intent": root / f"{_INTENT_PREFIX}{intent}.json",
    }


def _seed_real_phase295_lineage(
    root: Path,
    *,
    previous_recovery_kind: str,
    result_kind: str,
    result_sha256: str | None,
) -> Path:
    """Build only local durable Phase 295/296/298 artifacts for integration."""
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
        schema_version="external-publication-operation-start.v1",
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
        external_publication_recovery_resume_start_authorization_digest(  # noqa: E501
            authorization
        )
    )
    authorization_path = root / (
        "external-publication-recovery-resume-start-authorization-"
        f"{phase295_digest}.json"
    )
    start_path = root / (
        f"external-publication-recovery-resume-start-{authorization_digest}.json"
    )
    outcome_path = root / (
        f"external-publication-recovery-resume-outcome-{authorization_digest}.json"
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
    persist_external_publication_recovery_resume_intent_binding(
        anchor, phase295_binding
    )
    persist_external_publication_recovery_resume_start_authorization(
        authorization_path, authorization
    )
    start_path.write_bytes(external_publication_operation_start_canonical_bytes(start))
    persist_external_publication_recovery_resume_outcome(outcome_path, outcome)
    return anchor


def _orchestrate(
    tmp_path: Path,
    *,
    required: ExternalPublicationRecoveryResumeDecisionRequired | object | None = None,
    decision: ExternalPublicationRecoveryResumeDecision | object | None = None,
    preparation: ExternalPublicationRecoveryResumeDecisionPreparation
    | object
    | None = None,
    phase299: _Recorder | None = None,
    decision_loader: _Recorder | None = None,
    decision_digest: _Recorder | None = None,
    preparation_loader: _Recorder | None = None,
    preparation_digest: _Recorder | None = None,
    intent_loader: _Recorder | None = None,
    intent_digest: _Recorder | None = None,
    intent_persist: _Recorder | None = None,
    **kwargs: object,
) -> object:
    required_value = _required() if required is None else required
    decision_value = (
        _decision(required_value)  # type: ignore[arg-type]
        if decision is None
        else decision
    )
    preparation_value = (
        _preparation(_decision(required_value))  # type: ignore[arg-type]
        if preparation is None
        else preparation
    )
    phase299 = phase299 or _Recorder(required_value)
    decision_loader = decision_loader or _Recorder(decision_value)
    decision_digest = decision_digest or _Recorder("a" * 64)
    preparation_loader = preparation_loader or _Recorder(preparation_value)
    preparation_digest = preparation_digest or _Recorder("b" * 64)
    intent_digest = intent_digest or _Recorder("c" * 64)
    intent_persist = intent_persist or _Recorder()
    return materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(  # noqa: E501
        resume_intent_binding_path=tmp_path / "phase-295-binding.json",
        phase299_function=phase299,
        decision_loader=decision_loader,
        decision_digest_function=decision_digest,
        preparation_loader=preparation_loader,
        preparation_digest_function=preparation_digest,
        intent_loader=intent_loader or _Recorder(),
        intent_digest_function=intent_digest,
        intent_persist_function=intent_persist,
        **kwargs,
    )


def test_public_exports_signature_and_default_dependencies() -> None:
    expected = {
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding",
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError",
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError",
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingError",
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingFailureDetail",
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError",
        "ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError",
        "external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes",
        "external_publication_recovery_resume_decision_preparation_intent_binding_digest",
        "load_external_publication_recovery_resume_decision_preparation_intent_binding",
        "materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent",
        "persist_external_publication_recovery_resume_decision_preparation_intent_binding",
        "serialize_external_publication_recovery_resume_decision_preparation_intent_binding_canonical",
    }
    import ai_office.engine as engine

    assert expected <= set(engine.__all__)
    for name in expected:
        assert getattr(engine, name) is getattr(binding_module, name)

    signature = inspect.signature(
        materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert list(signature.parameters) == [
        "resume_intent_binding_path",
        "phase299_function",
        "decision_loader",
        "decision_digest_function",
        "preparation_loader",
        "preparation_digest_function",
        "intent_loader",
        "intent_digest_function",
        "intent_persist_function",
    ]
    assert (
        signature.parameters["phase299_function"].default
        is binding_module.route_external_publication_recovery_resume_outcome
    )
    assert (
        signature.parameters["decision_loader"].default
        is binding_module.load_external_publication_recovery_resume_decision
    )
    assert (
        signature.parameters["decision_digest_function"].default
        is external_publication_recovery_resume_decision_digest
    )
    assert (
        signature.parameters["preparation_loader"].default
        is binding_module.load_external_publication_recovery_resume_decision_preparation
    )
    assert (
        signature.parameters["preparation_digest_function"].default
        is external_publication_recovery_resume_decision_preparation_digest
    )
    assert (
        signature.parameters["intent_loader"].default
        is load_external_publication_operation_intent
    )
    assert (
        signature.parameters["intent_digest_function"].default
        is external_publication_operation_intent_digest
    )
    assert (
        signature.parameters["intent_persist_function"].default
        is persist_external_publication_operation_intent
    )


def test_model_has_exact_frozen_fields_and_invariants() -> None:
    assert [
        field.name
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
        )
    ] == [
        "schema_version",
        "decision_preparation_sha256",
        "recovery_resume_decision_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "operation_intent_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "result_kind",
        "result_sha256",
        "operation",
        "state",
    ]
    value = _binding()
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.state = "authorized"  # type: ignore[misc]

    invalid = (
        {"operation": "fresh"},
        {"state": "prepared"},
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
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
        ):
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding(
                **{
                    field.name: overrides.get(field.name, getattr(value, field.name))
                    for field in dataclasses.fields(value)
                }  # type: ignore[arg-type]
            )


def test_canonical_json_has_exact_keys_and_stable_digest() -> None:
    value = _binding()
    encoded = serialize_external_publication_recovery_resume_decision_preparation_intent_binding_canonical(  # noqa: E501
        value
    )
    parsed = json.loads(encoded)
    assert frozenset(parsed) == _BINDING_KEYS
    assert encoded == json.dumps(
        parsed,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    assert (
        binding_module.external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(
            value
        )
        == encoded.encode()
    )
    digest = binding_module.external_publication_recovery_resume_decision_preparation_intent_binding_digest(  # noqa: E501
        value
    )
    assert (
        digest
        == binding_module.external_publication_recovery_resume_decision_preparation_intent_binding_digest(  # noqa: E501
            value
        )
    )
    assert len(digest) == 64 and digest == digest.lower()


def test_loader_rejects_duplicate_constants_keys_and_noncanonical(
    tmp_path: Path,
) -> None:
    value = _binding()
    path = tmp_path / "binding.json"
    canonical = binding_module.external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(  # noqa: E501
        value
    )
    path.write_bytes(canonical)
    assert (
        load_external_publication_recovery_resume_decision_preparation_intent_binding(
            path
        )
        == value
    )

    cases = {
        "duplicate": b'{"state":"authorized","state":"authorized"}',
        "constant": canonical.replace(b'"result_sha256":"f', b'"result_sha256":NaN'),
        "extra": canonical[:-1] + b',"extra":1}',
        "missing": canonical.replace(b',"state":"authorized"', b""),
        "noncanonical": json.dumps(json.loads(canonical), indent=2).encode(),
    }
    for name, contents in cases.items():
        path.write_bytes(contents)
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingLoadError
        ) as caught:
            load_external_publication_recovery_resume_decision_preparation_intent_binding(
                path
            )
        _assert_load_error(
            caught.value,
            "noncanonical"
            if name == "noncanonical"
            else ("keys" if name in {"extra", "missing"} else "parse"),
        )


def test_append_only_persistence_is_idempotent_and_conflict_is_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "binding.json"
    value = _binding()
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        path, value
    )
    original = path.read_bytes()
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        path, value
    )
    assert path.read_bytes() == original

    different = _binding(publication_plan_sha256="0" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_intent_binding(
            path, different
        )
    _assert_conflict(caught.value)
    assert path.read_bytes() == original

    path.write_bytes(b"partial")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_intent_binding(
            path, value
        )
    _assert_conflict(caught.value)
    assert path.read_bytes() == b"partial"


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
def test_ambiguous_binding_persistence_retains_artifact_without_retry_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / f"{stage}.json"
    value = _binding()
    exclusive_open_calls: list[Path] = []
    if stage not in {"file_fsync", "dir_fsync"}:
        real_open = Path.open

        def _recording_open(
            candidate: Path, mode: str = "r", *args: object, **kwargs: object
        ) -> object:
            if mode == "xb":
                exclusive_open_calls.append(candidate)
            return real_open(candidate, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _recording_open)
    _install_binding_persistence_fault(monkeypatch, stage)

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_intent_binding(
            path, value
        )
    _assert_persistence_error(caught.value, "ambiguous")
    assert path.exists()
    assert isinstance(path.read_bytes(), bytes)
    if stage not in {"file_fsync", "dir_fsync"}:
        assert exclusive_open_calls == [path]


def test_intent_persistence_failure_keeps_new_binding_and_does_not_retry(
    tmp_path: Path,
) -> None:
    fault = ExternalPublicationOperationIntentPersistenceError("ambiguous")
    intent_persist = _Recorder(fault=fault)
    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as caught:
        _orchestrate(tmp_path, intent_persist=intent_persist)
    assert caught.value is fault
    assert intent_persist.call_count == 1
    binding_paths = list(tmp_path.glob(f"{_BINDING_PREFIX}*.json"))
    assert len(binding_paths) == 1
    binding_bytes = binding_paths[0].read_bytes()
    assert (
        binding_bytes
        == binding_module.external_publication_recovery_resume_decision_preparation_intent_binding_canonical_bytes(  # noqa: E501
            _binding(
                decision_preparation_sha256="b" * 64,
                recovery_resume_decision_sha256="a" * 64,
                publication_approval_sha256="5" * 64,
                publication_plan_sha256="6" * 64,
                operation_intent_sha256="c" * 64,
                result_sha256="7" * 64,
            )
        )
    )


def test_persistence_rejects_path_types_and_parent_or_target(tmp_path: Path) -> None:
    value = _binding()
    for path in (str(tmp_path / "x"), _PathChild(tmp_path / "x")):
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
        ) as caught:
            persist_external_publication_recovery_resume_decision_preparation_intent_binding(
                path, value
            )  # type: ignore[arg-type]
        _assert_persistence_error(caught.value, "path_type")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_intent_binding(
            tmp_path / "missing" / "x", value
        )
    _assert_persistence_error(caught.value, "parent")
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation_intent_binding(
            directory, value
        )
    _assert_persistence_error(caught.value, "target")


def test_preflight_rejects_noncallable_dependencies_before_phase299(
    tmp_path: Path,
) -> None:
    phase299 = _Recorder(_required())
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(
            resume_intent_binding_path=tmp_path / "anchor.json",
            phase299_function=phase299,
            decision_loader=None,  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "configuration")
    assert phase299.call_count == 0
    assert list(tmp_path.iterdir()) == []

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(
            resume_intent_binding_path=_PathChild(tmp_path / "anchor.json"),
        )
    _assert_error(caught.value, "path_type")


def test_phase299_is_called_once_with_only_anchor_path(tmp_path: Path) -> None:
    required = _required()
    phase299 = _Recorder(required)
    result = _orchestrate(tmp_path, phase299=phase299)
    assert (
        type(result)
        is ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    )
    assert phase299.call_count == 1
    assert phase299.calls[0][0] == ()
    assert phase299.calls[0][1] == {
        "resume_intent_binding_path": tmp_path / "phase-295-binding.json"
    }


def test_authorize_route_has_exact_paths_identity_and_binding_first_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    events: list[str] = []
    phase299 = _Recorder(required, events=events, event="phase299")
    decision_loader = _Recorder(decision, events=events, event="decision_load")
    decision_digest = _Recorder("a" * 64, events=events, event="decision_digest")
    preparation_loader = _Recorder(preparation, events=events, event="preparation_load")
    preparation_digest = _Recorder("b" * 64, events=events, event="preparation_digest")
    intent_digest = _Recorder("c" * 64, events=events, event="intent_digest")
    binding_persist = _Recorder(events=events, event="binding_persist")
    intent_persist = _Recorder(events=events, event="intent_persist")
    monkeypatch.setattr(
        binding_module,
        "persist_external_publication_recovery_resume_decision_preparation_intent_binding",
        binding_persist,
    )

    result = materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(  # noqa: E501
        resume_intent_binding_path=tmp_path / "anchor.json",
        phase299_function=phase299,
        decision_loader=decision_loader,
        decision_digest_function=decision_digest,
        preparation_loader=preparation_loader,
        preparation_digest_function=preparation_digest,
        intent_loader=_Recorder(events=events, event="intent_load"),
        intent_digest_function=intent_digest,
        intent_persist_function=intent_persist,
    )
    assert (
        type(result)
        is ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    )
    assert (
        phase299.call_count
        == decision_loader.call_count
        == preparation_loader.call_count
        == 1
    )
    assert (
        decision_digest.call_count
        == preparation_digest.call_count
        == intent_digest.call_count
        == 1
    )
    assert decision_digest.calls[0][0][0] is decision
    assert preparation_digest.calls[0][0][0] is preparation
    expected_intent = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation="resume",
    )
    assert intent_digest.calls[0][0][0] == expected_intent
    assert intent_persist.call_count == 1
    assert binding_persist.call_count == 1
    binding_path, persisted_binding = binding_persist.calls[0][0]
    assert binding_path == tmp_path / f"{_BINDING_PREFIX}{'b' * 64}.json"
    assert persisted_binding is result
    intent_path, persisted_intent = intent_persist.calls[0][0]
    assert intent_path == tmp_path / f"{_INTENT_PREFIX}{'c' * 64}.json"
    assert persisted_intent == expected_intent
    assert events.index("binding_persist") < events.index("intent_persist")
    assert "intent_load" not in events
    assert phase299.calls[0][1] == {
        "resume_intent_binding_path": tmp_path / "anchor.json"
    }


def test_completed_route_is_terminal_and_all_later_calls_are_zero(
    tmp_path: Path,
) -> None:
    completed = ExternalPublicationRecoveryResumeOutcome(
        schema_version="external-publication-recovery-resume-outcome.v1",
        resume_start_authorization_sha256="1" * 64,
        resume_intent_binding_sha256="2" * 64,
        operation_intent_sha256="3" * 64,
        operation_start_sha256="4" * 64,
        publication_approval_sha256="5" * 64,
        publication_plan_sha256="6" * 64,
        source_operation="resume",
        recovery_kind="reconciliation_mismatch",
        operation="resume",
        state="completed",
        result_kind="reconciliation",
        result_sha256="7" * 64,
    )
    phase299 = _Recorder(completed)
    decision_loader = _Recorder()
    decision_digest = _Recorder()
    preparation_loader = _Recorder()
    preparation_digest = _Recorder()
    intent_digest = _Recorder()
    intent_persist = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(
            resume_intent_binding_path=tmp_path / "anchor.json",
            phase299_function=phase299,
            decision_loader=decision_loader,
            decision_digest_function=decision_digest,
            preparation_loader=preparation_loader,
            preparation_digest_function=preparation_digest,
            intent_loader=_Recorder(),
            intent_digest_function=intent_digest,
            intent_persist_function=intent_persist,
        )
    _assert_error(caught.value, "materialization_not_required")
    assert phase299.call_count == 1
    assert decision_loader.call_count == decision_digest.call_count == 0
    assert preparation_loader.call_count == preparation_digest.call_count == 0
    assert intent_digest.call_count == intent_persist.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_stop_route_is_terminal_without_decision_digest_or_later_work(
    tmp_path: Path,
) -> None:
    required = _required()
    decision = _decision(required, decision="stop")
    phase299 = _Recorder(required)
    decision_loader = _Recorder(decision)
    decision_digest = _Recorder()
    preparation_loader = _Recorder()
    preparation_digest = _Recorder()
    intent_digest = _Recorder()
    intent_persist = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(
            resume_intent_binding_path=tmp_path / "anchor.json",
            phase299_function=phase299,
            decision_loader=decision_loader,
            decision_digest_function=decision_digest,
            preparation_loader=preparation_loader,
            preparation_digest_function=preparation_digest,
            intent_loader=_Recorder(),
            intent_digest_function=intent_digest,
            intent_persist_function=intent_persist,
        )
    _assert_error(caught.value, "materialization_not_authorized")
    assert decision_loader.call_count == 1
    assert decision_digest.call_count == 0
    assert preparation_loader.call_count == preparation_digest.call_count == 0
    assert intent_digest.call_count == intent_persist.call_count == 0


@pytest.mark.parametrize(
    "field_name",
    [
        "recovery_resume_outcome_sha256",
        "resume_start_authorization_sha256",
        "resume_intent_binding_sha256",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "result_kind",
        "result_sha256",
    ],
)
def test_phase299_to_phase300_lineage_mismatch_precedes_decision_digest(
    tmp_path: Path, field_name: str
) -> None:
    required = _required()
    decision = _decision(required)
    replacement: object = "8" * 64
    if field_name == "source_operation":
        replacement = "fresh"
    elif field_name == "previous_recovery_kind":
        replacement = "reconciliation_mismatch"
    elif field_name == "recovery_kind":
        replacement = "already_acquired"
    elif field_name == "result_kind":
        replacement = "none"
    elif field_name == "result_sha256":
        replacement = None
    mismatched = _forged(decision, **{field_name: replacement})
    decision_digest = _Recorder("a" * 64)
    preparation_loader = _Recorder()
    preparation_digest = _Recorder()
    intent_digest = _Recorder()
    intent_persist = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        _orchestrate(
            tmp_path,
            required=required,
            decision=mismatched,
            decision_digest=decision_digest,
            preparation_loader=preparation_loader,
            preparation_digest=preparation_digest,
            intent_digest=intent_digest,
            intent_persist=intent_persist,
        )
    expected_classification = (
        "predecessor_contract"
        if field_name in {"recovery_kind", "result_kind", "result_sha256"}
        else "predecessor_lineage"
    )
    _assert_error(caught.value, expected_classification)
    assert decision_digest.call_count == 0
    assert preparation_loader.call_count == preparation_digest.call_count == 0
    assert intent_digest.call_count == intent_persist.call_count == 0


@pytest.mark.parametrize(
    "field_name",
    [
        "recovery_resume_decision_sha256",
        "recovery_resume_outcome_sha256",
        "resume_start_authorization_sha256",
        "resume_intent_binding_sha256",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "result_kind",
        "result_sha256",
        "decision",
        "target_operation",
        "state",
    ],
)
def test_phase300_to_phase301_lineage_mismatch_precedes_preparation_digest(
    tmp_path: Path, field_name: str
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    replacement: object = "9" * 64
    if field_name == "source_operation":
        replacement = "fresh"
    elif field_name == "previous_recovery_kind":
        replacement = "reconciliation_mismatch"
    elif field_name == "recovery_kind":
        replacement = "already_acquired"
    elif field_name == "result_kind":
        replacement = "none"
    elif field_name == "result_sha256":
        replacement = None
    elif field_name == "decision":
        replacement = "stop"
    elif field_name == "target_operation":
        replacement = "fresh"
    elif field_name == "state":
        replacement = "decided"
    mismatched = _forged(preparation, **{field_name: replacement})
    preparation_digest = _Recorder("b" * 64)
    intent_digest = _Recorder()
    intent_persist = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        _orchestrate(
            tmp_path,
            required=required,
            decision=decision,
            preparation=mismatched,
            preparation_digest=preparation_digest,
            intent_digest=intent_digest,
            intent_persist=intent_persist,
        )
    expected_classification = (
        "predecessor_contract"
        if field_name in {"decision", "target_operation", "state"}
        or (
            field_name in {"recovery_kind", "result_kind", "result_sha256"}
            and replacement in {"already_acquired", "none", None}
        )
        else "predecessor_lineage"
    )
    _assert_error(caught.value, expected_classification)
    assert preparation_digest.call_count == 0
    assert intent_digest.call_count == intent_persist.call_count == 0


def test_expected_intent_is_direct_resume_construction_and_digest_once(
    tmp_path: Path,
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    intent_digest = _Recorder("c" * 64)
    intent_persist = _Recorder()
    result = _orchestrate(
        tmp_path,
        required=required,
        decision=decision,
        preparation=preparation,
        intent_digest=intent_digest,
        intent_persist=intent_persist,
    )
    expected = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation="resume",
    )
    assert intent_digest.call_count == 1
    assert intent_digest.calls[0][0][0] is not None
    assert intent_digest.calls[0][0][0] == expected
    assert result.operation_intent_sha256 == "c" * 64
    assert result.operation == "resume"
    assert intent_persist.calls[0][0][1] == expected


def test_existing_identical_prior_cycle_intent_is_accepted_only_after_new_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    expected_intent = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation="resume",
    )
    intent_digest = external_publication_operation_intent_digest(expected_intent)
    intent_path = tmp_path / f"{_INTENT_PREFIX}{intent_digest}.json"
    persist_external_publication_operation_intent(intent_path, expected_intent)
    events: list[str] = []
    real_binding_persist = binding_module.persist_external_publication_recovery_resume_decision_preparation_intent_binding  # noqa: E501
    binding_persist = _Recorder(
        delegate=real_binding_persist, events=events, event="binding_persist"
    )
    intent_loader = _Recorder(
        delegate=load_external_publication_operation_intent,
        events=events,
        event="intent_load",
    )
    monkeypatch.setattr(
        binding_module,
        "persist_external_publication_recovery_resume_decision_preparation_intent_binding",
        binding_persist,
    )
    intent_persist = _Recorder(events=events, event="intent_persist")
    result = _orchestrate(
        tmp_path,
        required=required,
        decision=decision,
        preparation=preparation,
        intent_loader=intent_loader,
        intent_persist=intent_persist,
        intent_digest=_Recorder(intent_digest),
    )
    assert (
        type(result)
        is ExternalPublicationRecoveryResumeDecisionPreparationIntentBinding
    )
    assert binding_persist.call_count == 1
    assert intent_loader.call_count == 1
    assert intent_persist.call_count == 0
    assert events.index("binding_persist") < events.index("intent_load")
    assert (tmp_path / f"{_BINDING_PREFIX}{'b' * 64}.json").is_file()


def test_existing_different_binding_fails_before_intent_calls(tmp_path: Path) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    binding_path = tmp_path / f"{_BINDING_PREFIX}{'b' * 64}.json"
    different = _binding(
        decision_preparation_sha256="b" * 64,
        recovery_resume_decision_sha256="c" * 64,
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation_intent_sha256="d" * 64,
    )
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        binding_path, different
    )
    original = binding_path.read_bytes()
    intent_loader = _Recorder()
    intent_persist = _Recorder()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingConflictError
    ) as caught:
        _orchestrate(
            tmp_path,
            required=required,
            decision=decision,
            preparation=preparation,
            intent_loader=intent_loader,
            intent_persist=intent_persist,
        )
    _assert_conflict(caught.value)
    assert intent_loader.call_count == intent_persist.call_count == 0
    assert binding_path.read_bytes() == original


def test_binding_persistence_failure_or_ambiguity_leaves_intent_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fault = ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError(  # noqa: E501
        "ambiguous"
    )
    binding_persist = _Recorder(fault=fault)
    intent_loader = _Recorder()
    intent_persist = _Recorder()
    monkeypatch.setattr(
        binding_module,
        "persist_external_publication_recovery_resume_decision_preparation_intent_binding",
        binding_persist,
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingPersistenceError
    ) as caught:
        _orchestrate(
            tmp_path,
            intent_loader=intent_loader,
            intent_persist=intent_persist,
        )
    assert caught.value is fault
    assert binding_persist.call_count == 1
    assert intent_loader.call_count == intent_persist.call_count == 0


def test_existing_exact_binding_returns_loader_identity_and_then_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    expected = _binding(
        decision_preparation_sha256="b" * 64,
        recovery_resume_decision_sha256="a" * 64,
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation_intent_sha256="c" * 64,
        result_sha256="7" * 64,
    )
    binding_path = tmp_path / f"{_BINDING_PREFIX}{'b' * 64}.json"
    persist_external_publication_recovery_resume_decision_preparation_intent_binding(
        binding_path, expected
    )
    loaded = _binding(
        decision_preparation_sha256="b" * 64,
        recovery_resume_decision_sha256="a" * 64,
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation_intent_sha256="c" * 64,
        result_sha256="7" * 64,
    )
    binding_loader = _Recorder(loaded)
    monkeypatch.setattr(
        binding_module,
        "load_external_publication_recovery_resume_decision_preparation_intent_binding",
        binding_loader,
    )
    result = _orchestrate(
        tmp_path,
        required=required,
        decision=decision,
        preparation=preparation,
        intent_persist=_Recorder(),
    )
    assert binding_loader.call_count == 1
    assert result is loaded


def test_known_dependency_errors_preserve_identity_and_unexpected_is_sanitized(
    tmp_path: Path,
) -> None:
    known_phase299 = ExternalPublicationRecoveryResumeOutcomeRoutingError(
        "route_contract"
    )
    phase299 = _Recorder(fault=known_phase299)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeRoutingError) as caught:
        _orchestrate(tmp_path, phase299=phase299)
    assert caught.value is known_phase299

    unexpected = _Recorder(fault=RuntimeError("secret path"))
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        _orchestrate(tmp_path, phase299=unexpected)
    _assert_error(caught.value, "dependency_error")
    assert "secret" not in str(caught.value)

    required = _required()
    decision = _decision(required)
    known_decision = ExternalPublicationRecoveryResumeDecisionError("load")
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionError) as caught:
        _orchestrate(
            tmp_path, required=required, decision_loader=_Recorder(fault=known_decision)
        )
    assert caught.value is known_decision

    known_preparation = ExternalPublicationRecoveryResumeDecisionPreparation(
        schema_version=_PREPARATION_SCHEMA,
        recovery_resume_decision_sha256="a" * 64,
        recovery_resume_outcome_sha256="b" * 64,
        resume_start_authorization_sha256="c" * 64,
        resume_intent_binding_sha256="d" * 64,
        operation_intent_sha256="e" * 64,
        operation_start_sha256="f" * 64,
        publication_approval_sha256="0" * 64,
        publication_plan_sha256="1" * 64,
        source_operation="resume",
        previous_recovery_kind="already_acquired",
        recovery_kind="reconciliation_mismatch",
        result_kind="reconciliation",
        result_sha256="2" * 64,
        decision="authorize_resume_preparation",
        target_operation="resume",
        state="prepared",
    )
    known_preparation_error = (
        binding_module.ExternalPublicationRecoveryResumeDecisionPreparationError("load")
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationError
    ) as caught:
        _orchestrate(
            tmp_path,
            required=required,
            decision=decision,
            preparation=known_preparation,
            preparation_loader=_Recorder(fault=known_preparation_error),
        )
    assert caught.value is known_preparation_error

    known_intent = ExternalPublicationOperationIntentError("load")
    with pytest.raises(ExternalPublicationOperationIntentError) as caught:
        _orchestrate(tmp_path, intent_persist=_Recorder(fault=known_intent))
    assert caught.value is known_intent


@pytest.mark.parametrize(
    "dependency_name",
    ["decision_digest", "preparation_digest", "intent_digest"],
)
def test_known_digest_errors_preserve_identity_and_stop_downstream(
    tmp_path: Path, dependency_name: str
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    if dependency_name == "decision_digest":
        error: BaseException = ExternalPublicationRecoveryResumeDecisionError("digest")
        decision_digest = _Recorder(fault=error)
        preparation_loader = _Recorder(preparation)
        preparation_digest = _Recorder("b" * 64)
        intent_digest = _Recorder("c" * 64)
        intent_persist = _Recorder()
        zero_downstream = preparation_loader
    elif dependency_name == "preparation_digest":
        error = ExternalPublicationRecoveryResumeDecisionPreparationError("digest")
        decision_digest = _Recorder("a" * 64)
        preparation_loader = _Recorder(preparation)
        preparation_digest = _Recorder(fault=error)
        intent_digest = _Recorder("c" * 64)
        intent_persist = _Recorder()
        zero_downstream = intent_digest
    else:
        error = ExternalPublicationOperationIntentError("digest")
        decision_digest = _Recorder("a" * 64)
        preparation_loader = _Recorder(preparation)
        preparation_digest = _Recorder("b" * 64)
        intent_digest = _Recorder(fault=error)
        intent_persist = _Recorder()
        zero_downstream = intent_persist
    with pytest.raises(type(error)) as caught:
        _orchestrate(
            tmp_path,
            required=required,
            decision=decision,
            preparation=preparation,
            decision_digest=decision_digest,
            preparation_loader=preparation_loader,
            preparation_digest=preparation_digest,
            intent_digest=intent_digest,
            intent_persist=intent_persist,
        )
    assert caught.value is error
    assert zero_downstream.call_count == 0


@pytest.mark.parametrize(
    ("dependency_name", "classification"),
    [
        ("decision_digest", "decision_digest"),
        ("preparation_digest", "preparation_digest"),
        ("intent_digest", "intent_digest"),
    ],
)
def test_malformed_digest_returns_fail_closed_before_downstream(
    tmp_path: Path, dependency_name: str, classification: str
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    decision_digest = _Recorder("bad")
    preparation_loader = _Recorder(preparation)
    preparation_digest = _Recorder("bad")
    intent_digest = _Recorder("bad")
    intent_persist = _Recorder()
    if dependency_name == "decision_digest":
        zero_downstream = preparation_loader
    elif dependency_name == "preparation_digest":
        decision_digest = _Recorder("a" * 64)
        zero_downstream = intent_digest
    else:
        decision_digest = _Recorder("a" * 64)
        preparation_digest = _Recorder("b" * 64)
        zero_downstream = intent_persist
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationIntentBindingCompatibilityError
    ) as caught:
        _orchestrate(
            tmp_path,
            required=required,
            decision=decision,
            preparation=preparation,
            decision_digest=decision_digest,
            preparation_loader=preparation_loader,
            preparation_digest=preparation_digest,
            intent_digest=intent_digest,
            intent_persist=intent_persist,
        )
    _assert_error(caught.value, classification)
    assert zero_downstream.call_count == 0


def test_real_phase295_to_phase302_integration_preserves_cycle_lineage(
    tmp_path: Path,
) -> None:
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
        before = set(path.name for path in root.iterdir())
        decision = decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=anchor,
            decision="authorize_resume_preparation",
            decided_by="operator@example.test",
            decision_id=f"{previous}-{result_kind}",
        )
        preparation = (
            prepare_and_persist_external_publication_recovery_resume_decision_lineage(  # noqa: E501
                resume_intent_binding_path=anchor
            )
        )
        result = materialize_and_bind_external_publication_recovery_resume_decision_preparation_intent(  # noqa: E501
            resume_intent_binding_path=anchor
        )
        expected_intent = ExternalPublicationOperationIntent(
            schema_version=_INTENT_SCHEMA,
            publication_approval_sha256="c" * 64,
            publication_plan_sha256="d" * 64,
            operation="resume",
        )
        intent_digest = external_publication_operation_intent_digest(expected_intent)
        decision_digest = external_publication_recovery_resume_decision_digest(decision)
        preparation_digest = (
            external_publication_recovery_resume_decision_preparation_digest(  # noqa: E501
                preparation
            )
        )
        binding_path = root / f"{_BINDING_PREFIX}{preparation_digest}.json"
        intent_path = root / f"{_INTENT_PREFIX}{intent_digest}.json"
        assert result.decision_preparation_sha256 == preparation_digest
        assert result.recovery_resume_decision_sha256 == decision_digest
        assert result.publication_approval_sha256 == "c" * 64
        assert result.publication_plan_sha256 == "d" * 64
        assert result.operation_intent_sha256 == intent_digest
        assert result.previous_recovery_kind == previous
        assert result.recovery_kind == current
        assert result.result_kind == result_kind
        assert result.result_sha256 == result_sha256
        assert (
            load_external_publication_recovery_resume_decision_preparation_intent_binding(
                binding_path
            )
            == result
        )
        assert (
            load_external_publication_operation_intent(intent_path) == expected_intent
        )
        assert set(path.name for path in root.iterdir()) == before | {
            f"{_DECISION_PREFIX}{decision.recovery_resume_outcome_sha256}.json",
            f"{_PREPARATION_PREFIX}{decision_digest}.json",
            binding_path.name,
            intent_path.name,
        }


def test_intent_digest_can_equal_historical_intent_without_collapsing_binding(
    tmp_path: Path,
) -> None:
    required = _required()
    decision = _decision(required)
    preparation = _preparation(decision)
    historical_intent_digest = preparation.operation_intent_sha256
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _orchestrate(
        first_root,
        required=required,
        decision=decision,
        preparation=preparation,
        preparation_digest=_Recorder("b" * 64),
        intent_digest=_Recorder(historical_intent_digest),
        intent_persist=_Recorder(),
    )
    second = _orchestrate(
        second_root,
        required=required,
        decision=decision,
        preparation=preparation,
        preparation_digest=_Recorder("c" * 64),
        intent_digest=_Recorder(historical_intent_digest),
        intent_persist=_Recorder(),
    )
    assert first.operation_intent_sha256 == historical_intent_digest
    assert second.operation_intent_sha256 == historical_intent_digest
    assert first.decision_preparation_sha256 != second.decision_preparation_sha256
    first_binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            first
        )
    )
    second_binding_digest = (
        external_publication_recovery_resume_decision_preparation_intent_binding_digest(
            second
        )
    )
    assert first_binding_digest != second_binding_digest


def test_cross_cycle_provenance_is_preserved_and_binding_is_cycle_specific(
    tmp_path: Path,
) -> None:
    for previous, current, result_kind, result_sha in (
        ("already_acquired", "reconciliation_mismatch", "reconciliation", "7" * 64),
        ("reconciliation_mismatch", "already_acquired", "none", None),
    ):
        cycle_root = tmp_path / previous
        cycle_root.mkdir()
        required = _required(
            previous_recovery_kind=previous,
            recovery_kind=current,
            result_kind=result_kind,
            result_sha256=result_sha,
        )
        decision = _decision(required)
        preparation = _preparation(decision)
        result = _orchestrate(
            cycle_root,
            required=required,
            decision=decision,
            preparation=preparation,
            intent_digest=_Recorder("c" * 64),
            intent_persist=_Recorder(),
        )
        assert result.previous_recovery_kind == previous
        assert result.recovery_kind == current
        assert result.result_kind == result_kind
        assert result.result_sha256 == result_sha
        assert result.operation_intent_sha256 == "c" * 64
        assert result.decision_preparation_sha256 == "b" * 64


def test_source_audit_excludes_forbidden_authority_and_runtime_calls() -> None:
    source_path = Path(binding_module.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    assert {"socket", "subprocess", "time", "uuid"}.isdisjoint(imported)
    assert {
        "build_external_publication_operation_intent",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_decision_lineage",
        "decide_and_persist_external_publication_recovery_resume",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "run_and_persist_external_publication_recovery_resume_outcome",
        "resolve",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
    }.isdisjoint(called)
    for token in (
        "os.environ",
        "os.getenv",
        "uuid4",
        "token_hex",
        "Path.resolve",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
        "readlink",
    ):
        assert token not in source
    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "phase302" not in cli_source.lower()
    assert "preparation_intent_binding" not in cli_source
