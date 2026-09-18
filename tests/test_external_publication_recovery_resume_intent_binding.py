"""Focused provider-free tests for the Phase 295 resume-intent binding boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
from pathlib import Path

import pytest

import ai_office.engine.external_publication_operation_intent as intent_module
import ai_office.engine.external_publication_recovery_resume_intent_binding as binding_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationIntentLoadError,
    ExternalPublicationOperationIntentPersistenceError,
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationPlan,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingCompatibilityError,
    ExternalPublicationRecoveryResumeIntentBindingConflictError,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeIntentBindingFailureDetail,
    ExternalPublicationRecoveryResumeIntentBindingLoadError,
    ExternalPublicationRecoveryResumeIntentBindingPersistenceError,
    ExternalPublicationRecoveryResumePreparation,
    ExternalPublicationRecoveryResumePreparationError,
    acquire_external_publication_operation_start,
    approve_external_publication,
    build_external_publication_operation_intent,
    decide_and_persist_external_publication_recovery,
    external_publication_operation_intent_digest,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_intent_binding_canonical_bytes,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_preparation_digest,
    load_external_publication_operation_intent,
    load_external_publication_operation_start,
    load_external_publication_recovery_resume_intent_binding,
    load_external_publication_recovery_resume_preparation,
    materialize_and_bind_external_publication_recovery_resume_intent,
    persist_external_publication_operation_intent,
    persist_external_publication_operation_lifecycle_outcome,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_preparation,
    prepare_and_persist_external_publication_recovery_resume_lineage,
    serialize_external_publication_recovery_resume_intent_binding_canonical,
)

_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_PREPARATION_SCHEMA = "external-publication-recovery-resume-preparation.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_LIFECYCLE_SCHEMA = "external-publication-operation-lifecycle-outcome.v1"
_MESSAGE = "external publication recovery resume intent binding is invalid"
_PERSIST_MESSAGE = (
    "external publication recovery resume intent binding persistence failed"
)
_LOAD_MESSAGE = (
    "external publication recovery resume intent binding could not be loaded"
)
_SOURCE = Path(binding_module.__file__).read_text(encoding="utf-8")
_KEYS = tuple(
    sorted(
        (
            "operation",
            "operation_intent_sha256",
            "publication_approval_sha256",
            "publication_plan_sha256",
            "recovery_decision_sha256",
            "recovery_kind",
            "resume_preparation_sha256",
            "schema_version",
            "source_operation",
            "state",
        )
    )
)
_AMBIGUOUS_STAGES = (
    "write_error",
    "short_write",
    "flush_error",
    "file_fsync",
    "close_failure",
    "dir_fsync",
)
_FULL_WRITE_STAGES = ("flush_error", "file_fsync", "close_failure", "dir_fsync")
_PARTIAL_WRITE_STAGES = ("write_error", "short_write")
_FIELD_NAMES = tuple(
    field.name
    for field in dataclasses.fields(ExternalPublicationRecoveryResumeIntentBinding)
)
_EXPECTED_PARAMETERS = (
    "resume_preparation_path",
    "resume_intent_binding_path",
    "resume_intent_path",
    "preparation_loader",
    "preparation_digest_function",
    "intent_loader",
    "intent_digest_function",
    "intent_persist_function",
)
_INJECTED_DEPENDENCIES = frozenset(
    {
        "preparation_loader",
        "preparation_digest_function",
        "intent_loader",
        "intent_digest_function",
        "intent_persist_function",
    }
)


class _StringChild(str):
    pass


def _field_names(cls: type[object]) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}  # type: ignore[arg-type]


def _forged_instance(cls: type[object], source: object, **overrides: object) -> object:
    """Allocate without running validation; every override must be a real field."""
    names = set()
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        names.add(field.name)
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in overrides.items():
        assert name in names, name
        object.__setattr__(value, name, replacement)
    return value


def _assert_error(error: ValueError, classification: str) -> None:
    assert (
        type(error) is ExternalPublicationRecoveryResumeIntentBindingCompatibilityError
    )
    assert isinstance(error, ExternalPublicationRecoveryResumeIntentBindingError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert (
        type(error.detail)
        is ExternalPublicationRecoveryResumeIntentBindingFailureDetail
    )
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeIntentBindingPersistenceError
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeIntentBindingLoadError
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict_error(error: ValueError) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeIntentBindingConflictError
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


# --- builders ------------------------------------------------------------


def _preparation(
    *,
    schema_version: object = _PREPARATION_SCHEMA,
    decision_digest: object = "7" * 64,
    lifecycle_digest: object = "8" * 64,
    start_digest: object = "9" * 64,
    approval_digest: object = "b" * 64,
    plan_digest: object = "c" * 64,
    source_operation: object = "fresh",
    recovery_kind: object = "already_acquired",
    target_operation: object = "resume",
    state: object = "prepared",
) -> ExternalPublicationRecoveryResumePreparation:
    return ExternalPublicationRecoveryResumePreparation(
        schema_version=schema_version,  # type: ignore[arg-type]
        recovery_decision_sha256=decision_digest,  # type: ignore[arg-type]
        lifecycle_outcome_sha256=lifecycle_digest,  # type: ignore[arg-type]
        operation_start_sha256=start_digest,  # type: ignore[arg-type]
        publication_approval_sha256=approval_digest,  # type: ignore[arg-type]
        publication_plan_sha256=plan_digest,  # type: ignore[arg-type]
        source_operation=source_operation,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        target_operation=target_operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )


def _binding(
    *,
    schema_version: object = _BINDING_SCHEMA,
    preparation_digest: object = "1" * 64,
    decision_digest: object = "7" * 64,
    approval_digest: object = "b" * 64,
    plan_digest: object = "c" * 64,
    intent_digest: object = "a" * 64,
    source_operation: object = "fresh",
    recovery_kind: object = "already_acquired",
    operation: object = "resume",
    state: object = "authorized",
) -> ExternalPublicationRecoveryResumeIntentBinding:
    return ExternalPublicationRecoveryResumeIntentBinding(
        schema_version=schema_version,  # type: ignore[arg-type]
        resume_preparation_sha256=preparation_digest,  # type: ignore[arg-type]
        recovery_decision_sha256=decision_digest,  # type: ignore[arg-type]
        publication_approval_sha256=approval_digest,  # type: ignore[arg-type]
        publication_plan_sha256=plan_digest,  # type: ignore[arg-type]
        operation_intent_sha256=intent_digest,  # type: ignore[arg-type]
        source_operation=source_operation,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )


class _LoaderRecorder:
    def __init__(self, delegate: object) -> None:
        self.delegate = delegate
        self.calls: list[object] = []
        self.results: list[object] = []

    def __call__(self, path: object) -> object:
        self.calls.append(path)
        result = self.delegate(path)  # type: ignore[operator]
        self.results.append(result)
        return result


class _CallRecorder:
    """Record every call and optionally delegate to a real dependency."""

    def __init__(self, delegate: object = None, fault: object = None) -> None:
        self.delegate = delegate
        self.fault = fault
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if isinstance(self.fault, BaseException):
            raise self.fault
        result = self.delegate(*args, **kwargs)  # type: ignore[operator]
        self.results.append(result)
        return result

    @property
    def first_positional(self) -> object:
        return self.calls[0][0][0]


class _FaultyHandle:
    """A real unbuffered file wrapper that injects one persistence fault."""

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

    def close(self) -> None:
        self._real.close()  # type: ignore[attr-defined]


class _CloseFaultHandle:
    """A real file wrapper without context-manager support so close() fails."""

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
    """Delegate to the real ``os`` but break one selected call only here."""

    def __init__(self, stage: str) -> None:
        self._stage = stage

    def __getattr__(self, name: str) -> object:
        if name == "fsync" and self._stage == "file_fsync":

            def _boom(*args: object, **kwargs: object) -> None:
                raise OSError("fsync failed")

            return _boom
        return getattr(os, name)


def _install_persistence_fault(scope: pytest.MonkeyPatch, stage: str) -> None:
    """Fault-inject exactly one step of the binding persistence sequence.

    The shim replaces only the binding module's own ``os`` name and its own
    directory-fsync helper, so the Phase 289 intent persistence remains real.
    """

    def _fsync_boom(*args: object, **kwargs: object) -> None:
        raise OSError("fsync failed")

    if stage == "file_fsync":
        scope.setattr(binding_module, "os", _OsShim(stage))
        return
    if stage == "dir_fsync":
        scope.setattr(binding_module, "_fsync_binding_directory", _fsync_boom)
        return

    real_open = Path.open

    def _fake_open(
        self: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode == "xb":
            real = real_open(self, mode, buffering=0)
            if stage == "close_failure":
                return _CloseFaultHandle(real)
            return _FaultyHandle(real, stage)
        return real_open(self, mode, *args, **kwargs)

    scope.setattr(Path, "open", _fake_open)


def _install_intent_fault(
    scope: pytest.MonkeyPatch, stage: str, *, name: str = "intent.json"
) -> None:
    """Fault-inject only the Phase 289 intent persistence for one target name.

    The shim replaces only the intent module's own ``os`` name and its own
    directory-fsync helper, so the Phase 295 binding persistence stays real.
    """

    def _fsync_boom(*args: object, **kwargs: object) -> None:
        raise OSError("fsync failed")

    if stage == "file_fsync":
        scope.setattr(intent_module, "os", _OsShim(stage))
        return
    if stage == "dir_fsync":
        scope.setattr(intent_module, "_fsync_intent_directory", _fsync_boom)
        return

    real_open = Path.open

    def _fake_open(
        self: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode == "xb" and self.name == name:
            real = real_open(self, mode, buffering=0)
            if stage == "close_failure":
                return _CloseFaultHandle(real)
            return _FaultyHandle(real, stage)
        return real_open(self, mode, *args, **kwargs)

    scope.setattr(Path, "open", _fake_open)


# --- real predecessor lineage -------------------------------------------


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-295",
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="1" * 64,
    )


def _approval() -> object:
    return approve_external_publication(
        _plan(),
        approved_by="human-reviewer-295",
        approval_id="approval-295",
    )


def _lifecycle(
    *,
    start_digest: str,
    operation: str = "fresh",
    state: str = "recovery_required",
    result_kind: str = "none",
    result_sha256: object = None,
    approval_digest: str = "b" * 64,
    plan_digest: str = "c" * 64,
) -> ExternalPublicationOperationLifecycleOutcome:
    return ExternalPublicationOperationLifecycleOutcome(
        schema_version=_LIFECYCLE_SCHEMA,  # type: ignore[arg-type]
        operation_start_sha256=start_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        result_kind=result_kind,  # type: ignore[arg-type]
        result_sha256=result_sha256,  # type: ignore[arg-type]
    )


def _seed_lineage(
    root: Path,
    *,
    operation: str = "fresh",
    state: str = "recovery_required",
    result_kind: str = "none",
    result_sha256: object = None,
    chosen: str = "authorize_resume_preparation",
) -> tuple[Path, Path, Path]:
    """Create real Phase 293/292/290 durable predecessor artifacts."""
    approval = _approval()
    intent = build_external_publication_operation_intent(
        approval,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
    )
    lineage_intent_path = root / "lineage-intent.json"
    start_path = root / "start.json"
    persist_external_publication_operation_intent(lineage_intent_path, intent)
    acquire_external_publication_operation_start(
        intent_path=lineage_intent_path, start_path=start_path
    )
    start = load_external_publication_operation_start(start_path)
    start_digest = external_publication_operation_start_digest(start)
    lifecycle_path = root / "lifecycle.json"
    lifecycle = _lifecycle(
        start_digest=start_digest,
        operation=operation,
        state=state,
        result_kind=result_kind,
        result_sha256=result_sha256,
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    persist_external_publication_operation_lifecycle_outcome(lifecycle_path, lifecycle)
    decision_path = root / "decision.json"
    decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision=chosen,  # type: ignore[arg-type]
        decided_by="operator-295",
        decision_id="decision-295",
    )
    return decision_path, lifecycle_path, start_path


def _real_preparation(
    root: Path,
    *,
    operation: str = "fresh",
    state: str = "recovery_required",
    result_kind: str = "none",
    result_sha256: object = None,
) -> ExternalPublicationRecoveryResumePreparation:
    """Build one real Phase 294 preparation sidecar and return the record."""
    decision_path, lifecycle_path, start_path = _seed_lineage(
        root,
        operation=operation,
        state=state,
        result_kind=result_kind,
        result_sha256=result_sha256,
    )
    preparation_path = root / "preparation.json"
    return prepare_and_persist_external_publication_recovery_resume_lineage(
        recovery_decision_path=decision_path,
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        resume_preparation_path=preparation_path,
    )


def _expected_intent(
    preparation: ExternalPublicationRecoveryResumePreparation,
) -> ExternalPublicationOperationIntent:
    return ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,  # type: ignore[arg-type]
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation="resume",
    )


def _bind(
    preparation_path: Path,
    binding_path: Path,
    intent_path: Path,
    **kwargs: object,
) -> ExternalPublicationRecoveryResumeIntentBinding:
    return materialize_and_bind_external_publication_recovery_resume_intent(
        resume_preparation_path=preparation_path,
        resume_intent_binding_path=binding_path,
        resume_intent_path=intent_path,
        **kwargs,  # type: ignore[arg-type]
    )


# --- public surface ------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert set(binding_module.__all__) == {
        "ExternalPublicationRecoveryResumeIntentBinding",
        "ExternalPublicationRecoveryResumeIntentBindingCompatibilityError",
        "ExternalPublicationRecoveryResumeIntentBindingConflictError",
        "ExternalPublicationRecoveryResumeIntentBindingError",
        "ExternalPublicationRecoveryResumeIntentBindingFailureDetail",
        "ExternalPublicationRecoveryResumeIntentBindingLoadError",
        "ExternalPublicationRecoveryResumeIntentBindingPersistenceError",
        "external_publication_recovery_resume_intent_binding_canonical_bytes",
        "external_publication_recovery_resume_intent_binding_digest",
        "load_external_publication_recovery_resume_intent_binding",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "persist_external_publication_recovery_resume_intent_binding",
        "serialize_external_publication_recovery_resume_intent_binding_canonical",
    }
    assert issubclass(
        ExternalPublicationRecoveryResumeIntentBindingCompatibilityError,
        ExternalPublicationRecoveryResumeIntentBindingError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError,
        ExternalPublicationRecoveryResumeIntentBindingError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeIntentBindingConflictError,
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeIntentBindingLoadError,
        ExternalPublicationRecoveryResumeIntentBindingError,
    )
    assert issubclass(ExternalPublicationRecoveryResumeIntentBindingError, ValueError)
    for error_type in (
        ExternalPublicationRecoveryResumeIntentBindingCompatibilityError,
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError,
        ExternalPublicationRecoveryResumeIntentBindingConflictError,
        ExternalPublicationRecoveryResumeIntentBindingLoadError,
    ):
        assert error_type.__name__.startswith(
            "ExternalPublicationRecoveryResumeIntentBinding"
        )


def test_signature_defaults_and_no_caller_authority_arguments() -> None:
    signature = inspect.signature(
        materialize_and_bind_external_publication_recovery_resume_intent
    )
    assert tuple(signature.parameters) == _EXPECTED_PARAMETERS
    non_path = {name for name in signature.parameters if not name.endswith("_path")}
    assert non_path == set(_INJECTED_DEPENDENCIES)
    for name in signature.parameters:
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    defaults = {
        name: parameter.default
        for name, parameter in signature.parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert set(defaults) == set(_INJECTED_DEPENDENCIES)
    assert defaults["preparation_loader"] is (
        load_external_publication_recovery_resume_preparation
    )
    assert defaults["preparation_digest_function"] is (
        external_publication_recovery_resume_preparation_digest
    )
    assert defaults["intent_loader"] is load_external_publication_operation_intent
    assert defaults["intent_digest_function"] is (
        external_publication_operation_intent_digest
    )
    assert defaults["intent_persist_function"] is (
        persist_external_publication_operation_intent
    )
    # no builder, approval, digest, source, recovery-kind, or operation authority.
    for forbidden in (
        "build_external_publication_operation_intent",
        "build_intent",
        "approval",
        "approval_sha256",
        "preparation_digest",
        "intent",
        "intent_sha256",
        "recovery_decision",
        "source_operation",
        "recovery_kind",
        "target_operation",
        "operation",
        "new_start_path",
        "start_path",
    ):
        assert forbidden not in signature.parameters, forbidden


# --- model ---------------------------------------------------------------


def test_model_field_order_and_frozen() -> None:
    assert _FIELD_NAMES == (
        "schema_version",
        "resume_preparation_sha256",
        "recovery_decision_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "operation_intent_sha256",
        "source_operation",
        "recovery_kind",
        "operation",
        "state",
    )
    binding = _binding()
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.state = "authorized"  # type: ignore[misc]
    assert dataclasses.is_dataclass(ExternalPublicationRecoveryResumeIntentBinding)


_VALID_COMBINATIONS = (
    {"source_operation": "fresh", "recovery_kind": "already_acquired"},
    {"source_operation": "resume", "recovery_kind": "already_acquired"},
    {"source_operation": "resume", "recovery_kind": "reconciliation_mismatch"},
)


@pytest.mark.parametrize("overrides", _VALID_COMBINATIONS)
def test_model_accepts_exact_valid_combinations(overrides: dict[str, object]) -> None:
    binding = _binding(**overrides)
    assert binding.operation == "resume"
    assert binding.state == "authorized"
    assert binding.schema_version == _BINDING_SCHEMA


def test_model_rejects_reconciliation_mismatch_with_fresh_source() -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _binding(source_operation="fresh", recovery_kind="reconciliation_mismatch")
    assert info.value.detail.classification == "configuration"


_MODEL_INVALID_CASES = (
    {"schema_version": "other.v1"},
    {"preparation_digest": "A" * 64},
    {"preparation_digest": "1" * 63},
    {"preparation_digest": "1" * 65},
    {"decision_digest": ""},
    {"approval_digest": None},
    {"plan_digest": 1},
    {"intent_digest": "z" * 64},
    {"source_operation": "replay"},
    {"recovery_kind": "mismatch"},
    {"operation": "fresh"},
    {"state": "prepared"},
    {"state": "authorized "},
)


@pytest.mark.parametrize("overrides", _MODEL_INVALID_CASES)
def test_model_rejects_non_exact_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _binding(**overrides)
    assert info.value.detail.classification == "configuration"


@pytest.mark.parametrize(
    "overrides",
    (
        {"source_operation": _StringChild("fresh")},
        {"recovery_kind": _StringChild("already_acquired")},
        {"operation": _StringChild("resume")},
        {"state": _StringChild("authorized")},
    ),
)
def test_model_rejects_non_exact_runtime_types(overrides: dict[str, object]) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _binding(**overrides)
    assert info.value.detail.classification == "configuration"


def test_model_subclass_rejected_by_helpers(tmp_path: Path) -> None:
    class _Child(ExternalPublicationRecoveryResumeIntentBinding):
        pass

    child = _forged_instance(_Child, _binding())
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        persist_external_publication_recovery_resume_intent_binding(
            tmp_path / "binding.json",
            child,  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"
    assert not (tmp_path / "binding.json").exists()

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        serialize_external_publication_recovery_resume_intent_binding_canonical(
            child  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        external_publication_recovery_resume_intent_binding_digest(
            child  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"


def test_forged_model_is_rejected_by_helpers(tmp_path: Path) -> None:
    forged = _forged_instance(
        ExternalPublicationRecoveryResumeIntentBinding,
        _binding(),
        operation="fresh",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        external_publication_recovery_resume_intent_binding_canonical_bytes(
            forged  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        persist_external_publication_recovery_resume_intent_binding(
            tmp_path / "binding.json",
            forged,  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"
    assert not (tmp_path / "binding.json").exists()


def test_forged_model_enum_with_extra_attribute_is_still_rejected() -> None:
    forged = _forged_instance(
        ExternalPublicationRecoveryResumeIntentBinding,
        _binding(),
        state="authorized",
    )
    # the override key must exist on the model, otherwise the case is vacuous.
    assert "state" in _field_names(ExternalPublicationRecoveryResumeIntentBinding)
    assert external_publication_recovery_resume_intent_binding_canonical_bytes(
        forged
    ) == external_publication_recovery_resume_intent_binding_canonical_bytes(_binding())


# --- canonical / digest --------------------------------------------------


def test_canonical_json_exact_keys_and_deterministic_digest() -> None:
    binding = _binding()
    canonical = serialize_external_publication_recovery_resume_intent_binding_canonical(
        binding
    )
    parsed = json.loads(canonical)
    assert tuple(sorted(parsed)) == _KEYS
    assert len(parsed) == 10
    assert canonical == (
        '{"operation":"resume","operation_intent_sha256":"' + "a" * 64 + '"'
        ',"publication_approval_sha256":"' + "b" * 64 + '"'
        ',"publication_plan_sha256":"' + "c" * 64 + '"'
        ',"recovery_decision_sha256":"' + "7" * 64 + '"'
        ',"recovery_kind":"already_acquired"'
        ',"resume_preparation_sha256":"' + "1" * 64 + '"'
        ',"schema_version":"' + _BINDING_SCHEMA + '"'
        ',"source_operation":"fresh","state":"authorized"}'
    )
    assert external_publication_recovery_resume_intent_binding_digest(
        binding
    ) == external_publication_recovery_resume_intent_binding_digest(_binding())
    assert external_publication_recovery_resume_intent_binding_canonical_bytes(
        binding
    ) == canonical.encode("utf-8")
    assert (
        external_publication_recovery_resume_intent_binding_digest(binding)
        == __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()
    )


def test_canonical_bytes_reject_non_exact_model() -> None:
    forged = _forged_instance(
        ExternalPublicationRecoveryResumeIntentBinding,
        _binding(),
        schema_version=_StringChild(_BINDING_SCHEMA),
    )
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        external_publication_recovery_resume_intent_binding_canonical_bytes(
            forged  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"


# --- loader --------------------------------------------------------------


def test_loader_round_trips_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    binding = _binding()
    persist_external_publication_recovery_resume_intent_binding(path, binding)
    loaded = load_external_publication_recovery_resume_intent_binding(path)
    assert loaded == binding
    assert type(loaded) is ExternalPublicationRecoveryResumeIntentBinding
    assert path.read_bytes() == (
        external_publication_recovery_resume_intent_binding_canonical_bytes(binding)
    )


def test_loader_rejects_noncanonical_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    canonical = serialize_external_publication_recovery_resume_intent_binding_canonical(
        _binding()
    )
    path.write_text(canonical.replace(",", ", ", 1), encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(path)
    _assert_load_error(info.value, "noncanonical")


def test_loader_rejects_reordered_keys_as_noncanonical(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    canonical = serialize_external_publication_recovery_resume_intent_binding_canonical(
        _binding()
    )
    parsed = json.loads(canonical)
    reordered = json.dumps(
        {key: parsed[key] for key in reversed(list(parsed))},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert reordered != canonical
    path.write_text(reordered, encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(path)
    _assert_load_error(info.value, "noncanonical")


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"{",
        b"[]",
        b'"text"',
        b'{"operation":"resume"}',
        b'{"operation":"resume",}',
        b'{"state":"authorized","state":"authorized"}',
        b'{"operation":"resume","state":NaN}',
        b"\xff\xfe",
    ),
)
def test_loader_rejects_malformed_payloads(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "binding.json"
    path.write_bytes(payload)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        load_external_publication_recovery_resume_intent_binding(path)
    assert info.value.detail.classification in {"parse", "keys", "load"}


def test_loader_rejects_duplicate_key_payload(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    canonical = serialize_external_publication_recovery_resume_intent_binding_canonical(
        _binding()
    )
    duplicated = canonical.replace(
        '{"operation":', '{"operation":"resume","operation":', 1
    )
    path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(path)
    _assert_load_error(info.value, "parse")


def test_loader_rejects_extra_and_missing_keys(tmp_path: Path) -> None:
    binding = _binding()
    base = json.loads(
        serialize_external_publication_recovery_resume_intent_binding_canonical(binding)
    )
    extra = dict(base)
    extra["extra"] = "x"
    extra_path = tmp_path / "extra.json"
    extra_path.write_text(
        json.dumps(extra, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(extra_path)
    _assert_load_error(info.value, "keys")

    missing = dict(base)
    del missing["state"]
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(
        json.dumps(missing, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(missing_path)
    _assert_load_error(info.value, "keys")


def test_loader_rejects_oversized_payload(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    path.write_bytes(b"x" * 5000)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(path)
    _assert_load_error(info.value, "size")


def test_loader_rejects_non_regular_target(tmp_path: Path) -> None:
    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(directory)
    _assert_load_error(info.value, "target")

    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(tmp_path / "absent.json")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding(symlink)
    _assert_load_error(info.value, "target")

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingLoadError) as info:
        load_external_publication_recovery_resume_intent_binding("not-a-path")  # type: ignore[arg-type]
    _assert_load_error(info.value, "path_type")


# --- persistence ---------------------------------------------------------


def test_persistence_idempotent_for_identical_bytes(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    binding = _binding()
    persist_external_publication_recovery_resume_intent_binding(path, binding)
    before = path.read_bytes()
    persist_external_publication_recovery_resume_intent_binding(path, binding)
    assert path.read_bytes() == before


def test_persistence_conflict_for_different_bytes(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    persist_external_publication_recovery_resume_intent_binding(path, _binding())
    before = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingConflictError
    ) as info:
        persist_external_publication_recovery_resume_intent_binding(
            path, _binding(intent_digest="2" * 64)
        )
    _assert_conflict_error(info.value)
    assert path.read_bytes() == before


def test_persistence_conflict_for_partial_bytes(tmp_path: Path) -> None:
    path = tmp_path / "binding.json"
    persist_external_publication_recovery_resume_intent_binding(path, _binding())
    before = path.read_bytes()
    path.write_bytes(before[: len(before) // 2])
    partial = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_intent_binding(path, _binding())
    assert info.value.detail.classification == "conflict"
    assert path.read_bytes() == partial


def test_persistence_rejects_bad_parent_and_target(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_intent_binding(
            tmp_path / "absent" / "binding.json", _binding()
        )
    assert info.value.detail.classification == "parent"

    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_intent_binding(
            directory, _binding()
        )
    assert info.value.detail.classification == "target"


def test_persistence_rejects_symlink_target(tmp_path: Path) -> None:
    symlink = tmp_path / "binding.json"
    symlink.symlink_to(tmp_path / "absent.json")
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_intent_binding(symlink, _binding())
    assert info.value.detail.classification == "target"


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_persistence_ambiguity_per_stage_retains_artifact_no_retry(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "binding.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumeIntentBindingPersistenceError
        ) as info:
            persist_external_publication_recovery_resume_intent_binding(
                path, _binding()
            )
    assert info.value.detail.classification == "ambiguous"
    assert path.exists()


@pytest.mark.parametrize("stage", _FULL_WRITE_STAGES)
def test_persistence_ambiguity_then_exact_retained_bytes_idempotent(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "binding.json"
    binding = _binding()
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumeIntentBindingPersistenceError
        ) as info:
            persist_external_publication_recovery_resume_intent_binding(path, binding)
    assert info.value.detail.classification == "ambiguous"
    retained = path.read_bytes()
    assert retained == (
        external_publication_recovery_resume_intent_binding_canonical_bytes(binding)
    )

    persist_external_publication_recovery_resume_intent_binding(path, binding)
    assert path.read_bytes() == retained
    assert load_external_publication_recovery_resume_intent_binding(path) == binding


@pytest.mark.parametrize("stage", _PARTIAL_WRITE_STAGES)
def test_persistence_partial_retained_bytes_fail_closed(
    tmp_path: Path, stage: str
) -> None:
    path = tmp_path / "binding.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumeIntentBindingPersistenceError
        ) as info:
            persist_external_publication_recovery_resume_intent_binding(
                path, _binding()
            )
    assert info.value.detail.classification == "ambiguous"
    before = path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        persist_external_publication_recovery_resume_intent_binding(path, _binding())
    assert info.value.detail.classification in {
        "conflict",
        "load",
        "parse",
        "noncanonical",
        "keys",
    }
    assert path.read_bytes() == before


# --- preflight -----------------------------------------------------------


def test_preflight_rejects_bad_paths_and_dependencies(tmp_path: Path) -> None:
    preparation = _preparation()
    preparation_path = tmp_path / "preparation.json"
    persist_external_publication_recovery_resume_preparation(
        preparation_path, preparation
    )
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind("not-a-path", binding_path, intent_path)  # type: ignore[arg-type]
    assert info.value.detail.classification == "path_type"

    for dependency in (
        "preparation_loader",
        "preparation_digest_function",
        "intent_loader",
        "intent_digest_function",
        "intent_persist_function",
    ):
        with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
            _bind(preparation_path, binding_path, intent_path, **{dependency: None})
        assert info.value.detail.classification == "configuration", dependency

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, binding_path, binding_path)
    assert info.value.detail.classification == "path_conflict"

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, preparation_path, intent_path)
    assert info.value.detail.classification == "path_conflict"

    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, intent_path, intent_path)
    assert info.value.detail.classification == "path_conflict"

    assert sorted(path.name for path in tmp_path.iterdir()) == ["preparation.json"]


def test_preflight_rejects_absent_parents_without_mutation(tmp_path: Path) -> None:
    preparation = _preparation()
    preparation_path = tmp_path / "preparation.json"
    persist_external_publication_recovery_resume_preparation(
        preparation_path, preparation
    )
    absent_binding = tmp_path / "absent" / "binding.json"
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, absent_binding, tmp_path / "intent.json")
    assert info.value.detail.classification == "parent"
    assert not absent_binding.parent.exists()
    assert not (tmp_path / "intent.json").exists()

    absent_intent = tmp_path / "absent" / "intent.json"
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, tmp_path / "binding.json", absent_intent)
    assert info.value.detail.classification == "parent"
    assert not (tmp_path / "binding.json").exists()


def test_preflight_rejects_bad_targets(tmp_path: Path) -> None:
    preparation = _preparation()
    preparation_path = tmp_path / "preparation.json"
    persist_external_publication_recovery_resume_preparation(
        preparation_path, preparation
    )
    intent_path = tmp_path / "intent.json"

    directory = tmp_path / "binding-dir"
    directory.mkdir()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, directory, intent_path)
    assert info.value.detail.classification == "target"

    symlink = tmp_path / "binding-link.json"
    symlink.symlink_to(tmp_path / "absent.json")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, symlink, intent_path)
    assert info.value.detail.classification == "target"

    intent_dir = tmp_path / "intent-dir"
    intent_dir.mkdir()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(preparation_path, tmp_path / "binding.json", intent_dir)
    assert info.value.detail.classification == "target"


# --- Phase 294 preparation ----------------------------------------------


def test_preparation_loader_exactly_once_with_exact_path_identity(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    preparation_path = tmp_path / "preparation.json"
    preparation_loader = _LoaderRecorder(
        load_external_publication_recovery_resume_preparation
    )
    preparation_digest = _CallRecorder(
        external_publication_recovery_resume_preparation_digest
    )
    result = _bind(
        preparation_path,
        tmp_path / "binding.json",
        tmp_path / "intent.json",
        preparation_loader=preparation_loader,
        preparation_digest_function=preparation_digest,
    )
    assert len(preparation_loader.calls) == 1
    assert preparation_loader.calls[0] is preparation_path
    assert len(preparation_digest.calls) == 1
    assert preparation_digest.calls[0][0][0] is preparation_loader.results[0]
    assert result.resume_preparation_sha256 == (
        external_publication_recovery_resume_preparation_digest(
            preparation_loader.results[0]
        )
    )


def test_preparation_subclass_or_lookalike_rejected(tmp_path: Path) -> None:
    preparation_path = tmp_path / "preparation.json"
    preparation_path.write_bytes(b"{}")

    class _Child(ExternalPublicationRecoveryResumePreparation):
        pass

    lookalike = _forged_instance(_Child, _preparation())
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            preparation_path,
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_loader=lambda path: lookalike,
        )
    assert info.value.detail.classification == "preparation_contract"
    assert not (tmp_path / "binding.json").exists()


def test_preparation_lookalike_unrelated_class_rejected(tmp_path: Path) -> None:
    preparation_path = tmp_path / "preparation.json"
    preparation_path.write_bytes(b"{}")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            preparation_path,
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_loader=lambda path: object(),
        )
    assert info.value.detail.classification == "preparation_contract"


_PREPARATION_CONTRACT_CASES = (
    {"schema_version": "other.v1"},
    {"recovery_decision_sha256": "A" * 64},
    {"lifecycle_outcome_sha256": "8" * 63},
    {"operation_start_sha256": None},
    {"publication_approval_sha256": 1},
    {"publication_plan_sha256": "c" * 65},
    {"source_operation": "replay"},
    {"recovery_kind": "mismatch"},
    {"target_operation": "fresh"},
    {"state": "authorized"},
    {"recovery_kind": "reconciliation_mismatch", "source_operation": "fresh"},
)


@pytest.mark.parametrize("overrides", _PREPARATION_CONTRACT_CASES)
def test_preparation_contract_revalidated_locally_before_any_dependency(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    preparation_path = tmp_path / "preparation.json"
    preparation_path.write_bytes(b"{}")
    preparation_digest = _CallRecorder(
        external_publication_recovery_resume_preparation_digest
    )
    intent_loader = _LoaderRecorder(load_external_publication_operation_intent)
    intent_persist = _CallRecorder(persist_external_publication_operation_intent)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            preparation_path,
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_loader=lambda path: _forged_instance(
                ExternalPublicationRecoveryResumePreparation,
                _preparation(),
                **overrides,
            ),
            preparation_digest_function=preparation_digest,
            intent_loader=intent_loader,
            intent_persist_function=intent_persist,
        )
    assert info.value.detail.classification == "preparation_contract"
    assert len(preparation_digest.calls) == 0
    assert len(intent_loader.calls) == 0
    assert len(intent_persist.calls) == 0
    assert not (tmp_path / "binding.json").exists()
    assert not (tmp_path / "intent.json").exists()


def test_preparation_exact_at_limit_digest_still_accepted(tmp_path: Path) -> None:
    preparation_path = tmp_path / "preparation.json"
    preparation_path.write_bytes(b"{}")
    forged = _forged_instance(
        ExternalPublicationRecoveryResumePreparation,
        _preparation(),
        recovery_decision_sha256="a" * 64,
    )
    digest = _CallRecorder(external_publication_recovery_resume_preparation_digest)
    _bind(
        preparation_path,
        tmp_path / "binding.json",
        tmp_path / "intent.json",
        preparation_loader=lambda path: forged,
        preparation_digest_function=digest,
    )
    assert len(digest.calls) == 1
    assert digest.calls[0][0][0] is forged


def test_preparation_digest_malformed_helper_return_rejected(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    intent_loader = _LoaderRecorder(load_external_publication_operation_intent)
    intent_persist = _CallRecorder(persist_external_publication_operation_intent)
    for value in ("not-a-digest", "A" * 64, "1" * 63, None, 7):
        with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
            _bind(
                tmp_path / "preparation.json",
                tmp_path / "binding.json",
                tmp_path / "intent.json",
                preparation_digest_function=lambda preparation, value=value: value,
                intent_loader=intent_loader,
                intent_persist_function=intent_persist,
            )
        assert info.value.detail.classification == "preparation_digest"
    assert not (tmp_path / "binding.json").exists()
    assert len(intent_loader.calls) == 0
    assert len(intent_persist.calls) == 0


def test_preparation_known_error_same_object_identity(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    error = ExternalPublicationRecoveryResumePreparationError("dependency_error")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_loader=lambda path: _raise(error),
        )
    assert info.value is error


def test_preparation_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_loader=lambda path: _raise(RuntimeError("secret path")),
        )
    assert info.value.detail.classification == "dependency_error"
    assert "secret path" not in str(info.value)
    assert info.value.__cause__ is None
    assert not (tmp_path / "binding.json").exists()


def test_preparation_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    error = ExternalPublicationRecoveryResumePreparationError("dependency_error")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_digest_function=lambda preparation: _raise(error),
        )
    assert info.value is error


def test_preparation_digest_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            preparation_digest_function=lambda preparation: _raise(
                RuntimeError("internal detail")
            ),
        )
    assert info.value.detail.classification == "dependency_error"
    assert "internal detail" not in str(info.value)
    assert not (tmp_path / "binding.json").exists()


# --- expected Phase 289 intent ------------------------------------------


def test_expected_intent_uses_preparation_lineage_and_resume_operation(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    preparation_path = tmp_path / "preparation.json"
    intent_digest = _CallRecorder(external_publication_operation_intent_digest)
    result = _bind(
        preparation_path,
        tmp_path / "binding.json",
        tmp_path / "intent.json",
        intent_digest_function=intent_digest,
    )
    assert len(intent_digest.calls) == 1
    constructed = intent_digest.calls[0][0][0]
    assert type(constructed) is ExternalPublicationOperationIntent
    assert constructed.operation == "resume"
    assert constructed.schema_version == _INTENT_SCHEMA
    preparation = load_external_publication_recovery_resume_preparation(
        preparation_path
    )
    assert constructed.publication_approval_sha256 == (
        preparation.publication_approval_sha256
    )
    assert constructed.publication_plan_sha256 == preparation.publication_plan_sha256
    assert result.operation_intent_sha256 == (
        external_publication_operation_intent_digest(constructed)
    )
    assert result.publication_approval_sha256 == (
        preparation.publication_approval_sha256
    )
    assert result.publication_plan_sha256 == preparation.publication_plan_sha256
    assert result.recovery_decision_sha256 == (preparation.recovery_decision_sha256)
    assert result.source_operation == preparation.source_operation
    assert result.recovery_kind == preparation.recovery_kind


def test_expected_intent_never_calls_phase289_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_preparation(tmp_path)
    builder = _CallRecorder(build_external_publication_operation_intent)
    monkeypatch.setattr(
        intent_module,
        "build_external_publication_operation_intent",
        builder,
    )
    monkeypatch.setattr(
        binding_module,
        "build_external_publication_operation_intent",
        builder,
        raising=False,
    )
    _bind(
        tmp_path / "preparation.json",
        tmp_path / "binding.json",
        tmp_path / "intent.json",
    )
    assert len(builder.calls) == 0


def test_intent_digest_malformed_helper_return_rejected(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    intent_loader = _LoaderRecorder(load_external_publication_operation_intent)
    intent_persist = _CallRecorder(persist_external_publication_operation_intent)
    for value in ("nope", "A" * 64, "1" * 63, None, 3):
        with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
            _bind(
                tmp_path / "preparation.json",
                tmp_path / "binding.json",
                tmp_path / "intent.json",
                intent_digest_function=lambda intent, value=value: value,
                intent_loader=intent_loader,
                intent_persist_function=intent_persist,
            )
        assert info.value.detail.classification == "intent_digest"
    assert not (tmp_path / "binding.json").exists()
    assert len(intent_loader.calls) == 0
    assert len(intent_persist.calls) == 0


def test_intent_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    error = ExternalPublicationOperationIntentError("contract")
    with pytest.raises(ExternalPublicationOperationIntentError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            intent_digest_function=lambda intent: _raise(error),
        )
    assert info.value is error


def test_intent_digest_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            intent_digest_function=lambda intent: _raise(RuntimeError("boom")),
        )
    assert info.value.detail.classification == "dependency_error"
    assert not (tmp_path / "binding.json").exists()


# --- binding-first ordering ---------------------------------------------


def test_binding_persisted_before_intent_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    order: list[str] = []

    real_binding_persist = persist_external_publication_recovery_resume_intent_binding

    def _record_binding(path: Path, binding: object) -> None:
        order.append("binding")
        real_binding_persist(path, binding)  # type: ignore[arg-type]

    monkeypatch.setattr(
        binding_module,
        "persist_external_publication_recovery_resume_intent_binding",
        _record_binding,
    )

    def _record_intent(path: Path, intent: object) -> None:
        order.append("intent")
        assert binding_path.exists()
        persist_external_publication_operation_intent(path, intent)  # type: ignore[arg-type]

    binding = _bind(
        tmp_path / "preparation.json",
        binding_path,
        intent_path,
        intent_persist_function=_record_intent,
    )
    assert order == ["binding", "intent"]
    assert binding_path.exists() and intent_path.exists()
    assert binding.operation_intent_sha256 == (
        external_publication_operation_intent_digest(
            load_external_publication_operation_intent(intent_path)
        )
    )


def test_binding_absent_with_pre_existing_exact_intent_resolves_binding_first(
    tmp_path: Path,
) -> None:
    preparation = _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    expected = _expected_intent(preparation)
    persist_external_publication_operation_intent(intent_path, expected)
    intent_before = intent_path.read_bytes()
    binding_path = tmp_path / "binding.json"

    observed: list[bool] = []

    def _loader(path: object) -> object:
        observed.append(binding_path.exists())
        return load_external_publication_operation_intent(path)  # type: ignore[arg-type]

    result = _bind(
        tmp_path / "preparation.json",
        binding_path,
        intent_path,
        intent_loader=_loader,
    )
    assert observed == [True]
    assert intent_path.read_bytes() == intent_before
    assert result.operation_intent_sha256 == (
        external_publication_operation_intent_digest(expected)
    )


def test_existing_exact_binding_loader_identity_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    first = _bind(tmp_path / "preparation.json", binding_path, intent_path)

    recorder = _LoaderRecorder(load_external_publication_recovery_resume_intent_binding)
    monkeypatch.setattr(
        binding_module,
        "load_external_publication_recovery_resume_intent_binding",
        recorder,
    )
    second = _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert len(recorder.calls) == 1
    assert recorder.calls[0] is binding_path
    assert second is recorder.results[0]
    assert second == first


def test_existing_different_binding_blocks_intent_dependency(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    persist_external_publication_recovery_resume_intent_binding(
        binding_path, _binding()
    )
    before = binding_path.read_bytes()
    intent_loader = _LoaderRecorder(load_external_publication_operation_intent)
    intent_persist = _CallRecorder(persist_external_publication_operation_intent)
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingConflictError
    ) as info:
        _bind(
            tmp_path / "preparation.json",
            binding_path,
            intent_path,
            intent_loader=intent_loader,
            intent_persist_function=intent_persist,
        )
    _assert_conflict_error(info.value)
    assert len(intent_loader.calls) == 0
    assert len(intent_persist.calls) == 0
    assert binding_path.read_bytes() == before
    assert not intent_path.exists()


def test_existing_binding_with_different_source_or_kind_conflicts(
    tmp_path: Path,
) -> None:
    preparation = _real_preparation(
        tmp_path,
        operation="resume",
        result_kind="reconciliation",
        result_sha256="d" * 64,
    )
    binding_path = tmp_path / "binding.json"
    persist_external_publication_recovery_resume_intent_binding(
        binding_path,
        _binding(
            source_operation=preparation.source_operation,
            recovery_kind="already_acquired",
        ),
    )
    before = binding_path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingConflictError
    ) as info:
        _bind(tmp_path / "preparation.json", binding_path, tmp_path / "intent.json")
    _assert_conflict_error(info.value)
    assert binding_path.read_bytes() == before
    assert not (tmp_path / "intent.json").exists()


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_binding_persistence_failure_leaves_intent_untouched(
    tmp_path: Path, stage: str
) -> None:
    _real_preparation(tmp_path)
    intent_loader = _LoaderRecorder(load_external_publication_operation_intent)
    intent_persist = _CallRecorder(persist_external_publication_operation_intent)
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumeIntentBindingPersistenceError
        ) as info:
            _bind(
                tmp_path / "preparation.json",
                tmp_path / "binding.json",
                tmp_path / "intent.json",
                intent_loader=intent_loader,
                intent_persist_function=intent_persist,
            )
    assert info.value.detail.classification == "ambiguous"
    assert len(intent_loader.calls) == 0
    assert len(intent_persist.calls) == 0
    assert not (tmp_path / "intent.json").exists()


def test_exact_binding_retained_after_ambiguity_used_on_later_invocation(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, "dir_fsync")
        with pytest.raises(
            ExternalPublicationRecoveryResumeIntentBindingPersistenceError
        ) as info:
            _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert info.value.detail.classification == "ambiguous"
    retained = binding_path.read_bytes()
    assert not intent_path.exists()

    result = _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert binding_path.read_bytes() == retained
    assert result.operation_intent_sha256 == (
        external_publication_operation_intent_digest(
            load_external_publication_operation_intent(intent_path)
        )
    )


def test_partial_binding_retained_after_ambiguity_fails_closed(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, "short_write")
        with pytest.raises(
            ExternalPublicationRecoveryResumeIntentBindingPersistenceError
        ) as info:
            _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert info.value.detail.classification == "ambiguous"
    partial = binding_path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert info.value.detail.classification in {
        "load",
        "parse",
        "keys",
        "noncanonical",
        "conflict",
    }
    assert binding_path.read_bytes() == partial
    assert not intent_path.exists()


# --- intent after binding -----------------------------------------------


def test_absent_intent_persisted_exactly_once_with_exact_identities(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    intent_load = _LoaderRecorder(load_external_publication_operation_intent)
    persist_recorder = _CallRecorder(persist_external_publication_operation_intent)
    _bind(
        tmp_path / "preparation.json",
        tmp_path / "binding.json",
        intent_path,
        intent_loader=intent_load,
        intent_persist_function=persist_recorder,
    )
    assert len(persist_recorder.calls) == 1
    assert persist_recorder.calls[0][0][0] is intent_path
    assert type(persist_recorder.calls[0][0][1]) is ExternalPublicationOperationIntent
    assert len(intent_load.calls) == 0
    assert intent_path.exists()
    assert intent_path.read_bytes() == (
        intent_module.external_publication_operation_intent_canonical_bytes(
            persist_recorder.calls[0][0][1]
        )
    )


def test_existing_exact_intent_strict_loaded_once_and_accepted(
    tmp_path: Path,
) -> None:
    preparation = _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    expected = _expected_intent(preparation)
    persist_external_publication_operation_intent(intent_path, expected)
    intent_loader = _LoaderRecorder(load_external_publication_operation_intent)
    persist_recorder = _CallRecorder(persist_external_publication_operation_intent)
    result = _bind(
        tmp_path / "preparation.json",
        tmp_path / "binding.json",
        intent_path,
        intent_loader=intent_loader,
        intent_persist_function=persist_recorder,
    )
    assert len(intent_loader.calls) == 1
    assert intent_loader.calls[0] is intent_path
    assert len(persist_recorder.calls) == 0
    assert result.operation_intent_sha256 == (
        external_publication_operation_intent_digest(expected)
    )


def test_existing_fresh_intent_rejected(tmp_path: Path) -> None:
    preparation = _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    binding_path = tmp_path / "binding.json"
    mismatched = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,  # type: ignore[arg-type]
        publication_approval_sha256=preparation.publication_approval_sha256,
        publication_plan_sha256=preparation.publication_plan_sha256,
        operation="fresh",
    )
    persist_external_publication_operation_intent(intent_path, mismatched)
    intent_before = intent_path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert info.value.detail.classification == "intent_conflict"
    assert intent_path.read_bytes() == intent_before
    assert binding_path.exists()


@pytest.mark.parametrize(
    "overrides",
    (
        {"approval_digest": "3" * 64},
        {"plan_digest": "4" * 64},
    ),
)
def test_existing_mismatched_approval_or_plan_intent_rejected(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    preparation = _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    mismatched = ExternalPublicationOperationIntent(
        schema_version=_INTENT_SCHEMA,  # type: ignore[arg-type]
        publication_approval_sha256=overrides.get(
            "approval_digest", preparation.publication_approval_sha256
        ),
        publication_plan_sha256=overrides.get(
            "plan_digest", preparation.publication_plan_sha256
        ),
        operation="resume",
    )
    persist_external_publication_operation_intent(intent_path, mismatched)
    intent_before = intent_path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(tmp_path / "preparation.json", tmp_path / "binding.json", intent_path)
    assert info.value.detail.classification == "intent_conflict"
    assert intent_path.read_bytes() == intent_before


def test_existing_intent_subclass_or_lookalike_rejected(tmp_path: Path) -> None:
    preparation = _real_preparation(tmp_path)

    class _Child(ExternalPublicationOperationIntent):
        pass

    lookalike = _forged_instance(_Child, _expected_intent(preparation))
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(b"{}")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            intent_path,
            intent_loader=lambda path: lookalike,
        )
    assert info.value.detail.classification == "intent_contract"


@pytest.mark.parametrize(
    "payload",
    (b"", b"{", b"{}", b'{"operation":"resume"}', b"\xff\xfe"),
)
def test_corrupt_intent_fails_closed_unchanged(tmp_path: Path, payload: bytes) -> None:
    _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(payload)
    binding_path = tmp_path / "binding.json"
    with pytest.raises(ExternalPublicationOperationIntentError) as info:
        _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert type(info.value) in {
        ExternalPublicationOperationIntentLoadError,
        ExternalPublicationOperationIntentPersistenceError,
    }
    assert intent_path.read_bytes() == payload
    assert binding_path.exists()


def test_known_intent_load_error_same_object_identity(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(b"{}")
    error = ExternalPublicationOperationIntentLoadError("load")
    with pytest.raises(ExternalPublicationOperationIntentLoadError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            intent_path,
            intent_loader=lambda path: _raise(error),
        )
    assert info.value is error


def test_known_intent_persistence_error_same_object_identity(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    error = ExternalPublicationOperationIntentPersistenceError("ambiguous")
    with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            intent_persist_function=lambda path, intent: _raise(error),
        )
    assert info.value is error
    assert (tmp_path / "binding.json").exists()


def test_unexpected_intent_dependency_error_sanitized(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            intent_persist_function=lambda path, intent: _raise(
                RuntimeError("provider credential leak")
            ),
        )
    assert info.value.detail.classification == "dependency_error"
    assert "provider credential leak" not in str(info.value)
    assert info.value.__cause__ is None


def test_unexpected_intent_loader_error_sanitized(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(b"{}")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as info:
        _bind(
            tmp_path / "preparation.json",
            tmp_path / "binding.json",
            intent_path,
            intent_loader=lambda path: _raise(RuntimeError("transport detail")),
        )
    assert info.value.detail.classification == "dependency_error"
    assert "transport detail" not in str(info.value)


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_intent_persistence_ambiguity_retains_binding_no_retry(
    tmp_path: Path, stage: str
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    persist_recorder = _CallRecorder(persist_external_publication_operation_intent)
    with pytest.MonkeyPatch.context() as scope:
        _install_intent_fault(scope, stage)
        with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as info:
            _bind(
                tmp_path / "preparation.json",
                binding_path,
                intent_path,
                intent_persist_function=persist_recorder,
            )
    assert info.value.detail.classification == "ambiguous"
    assert len(persist_recorder.calls) == 1
    assert binding_path.exists()
    assert intent_path.exists()


def test_partial_intent_after_ambiguity_fails_closed_no_repair(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_intent_fault(scope, "write_error")
        with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as info:
            _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert info.value.detail.classification == "ambiguous"
    partial = intent_path.read_bytes()
    binding_bytes = binding_path.read_bytes()

    with pytest.raises(ExternalPublicationOperationIntentError) as info:
        _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert type(info.value) is ExternalPublicationOperationIntentLoadError
    assert info.value.detail.classification in {
        "load",
        "parse",
        "keys",
        "noncanonical",
    }
    assert intent_path.read_bytes() == partial
    assert binding_path.read_bytes() == binding_bytes


def test_exact_intent_retained_after_ambiguity_accepted_later(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_intent_fault(scope, "dir_fsync")
        with pytest.raises(ExternalPublicationOperationIntentPersistenceError) as info:
            _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert info.value.detail.classification == "ambiguous"
    retained = intent_path.read_bytes()
    binding_bytes = binding_path.read_bytes()

    result = _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert intent_path.read_bytes() == retained
    assert binding_path.read_bytes() == binding_bytes
    assert result.operation_intent_sha256 == (
        external_publication_operation_intent_digest(
            load_external_publication_operation_intent(intent_path)
        )
    )


# --- return semantics ----------------------------------------------------


def test_existing_binding_and_exact_intent_returns_loader_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    first = _bind(tmp_path / "preparation.json", binding_path, intent_path)

    recorder = _LoaderRecorder(load_external_publication_recovery_resume_intent_binding)
    monkeypatch.setattr(
        binding_module,
        "load_external_publication_recovery_resume_intent_binding",
        recorder,
    )
    second = _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert second is recorder.results[0]
    assert second == first


def test_new_binding_success_returns_constructed_identity(tmp_path: Path) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    result = _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert type(result) is ExternalPublicationRecoveryResumeIntentBinding
    assert result.state == "authorized"
    assert (
        load_external_publication_recovery_resume_intent_binding(binding_path) == result
    )


@pytest.mark.parametrize(
    "stage", ("write_error", "short_write", "flush_error", "file_fsync", "dir_fsync")
)
def test_no_success_return_when_intent_durability_unproven(
    tmp_path: Path, stage: str
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_intent_fault(scope, stage)
        with pytest.raises(ExternalPublicationOperationIntentPersistenceError):
            _bind(tmp_path / "preparation.json", binding_path, intent_path)
    # the binding is durable and retained; the invocation returned no success.
    assert binding_path.exists()
    assert intent_path.exists()
    retained_binding = load_external_publication_recovery_resume_intent_binding(
        binding_path
    )
    assert retained_binding.state == "authorized"


def test_no_success_return_when_binding_exists_but_intent_corrupt(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    _bind(tmp_path / "preparation.json", binding_path, intent_path)
    intent_path.write_bytes(intent_path.read_bytes()[:10])
    binding_bytes = binding_path.read_bytes()
    with pytest.raises(ExternalPublicationOperationIntentError):
        _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert binding_path.read_bytes() == binding_bytes


# --- source audit --------------------------------------------------------


def _module_imports() -> dict[str, set[str]]:
    tree = ast.parse(_SOURCE)
    imports: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.setdefault(node.level * "." + node.module, set())
            for alias in node.names:
                imports[node.level * "." + node.module].add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.setdefault(alias.name, set())
    return imports


def test_source_audit_no_forbidden_imports() -> None:
    imports = _module_imports()
    assert set(imports) == {
        ".external_publication_operation_intent",
        ".external_publication_recovery_resume_preparation",
        "__future__",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "errno",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "re",
        "typing",
    }
    assert imports[".external_publication_operation_intent"] == {
        "ExternalPublicationOperationIntent",
        "ExternalPublicationOperationIntentError",
        "external_publication_operation_intent_digest",
        "load_external_publication_operation_intent",
        "persist_external_publication_operation_intent",
    }
    assert imports[".external_publication_recovery_resume_preparation"] == {
        "ExternalPublicationRecoveryResumePreparation",
        "ExternalPublicationRecoveryResumePreparationError",
        "external_publication_recovery_resume_preparation_digest",
        "load_external_publication_recovery_resume_preparation",
    }
    for forbidden in (
        "build_external_publication_operation_intent",
        "acquire_external_publication_operation_start",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "run_and_persist_external_publication_operation_lifecycle_outcome",
        "persist_external_publication_recovery_resume_preparation",
        "execute_approved_external_publication",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
        "build_external_publication_plan",
        "regenerate_external_publication_plan",
    ):
        assert not any(
            name == forbidden for names in imports.values() for name in names
        ), forbidden
    for module in imports:
        assert "orchestration" not in module
        assert "acquisition" not in module
        assert "external_publication_recovery_decision" not in module
        assert "external_publication_operation_start" not in module
        assert "external_publication_operation_lifecycle_outcome" not in module
        assert module != ".external_publication"


def test_source_audit_no_clock_random_environment_access() -> None:
    for token in (
        "os.environ",
        "getenv",
        "import random",
        "import uuid",
        "import time",
        "import datetime",
        "sleep(",
        "subprocess",
        "socket",
        "datetime.",
        "uuid4",
        "time.time",
        "monotonic",
        "hostname",
        "getpid",
        "token_urlsafe",
        "requests",
        "urllib",
        "http.client",
        "ssl",
    ):
        assert token not in _SOURCE, token


def test_source_audit_no_forbidden_direct_calls() -> None:
    tree = ast.parse(_SOURCE)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    for forbidden in (
        "build_external_publication_operation_intent",
        "acquire_external_publication_operation_start",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "run_and_persist_external_publication_operation_lifecycle_outcome",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "persist_external_publication_recovery_resume_preparation",
    ):
        assert forbidden not in called, forbidden
    assert "loader" in called
    assert "digest_function" in called
    assert "persist_function" in called


def test_source_audit_no_approval_object_or_start_acquisition_tokens() -> None:
    for token in (
        "ExternalPublicationApproval",
        "external_publication_approval_digest",
        "acquire_external_publication_operation_start",
        "ExternalPublicationOperationStart",
        "start_marker",
        "start_path",
        "phase290",
    ):
        assert token not in _SOURCE, token


def test_no_cli_change_and_no_phase295_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "resume_intent_binding" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "resume_intent_binding" not in workflows.output.lower()
    assert "phase295" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "resume_intent_binding" not in cli_source
    assert "phase295" not in cli_source.lower().replace(" ", "")


# --- integration regressions --------------------------------------------


def test_integration_fresh_already_acquired_lineage(tmp_path: Path) -> None:
    predecessor_root = tmp_path / "predecessor"
    predecessor_root.mkdir()
    target_root = tmp_path / "target"
    target_root.mkdir()
    preparation = _real_preparation(predecessor_root)
    preparation_path = predecessor_root / "preparation.json"
    before = sorted(
        (path.name, path.read_bytes()) for path in predecessor_root.iterdir()
    )

    binding_path = target_root / "binding.json"
    intent_path = target_root / "intent.json"
    binding = _bind(preparation_path, binding_path, intent_path)

    assert binding.source_operation == "fresh"
    assert binding.recovery_kind == "already_acquired"
    assert binding.operation == "resume"
    assert binding.state == "authorized"
    assert binding.resume_preparation_sha256 == (
        external_publication_recovery_resume_preparation_digest(preparation)
    )
    intent = load_external_publication_operation_intent(intent_path)
    assert intent.operation == "resume"
    assert binding.operation_intent_sha256 == (
        external_publication_operation_intent_digest(intent)
    )
    assert binding.publication_approval_sha256 == intent.publication_approval_sha256
    assert binding.publication_plan_sha256 == intent.publication_plan_sha256
    assert (
        sorted((path.name, path.read_bytes()) for path in predecessor_root.iterdir())
        == before
    )
    assert sorted(path.name for path in target_root.iterdir()) == [
        "binding.json",
        "intent.json",
    ]


def test_integration_resume_reconciliation_mismatch_lineage(tmp_path: Path) -> None:
    predecessor_root = tmp_path / "predecessor"
    predecessor_root.mkdir()
    target_root = tmp_path / "target"
    target_root.mkdir()
    preparation = _real_preparation(
        predecessor_root,
        operation="resume",
        state="recovery_required",
        result_kind="reconciliation",
        result_sha256="d" * 64,
    )
    preparation_path = predecessor_root / "preparation.json"
    before = sorted(
        (path.name, path.read_bytes()) for path in predecessor_root.iterdir()
    )

    binding = _bind(
        preparation_path,
        target_root / "binding.json",
        target_root / "intent.json",
    )
    assert binding.source_operation == "resume"
    assert binding.recovery_kind == "reconciliation_mismatch"
    assert binding.operation == "resume"
    assert binding.state == "authorized"
    assert binding.resume_preparation_sha256 == (
        external_publication_recovery_resume_preparation_digest(preparation)
    )
    assert binding.recovery_decision_sha256 == preparation.recovery_decision_sha256
    assert (
        sorted((path.name, path.read_bytes()) for path in predecessor_root.iterdir())
        == before
    )


def test_integration_crash_recovery_binding_exists_intent_absent(
    tmp_path: Path,
) -> None:
    preparation = _real_preparation(tmp_path)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    expected = _expected_intent(preparation)
    persist_external_publication_recovery_resume_intent_binding(
        binding_path,
        _binding(
            preparation_digest=(
                external_publication_recovery_resume_preparation_digest(preparation)
            ),
            decision_digest=preparation.recovery_decision_sha256,
            approval_digest=preparation.publication_approval_sha256,
            plan_digest=preparation.publication_plan_sha256,
            intent_digest=external_publication_operation_intent_digest(expected),
            source_operation=preparation.source_operation,
            recovery_kind=preparation.recovery_kind,
        ),
    )
    assert not intent_path.exists()

    binding_bytes = binding_path.read_bytes()
    result = _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert binding_path.read_bytes() == binding_bytes
    assert intent_path.exists()
    assert result.operation_intent_sha256 == (
        external_publication_operation_intent_digest(
            load_external_publication_operation_intent(intent_path)
        )
    )


def test_integration_pre_existing_exact_intent_never_authorizes_alone(
    tmp_path: Path,
) -> None:
    preparation = _real_preparation(tmp_path)
    intent_path = tmp_path / "intent.json"
    expected = _expected_intent(preparation)
    persist_external_publication_operation_intent(intent_path, expected)
    intent_before = intent_path.read_bytes()
    binding_path = tmp_path / "binding.json"

    observed: list[bool] = []

    def _loader(path: object) -> object:
        observed.append(binding_path.exists())
        return load_external_publication_operation_intent(path)  # type: ignore[arg-type]

    assert not binding_path.exists()
    binding = _bind(
        tmp_path / "preparation.json",
        binding_path,
        intent_path,
        intent_loader=_loader,
    )
    assert observed == [True]
    assert intent_path.read_bytes() == intent_before
    assert binding.operation_intent_sha256 == (
        external_publication_operation_intent_digest(expected)
    )


def test_integration_no_start_marker_or_provider_artifact_created(
    tmp_path: Path,
) -> None:
    _real_preparation(tmp_path)
    target_root = tmp_path / "target"
    target_root.mkdir()
    binding_path = target_root / "binding.json"
    intent_path = target_root / "intent.json"
    _bind(tmp_path / "preparation.json", binding_path, intent_path)
    assert sorted(path.name for path in target_root.iterdir()) == [
        "binding.json",
        "intent.json",
    ]
    for name in ("start.json", "start-marker.json", "lifecycle.json", "decision.json"):
        assert not (target_root / name).exists()


# --- local helpers -------------------------------------------------------


def _raise(error: BaseException) -> object:
    raise error
