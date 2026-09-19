"""Focused provider-free tests for the Phase 296 resume-start authorization.

Phase 296 records one durable, append-only authorization that one exact Phase
295 recovery resume-intent binding together with its exact bound Phase 289
``resume`` operation intent authorize one *future* attempt to acquire the exact
expected Phase 290 resume start identity.  It never acquires a start marker,
never accepts a caller start path, and never reconstructs ``acquired``.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_start_authorization as authorization_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationIntent,
    ExternalPublicationOperationIntentError,
    ExternalPublicationOperationIntentLoadError,
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationPlan,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeIntentBindingLoadError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError,
    ExternalPublicationRecoveryResumeStartAuthorizationConflictError,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail,
    ExternalPublicationRecoveryResumeStartAuthorizationLoadError,
    ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError,
    acquire_external_publication_operation_start,
    approve_external_publication,
    authorize_and_persist_external_publication_recovery_resume_start,
    build_external_publication_operation_intent,
    decide_and_persist_external_publication_recovery,
    external_publication_operation_intent_digest,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_intent_binding_digest,
    external_publication_recovery_resume_start_authorization_canonical_bytes,
    external_publication_recovery_resume_start_authorization_digest,
    load_external_publication_operation_intent,
    load_external_publication_operation_start,
    load_external_publication_recovery_resume_intent_binding,
    load_external_publication_recovery_resume_start_authorization,
    materialize_and_bind_external_publication_recovery_resume_intent,
    persist_external_publication_operation_intent,
    persist_external_publication_operation_lifecycle_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    prepare_and_persist_external_publication_recovery_resume_lineage,
    serialize_external_publication_recovery_resume_start_authorization_canonical,
)

_AUTHORIZATION_SCHEMA = "external-publication-recovery-resume-start-authorization.v1"
_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_INTENT_SCHEMA = "external-publication-operation-intent.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_LIFECYCLE_SCHEMA = "external-publication-operation-lifecycle-outcome.v1"
_MESSAGE = "external publication recovery resume start authorization is invalid"
_PERSIST_MESSAGE = (
    "external publication recovery resume start authorization persistence failed"
)
_LOAD_MESSAGE = (
    "external publication recovery resume start authorization could not be loaded"
)
_SOURCE = Path(authorization_module.__file__).read_text(encoding="utf-8")
_KEYS = tuple(
    sorted(
        {
            "expected_operation_start_sha256",
            "operation",
            "operation_intent_sha256",
            "publication_approval_sha256",
            "publication_plan_sha256",
            "recovery_decision_sha256",
            "recovery_kind",
            "resume_intent_binding_sha256",
            "resume_preparation_sha256",
            "schema_version",
            "source_operation",
            "state",
        }
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
_FIELD_NAMES = tuple(
    field.name
    for field in dataclasses.fields(ExternalPublicationRecoveryResumeStartAuthorization)
)
_EXPECTED_PARAMETERS = (
    "resume_intent_binding_path",
    "resume_intent_path",
    "resume_start_authorization_path",
    "binding_loader",
    "binding_digest_function",
    "intent_loader",
    "intent_digest_function",
    "start_digest_function",
)
_INJECTED_DEPENDENCIES = frozenset(
    {
        "binding_loader",
        "binding_digest_function",
        "intent_loader",
        "intent_digest_function",
        "start_digest_function",
    }
)
_FUTURE_START_PREFIX = "external-publication-recovery-resume-start-"
_AUTH_FILENAME_PREFIX = "external-publication-recovery-resume-start-authorization-"

_DIGESTS = ("a", "b", "c", "d", "e", "f", "1", "2")


class _StringChild(str):
    pass


def _field_names(cls: type[object]) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}  # type: ignore[arg-type]


def _forged_instance(cls: type[object], source: object, **overrides: object) -> object:
    """Allocate without running validation; every override must be a real field."""
    names: set[str] = set()
    value = object.__new__(cls)  # type: ignore[call-overload]
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        names.add(field.name)
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in overrides.items():
        assert name in names, name
        object.__setattr__(value, name, replacement)
    return value


def _assert_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    )
    assert isinstance(error, ExternalPublicationRecoveryResumeStartAuthorizationError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert (
        type(error.detail)
        is ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail
    )
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError
    )
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict_error(error: ValueError) -> None:
    assert (
        type(error) is ExternalPublicationRecoveryResumeStartAuthorizationConflictError
    )
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


# --- builders ------------------------------------------------------------


def _binding(
    *,
    schema_version: object = _BINDING_SCHEMA,
    resume_preparation_sha256: object = "a" * 64,
    recovery_decision_sha256: object = "b" * 64,
    publication_approval_sha256: object = "c" * 64,
    publication_plan_sha256: object = "d" * 64,
    operation_intent_sha256: object = "e" * 64,
    source_operation: object = "fresh",
    recovery_kind: object = "already_acquired",
    operation: object = "resume",
    state: object = "authorized",
) -> ExternalPublicationRecoveryResumeIntentBinding:
    return ExternalPublicationRecoveryResumeIntentBinding(
        schema_version=schema_version,  # type: ignore[arg-type]
        resume_preparation_sha256=resume_preparation_sha256,  # type: ignore[arg-type]
        recovery_decision_sha256=recovery_decision_sha256,  # type: ignore[arg-type]
        publication_approval_sha256=publication_approval_sha256,  # type: ignore[arg-type]
        publication_plan_sha256=publication_plan_sha256,  # type: ignore[arg-type]
        operation_intent_sha256=operation_intent_sha256,  # type: ignore[arg-type]
        source_operation=source_operation,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
    )


def _intent(
    *,
    schema_version: object = _INTENT_SCHEMA,
    publication_approval_sha256: object = "c" * 64,
    publication_plan_sha256: object = "d" * 64,
    operation: object = "resume",
) -> ExternalPublicationOperationIntent:
    return ExternalPublicationOperationIntent(
        schema_version=schema_version,  # type: ignore[arg-type]
        publication_approval_sha256=publication_approval_sha256,  # type: ignore[arg-type]
        publication_plan_sha256=publication_plan_sha256,  # type: ignore[arg-type]
        operation=operation,  # type: ignore[arg-type]
    )


def _authorization(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    values: dict[str, object] = {
        "schema_version": _AUTHORIZATION_SCHEMA,
        "resume_intent_binding_sha256": "1" * 64,
        "resume_preparation_sha256": "a" * 64,
        "recovery_decision_sha256": "b" * 64,
        "operation_intent_sha256": "2" * 64,
        "expected_operation_start_sha256": "3" * 64,
        "publication_approval_sha256": "c" * 64,
        "publication_plan_sha256": "d" * 64,
        "source_operation": "fresh",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "authorized",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeStartAuthorization(**values)  # type: ignore[arg-type]


# --- dependency recorders ------------------------------------------------


class _CallRecorder:
    """Record every call and optionally delegate, fault, or return a value."""

    def __init__(
        self,
        delegate: object = None,
        fault: object = None,
        result: object = None,
    ) -> None:
        self.delegate = delegate
        self.fault = fault
        self.result = result
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.results: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if isinstance(self.fault, BaseException):
            raise self.fault
        if self.delegate is not None:
            value = self.delegate(*args, **kwargs)  # type: ignore[operator]
            self.results.append(value)
            return value
        self.results.append(self.result)
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def first_positional(self) -> object:
        return self.calls[0][0][0]

    @property
    def first_result(self) -> object:
        return self.results[0]


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


def _install_persistence_fault(
    scope: pytest.MonkeyPatch,
    stage: str,
    *,
    name: str | None = None,
) -> None:
    """Fault-inject exactly one step of the authorization persistence sequence.

    Only the authorization module's own ``os`` name, its own directory-fsync
    helper, and exclusive creation of its own target are shimmed so that no
    other persistence in the invocation is disturbed.
    """

    def _fsync_boom(*args: object, **kwargs: object) -> None:
        raise OSError("fsync failed")

    if stage == "file_fsync":
        scope.setattr(authorization_module, "os", _OsShim(stage))
        return
    if stage == "dir_fsync":
        scope.setattr(
            authorization_module, "_fsync_authorization_directory", _fsync_boom
        )
        return

    real_open = Path.open

    def _fake_open(
        self: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode == "xb" and (name is None or self.name == name):
            real = real_open(self, mode, buffering=0)
            if stage == "close_failure":
                return _CloseFaultHandle(real)
            return _FaultyHandle(real, stage)
        return real_open(self, mode, *args, **kwargs)

    scope.setattr(Path, "open", _fake_open)


# --- real predecessor lineage -------------------------------------------


def _bound_pair(
    *,
    intent_digest: str = "2" * 64,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
    source_operation: str = "fresh",
    recovery_kind: str = "already_acquired",
) -> tuple[object, object]:
    """Build one exactly self-consistent Phase 295 binding + Phase 289 intent."""
    binding = _binding(
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
        operation_intent_sha256=intent_digest,
        source_operation=source_operation,
        recovery_kind=recovery_kind,
    )
    intent = _intent(
        publication_approval_sha256=approval,
        publication_plan_sha256=plan,
    )
    return binding, intent


def _authorize_pair(
    tmp_path: Path,
    *,
    intent_digest: str = "2" * 64,
    binding_digest: str = "1" * 64,
    start_digest: object = "3" * 64,
    approval: str = "c" * 64,
    plan: str = "d" * 64,
    source_operation: str = "fresh",
    recovery_kind: str = "already_acquired",
    target_name: str | None = None,
) -> tuple[ExternalPublicationRecoveryResumeStartAuthorization, _CallRecorder]:
    """Run the boundary with fully injected, self-consistent dependencies."""
    binding, intent = _bound_pair(
        intent_digest=intent_digest,
        approval=approval,
        plan=plan,
        source_operation=source_operation,
        recovery_kind=recovery_kind,
    )
    start_recorder = _CallRecorder(result=start_digest)
    binding_path = tmp_path / "binding.json"
    target = (
        binding_path.parent / target_name
        if target_name is not None
        else _target(binding_path, binding_digest)
    )
    authorization = _authorize(
        binding_path,
        tmp_path / "intent.json",
        target,
        binding_loader=_CallRecorder(result=binding),
        binding_digest_function=_CallRecorder(result=binding_digest),
        intent_loader=_CallRecorder(result=intent),
        intent_digest_function=_CallRecorder(result=intent_digest),
        start_digest_function=start_recorder,
    )
    return authorization, start_recorder


def _plan(regeneration_id: str = "regen-296") -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id=regeneration_id,
        reconciliation_evidence_sha256="d" * 64,
        receipt_sha256="e" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=11,
        provider="future-provider",
        publication_target_sha256="1" * 64,
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


def _seed_phase_294(
    root: Path,
    *,
    operation: str = "fresh",
    result_kind: str = "none",
    regeneration_id: str = "regen-296",
) -> ExternalPublicationRecoveryResumeIntentBinding:
    """Create real Phase 293/292/290 artifacts plus a real Phase 294 preparation."""
    approval = approve_external_publication(
        _plan(regeneration_id),
        approved_by=f"human-reviewer-296-{regeneration_id}",
        approval_id=f"approval-296-{regeneration_id}",
    )
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
        result_kind=result_kind,
        result_sha256=("d" * 64) if result_kind == "reconciliation" else None,
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    persist_external_publication_operation_lifecycle_outcome(lifecycle_path, lifecycle)
    decision_path = root / "decision.json"
    decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="authorize_resume_preparation",  # type: ignore[arg-type]
        decided_by="operator-296",
        decision_id="decision-296",
    )
    preparation_path = root / "preparation.json"
    prepare_and_persist_external_publication_recovery_resume_lineage(
        recovery_decision_path=decision_path,
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        resume_preparation_path=preparation_path,
    )
    return _materialize_binding(root, preparation_path)


def _materialize_binding(
    root: Path, preparation_path: Path
) -> ExternalPublicationRecoveryResumeIntentBinding:
    """Create a real Phase 295 binding and its exact bound Phase 289 intent."""
    return materialize_and_bind_external_publication_recovery_resume_intent(
        resume_preparation_path=preparation_path,
        resume_intent_binding_path=root / "binding.json",
        resume_intent_path=root / "intent.json",
    )


def _seed(
    root: Path,
    *,
    operation: str = "fresh",
    result_kind: str = "none",
    regeneration_id: str = "regen-296",
) -> tuple[Path, Path]:
    """Return exact Phase 295 binding path and exact bound Phase 289 intent path."""
    _seed_phase_294(
        root,
        operation=operation,
        result_kind=result_kind,
        regeneration_id=regeneration_id,
    )
    return root / "binding.json", root / "intent.json"


def _target(binding_path: Path, binding_digest: str = "1" * 64) -> Path:
    """Return the canonical Phase 296 authorization target for one binding."""
    return binding_path.parent / f"{_AUTH_FILENAME_PREFIX}{binding_digest}.json"


def _seeded_target(binding_path: Path) -> Path:
    """Return the canonical target for one real seeded Phase 295 binding."""
    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    return _target(
        binding_path,
        external_publication_recovery_resume_intent_binding_digest(binding),
    )


def _authorize(
    binding_path: Path, intent_path: Path, authorization_path: Path, **kwargs: object
) -> ExternalPublicationRecoveryResumeStartAuthorization:
    return authorize_and_persist_external_publication_recovery_resume_start(
        resume_intent_binding_path=binding_path,
        resume_intent_path=intent_path,
        resume_start_authorization_path=authorization_path,
        **kwargs,  # type: ignore[arg-type]
    )


# --- public surface ------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert set(authorization_module.__all__) == {
        "ExternalPublicationRecoveryResumeStartAuthorization",
        "ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError",
        "ExternalPublicationRecoveryResumeStartAuthorizationConflictError",
        "ExternalPublicationRecoveryResumeStartAuthorizationError",
        "ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail",
        "ExternalPublicationRecoveryResumeStartAuthorizationLoadError",
        "ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError",
        "authorize_and_persist_external_publication_recovery_resume_start",
        "external_publication_recovery_resume_start_authorization_canonical_bytes",
        "external_publication_recovery_resume_start_authorization_digest",
        "load_external_publication_recovery_resume_start_authorization",
        "persist_external_publication_recovery_resume_start_authorization",
        "serialize_external_publication_recovery_resume_start_authorization_canonical",
    }
    for name in authorization_module.__all__:
        assert hasattr(authorization_module, name), name


def test_error_family_hierarchy_and_messages() -> None:
    assert issubclass(
        ExternalPublicationRecoveryResumeStartAuthorizationError, ValueError
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError,
        ExternalPublicationRecoveryResumeStartAuthorizationError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError,
        ExternalPublicationRecoveryResumeStartAuthorizationError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeStartAuthorizationConflictError,
        ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError,
        ExternalPublicationRecoveryResumeStartAuthorizationError,
    )
    detail = ExternalPublicationRecoveryResumeStartAuthorizationFailureDetail(
        classification="conflict"
    )
    assert dataclasses.is_dataclass(detail)
    assert detail.classification == "conflict"
    error = ExternalPublicationRecoveryResumeStartAuthorizationError()
    assert error.detail.classification == "dependency_error"
    assert str(error) == _MESSAGE


def test_signature_defaults_and_no_caller_authority_arguments() -> None:
    signature = inspect.signature(
        authorize_and_persist_external_publication_recovery_resume_start
    )
    assert tuple(signature.parameters) == _EXPECTED_PARAMETERS
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    defaults = {
        name: parameter.default
        for name, parameter in signature.parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }
    assert set(defaults) == _INJECTED_DEPENDENCIES
    from ai_office.engine import (
        external_publication_operation_intent_digest,
        external_publication_operation_start_digest,
        external_publication_recovery_resume_intent_binding_digest,
        load_external_publication_operation_intent,
        load_external_publication_recovery_resume_intent_binding,
    )

    assert defaults["binding_loader"] is (
        load_external_publication_recovery_resume_intent_binding
    )
    assert defaults["binding_digest_function"] is (
        external_publication_recovery_resume_intent_binding_digest
    )
    assert defaults["intent_loader"] is load_external_publication_operation_intent
    assert defaults["intent_digest_function"] is (
        external_publication_operation_intent_digest
    )
    assert defaults["start_digest_function"] is (
        external_publication_operation_start_digest
    )

    for forbidden in (
        "binding",
        "binding_digest",
        "intent",
        "intent_digest",
        "approval",
        "preparation",
        "decision",
        "start_object",
        "expected_start",
        "expected_operation_start",
        "source_operation",
        "recovery_kind",
        "start_path",
        "acquired",
    ):
        for name in signature.parameters:
            if name in _INJECTED_DEPENDENCIES:
                continue
            assert name != forbidden, (name, forbidden)


# --- model ---------------------------------------------------------------


def test_model_field_order_and_frozen() -> None:
    assert _FIELD_NAMES == (
        "schema_version",
        "resume_intent_binding_sha256",
        "resume_preparation_sha256",
        "recovery_decision_sha256",
        "operation_intent_sha256",
        "expected_operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "recovery_kind",
        "operation",
        "state",
    )
    authorization = _authorization()
    with pytest.raises(dataclasses.FrozenInstanceError):
        authorization.state = "acquired"  # type: ignore[misc]


def test_model_accepts_exact_valid_combinations() -> None:
    for source_operation, recovery_kind in (
        ("fresh", "already_acquired"),
        ("resume", "already_acquired"),
        ("resume", "reconciliation_mismatch"),
    ):
        authorization = _authorization(
            source_operation=source_operation, recovery_kind=recovery_kind
        )
        assert authorization.operation == "resume"
        assert authorization.state == "authorized"


def test_model_rejects_reconciliation_mismatch_with_fresh_source() -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorization(
            source_operation="fresh", recovery_kind="reconciliation_mismatch"
        )
    _assert_error(caught.value, "configuration")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "schema_version",
            "external-publication-recovery-resume-start-authorization.v2",
        ),
        ("schema_version", _StringChild(_AUTHORIZATION_SCHEMA)),
        ("resume_intent_binding_sha256", "z" * 64),
        ("resume_intent_binding_sha256", "A" * 64),
        ("resume_intent_binding_sha256", "1" * 63),
        ("resume_intent_binding_sha256", _StringChild("1" * 64)),
        ("resume_preparation_sha256", "not-a-digest"),
        ("recovery_decision_sha256", ""),
        ("operation_intent_sha256", None),
        ("expected_operation_start_sha256", "3" * 65),
        ("publication_approval_sha256", 1),
        ("publication_plan_sha256", b"d" * 64),
        ("source_operation", "resume "),
        ("source_operation", "unknown"),
        ("recovery_kind", "already_acquired "),
        ("recovery_kind", "mismatch"),
        ("operation", "fresh"),
        ("state", "acquired"),
        ("state", "authorized "),
        ("state", _StringChild("authorized")),
    ],
)
def test_model_rejects_non_exact_values(field: str, value: object) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorization(**{field: value})
    _assert_error(caught.value, "configuration")


def test_helpers_reject_subclass_and_forged_instances(tmp_path: Path) -> None:
    authorization = _authorization()

    class _Child(ExternalPublicationRecoveryResumeStartAuthorization):
        pass

    child = object.__new__(_Child)
    for name in _FIELD_NAMES:
        object.__setattr__(child, name, getattr(authorization, name))

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        serialize_external_publication_recovery_resume_start_authorization_canonical(
            child  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "configuration")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        persist_external_publication_recovery_resume_start_authorization(
            _target(tmp_path / "binding.json"),
            child,  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "configuration")
    assert not (_target(tmp_path / "binding.json")).exists()


def test_forged_enum_with_extra_attribute_is_still_rejected() -> None:
    authorization = _authorization()
    forged = _forged_instance(
        ExternalPublicationRecoveryResumeStartAuthorization,
        authorization,
        state="acquired",
    )
    object.__setattr__(forged, "acquired", True)
    object.__setattr__(forged, "start_marker_exists", True)
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        serialize_external_publication_recovery_resume_start_authorization_canonical(
            forged  # type: ignore[arg-type]
        )
    _assert_error(caught.value, "configuration")


# --- canonical / digest / loader / persistence ---------------------------


def test_canonical_json_exact_keys_and_deterministic_digest() -> None:
    authorization = _authorization()
    text = serialize_external_publication_recovery_resume_start_authorization_canonical(
        authorization
    )
    assert tuple(sorted(json.loads(text))) == _KEYS
    assert text == json.dumps(json.loads(text), separators=(",", ":"), sort_keys=True)
    assert " " not in text
    encoded = external_publication_recovery_resume_start_authorization_canonical_bytes(
        authorization
    )
    assert type(encoded) is bytes
    assert encoded == text.encode("utf-8")
    assert external_publication_recovery_resume_start_authorization_digest(
        authorization
    ) == external_publication_recovery_resume_start_authorization_digest(
        _authorization()
    )
    assert (
        external_publication_recovery_resume_start_authorization_digest(authorization)
        == __import__("hashlib").sha256(encoded).hexdigest()
    )


def test_loader_round_trips_exact_bytes(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    authorization = _authorization()
    persist_external_publication_recovery_resume_start_authorization(
        path, authorization
    )
    assert (
        load_external_publication_recovery_resume_start_authorization(path)
        == authorization
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.replace(
            b'"state":"authorized"', b'"state": "authorized"'
        ),
        lambda payload: payload.replace(b",", b", ", 1),
    ],
)
def test_loader_rejects_noncanonical_bytes(tmp_path: Path, mutate: object) -> None:
    path = _target(tmp_path / "binding.json")
    path.write_bytes(
        mutate(  # type: ignore[operator]
            external_publication_recovery_resume_start_authorization_canonical_bytes(
                _authorization()
            )
        )
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "noncanonical")


def test_loader_rejects_reordered_keys_as_noncanonical(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    payload = json.loads(
        external_publication_recovery_resume_start_authorization_canonical_bytes(
            _authorization()
        ).decode("utf-8")
    )
    reordered = {key: payload[key] for key in reversed(tuple(payload))}
    path.write_text(json.dumps(reordered, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "noncanonical")


def test_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    body = external_publication_recovery_resume_start_authorization_canonical_bytes(
        _authorization()
    ).decode("utf-8")
    duplicate = body[:-1] + ',"state":"authorized"}'
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "parse")


def test_loader_rejects_nonstandard_json_constant(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    body = external_publication_recovery_resume_start_authorization_canonical_bytes(
        _authorization()
    ).decode("utf-8")
    path.write_text(
        body.replace('"state":"authorized"', '"state":NaN'), encoding="utf-8"
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "parse")


@pytest.mark.parametrize(
    ("drop", "add"),
    [
        (("state",), ()),
        ((), (("extra", "1"),)),
        (("state", "operation"), ()),
        (("state",), (("extra", "authorized"),)),
    ],
)
def test_loader_rejects_extra_and_missing_keys(
    tmp_path: Path, drop: tuple[str, ...], add: tuple[tuple[str, str], ...]
) -> None:
    path = _target(tmp_path / "binding.json")
    payload = json.loads(
        external_publication_recovery_resume_start_authorization_canonical_bytes(
            _authorization()
        ).decode("utf-8")
    )
    for key in drop:
        payload.pop(key, None)
    for key, value in add:
        payload[key] = value
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "keys")


def test_loader_rejects_malformed_payload(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    path.write_bytes(b"{not json")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "parse")


def test_loader_rejects_invalid_field_values_as_load(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    payload = json.loads(
        external_publication_recovery_resume_start_authorization_canonical_bytes(
            _authorization()
        ).decode("utf-8")
    )
    payload["state"] = "acquired"
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(path)
    _assert_load_error(caught.value, "load")


def test_loader_rejects_oversized_and_non_regular_targets(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 5000)
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(oversized)
    _assert_load_error(caught.value, "size")

    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(directory)
    _assert_load_error(caught.value, "target")

    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(oversized)
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization(symlink)
    _assert_load_error(caught.value, "target")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        load_external_publication_recovery_resume_start_authorization("path")  # type: ignore[arg-type]
    _assert_load_error(caught.value, "path_type")


def test_persistence_idempotent_for_identical_bytes(tmp_path: Path) -> None:
    path = _target(tmp_path / "binding.json")
    authorization = _authorization()
    persist_external_publication_recovery_resume_start_authorization(
        path, authorization
    )
    before = path.read_bytes()
    persist_external_publication_recovery_resume_start_authorization(
        path, authorization
    )
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "replacement",
    [
        b"",
        b'{"state":"authorized"}',
        b"x",
    ],
)
def test_persistence_conflict_leaves_bytes_unchanged(
    tmp_path: Path, replacement: bytes
) -> None:
    path = _target(tmp_path / "binding.json")
    path.write_bytes(replacement)
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationConflictError
    ) as caught:
        persist_external_publication_recovery_resume_start_authorization(
            path, _authorization()
        )
    _assert_conflict_error(caught.value)
    assert path.read_bytes() == replacement


@pytest.mark.parametrize(
    "target", ["missing_parent", "symlink", "directory", "non_regular"]
)
def test_persistence_rejects_bad_targets(tmp_path: Path, target: str) -> None:
    if target == "missing_parent":
        path = tmp_path / "absent" / "authorization.json"
        classification = "parent"
    elif target == "symlink":
        real = tmp_path / "real.json"
        real.write_bytes(b"x")
        path = _target(tmp_path / "binding.json")
        path.symlink_to(real)
        classification = "target"
    elif target == "directory":
        path = _target(tmp_path / "binding.json")
        path.mkdir()
        classification = "target"
    else:
        path = _target(tmp_path / "binding.json")
        os.mkfifo(path)
        classification = "target"

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_start_authorization(
            path, _authorization()
        )
    _assert_persistence_error(caught.value, classification)


def test_persistence_rejects_bad_path_type(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_start_authorization(
            "authorization.json",
            _authorization(),  # type: ignore[arg-type]
        )
    _assert_persistence_error(caught.value, "path_type")


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_persistence_ambiguity_retains_artifact_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = _target(tmp_path / "binding.json")
    _install_persistence_fault(monkeypatch, stage)
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_start_authorization(
            path, _authorization()
        )
    _assert_persistence_error(caught.value, "ambiguous")

    monkeypatch.undo()
    assert path.exists()
    retained = path.read_bytes()
    expected = external_publication_recovery_resume_start_authorization_canonical_bytes(
        _authorization()
    )
    if stage in {"write_error", "short_write"}:
        assert retained != expected
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationConflictError
        ):
            persist_external_publication_recovery_resume_start_authorization(
                path, _authorization()
            )
        assert path.read_bytes() == retained
    else:
        assert retained == expected
        persist_external_publication_recovery_resume_start_authorization(
            path, _authorization()
        )
        assert path.read_bytes() == expected


# --- preflight -----------------------------------------------------------


def test_preflight_rejects_non_exact_paths_and_non_callable_dependencies(
    tmp_path: Path,
) -> None:
    binding = tmp_path / "binding.json"
    intent = tmp_path / "intent.json"
    target = _target(tmp_path / "binding.json")
    binding.write_bytes(b"{}")
    intent.write_bytes(b"{}")

    for bad in ("binding", "intent", "target"):
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
        ) as caught:
            _authorize(
                "binding.json" if bad == "binding" else binding,
                "intent.json" if bad == "intent" else intent,
                "authorization.json" if bad == "target" else target,
            )
        _assert_error(caught.value, "path_type")

    for name in sorted(_INJECTED_DEPENDENCIES):
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
        ) as caught:
            _authorize(binding, intent, target, **{name: "not-callable"})
        _assert_error(caught.value, "configuration")
    assert not target.exists()


def test_preflight_rejects_path_conflicts_and_bad_authorization_target(
    tmp_path: Path,
) -> None:
    binding = tmp_path / "binding.json"
    intent = tmp_path / "intent.json"

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(binding, binding, _target(tmp_path / "binding.json"))
    _assert_error(caught.value, "path_conflict")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(binding, intent, intent)
    _assert_error(caught.value, "path_conflict")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(binding, intent, tmp_path / "absent" / "authorization.json")
    _assert_error(caught.value, "parent")

    directory = tmp_path / "directory.json"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(binding, intent, directory)
    _assert_error(caught.value, "target")

    real = tmp_path / "real.json"
    real.write_bytes(b"x")
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(real)
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(binding, intent, symlink)
    _assert_error(caught.value, "target")
    assert real.read_bytes() == b"x"


# --- Phase 295 binding ---------------------------------------------------


def test_binding_loader_once_with_exact_path_identity_and_no_mutation(
    tmp_path: Path,
) -> None:
    binding = _binding()
    loader = _CallRecorder(result=binding)
    digest = _CallRecorder(result="1" * 64)
    intent = _intent()
    intent_loader = _CallRecorder(result=intent)
    intent_digest = _CallRecorder(result="2" * 64)
    start_digest = _CallRecorder(result="3" * 64)
    binding_path = tmp_path / "binding.json"
    intent_path = tmp_path / "intent.json"
    target = _target(tmp_path / "binding.json")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            binding_path,
            intent_path,
            target,
            binding_loader=loader,
            binding_digest_function=digest,
            intent_loader=intent_loader,
            intent_digest_function=intent_digest,
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "intent_lineage")
    assert loader.call_count == 1
    assert loader.first_positional is binding_path
    assert digest.call_count == 1
    assert digest.first_positional is binding
    assert intent_loader.call_count == 1
    assert intent_loader.first_positional is intent_path
    assert intent_digest.call_count == 1
    assert intent_digest.first_positional is intent
    assert start_digest.call_count == 0
    assert not target.exists()


def test_binding_exact_model_only_subclass_and_lookalike_rejected(
    tmp_path: Path,
) -> None:
    valid = _binding()

    class _Child(ExternalPublicationRecoveryResumeIntentBinding):
        pass

    child = object.__new__(_Child)
    for field in dataclasses.fields(valid):
        object.__setattr__(child, field.name, getattr(valid, field.name))

    for lookalike in (child, object(), None, "binding"):
        digest = _CallRecorder(result="1" * 64)
        intent_loader = _CallRecorder(result=_intent())
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
        ) as caught:
            _authorize(
                tmp_path / "binding.json",
                tmp_path / "intent.json",
                _target(tmp_path / "binding.json"),
                binding_loader=_CallRecorder(result=lookalike),
                binding_digest_function=digest,
                intent_loader=intent_loader,
            )
        _assert_error(caught.value, "binding_contract")
        assert digest.call_count == 0
        assert intent_loader.call_count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "external-publication-recovery-resume-intent-binding.v2"),
        ("resume_preparation_sha256", "z" * 64),
        ("recovery_decision_sha256", "b" * 63),
        ("publication_approval_sha256", _StringChild("c" * 64)),
        ("publication_plan_sha256", None),
        ("operation_intent_sha256", "E" * 64),
        ("source_operation", "other"),
        ("recovery_kind", "mismatch"),
        ("operation", "fresh"),
        ("state", "acquired"),
    ],
)
def test_binding_fields_locally_revalidated_even_with_fake_digest(
    tmp_path: Path, field: str, value: object
) -> None:
    forged = _forged_instance(
        ExternalPublicationRecoveryResumeIntentBinding, _binding(), **{field: value}
    )
    digest = _CallRecorder(result="1" * 64)
    intent_loader = _CallRecorder(result=_intent())
    intent_digest = _CallRecorder(result="2" * 64)
    start_digest = _CallRecorder(result="3" * 64)
    target = _target(tmp_path / "binding.json")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            target,
            binding_loader=_CallRecorder(result=forged),
            binding_digest_function=digest,
            intent_loader=intent_loader,
            intent_digest_function=intent_digest,
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "binding_contract")
    assert digest.call_count == 0
    assert intent_loader.call_count == 0
    assert intent_digest.call_count == 0
    assert start_digest.call_count == 0
    assert not target.exists()


def test_binding_reconciliation_mismatch_with_fresh_source_rejected(
    tmp_path: Path,
) -> None:
    forged = _forged_instance(
        ExternalPublicationRecoveryResumeIntentBinding,
        _binding(),
        recovery_kind="reconciliation_mismatch",
        source_operation="fresh",
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=forged),
        )
    _assert_error(caught.value, "binding_contract")


def test_binding_digest_exactly_once_and_malformed_rejected(tmp_path: Path) -> None:
    binding = _binding()
    loaded: list[object] = []
    digest = _CallRecorder(result="not-a-digest")
    intent_loader = _CallRecorder(result=_intent())

    def _digest(value: object) -> object:
        loaded.append(value)
        return digest(value)

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=binding),
            binding_digest_function=_digest,
            intent_loader=intent_loader,
        )
    _assert_error(caught.value, "binding_digest")
    assert loaded == [binding]
    assert intent_loader.call_count == 0


def test_binding_loader_known_error_propagates_by_object_identity(
    tmp_path: Path,
) -> None:
    error = ExternalPublicationRecoveryResumeIntentBindingLoadError("target")
    with pytest.raises(
        ExternalPublicationRecoveryResumeIntentBindingLoadError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(fault=error),
        )
    assert caught.value is error


def test_binding_digest_known_error_propagates_by_object_identity(
    tmp_path: Path,
) -> None:
    error = ExternalPublicationRecoveryResumeIntentBindingError("dependency_error")
    with pytest.raises(ExternalPublicationRecoveryResumeIntentBindingError) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(fault=error),
        )
    assert caught.value is error


def test_binding_unexpected_errors_sanitize_to_dependency_error(
    tmp_path: Path,
) -> None:
    for fault in (RuntimeError("boom"), ValueError("boom"), OSError("boom")):
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
        ) as caught:
            _authorize(
                tmp_path / "binding.json",
                tmp_path / "intent.json",
                _target(tmp_path / "binding.json"),
                binding_loader=_CallRecorder(fault=fault),
            )
        _assert_error(caught.value, "dependency_error")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(fault=RuntimeError("boom")),
        )
    _assert_error(caught.value, "dependency_error")


# --- Phase 289 intent ---------------------------------------------------


def test_binding_validated_and_digested_before_intent_loader(tmp_path: Path) -> None:
    order: list[str] = []

    def _binding_loader(path: object) -> object:
        order.append("binding_loader")
        return _binding()

    binding = _binding()

    def _binding_digest(value: object) -> object:
        order.append("binding_digest")
        return "1" * 64

    def _intent_loader(path: object) -> object:
        order.append("intent_loader")
        return _intent()

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ):
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_binding_loader,
            binding_digest_function=_binding_digest,
            intent_loader=_intent_loader,
        )
    assert order == ["binding_loader", "binding_digest", "intent_loader"]
    assert binding.state == "authorized"


def test_malformed_binding_digest_blocks_intent_loading(tmp_path: Path) -> None:
    intent_loader = _CallRecorder(result=_intent())
    target = _target(tmp_path / "binding.json")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            target,
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=intent_loader,
            intent_digest_function=_CallRecorder(result="bad"),
        )
    _assert_error(caught.value, "intent_digest")
    assert intent_loader.call_count == 1
    assert not target.exists()


def test_intent_exact_model_only_and_lookalikes_rejected(tmp_path: Path) -> None:
    valid = _intent()

    class _Child(ExternalPublicationOperationIntent):
        pass

    child = object.__new__(_Child)
    for field in dataclasses.fields(valid):
        object.__setattr__(child, field.name, getattr(valid, field.name))

    for lookalike in (child, object(), None, {"operation": "resume"}):
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
        ) as caught:
            _authorize(
                tmp_path / "binding.json",
                tmp_path / "intent.json",
                _target(tmp_path / "binding.json"),
                binding_loader=_CallRecorder(result=_binding()),
                binding_digest_function=_CallRecorder(result="1" * 64),
                intent_loader=_CallRecorder(result=lookalike),
            )
        _assert_error(caught.value, "intent_contract")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "external-publication-operation-intent.v2"),
        ("publication_approval_sha256", "C" * 64),
        ("publication_plan_sha256", "d" * 63),
        ("operation", "fresh"),
        ("operation", "resume "),
    ],
)
def test_intent_locally_revalidated_with_fake_digest_helper(
    tmp_path: Path, field: str, value: object
) -> None:
    forged = _forged_instance(
        ExternalPublicationOperationIntent, _intent(), **{field: value}
    )
    start_digest = _CallRecorder(result="3" * 64)
    target = _target(tmp_path / "binding.json")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            target,
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=forged),
            intent_digest_function=_CallRecorder(result="2" * 64),
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "intent_contract")
    assert start_digest.call_count == 0
    assert not target.exists()


def test_intent_digest_exactly_once_with_exact_loaded_object(tmp_path: Path) -> None:
    binding = _binding()
    intent = _intent()
    digest = _CallRecorder(result="2" * 64)
    target = _target(tmp_path / "binding.json")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            target,
            binding_loader=_CallRecorder(result=binding),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=intent),
            intent_digest_function=digest,
            start_digest_function=_CallRecorder(result="3" * 64),
        )
    _assert_error(caught.value, "intent_lineage")
    assert digest.call_count == 1
    assert digest.first_positional is intent


@pytest.mark.parametrize(
    ("binding_overrides", "intent_overrides", "digest_value"),
    [
        ({}, {}, "9" * 64),
        ({"publication_approval_sha256": "9" * 64}, {}, "2" * 64),
        ({"publication_plan_sha256": "9" * 64}, {}, "2" * 64),
    ],
)
def test_binding_intent_mismatch_rejected_before_authorization(
    tmp_path: Path,
    binding_overrides: dict[str, object],
    intent_overrides: dict[str, object],
    digest_value: str,
) -> None:
    binding = _binding(**binding_overrides)
    intent = _intent(**intent_overrides)
    start_digest = _CallRecorder(result="3" * 64)
    target = _target(tmp_path / "binding.json")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            target,
            binding_loader=_CallRecorder(result=binding),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=intent),
            intent_digest_function=_CallRecorder(result=digest_value),
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "intent_lineage")
    assert start_digest.call_count == 0
    assert not target.exists()


def test_intent_loader_known_error_propagates_by_object_identity(
    tmp_path: Path,
) -> None:
    error = ExternalPublicationOperationIntentLoadError("target")
    with pytest.raises(ExternalPublicationOperationIntentError) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(fault=error),
        )
    assert caught.value is error


def test_intent_unexpected_errors_sanitize(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(fault=RuntimeError("boom")),
        )
    _assert_error(caught.value, "dependency_error")

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(result=_binding()),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=_intent()),
            intent_digest_function=_CallRecorder(fault=RuntimeError("boom")),
        )
    _assert_error(caught.value, "dependency_error")


# --- expected Phase 290 start identity ----------------------------------


def test_expected_start_constructed_with_exact_bound_lineage(tmp_path: Path) -> None:
    binding = _binding(operation_intent_sha256="2" * 64)
    intent = _intent()
    captured: list[object] = []

    def _start_digest(value: object) -> object:
        captured.append(value)
        return "3" * 64

    authorization = _authorize(
        tmp_path / "binding.json",
        tmp_path / "intent.json",
        _target(tmp_path / "binding.json"),
        binding_loader=_CallRecorder(result=binding),
        binding_digest_function=_CallRecorder(result="1" * 64),
        intent_loader=_CallRecorder(result=intent),
        intent_digest_function=_CallRecorder(result="2" * 64),
        start_digest_function=_start_digest,
    )
    assert len(captured) == 1
    start = captured[0]
    assert type(start) is ExternalPublicationOperationStart
    assert start.schema_version == _START_SCHEMA
    assert start.operation_intent_sha256 == "2" * 64
    assert start.publication_approval_sha256 == intent.publication_approval_sha256
    assert start.publication_plan_sha256 == intent.publication_plan_sha256
    assert start.operation == "resume"
    assert start.state == "started"
    assert authorization.expected_operation_start_sha256 == "3" * 64


def test_malformed_start_digest_rejected_and_nothing_persisted(tmp_path: Path) -> None:
    target = _target(tmp_path / "binding.json")
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            target,
            binding_loader=_CallRecorder(
                result=_binding(operation_intent_sha256="2" * 64)
            ),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=_intent()),
            intent_digest_function=_CallRecorder(result="2" * 64),
            start_digest_function=_CallRecorder(result="not-a-digest"),
        )
    _assert_error(caught.value, "start_digest")
    assert not target.exists()


def test_start_digest_known_error_propagates_by_object_identity(
    tmp_path: Path,
) -> None:
    error = ExternalPublicationOperationStartError("start")
    with pytest.raises(ExternalPublicationOperationStartError) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(
                result=_binding(operation_intent_sha256="2" * 64)
            ),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=_intent()),
            intent_digest_function=_CallRecorder(result="2" * 64),
            start_digest_function=_CallRecorder(fault=error),
        )
    assert caught.value is error


def test_start_digest_unexpected_error_sanitizes(tmp_path: Path) -> None:
    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            tmp_path / "binding.json",
            tmp_path / "intent.json",
            _target(tmp_path / "binding.json"),
            binding_loader=_CallRecorder(
                result=_binding(operation_intent_sha256="2" * 64)
            ),
            binding_digest_function=_CallRecorder(result="1" * 64),
            intent_loader=_CallRecorder(result=_intent()),
            intent_digest_function=_CallRecorder(result="2" * 64),
            start_digest_function=_CallRecorder(fault=RuntimeError("boom")),
        )
    _assert_error(caught.value, "dependency_error")


def test_real_expected_start_digest_matches_public_phase290_helper(
    tmp_path: Path,
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    authorization = _authorize(binding_path, intent_path, _seeded_target(binding_path))
    intent = load_external_publication_operation_intent(intent_path)
    expected = ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,  # type: ignore[arg-type]
        operation_intent_sha256=external_publication_operation_intent_digest(intent),
        publication_approval_sha256=intent.publication_approval_sha256,
        publication_plan_sha256=intent.publication_plan_sha256,
        operation="resume",
        state="started",
    )
    assert authorization.expected_operation_start_sha256 == (
        external_publication_operation_start_digest(expected)
    )


# --- authorization record -----------------------------------------------


def test_authorization_fields_bind_exact_lineage(tmp_path: Path) -> None:
    binding_path, intent_path = _seed(tmp_path)
    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    intent = load_external_publication_operation_intent(intent_path)
    authorization = _authorize(binding_path, intent_path, _seeded_target(binding_path))

    assert authorization.schema_version == _AUTHORIZATION_SCHEMA
    assert authorization.resume_intent_binding_sha256 == (
        external_publication_recovery_resume_intent_binding_digest(binding)
    )
    assert authorization.resume_preparation_sha256 == (
        binding.resume_preparation_sha256
    )
    assert authorization.recovery_decision_sha256 == binding.recovery_decision_sha256
    assert authorization.operation_intent_sha256 == (
        external_publication_operation_intent_digest(intent)
    )
    assert authorization.publication_approval_sha256 == (
        intent.publication_approval_sha256
    )
    assert authorization.publication_plan_sha256 == intent.publication_plan_sha256
    assert authorization.source_operation == binding.source_operation
    assert authorization.recovery_kind == binding.recovery_kind
    assert authorization.operation == "resume"
    assert authorization.state == "authorized"


def test_existing_authorization_loader_once_and_identity_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    first = _authorize(binding_path, intent_path, target)
    before = target.read_bytes()

    real_loader = authorization_module.load_external_publication_recovery_resume_start_authorization  # noqa: E501
    recorder = _CallRecorder(delegate=real_loader)
    monkeypatch.setattr(
        authorization_module,
        "load_external_publication_recovery_resume_start_authorization",
        recorder,
    )

    second = _authorize(binding_path, intent_path, target)
    assert recorder.call_count == 1
    assert recorder.first_positional == target
    assert second is recorder.first_result
    assert second == first
    assert target.read_bytes() == before


def test_existing_authorization_mismatch_is_fixed_conflict_unchanged(
    tmp_path: Path,
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    _authorize(binding_path, intent_path, target)

    other = _authorization()
    replacement = (
        external_publication_recovery_resume_start_authorization_canonical_bytes(other)
    )
    target.write_bytes(replacement)

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationConflictError
    ) as caught:
        _authorize(binding_path, intent_path, target)
    _assert_conflict_error(caught.value)
    assert target.read_bytes() == replacement


def test_existing_authorization_noncanonical_is_load_error_unchanged(
    tmp_path: Path,
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    payload = external_publication_recovery_resume_start_authorization_canonical_bytes(
        _authorization()
    ).decode("utf-8")
    target.write_text(payload.replace(",", ", ", 1), encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationLoadError
    ) as caught:
        _authorize(binding_path, intent_path, target)
    _assert_load_error(caught.value, "noncanonical")
    assert target.read_bytes() == before


def test_absent_authorization_persisted_once_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    real_persist = authorization_module.persist_external_publication_recovery_resume_start_authorization  # noqa: E501
    recorder = _CallRecorder(delegate=real_persist)
    monkeypatch.setattr(
        authorization_module,
        "persist_external_publication_recovery_resume_start_authorization",
        recorder,
    )

    authorization = _authorize(binding_path, intent_path, target)
    assert recorder.call_count == 1
    assert recorder.calls[0][0][0] == target
    assert recorder.calls[0][0][1] == authorization
    assert load_external_publication_recovery_resume_start_authorization(target) == (
        authorization
    )


# --- future deterministic start target fence ----------------------------


def test_future_start_target_formula_is_pinned() -> None:
    assert authorization_module._FUTURE_START_FILENAME_PREFIX == _FUTURE_START_PREFIX
    assert authorization_module._FUTURE_START_FILENAME_SUFFIX == ".json"
    binding_path = Path("/tmp/phase296-binding/binding.json")
    digest = "a" * 64
    derived = authorization_module._future_start_target_path(binding_path, digest)
    assert derived == binding_path.parent / (
        f"external-publication-recovery-resume-start-{digest}.json"
    )
    assert derived.parent == binding_path.parent
    distinct = authorization_module._future_start_target_path(binding_path, "b" * 64)
    assert distinct != derived


def test_future_start_target_uses_binding_parent_and_authorization_digest(
    tmp_path: Path,
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    authorization = _authorize(binding_path, intent_path, target)
    digest = external_publication_recovery_resume_start_authorization_digest(
        authorization
    )
    future = authorization_module._future_start_target_path(binding_path, digest)
    assert future == binding_path.parent / (f"{_FUTURE_START_PREFIX}{digest}.json")
    assert future.parent == binding_path.parent
    assert digest in future.name


def test_canonical_authorization_target_formula_uses_binding_digest() -> None:
    binding_path = Path("/tmp/phase296-binding/binding.json")
    digest = "c" * 64
    derived = authorization_module._canonical_authorization_target_path(
        binding_path, digest
    )
    assert derived == binding_path.parent / (f"{_AUTH_FILENAME_PREFIX}{digest}.json")
    assert derived.parent == binding_path.parent
    assert digest in derived.name
    assert (
        authorization_module._canonical_authorization_target_path(
            Path("/tmp/other/binding.json"), digest
        ).parent
        != derived.parent
    )


def test_canonical_authorization_path_succeeds(tmp_path: Path) -> None:
    binding_path, intent_path = _seed(tmp_path)
    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    binding_digest = external_publication_recovery_resume_intent_binding_digest(binding)
    target = _target(binding_path, binding_digest)
    assert target.name == f"{_AUTH_FILENAME_PREFIX}{binding_digest}.json"

    authorization = _authorize(binding_path, intent_path, target)
    assert authorization.resume_intent_binding_sha256 == binding_digest
    assert target.exists()
    assert (
        load_external_publication_recovery_resume_start_authorization(target)
        == authorization
    )


def test_different_authorization_filename_in_same_parent_rejected(
    tmp_path: Path,
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())

    loader = _CallRecorder(delegate=load_external_publication_operation_intent)
    digest = _CallRecorder(delegate=external_publication_operation_intent_digest)
    start_digest = _CallRecorder(delegate=external_publication_operation_start_digest)
    wrong = tmp_path / f"{_AUTH_FILENAME_PREFIX}{'0' * 64}.json"

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            binding_path,
            intent_path,
            wrong,
            intent_loader=loader,
            intent_digest_function=digest,
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "authorization_path")
    assert loader.call_count == 0
    assert digest.call_count == 0
    assert start_digest.call_count == 0
    assert not wrong.exists()
    assert sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir()) == before


def test_different_authorization_parent_rejected(tmp_path: Path) -> None:
    binding_path, intent_path = _seed(tmp_path)
    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    binding_digest = external_publication_recovery_resume_intent_binding_digest(binding)
    other_root = tmp_path / "other"
    other_root.mkdir()
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir() if p.is_file())

    loader = _CallRecorder(delegate=load_external_publication_operation_intent)
    digest = _CallRecorder(delegate=external_publication_operation_intent_digest)
    start_digest = _CallRecorder(delegate=external_publication_operation_start_digest)
    relocated = _target(other_root / "binding.json", binding_digest)

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            binding_path,
            intent_path,
            relocated,
            intent_loader=loader,
            intent_digest_function=digest,
            start_digest_function=start_digest,
        )
    _assert_error(caught.value, "authorization_path")
    assert loader.call_count == 0
    assert digest.call_count == 0
    assert start_digest.call_count == 0
    assert not relocated.exists()
    after = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir() if p.is_file())
    assert after == before
    assert list(other_root.iterdir()) == []


def test_one_binding_cannot_yield_two_valid_authorizations(tmp_path: Path) -> None:
    binding_path, intent_path = _seed(tmp_path)
    canonical = _seeded_target(binding_path)
    first = _authorize(binding_path, intent_path, canonical)
    assert canonical.exists()

    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    binding_digest = external_publication_recovery_resume_intent_binding_digest(binding)
    alternative_root = tmp_path / "alternative"
    alternative_root.mkdir()
    alternatives = (
        tmp_path / f"{_AUTH_FILENAME_PREFIX}{binding_digest}-other.json",
        alternative_root / f"{_AUTH_FILENAME_PREFIX}{binding_digest}.json",
    )
    for alternative in alternatives:
        with pytest.raises(
            ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
        ) as caught:
            _authorize(binding_path, intent_path, alternative)
        _assert_error(caught.value, "authorization_path")
        assert not alternative.exists()

    assert list(alternative_root.iterdir()) == []
    second = _authorize(binding_path, intent_path, canonical)
    assert second == first
    assert (
        load_external_publication_recovery_resume_start_authorization(canonical)
        == first
    )


def test_authorization_path_check_runs_before_intent_loader(tmp_path: Path) -> None:
    order: list[str] = []
    binding = _binding(operation_intent_sha256="2" * 64)
    binding_path = tmp_path / "binding.json"

    def _binding_loader(path: object) -> object:
        order.append("binding_loader")
        return binding

    def _binding_digest(value: object) -> object:
        order.append("binding_digest")
        return "1" * 64

    def _intent_loader(path: object) -> object:
        order.append("intent_loader")
        return _intent()

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(
            binding_path,
            tmp_path / "intent.json",
            tmp_path / "not-canonical.json",
            binding_loader=_binding_loader,
            binding_digest_function=_binding_digest,
            intent_loader=_intent_loader,
        )
    _assert_error(caught.value, "authorization_path")
    assert order == ["binding_loader", "binding_digest"]


def test_phase296_creates_no_future_start_marker(tmp_path: Path) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    authorization = _authorize(binding_path, intent_path, target)
    digest = external_publication_recovery_resume_start_authorization_digest(
        authorization
    )
    future = authorization_module._future_start_target_path(binding_path, digest)
    assert future.parent == tmp_path
    assert not future.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        [
            "binding.json",
            "decision.json",
            "intent.json",
            "lifecycle.json",
            "lineage-intent.json",
            "preparation.json",
            "start.json",
            target.name,
        ]
    )


def test_api_has_no_start_path_or_acquisition_argument() -> None:
    signature = inspect.signature(
        authorize_and_persist_external_publication_recovery_resume_start
    )
    for name in signature.parameters:
        assert "start_path" not in name
        assert "acquire" not in name
        assert "acquired" not in name


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


def test_source_audit_exact_import_set() -> None:
    imports = _module_imports()
    assert set(imports) == {
        ".external_publication_operation_intent",
        ".external_publication_operation_start",
        ".external_publication_recovery_resume_intent_binding",
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
    assert imports[".external_publication_recovery_resume_intent_binding"] == {
        "ExternalPublicationRecoveryResumeIntentBinding",
        "ExternalPublicationRecoveryResumeIntentBindingError",
        "external_publication_recovery_resume_intent_binding_digest",
        "load_external_publication_recovery_resume_intent_binding",
    }
    assert imports[".external_publication_operation_intent"] == {
        "ExternalPublicationOperationIntent",
        "ExternalPublicationOperationIntentError",
        "external_publication_operation_intent_digest",
        "load_external_publication_operation_intent",
    }
    assert imports[".external_publication_operation_start"] == {
        "ExternalPublicationOperationStart",
        "ExternalPublicationOperationStartError",
        "external_publication_operation_start_digest",
    }


def test_source_audit_no_forbidden_imports_or_modules() -> None:
    imports = _module_imports()
    for forbidden in (
        "acquire_external_publication_operation_start",
        "load_external_publication_operation_start",
        "persist_external_publication_operation_start",
        "build_external_publication_operation_intent",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "run_external_publication_operation_start_handoff",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
        "build_external_publication_plan",
    ):
        assert not any(
            name == forbidden for names in imports.values() for name in names
        ), forbidden
    for module in imports:
        assert "orchestration" not in module
        assert "handoff" not in module
        assert "acquisition" not in module
        assert module != ".external_publication"


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
        "acquire_external_publication_operation_start",
        "load_external_publication_operation_start",
        "persist_external_publication_operation_start",
        "materialize_and_bind_external_publication_recovery_resume_intent",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
        "decide_and_persist_external_publication_recovery",
        "run_external_publication_operation_start_handoff",
        "execute_and_persist_approved_external_publication",
    ):
        assert forbidden not in called, forbidden
    assert "loader" in called
    assert "digest_function" in called


def test_source_audit_no_forbidden_tokens() -> None:
    for token in (
        "acquire_external_publication_operation_start",
        "start_path",
        "caller_start",
        "start_marker",
        "hostname",
        "getpid",
        "getenv",
        "os.environ",
        "import random",
        "import uuid",
        "import time",
        "import datetime",
        "sleep(",
        "subprocess",
        "socket",
        "uuid4",
        "time.time",
        "monotonic",
        "requests",
        "urllib",
        "http.client",
        "phase291",
        "phase288",
        "phase297",
    ):
        assert token not in _SOURCE, token


def test_source_audit_pins_future_start_filename_formula() -> None:
    assert "_FUTURE_START_FILENAME_PREFIX" in _SOURCE
    assert "_FUTURE_START_FILENAME_SUFFIX" in _SOURCE
    assert '"external-publication-recovery-resume-start-"' in _SOURCE
    assert '".json"' in _SOURCE


def test_source_audit_pins_canonical_authorization_filename_formula() -> None:
    assert "_AUTHORIZATION_FILENAME_PREFIX" in _SOURCE
    assert "_AUTHORIZATION_FILENAME_SUFFIX" in _SOURCE
    assert (
        '"external-publication-recovery-resume-start-authorization-"' in _SOURCE
    )
    assert "_canonical_authorization_target_path(" in _SOURCE
    assert "_validate_canonical_authorization_path(" in _SOURCE
    assert "authorization_path" in _SOURCE


def test_source_audit_uses_binding_parent_not_authorization_parent() -> None:
    tree = ast.parse(_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_canonical_authorization_target_path",
            "_future_start_target_path",
        }:
            args = [arg.arg for arg in node.args.args]
            assert args[:1] == ["resume_intent_binding_path"], node.name
    assert "resume_start_authorization_path.parent" not in _SOURCE
    assert "resume_intent_binding_path.parent" in _SOURCE


def test_source_audit_no_normalization_or_symlink_equivalence() -> None:
    for token in (
        ".resolve(",
        "realpath",
        "normpath",
        "os.path.abspath",
        "samefile",
        "readlink",
        "casefold",
    ):
        assert token not in _SOURCE, token


def test_no_cli_change_and_no_phase296_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "resume_start_authorization" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "resume_start_authorization" not in workflows.output.lower()
    assert "phase296" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "resume_start_authorization" not in cli_source
    assert "phase296" not in cli_source.lower().replace(" ", "")


# --- integration regressions --------------------------------------------


def test_integration_fresh_already_acquired_lineage(tmp_path: Path) -> None:
    predecessor_root = tmp_path / "predecessor"
    predecessor_root.mkdir()

    binding_path, intent_path = _seed(predecessor_root)
    binding = load_external_publication_recovery_resume_intent_binding(binding_path)
    before = sorted(
        (path.name, path.read_bytes()) for path in predecessor_root.iterdir()
    )

    target = _seeded_target(binding_path)
    authorization = _authorize(binding_path, intent_path, target)
    assert authorization.source_operation == "fresh"
    assert authorization.recovery_kind == "already_acquired"
    assert authorization.operation == "resume"
    assert authorization.state == "authorized"
    assert authorization.resume_intent_binding_sha256 == (
        external_publication_recovery_resume_intent_binding_digest(binding)
    )
    assert authorization.resume_preparation_sha256 == (
        binding.resume_preparation_sha256
    )
    assert authorization.recovery_decision_sha256 == binding.recovery_decision_sha256
    assert target == predecessor_root / (
        f"{_AUTH_FILENAME_PREFIX}"
        f"{external_publication_recovery_resume_intent_binding_digest(binding)}.json"
    )
    after = sorted(
        (path.name, path.read_bytes())
        for path in predecessor_root.iterdir()
        if path.name != target.name
    )
    assert after == before
    assert sorted(path.name for path in predecessor_root.iterdir()) == sorted(
        [
            "binding.json",
            "decision.json",
            "intent.json",
            "lifecycle.json",
            "lineage-intent.json",
            "preparation.json",
            "start.json",
            target.name,
        ]
    )


def test_integration_resume_reconciliation_mismatch_lineage(tmp_path: Path) -> None:
    predecessor_root = tmp_path / "predecessor"
    predecessor_root.mkdir()

    binding_path, intent_path = _seed(
        predecessor_root, operation="resume", result_kind="reconciliation"
    )
    before = sorted(
        (path.name, path.read_bytes()) for path in predecessor_root.iterdir()
    )
    target = _seeded_target(binding_path)
    authorization = _authorize(binding_path, intent_path, target)
    assert authorization.source_operation == "resume"
    assert authorization.recovery_kind == "reconciliation_mismatch"
    assert authorization.operation == "resume"
    assert authorization.state == "authorized"
    after = sorted(
        (path.name, path.read_bytes())
        for path in predecessor_root.iterdir()
        if path.name != target.name
    )
    assert after == before


def test_integration_second_invocation_returns_same_authorization(
    tmp_path: Path,
) -> None:
    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    first = _authorize(binding_path, intent_path, target)
    before = sorted(
        (path.name, path.read_bytes())
        for path in tmp_path.iterdir()
        if path.name != "authorization.json"
    )
    second = _authorize(binding_path, intent_path, target)
    assert second == first
    assert (
        sorted(
            (path.name, path.read_bytes())
            for path in tmp_path.iterdir()
            if path.name != "authorization.json"
        )
        == before
    )


def test_integration_binding_intent_mismatch_fails_before_mutation(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_a.mkdir()
    root_b = tmp_path / "b"
    root_b.mkdir()
    binding_path, _ = _seed(root_a)
    _, other_intent_path = _seed(root_b, regeneration_id="regen-296-other")

    before_a = sorted((p.name, p.read_bytes()) for p in root_a.iterdir())
    before_b = sorted((p.name, p.read_bytes()) for p in root_b.iterdir())
    target = _seeded_target(binding_path)

    with pytest.raises(
        ExternalPublicationRecoveryResumeStartAuthorizationCompatibilityError
    ) as caught:
        _authorize(binding_path, other_intent_path, target)
    _assert_error(caught.value, "intent_lineage")
    assert not target.exists()
    assert sorted((p.name, p.read_bytes()) for p in root_a.iterdir()) == before_a
    assert sorted((p.name, p.read_bytes()) for p in root_b.iterdir()) == before_b


def test_integration_no_start_marker_or_provider_artifact_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    def _forbidden(*args: object, **kwargs: object) -> object:
        called.append("forbidden")
        raise AssertionError("Phase 296 must not call this")

    for name in (
        "acquire_external_publication_operation_start",
        "load_external_publication_operation_start",
        "run_external_publication_operation_start_handoff",
    ):
        monkeypatch.setattr(authorization_module, name, _forbidden, raising=False)

    binding_path, intent_path = _seed(tmp_path)
    target = _seeded_target(binding_path)
    authorization = _authorize(binding_path, intent_path, target)
    assert called == []
    assert authorization.state == "authorized"

    digest = external_publication_recovery_resume_start_authorization_digest(
        authorization
    )
    assert not authorization_module._future_start_target_path(target, digest).exists()
