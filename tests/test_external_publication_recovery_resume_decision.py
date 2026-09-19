"""Focused and integration regressions for the Phase 300 decision boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision as decision_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecision,
    ExternalPublicationRecoveryResumeDecisionCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionConflictError,
    ExternalPublicationRecoveryResumeDecisionError,
    ExternalPublicationRecoveryResumeDecisionLoadError,
    ExternalPublicationRecoveryResumeDecisionPersistenceError,
    ExternalPublicationRecoveryResumeDecisionRequired,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    decide_and_persist_external_publication_recovery_resume,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_canonical_bytes,
    external_publication_recovery_resume_decision_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_outcome_digest,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_recovery_resume_decision,
    persist_external_publication_recovery_resume_decision,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    route_external_publication_recovery_resume_outcome,
    serialize_external_publication_recovery_resume_decision_canonical,
)

_DECISION_SCHEMA = "external-publication-recovery-resume-decision.v1"
_DECISION_KEYS = frozenset(
    {
        "decision",
        "decided_by",
        "decision_id",
        "operation",
        "operation_intent_sha256",
        "operation_start_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_outcome_sha256",
        "result_kind",
        "result_sha256",
        "resume_intent_binding_sha256",
        "resume_start_authorization_sha256",
        "schema_version",
        "source_operation",
        "state",
    }
)
_ROUTING_MESSAGE = "external publication recovery resume outcome routing is blocked"
_DECISION_MESSAGE = "external publication recovery resume decision is blocked"
_PERSISTENCE_MESSAGE = (
    "external publication recovery resume decision persistence failed"
)
_LOAD_MESSAGE = "external publication recovery resume decision could not be loaded"
_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA = "external-publication-recovery-resume-start-authorization.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_OUTCOME_SCHEMA = "external-publication-recovery-resume-outcome.v1"
_DECISION_REQUIRED_SCHEMA = "external-publication-recovery-resume-decision-required.v1"
_AUTHORIZATION_PREFIX = "external-publication-recovery-resume-start-authorization-"
_START_PREFIX = "external-publication-recovery-resume-start-"
_OUTCOME_PREFIX = "external-publication-recovery-resume-outcome-"
_DECISION_PREFIX = "external-publication-recovery-resume-decision-"
_SUFFIX = ".json"


class _StringChild(str):
    pass


class _CallRecorder:
    """Record exact calls while optionally returning or raising a fixed value."""

    def __init__(
        self,
        *,
        result: object = None,
        fault: BaseException | None = None,
        delegate: object = None,
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
        if self.delegate is not None:
            value = self.delegate(*args, **kwargs)  # type: ignore[operator]
        else:
            value = self.result
        self.results.append(value)
        return value

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def first_kwargs(self) -> dict[str, object]:
        return self.calls[0][1]


class _StringPathChild(type(Path())):
    pass


def _assert_decision_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeDecisionCompatibilityError
    assert isinstance(error, ExternalPublicationRecoveryResumeDecisionError)
    assert isinstance(error, ValueError)
    assert str(error) == _DECISION_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeDecisionPersistenceError
    assert isinstance(error, ExternalPublicationRecoveryResumeDecisionError)
    assert isinstance(error, ValueError)
    assert str(error) == _PERSISTENCE_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeDecisionLoadError
    assert isinstance(error, ExternalPublicationRecoveryResumeDecisionError)
    assert isinstance(error, ValueError)
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_routing_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeOutcomeRoutingError
    assert isinstance(error, ValueError)
    assert str(error) == _ROUTING_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _field_names(cls: type[object]) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}  # type: ignore[arg-type]


def _forged_instance(
    source: object, *, cls: type[object] | None = None, **overrides: object
) -> object:
    """Allocate a dataclass without running validation and override real fields."""
    source_type = type(source)
    target_type = source_type if cls is None else cls
    names = _field_names(source_type)
    for name in overrides:
        assert name in names, name
    value = object.__new__(target_type)  # type: ignore[call-overload]
    for name in names:
        object.__setattr__(value, name, getattr(source, name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _decision_required(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeDecisionRequired:
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


def _decision(**overrides: object) -> ExternalPublicationRecoveryResumeDecision:
    values: dict[str, object] = {
        "schema_version": _DECISION_SCHEMA,
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
        "decision": "stop",
        "decided_by": "operator@example.test",
        "decision_id": "decision-1",
        "state": "decided",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecision(**values)  # type: ignore[arg-type]


def _binding(**overrides: object) -> ExternalPublicationRecoveryResumeIntentBinding:
    values: dict[str, object] = {
        "schema_version": _BINDING_SCHEMA,
        "resume_preparation_sha256": "a" * 64,
        "recovery_decision_sha256": "b" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation_intent_sha256": "e" * 64,
        "source_operation": "resume",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeIntentBinding(**values)  # type: ignore[arg-type]


def _authorization(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    values: dict[str, object] = {
        "schema_version": _AUTHORIZATION_SCHEMA,
        "resume_intent_binding_sha256": "1" * 64,
        "resume_preparation_sha256": "a" * 64,
        "recovery_decision_sha256": "b" * 64,
        "operation_intent_sha256": "e" * 64,
        "expected_operation_start_sha256": "6" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "source_operation": "resume",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeStartAuthorization(**values)  # type: ignore[arg-type]


def _start(**overrides: object) -> ExternalPublicationOperationStart:
    values: dict[str, object] = {
        "schema_version": _START_SCHEMA,
        "operation_intent_sha256": "e" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "operation": "resume",
        "state": "started",
    }
    values.update(overrides)
    return ExternalPublicationOperationStart(**values)  # type: ignore[arg-type]


def _outcome(**overrides: object) -> ExternalPublicationRecoveryResumeOutcome:
    values: dict[str, object] = {
        "schema_version": _OUTCOME_SCHEMA,
        "resume_start_authorization_sha256": "1" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "operation_intent_sha256": "e" * 64,
        "operation_start_sha256": "6" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "source_operation": "resume",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "recovery_required",
        "result_kind": "none",
        "result_sha256": None,
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeOutcome(**values)  # type: ignore[arg-type]


class _Lineage:
    """One exact local Phase 295/296/298/290 lineage for default routing."""

    def __init__(
        self, root: Path, *, previous_recovery_kind: str = "already_acquired"
    ) -> None:
        self.root = root
        self.binding_path = root / "binding.json"
        self.binding = _binding(recovery_kind=previous_recovery_kind)
        self.binding_digest = (
            external_publication_recovery_resume_intent_binding_digest(self.binding)
        )
        self.start = _start()
        self.start_digest = external_publication_operation_start_digest(self.start)
        self.authorization = _authorization(
            resume_intent_binding_sha256=self.binding_digest,
            expected_operation_start_sha256=self.start_digest,
            recovery_kind=previous_recovery_kind,
        )
        self.authorization_digest = (
            external_publication_recovery_resume_start_authorization_digest(
                self.authorization
            )
        )
        self.outcome = self.build_outcome()

    @property
    def authorization_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_AUTHORIZATION_PREFIX}{self.binding_digest}{_SUFFIX}"
        )

    @property
    def start_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_START_PREFIX}{self.authorization_digest}{_SUFFIX}"
        )

    @property
    def outcome_path(self) -> Path:
        return self.binding_path.parent / (
            f"{_OUTCOME_PREFIX}{self.authorization_digest}{_SUFFIX}"
        )

    def build_outcome(
        self,
        *,
        state: str = "recovery_required",
        result_kind: str = "none",
        result_sha256: object = None,
        **overrides: object,
    ) -> ExternalPublicationRecoveryResumeOutcome:
        values: dict[str, object] = {
            "resume_start_authorization_sha256": self.authorization_digest,
            "resume_intent_binding_sha256": self.binding_digest,
            "operation_intent_sha256": self.binding.operation_intent_sha256,
            "operation_start_sha256": self.start_digest,
            "publication_approval_sha256": self.binding.publication_approval_sha256,
            "publication_plan_sha256": self.binding.publication_plan_sha256,
            "source_operation": self.binding.source_operation,
            "recovery_kind": self.binding.recovery_kind,
            "operation": "resume",
            "state": state,
            "result_kind": result_kind,
            "result_sha256": result_sha256,
        }
        values.update(overrides)
        return _outcome(**values)


def _persist_lineage(
    root: Path,
    *,
    state: str,
    result_kind: str,
    result_sha256: object,
    previous_recovery_kind: str,
) -> _Lineage:
    lineage = _Lineage(root, previous_recovery_kind=previous_recovery_kind)
    lineage.outcome = lineage.build_outcome(
        state=state, result_kind=result_kind, result_sha256=result_sha256
    )
    persist_external_publication_recovery_resume_intent_binding(
        lineage.binding_path, lineage.binding
    )
    persist_external_publication_recovery_resume_start_authorization(
        lineage.authorization_path, lineage.authorization
    )
    lineage.start_path.write_bytes(
        external_publication_operation_start_canonical_bytes(lineage.start)
    )
    persist_external_publication_recovery_resume_outcome(
        lineage.outcome_path, lineage.outcome
    )
    return lineage


# --- public model and canonical form ------------------------------------


def test_public_exports_and_signature() -> None:
    expected_exports = {
        "ExternalPublicationRecoveryResumeDecision",
        "ExternalPublicationRecoveryResumeDecisionCompatibilityError",
        "ExternalPublicationRecoveryResumeDecisionConflictError",
        "ExternalPublicationRecoveryResumeDecisionError",
        "ExternalPublicationRecoveryResumeDecisionFailureDetail",
        "ExternalPublicationRecoveryResumeDecisionLoadError",
        "ExternalPublicationRecoveryResumeDecisionPersistenceError",
        "decide_and_persist_external_publication_recovery_resume",
        "external_publication_recovery_resume_decision_canonical_bytes",
        "external_publication_recovery_resume_decision_digest",
        "load_external_publication_recovery_resume_decision",
        "persist_external_publication_recovery_resume_decision",
        "serialize_external_publication_recovery_resume_decision_canonical",
    }
    import ai_office.engine as engine

    assert expected_exports <= set(engine.__all__)
    for name in expected_exports:
        assert getattr(engine, name) is getattr(decision_module, name)

    signature = inspect.signature(
        decide_and_persist_external_publication_recovery_resume
    )
    assert tuple(signature.parameters) == (
        "resume_intent_binding_path",
        "decision",
        "decided_by",
        "decision_id",
        "phase299_function",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert (
        signature.parameters["phase299_function"].default
        is route_external_publication_recovery_resume_outcome
    )
    forbidden = {
        "decision_required",
        "phase299_decision_required",
        "decision_path",
        "authorization_path",
        "outcome_path",
        "start_path",
        "binding_digest",
        "operation_intent_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "previous_recovery_kind",
        "recovery_kind",
        "result_kind",
        "result_sha256",
        "phase298_function",
        "phase297_function",
        "phase294_function",
        "phase293_function",
        "phase291_function",
        "phase290_function",
        "provider",
        "transport",
        "runtime_request",
    }
    assert forbidden.isdisjoint(signature.parameters)


def test_model_field_order_frozen_and_valid_cross_cycle_combinations() -> None:
    expected = (
        "schema_version",
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
        "operation",
        "result_kind",
        "result_sha256",
        "decision",
        "decided_by",
        "decision_id",
        "state",
    )
    assert tuple(field.name for field in dataclasses.fields(_decision())) == expected
    with pytest.raises(dataclasses.FrozenInstanceError):
        _decision().state = "other"  # type: ignore[misc]

    mismatch = _decision()
    assert mismatch.previous_recovery_kind == "already_acquired"
    assert mismatch.recovery_kind == "reconciliation_mismatch"
    already = _decision(
        previous_recovery_kind="reconciliation_mismatch",
        recovery_kind="already_acquired",
        result_kind="none",
        result_sha256=None,
    )
    assert already.previous_recovery_kind == "reconciliation_mismatch"
    assert already.recovery_kind == "already_acquired"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", _StringChild(_DECISION_SCHEMA)),
        ("operation", _StringChild("resume")),
        ("decision", _StringChild("stop")),
        ("state", _StringChild("decided")),
        ("recovery_resume_outcome_sha256", "A" * 64),
        ("decided_by", ""),
        ("decision_id", "operator\n1"),
    ],
)
def test_model_rejects_non_exact_types_and_invalid_metadata(
    field: str, value: object
) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionCompatibilityError
    ) as caught:
        _decision(**{field: value})
    _assert_decision_error(
        caught.value,
        "operator_metadata"
        if field in {"decided_by", "decision_id"}
        else "configuration",
    )


@pytest.mark.parametrize(
    ("result_kind", "result_sha256", "recovery_kind"),
    [
        ("reconciliation", "7" * 64, "already_acquired"),
        ("reconciliation", None, "reconciliation_mismatch"),
        ("none", "7" * 64, "already_acquired"),
        ("none", None, "reconciliation_mismatch"),
    ],
)
def test_model_enforces_result_current_recovery_coupling(
    result_kind: str, result_sha256: object, recovery_kind: str
) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionCompatibilityError
    ) as caught:
        _decision(
            result_kind=result_kind,
            result_sha256=result_sha256,
            recovery_kind=recovery_kind,
        )
    _assert_decision_error(caught.value, "configuration")


def test_model_enforces_previous_mismatch_resume_source_and_metadata_rules() -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionCompatibilityError
    ) as caught:
        _decision(
            source_operation="fresh", previous_recovery_kind="reconciliation_mismatch"
        )
    _assert_decision_error(caught.value, "configuration")

    for value in (" operator", "operator ", "\x00", "\ud800", "x" * 257, 1, None):
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionCompatibilityError
        ) as caught:
            _decision(decided_by=value)
        _assert_decision_error(caught.value, "operator_metadata")


def test_canonical_json_has_exact_keys_and_deterministic_digest() -> None:
    decision = _decision(decided_by="日本語 operator")
    canonical = serialize_external_publication_recovery_resume_decision_canonical(
        decision
    )
    assert json.loads(canonical) == {
        "decision": "stop",
        "decided_by": "日本語 operator",
        "decision_id": "decision-1",
        "operation": "resume",
        "operation_intent_sha256": "3" * 64,
        "operation_start_sha256": "4" * 64,
        "previous_recovery_kind": "already_acquired",
        "publication_approval_sha256": "5" * 64,
        "publication_plan_sha256": "6" * 64,
        "recovery_kind": "reconciliation_mismatch",
        "recovery_resume_outcome_sha256": "0" * 64,
        "result_kind": "reconciliation",
        "result_sha256": "7" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "resume_start_authorization_sha256": "1" * 64,
        "schema_version": _DECISION_SCHEMA,
        "source_operation": "resume",
        "state": "decided",
    }
    assert frozenset(json.loads(canonical)) == _DECISION_KEYS
    assert ": " not in canonical
    assert ", " not in canonical
    assert "日本語" in canonical
    assert (
        external_publication_recovery_resume_decision_canonical_bytes(decision)
        == canonical.encode()
    )
    assert external_publication_recovery_resume_decision_digest(
        decision
    ) == external_publication_recovery_resume_decision_digest(decision)


def test_loader_round_trips_and_rejects_noncanonical_duplicate_constant_and_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decision.json"
    decision = _decision()
    persist_external_publication_recovery_resume_decision(path, decision)
    loaded = load_external_publication_recovery_resume_decision(path)
    assert loaded == decision
    assert type(loaded) is ExternalPublicationRecoveryResumeDecision

    variants = (
        b'{"decision":"stop","decision":"stop"}',
        b'{"decision":NaN}',
        b"{}",
        b'{"decision":"stop"}',
        b'{"decision":"stop","decided_by":"operator@example.test","decision_id":"decision-1","operation":"resume","operation_intent_sha256":"3"}'
        b"",
    )
    for index, payload in enumerate(variants):
        malformed = tmp_path / f"malformed-{index}.json"
        malformed.write_bytes(payload)
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionLoadError
        ) as caught:
            load_external_publication_recovery_resume_decision(malformed)
        assert caught.value.detail.classification in {"parse", "keys", "load"}

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(
        json.dumps(
            json.loads(path.read_text(encoding="utf-8")),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionLoadError) as caught:
        load_external_publication_recovery_resume_decision(noncanonical)
    _assert_load_error(caught.value, "noncanonical")


# --- persistence ---------------------------------------------------------


def test_persistence_is_idempotent_for_identical_bytes_and_conflicts_without_rewrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decision.json"
    first = _decision()
    persist_external_publication_recovery_resume_decision(path, first)
    before = path.read_bytes()
    persist_external_publication_recovery_resume_decision(path, first)
    assert path.read_bytes() == before

    different = _decision(decision="authorize_resume_preparation")
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision(path, different)
    assert caught.value.detail.classification == "conflict"
    assert path.read_bytes() == before

    partial = tmp_path / "partial.json"
    partial.write_bytes(b"partial")
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionConflictError):
        persist_external_publication_recovery_resume_decision(partial, first)
    assert partial.read_bytes() == b"partial"


def test_persistence_rejects_bad_parent_and_target_shapes(tmp_path: Path) -> None:
    decision = _decision()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision(
            tmp_path / "missing" / "decision.json", decision
        )
    _assert_persistence_error(caught.value, "parent")

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision(directory, decision)
    _assert_persistence_error(caught.value, "target")

    real = tmp_path / "real.json"
    persist_external_publication_recovery_resume_decision(real, decision)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision(link, decision)
    _assert_persistence_error(caught.value, "target")


class _FsyncShim:
    def __init__(self, real_os: object, *, fail_first: bool) -> None:
        self._real_os = real_os
        self._fail_first = fail_first
        self._calls = 0
        self.O_RDONLY = real_os.O_RDONLY  # type: ignore[attr-defined]
        self.O_DIRECTORY = getattr(real_os, "O_DIRECTORY", 0)

    def __getattr__(self, name: str) -> object:
        return getattr(self._real_os, name)

    def fsync(self, descriptor: int) -> None:
        self._calls += 1
        if self._fail_first and self._calls == 1:
            raise OSError("injected file fsync failure")
        self._real_os.fsync(descriptor)  # type: ignore[attr-defined]


class _FaultyHandle:
    """A real unbuffered file wrapper with one durability fault."""

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
            raise OSError("injected write failure")
        if self._stage == "short_write":
            self._real.write(data[:-1])  # type: ignore[attr-defined]
            return len(data) - 1
        self._real.write(data)  # type: ignore[attr-defined]
        return len(data)

    def flush(self) -> None:
        if self._stage == "flush_error":
            raise OSError("injected flush failure")
        self._real.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self._real.fileno()  # type: ignore[attr-defined]


class _CloseFaultHandle:
    """A real file wrapper without context-manager support whose close fails."""

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
        self._real.close()  # type: ignore[attr-defined]
        raise OSError("injected close failure")


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
    path = tmp_path / f"{stage}.json"
    decision = _decision()
    if stage == "file_fsync":
        monkeypatch.setattr(
            decision_module,
            "os",
            _FsyncShim(os, fail_first=True),
        )
    elif stage == "dir_fsync":

        def fail_directory(_: Path) -> None:
            raise OSError("injected directory fsync failure")

        monkeypatch.setattr(
            decision_module, "_fsync_decision_directory", fail_directory
        )
    else:
        real_open = decision_module.Path.open

        def faulty_open(
            target: Path, mode: str = "r", *args: object, **kwargs: object
        ) -> object:
            if mode == "xb":
                real = real_open(target, mode, buffering=0)
                if stage == "close_failure":
                    return _CloseFaultHandle(real)
                return _FaultyHandle(real, stage)
            return real_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(decision_module.Path, "open", faulty_open)

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision(path, decision)
    _assert_persistence_error(caught.value, "ambiguous")
    assert path.exists()
    retained = path.read_bytes()
    assert retained in {
        b"",
        external_publication_recovery_resume_decision_canonical_bytes(decision)[:-1],
        external_publication_recovery_resume_decision_canonical_bytes(decision),
    }

    monkeypatch.undo()
    if retained == external_publication_recovery_resume_decision_canonical_bytes(
        decision
    ):
        persist_external_publication_recovery_resume_decision(path, decision)
        assert path.read_bytes() == retained
    elif (
        retained
        == external_publication_recovery_resume_decision_canonical_bytes(decision)[:-1]
    ):
        with pytest.raises(ExternalPublicationRecoveryResumeDecisionConflictError):
            persist_external_publication_recovery_resume_decision(path, decision)
        assert path.read_bytes() == retained
    else:
        with pytest.raises(ExternalPublicationRecoveryResumeDecisionConflictError):
            persist_external_publication_recovery_resume_decision(path, decision)
        assert path.read_bytes() == retained


# --- public API and Phase 299 delegation --------------------------------


def _run(
    path: Path,
    routed: object,
    *,
    decision: str = "stop",
    decided_by: str = "operator@example.test",
    decision_id: str = "decision-1",
    persistence: _CallRecorder | None = None,
) -> object:
    phase299 = _CallRecorder(result=routed)
    if persistence is not None:
        pytest.fail("use _run_with_recorders for persistence injection")
    return decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=path,
        decision=decision,  # type: ignore[arg-type]
        decided_by=decided_by,
        decision_id=decision_id,
        phase299_function=phase299,
    )


def _run_with_recorders(
    tmp_path: Path,
    routed: object,
    *,
    decision: str = "stop",
    decided_by: str = "operator@example.test",
    decision_id: str = "decision-1",
) -> tuple[object, _CallRecorder, _CallRecorder]:
    phase299 = _CallRecorder(result=routed)
    persistence = _CallRecorder()
    original = decision_module.persist_external_publication_recovery_resume_decision
    decision_module.persist_external_publication_recovery_resume_decision = persistence  # type: ignore[assignment]
    try:
        result = decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=tmp_path / "binding.json",
            decision=decision,  # type: ignore[arg-type]
            decided_by=decided_by,
            decision_id=decision_id,
            phase299_function=phase299,
        )
    finally:
        decision_module.persist_external_publication_recovery_resume_decision = original
    return result, phase299, persistence


def test_preflight_rejects_invalid_inputs_before_phase299_or_mutation(
    tmp_path: Path,
) -> None:
    routed = _decision_required()
    phase299 = _CallRecorder(result=routed)
    target = tmp_path / "binding.json"
    cases = (
        (object(), "stop", "path_type", "operator@example.test", "decision-1"),
        ("binding.json", "stop", "path_type", "operator@example.test", "decision-1"),
        (target, "invalid", "decision", "operator@example.test", "decision-1"),
        (target, "stop", "operator_metadata", " operator", "decision-1"),
        (target, "stop", "operator_metadata", "operator@example.test", ""),
    )
    for path, decision, classification, decided_by, decision_id in cases:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionCompatibilityError
        ) as caught:
            decide_and_persist_external_publication_recovery_resume(
                resume_intent_binding_path=path,  # type: ignore[arg-type]
                decision=decision,  # type: ignore[arg-type]
                decided_by=decided_by,
                decision_id=decision_id,
                phase299_function=phase299,
            )
        _assert_decision_error(caught.value, classification)
        assert phase299.call_count == 0

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionCompatibilityError
    ) as caught:
        decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=target,
            decision="stop",
            decided_by="operator@example.test",
            decision_id="decision-1",
            phase299_function=object(),  # type: ignore[arg-type]
        )
    _assert_decision_error(caught.value, "configuration")
    assert phase299.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_phase299_is_called_exactly_once_with_only_exact_binding_path(
    tmp_path: Path,
) -> None:
    routed = _decision_required()
    result, phase299, persistence = _run_with_recorders(tmp_path, routed)
    assert type(result) is ExternalPublicationRecoveryResumeDecision
    assert phase299.call_count == 1
    assert phase299.calls[0][0] == ()
    assert phase299.first_kwargs == {
        "resume_intent_binding_path": tmp_path / "binding.json"
    }
    assert phase299.first_kwargs["resume_intent_binding_path"] is not None
    assert persistence.call_count == 1
    assert persistence.calls[0][0][0] == tmp_path / (
        f"{_DECISION_PREFIX}{routed.recovery_resume_outcome_sha256}{_SUFFIX}"
    )


def test_known_phase299_routing_error_preserves_identity_and_no_persistence(
    tmp_path: Path,
) -> None:
    error = ExternalPublicationRecoveryResumeOutcomeRoutingError("route_contract")
    phase299 = _CallRecorder(fault=error)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeRoutingError) as caught:
        decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=tmp_path / "binding.json",
            decision="stop",
            decided_by="operator@example.test",
            decision_id="decision-1",
            phase299_function=phase299,
        )
    assert caught.value is error
    assert phase299.call_count == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error",
    [
        ExternalPublicationRecoveryResumeIntentBindingError("configuration"),
        ExternalPublicationRecoveryResumeStartAuthorizationError("configuration"),
        ExternalPublicationRecoveryResumeOutcomeError("configuration"),
        ExternalPublicationOperationStartError("configuration"),
    ],
)
def test_known_phase299_predecessor_error_preserves_identity(
    tmp_path: Path, error: ValueError
) -> None:
    phase299 = _CallRecorder(fault=error)
    with pytest.raises(type(error)) as caught:
        decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=tmp_path / "binding.json",
            decision="stop",
            decided_by="operator@example.test",
            decision_id="decision-1",
            phase299_function=phase299,
        )
    assert caught.value is error
    assert list(tmp_path.iterdir()) == []


def test_unexpected_phase299_exception_is_sanitized_and_not_persisted(
    tmp_path: Path,
) -> None:
    phase299 = _CallRecorder(fault=RuntimeError("secret path and credential"))
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionCompatibilityError
    ) as caught:
        decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=tmp_path / "binding.json",
            decision="stop",
            decided_by="operator@example.test",
            decision_id="decision-1",
            phase299_function=phase299,
        )
    _assert_decision_error(caught.value, "dependency_error")
    assert "secret path" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []


# --- completed route and local predecessor validation -------------------


def test_completed_phase298_outcome_is_decision_not_required_with_zero_persistence(
    tmp_path: Path,
) -> None:
    outcome = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="7" * 64
    )
    phase299 = _CallRecorder(result=outcome)
    persistence = _CallRecorder()
    original = decision_module.persist_external_publication_recovery_resume_decision
    decision_module.persist_external_publication_recovery_resume_decision = persistence  # type: ignore[assignment]
    try:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionCompatibilityError
        ) as caught:
            decide_and_persist_external_publication_recovery_resume(
                resume_intent_binding_path=tmp_path / "binding.json",
                decision="stop",
                decided_by="operator@example.test",
                decision_id="decision-1",
                phase299_function=phase299,
            )
    finally:
        decision_module.persist_external_publication_recovery_resume_decision = original
    _assert_decision_error(caught.value, "decision_not_required")
    assert phase299.call_count == 1
    assert persistence.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_forged_completed_outcome_is_rejected_without_persistence(
    tmp_path: Path,
) -> None:
    source = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="7" * 64
    )
    forged = _forged_instance(source, operation_start_sha256="F" * 64)
    phase299 = _CallRecorder(result=forged)
    persistence = _CallRecorder()
    original = decision_module.persist_external_publication_recovery_resume_decision
    decision_module.persist_external_publication_recovery_resume_decision = persistence  # type: ignore[assignment]
    try:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionCompatibilityError
        ) as caught:
            decide_and_persist_external_publication_recovery_resume(
                resume_intent_binding_path=tmp_path / "binding.json",
                decision="stop",
                decided_by="operator@example.test",
                decision_id="decision-1",
                phase299_function=phase299,
            )
    finally:
        decision_module.persist_external_publication_recovery_resume_decision = original
    _assert_decision_error(caught.value, "predecessor_contract")
    assert persistence.call_count == 0


def test_forged_decision_required_is_rejected_before_persistence(
    tmp_path: Path,
) -> None:
    source = _decision_required()
    forged = _forged_instance(source, operation_start_sha256="F" * 64)
    phase299 = _CallRecorder(result=forged)
    persistence = _CallRecorder()
    original = decision_module.persist_external_publication_recovery_resume_decision
    decision_module.persist_external_publication_recovery_resume_decision = persistence  # type: ignore[assignment]
    try:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionCompatibilityError
        ) as caught:
            decide_and_persist_external_publication_recovery_resume(
                resume_intent_binding_path=tmp_path / "binding.json",
                decision="stop",
                decided_by="operator@example.test",
                decision_id="decision-1",
                phase299_function=phase299,
            )
    finally:
        decision_module.persist_external_publication_recovery_resume_decision = original
    _assert_decision_error(caught.value, "predecessor_contract")
    assert persistence.call_count == 0


def test_subclass_and_lookalike_decision_required_are_rejected(tmp_path: Path) -> None:
    class DecisionRequiredChild(ExternalPublicationRecoveryResumeDecisionRequired):
        pass

    source = _decision_required()
    subclass = _forged_instance(source, cls=DecisionRequiredChild)
    lookalike = object()
    for routed in (subclass, lookalike):
        phase299 = _CallRecorder(result=routed)
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionCompatibilityError
        ) as caught:
            decide_and_persist_external_publication_recovery_resume(
                resume_intent_binding_path=tmp_path / "binding.json",
                decision="stop",
                decided_by="operator@example.test",
                decision_id="decision-1",
                phase299_function=phase299,
            )
        _assert_decision_error(caught.value, "predecessor_contract")


# --- construction, one-shot, and conflict semantics ---------------------


def test_decision_copies_both_recovery_kinds_and_all_provenance_exactly(
    tmp_path: Path,
) -> None:
    routed = _decision_required(
        previous_recovery_kind="reconciliation_mismatch",
        recovery_kind="already_acquired",
        result_kind="none",
        result_sha256=None,
        source_operation="resume",
    )
    result, _, persistence = _run_with_recorders(
        tmp_path,
        routed,
        decision="authorize_resume_preparation",
        decided_by="operator-日本語",
        decision_id="id-2",
    )
    assert type(result) is ExternalPublicationRecoveryResumeDecision
    assert result is persistence.calls[0][0][1]
    assert result.decision == "authorize_resume_preparation"
    assert result.decided_by == "operator-日本語"
    assert result.decision_id == "id-2"
    for name in (
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
        "operation",
        "result_kind",
        "result_sha256",
    ):
        assert getattr(result, name) == getattr(routed, name)
    assert result.state == "decided"


def test_default_persistence_uses_canonical_target_and_authorize_creates_only_decision(
    tmp_path: Path,
) -> None:
    routed = _decision_required()
    result = _run(tmp_path / "binding.json", routed)
    assert type(result) is ExternalPublicationRecoveryResumeDecision
    path = (
        tmp_path / f"{_DECISION_PREFIX}{routed.recovery_resume_outcome_sha256}{_SUFFIX}"
    )
    assert path.exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == [path.name]


def test_same_exact_decision_is_idempotent_and_returns_loaded_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed = _decision_required()
    first = _run(tmp_path / "binding.json", routed)
    path = (
        tmp_path / f"{_DECISION_PREFIX}{routed.recovery_resume_outcome_sha256}{_SUFFIX}"
    )
    original = decision_module.load_external_publication_recovery_resume_decision
    loaded = _CallRecorder(delegate=original)
    monkeypatch.setattr(
        decision_module, "load_external_publication_recovery_resume_decision", loaded
    )
    phase299 = _CallRecorder(result=routed)
    second = decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=tmp_path / "binding.json",
        decision="stop",
        decided_by="operator@example.test",
        decision_id="decision-1",
        phase299_function=phase299,
    )
    assert first == second
    assert second is loaded.results[0]
    assert loaded.call_count == 1
    assert phase299.call_count == 1
    assert (
        path.read_bytes()
        == external_publication_recovery_resume_decision_canonical_bytes(second)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "authorize_resume_preparation"),
        ("decided_by", "another-operator"),
        ("decision_id", "decision-2"),
    ],
)
def test_changed_decision_metadata_conflicts_and_leaves_existing_bytes_unchanged(
    tmp_path: Path, field: str, value: str
) -> None:
    routed = _decision_required()
    _run(tmp_path / "binding.json", routed)
    path = (
        tmp_path / f"{_DECISION_PREFIX}{routed.recovery_resume_outcome_sha256}{_SUFFIX}"
    )
    before = path.read_bytes()
    kwargs = {
        "resume_intent_binding_path": tmp_path / "binding.json",
        "decision": "stop",
        "decided_by": "operator@example.test",
        "decision_id": "decision-1",
        "phase299_function": _CallRecorder(result=routed),
    }
    kwargs[field] = value
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionConflictError
    ) as caught:
        decide_and_persist_external_publication_recovery_resume(**kwargs)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "conflict"
    assert path.read_bytes() == before


def test_distinct_outcome_digest_derives_distinct_one_shot_target(
    tmp_path: Path,
) -> None:
    first = _decision_required(recovery_resume_outcome_sha256="0" * 64)
    second = _decision_required(recovery_resume_outcome_sha256="f" * 64)
    _run(tmp_path / "binding.json", first)
    _run(tmp_path / "binding.json", second, decision_id="decision-2")
    assert (tmp_path / f"{_DECISION_PREFIX}{'0' * 64}{_SUFFIX}").exists()
    assert (tmp_path / f"{_DECISION_PREFIX}{'f' * 64}{_SUFFIX}").exists()


# --- local durable integration ------------------------------------------


def test_default_route_previous_already_acquired_to_current_mismatch_and_stop(
    tmp_path: Path,
) -> None:
    lineage = _persist_lineage(
        tmp_path,
        state="recovery_required",
        result_kind="reconciliation",
        result_sha256="7" * 64,
        previous_recovery_kind="already_acquired",
    )
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    }
    result = decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=lineage.binding_path,
        decision="stop",
        decided_by="operator@example.test",
        decision_id="mismatch-stop",
    )
    assert result.previous_recovery_kind == "already_acquired"
    assert result.recovery_kind == "reconciliation_mismatch"
    assert result.result_kind == "reconciliation"
    assert result.result_sha256 == "7" * 64
    assert (
        result.recovery_resume_outcome_sha256
        == external_publication_recovery_resume_outcome_digest(lineage.outcome)
    )
    assert result.decision == "stop"
    assert set(before) | {
        Path(f"{_DECISION_PREFIX}{result.recovery_resume_outcome_sha256}{_SUFFIX}")
    } == {path.relative_to(tmp_path) for path in tmp_path.iterdir() if path.is_file()}


def test_default_route_previous_mismatch_to_current_already_acquired_and_authorize(
    tmp_path: Path,
) -> None:
    lineage = _persist_lineage(
        tmp_path,
        state="recovery_required",
        result_kind="none",
        result_sha256=None,
        previous_recovery_kind="reconciliation_mismatch",
    )
    result = decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=lineage.binding_path,
        decision="authorize_resume_preparation",
        decided_by="operator@example.test",
        decision_id="already-authorize",
    )
    assert result.previous_recovery_kind == "reconciliation_mismatch"
    assert result.recovery_kind == "already_acquired"
    assert result.result_kind == "none"
    assert result.result_sha256 is None
    assert result.decision == "authorize_resume_preparation"
    decision_path = tmp_path / (
        f"{_DECISION_PREFIX}{result.recovery_resume_outcome_sha256}{_SUFFIX}"
    )
    assert decision_path.exists()
    assert not (tmp_path / "external-publication-operation-intent.json").exists()


def test_default_completed_route_has_zero_decision_persistence(tmp_path: Path) -> None:
    lineage = _persist_lineage(
        tmp_path,
        state="completed",
        result_kind="reconciliation",
        result_sha256="7" * 64,
        previous_recovery_kind="already_acquired",
    )
    before = {path.name for path in tmp_path.iterdir()}
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionCompatibilityError
    ) as caught:
        decide_and_persist_external_publication_recovery_resume(
            resume_intent_binding_path=lineage.binding_path,
            decision="stop",
            decided_by="operator@example.test",
            decision_id="completed-stop",
        )
    _assert_decision_error(caught.value, "decision_not_required")
    assert {path.name for path in tmp_path.iterdir()} == before
    assert not any(
        path.name.startswith(_DECISION_PREFIX) for path in tmp_path.iterdir()
    )


# --- source audit --------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(Path(decision_module.__file__).read_text(encoding="utf-8"))


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
    return modules


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_source_audit_has_only_phase299_orchestration_dependency() -> None:
    modules = _imported_modules()
    assert "socket" not in modules
    assert "subprocess" not in modules
    assert "time" not in modules
    assert "uuid" not in modules
    forbidden_names = {
        "persist_external_publication_recovery_resume_outcome",
        "persist_external_publication_recovery_resume_intent_binding",
        "persist_external_publication_recovery_resume_start_authorization",
        "run_and_persist_external_publication_recovery_resume_outcome",
        "run_external_publication_recovery_resume_start_handoff",
        "authorize_and_persist_external_publication_recovery_resume_start",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "execute_and_persist_external_publication",
        "reconcile_and_persist_external_publication_execution",
        "socket",
        "subprocess",
        "resolve",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
    }
    assert forbidden_names.isdisjoint(_called_names())
    source = Path(decision_module.__file__).read_text(encoding="utf-8")
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


def test_no_cli_change_and_no_phase300_command() -> None:
    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "recovery_resume_decision" not in cli_source
    assert "phase300" not in cli_source.lower()
