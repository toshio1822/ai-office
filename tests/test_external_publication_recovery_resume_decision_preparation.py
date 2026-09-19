"""Focused and integration regressions for the Phase 301 boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import os
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_decision_preparation as preparation_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationRecoveryResumeDecision,
    ExternalPublicationRecoveryResumeDecisionError,
    ExternalPublicationRecoveryResumeDecisionPreparation,
    ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError,
    ExternalPublicationRecoveryResumeDecisionPreparationConflictError,
    ExternalPublicationRecoveryResumeDecisionPreparationError,
    ExternalPublicationRecoveryResumeDecisionPreparationLoadError,
    ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError,
    ExternalPublicationRecoveryResumeDecisionRequired,
    ExternalPublicationRecoveryResumeIntentBinding,
    ExternalPublicationRecoveryResumeIntentBindingError,
    ExternalPublicationRecoveryResumeOutcome,
    ExternalPublicationRecoveryResumeOutcomeError,
    ExternalPublicationRecoveryResumeOutcomeRoutingError,
    ExternalPublicationRecoveryResumeStartAuthorization,
    ExternalPublicationRecoveryResumeStartAuthorizationError,
    external_publication_operation_start_canonical_bytes,
    external_publication_operation_start_digest,
    external_publication_recovery_resume_decision_digest,
    external_publication_recovery_resume_decision_preparation_canonical_bytes,
    external_publication_recovery_resume_decision_preparation_digest,
    load_external_publication_recovery_resume_decision_preparation,
    persist_external_publication_recovery_resume_decision_preparation,
    persist_external_publication_recovery_resume_intent_binding,
    persist_external_publication_recovery_resume_outcome,
    persist_external_publication_recovery_resume_start_authorization,
    prepare_and_persist_external_publication_recovery_resume_decision_lineage,
    route_external_publication_recovery_resume_outcome,
    serialize_external_publication_recovery_resume_decision_preparation_canonical,
)

_PREPARATION_SCHEMA = "external-publication-recovery-resume-decision-preparation.v1"
_PREPARATION_KEYS = frozenset(
    {
        "decision",
        "operation_intent_sha256",
        "operation_start_sha256",
        "previous_recovery_kind",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "recovery_kind",
        "recovery_resume_decision_sha256",
        "recovery_resume_outcome_sha256",
        "result_kind",
        "result_sha256",
        "resume_intent_binding_sha256",
        "resume_start_authorization_sha256",
        "schema_version",
        "source_operation",
        "state",
        "target_operation",
    }
)
_DECISION_SCHEMA = "external-publication-recovery-resume-decision.v1"
_DECISION_REQUIRED_SCHEMA = "external-publication-recovery-resume-decision-required.v1"
_BINDING_SCHEMA = "external-publication-recovery-resume-intent-binding.v1"
_AUTHORIZATION_SCHEMA = "external-publication-recovery-resume-start-authorization.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_OUTCOME_SCHEMA = "external-publication-recovery-resume-outcome.v1"
_DECISION_PREFIX = "external-publication-recovery-resume-decision-"
_PREPARATION_PREFIX = "external-publication-recovery-resume-decision-preparation-"
_SUFFIX = ".json"
_PREPARATION_MESSAGE = (
    "external publication recovery resume decision preparation is blocked"
)
_PERSISTENCE_MESSAGE = (
    "external publication recovery resume decision preparation persistence failed"
)
_LOAD_MESSAGE = (
    "external publication recovery resume decision preparation could not be loaded"
)


class _CallRecorder:
    """Record exact calls while optionally returning, delegating, or raising."""

    def __init__(
        self,
        result: object = None,
        *,
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


class _StringPathChild(type(Path())):
    pass


def _assert_preparation_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    )
    assert isinstance(error, ExternalPublicationRecoveryResumeDecisionPreparationError)
    assert isinstance(error, ValueError)
    assert str(error) == _PREPARATION_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert (
        type(error)
        is ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError
    )
    assert isinstance(error, ExternalPublicationRecoveryResumeDecisionPreparationError)
    assert isinstance(error, ValueError)
    assert str(error) == _PERSISTENCE_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumeDecisionPreparationLoadError
    assert isinstance(error, ExternalPublicationRecoveryResumeDecisionPreparationError)
    assert isinstance(error, ValueError)
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _field_names(cls: type[object]) -> set[str]:
    return {field.name for field in dataclasses.fields(cls)}  # type: ignore[arg-type]


def _forged_instance(
    source: object, *, cls: type[object] | None = None, **overrides: object
) -> object:
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
        "decision": "authorize_resume_preparation",
        "decided_by": "operator@example.test",
        "decision_id": "decision-1",
        "state": "decided",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecision(**values)  # type: ignore[arg-type]


def _outcome(**overrides: object) -> ExternalPublicationRecoveryResumeOutcome:
    values: dict[str, object] = {
        "schema_version": _OUTCOME_SCHEMA,
        "resume_start_authorization_sha256": "1" * 64,
        "resume_intent_binding_sha256": "2" * 64,
        "operation_intent_sha256": "3" * 64,
        "operation_start_sha256": "4" * 64,
        "publication_approval_sha256": "5" * 64,
        "publication_plan_sha256": "6" * 64,
        "source_operation": "resume",
        "recovery_kind": "already_acquired",
        "operation": "resume",
        "state": "recovery_required",
        "result_kind": "none",
        "result_sha256": None,
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeOutcome(**values)  # type: ignore[arg-type]


def _preparation(
    **overrides: object,
) -> ExternalPublicationRecoveryResumeDecisionPreparation:
    values: dict[str, object] = {
        "schema_version": _PREPARATION_SCHEMA,
        "recovery_resume_decision_sha256": "a" * 64,
        "recovery_resume_outcome_sha256": "b" * 64,
        "resume_start_authorization_sha256": "c" * 64,
        "resume_intent_binding_sha256": "d" * 64,
        "operation_intent_sha256": "e" * 64,
        "operation_start_sha256": "f" * 64,
        "publication_approval_sha256": "0" * 64,
        "publication_plan_sha256": "1" * 64,
        "source_operation": "resume",
        "previous_recovery_kind": "already_acquired",
        "recovery_kind": "reconciliation_mismatch",
        "result_kind": "reconciliation",
        "result_sha256": "2" * 64,
        "decision": "authorize_resume_preparation",
        "target_operation": "resume",
        "state": "prepared",
    }
    values.update(overrides)
    return ExternalPublicationRecoveryResumeDecisionPreparation(**values)  # type: ignore[arg-type]


def _run(
    tmp_path: Path,
    routed: object,
    decision: object,
    *,
    phase299: _CallRecorder | None = None,
    decision_loader: _CallRecorder | None = None,
    decision_digest: _CallRecorder | None = None,
) -> object:
    return prepare_and_persist_external_publication_recovery_resume_decision_lineage(
        resume_intent_binding_path=tmp_path / "binding.json",
        phase299_function=phase299 or _CallRecorder(routed),
        decision_loader=decision_loader or _CallRecorder(decision),
        decision_digest_function=decision_digest
        or _CallRecorder(external_publication_recovery_resume_decision_digest),
    )


class _Lineage:
    """Small exact Phase 295/296/298/290 lineage for integration tests."""

    def __init__(self, root: Path, *, previous_recovery_kind: str) -> None:
        self.root = root
        self.binding_path = root / "binding.json"
        self.binding = ExternalPublicationRecoveryResumeIntentBinding(
            schema_version=_BINDING_SCHEMA,
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
        from ai_office.engine import (
            external_publication_recovery_resume_intent_binding_digest,
        )

        self.binding_digest = (
            external_publication_recovery_resume_intent_binding_digest(self.binding)
        )
        self.start = ExternalPublicationOperationStart(
            schema_version=_START_SCHEMA,
            operation_intent_sha256=self.binding.operation_intent_sha256,
            publication_approval_sha256=self.binding.publication_approval_sha256,
            publication_plan_sha256=self.binding.publication_plan_sha256,
            operation="resume",
            state="started",
        )
        self.start_digest = external_publication_operation_start_digest(self.start)
        self.authorization = ExternalPublicationRecoveryResumeStartAuthorization(
            schema_version=_AUTHORIZATION_SCHEMA,
            resume_intent_binding_sha256=self.binding_digest,
            resume_preparation_sha256=self.binding.resume_preparation_sha256,
            recovery_decision_sha256=self.binding.recovery_decision_sha256,
            operation_intent_sha256=self.binding.operation_intent_sha256,
            expected_operation_start_sha256=self.start_digest,
            publication_approval_sha256=self.binding.publication_approval_sha256,
            publication_plan_sha256=self.binding.publication_plan_sha256,
            source_operation=self.binding.source_operation,
            recovery_kind=self.binding.recovery_kind,
            operation="resume",
            state="authorized",
        )
        from ai_office.engine import (
            external_publication_recovery_resume_start_authorization_digest,
        )

        self.authorization_digest = (
            external_publication_recovery_resume_start_authorization_digest(
                self.authorization
            )
        )
        self.authorization_path = root / (
            f"external-publication-recovery-resume-start-authorization-{self.binding_digest}.json"
        )
        self.start_path = root / (
            f"external-publication-recovery-resume-start-{self.authorization_digest}.json"
        )
        self.outcome_path = root / (
            f"external-publication-recovery-resume-outcome-{self.authorization_digest}.json"
        )

    def seed(
        self,
        *,
        state: str,
        result_kind: str,
        result_sha256: str | None,
    ) -> None:
        self.outcome = ExternalPublicationRecoveryResumeOutcome(
            schema_version=_OUTCOME_SCHEMA,
            resume_start_authorization_sha256=self.authorization_digest,
            resume_intent_binding_sha256=self.binding_digest,
            operation_intent_sha256=self.binding.operation_intent_sha256,
            operation_start_sha256=self.start_digest,
            publication_approval_sha256=self.binding.publication_approval_sha256,
            publication_plan_sha256=self.binding.publication_plan_sha256,
            source_operation=self.binding.source_operation,
            recovery_kind=self.binding.recovery_kind,
            operation="resume",
            state=state,  # type: ignore[arg-type]
            result_kind=result_kind,  # type: ignore[arg-type]
            result_sha256=result_sha256,
        )
        persist_external_publication_recovery_resume_intent_binding(
            self.binding_path, self.binding
        )
        persist_external_publication_recovery_resume_start_authorization(
            self.authorization_path, self.authorization
        )
        self.start_path.write_bytes(
            external_publication_operation_start_canonical_bytes(self.start)
        )
        persist_external_publication_recovery_resume_outcome(
            self.outcome_path, self.outcome
        )


def test_public_exports_signature_and_defaults() -> None:
    expected = {
        "ExternalPublicationRecoveryResumeDecisionPreparation",
        "ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError",
        "ExternalPublicationRecoveryResumeDecisionPreparationConflictError",
        "ExternalPublicationRecoveryResumeDecisionPreparationError",
        "ExternalPublicationRecoveryResumeDecisionPreparationFailureDetail",
        "ExternalPublicationRecoveryResumeDecisionPreparationLoadError",
        "ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError",
        "external_publication_recovery_resume_decision_preparation_canonical_bytes",
        "external_publication_recovery_resume_decision_preparation_digest",
        "load_external_publication_recovery_resume_decision_preparation",
        "persist_external_publication_recovery_resume_decision_preparation",
        "prepare_and_persist_external_publication_recovery_resume_decision_lineage",
        "serialize_external_publication_recovery_resume_decision_preparation_canonical",
    }
    import ai_office.engine as engine

    assert expected <= set(engine.__all__)
    for name in expected:
        assert getattr(engine, name) is getattr(preparation_module, name)

    signature = inspect.signature(
        prepare_and_persist_external_publication_recovery_resume_decision_lineage
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
    ]
    assert (
        signature.parameters["phase299_function"].default
        is route_external_publication_recovery_resume_outcome
    )
    assert (
        signature.parameters["decision_loader"].default
        is preparation_module.load_external_publication_recovery_resume_decision
    )
    assert (
        signature.parameters["decision_digest_function"].default
        is external_publication_recovery_resume_decision_digest
    )


def test_model_field_order_frozen_and_cross_cycle_invariants() -> None:
    assert [
        field.name
        for field in dataclasses.fields(
            ExternalPublicationRecoveryResumeDecisionPreparation
        )
    ] == [
        "schema_version",
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
    ]
    assert (
        dataclasses.fields(ExternalPublicationRecoveryResumeDecisionPreparation)[0].type
        is not None
    )
    preparation = _preparation()
    with pytest.raises(dataclasses.FrozenInstanceError):
        preparation.state = "other"  # type: ignore[misc]
    assert (
        _preparation(
            previous_recovery_kind="reconciliation_mismatch",
            recovery_kind="already_acquired",
            result_kind="none",
            result_sha256=None,
        ).result_sha256
        is None
    )
    invalid = (
        {"recovery_kind": "already_acquired"},
        {
            "previous_recovery_kind": "reconciliation_mismatch",
            "source_operation": "fresh",
        },
        {"result_kind": "none", "result_sha256": "2" * 64},
        {"result_kind": "reconciliation", "result_sha256": None},
        {"recovery_kind": "already_acquired"},
        {"decision": "stop"},
        {"target_operation": "fresh"},
        {"state": "decided"},
        {"operation_start_sha256": _StringChild("f" * 64)},
    )
    for overrides in invalid:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
        ):
            _preparation(**overrides)


def test_canonical_json_exact_keys_and_digest() -> None:
    preparation = _preparation()
    payload = (
        serialize_external_publication_recovery_resume_decision_preparation_canonical(
            preparation
        )
    )
    assert list(json.loads(payload)) == sorted(_PREPARATION_KEYS)
    assert set(json.loads(payload)) == _PREPARATION_KEYS
    assert payload == payload.encode().decode()
    assert (
        preparation_module.external_publication_recovery_resume_decision_preparation_canonical_bytes(
            preparation
        )
        == payload.encode("utf-8")
    )
    assert external_publication_recovery_resume_decision_preparation_digest(
        preparation
    ) == external_publication_recovery_resume_decision_preparation_digest(preparation)

    forged = _forged_instance(preparation, operation_start_sha256="F" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ):
        preparation_module.external_publication_recovery_resume_decision_preparation_digest(
            forged
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "contents",
    [
        b'{"bad":1}',
        (
            b'{"decision":"authorize_resume_preparation", '
            b'"decision":"authorize_resume_preparation"}'
        ),
        b'{"decision":NaN}',
    ],
)
def test_loader_rejects_duplicate_constants_and_wrong_keys(
    tmp_path: Path, contents: bytes
) -> None:
    path = tmp_path / "preparation.json"
    path.write_bytes(contents)
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionPreparationLoadError):
        load_external_publication_recovery_resume_decision_preparation(path)


def test_loader_rejects_noncanonical_bytes_and_round_trips(tmp_path: Path) -> None:
    preparation = _preparation()
    path = tmp_path / "preparation.json"
    persist_external_publication_recovery_resume_decision_preparation(path, preparation)
    assert (
        load_external_publication_recovery_resume_decision_preparation(path)
        == preparation
    )
    original = path.read_bytes()
    path.write_bytes(b" " + original)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationLoadError
    ) as caught:
        load_external_publication_recovery_resume_decision_preparation(path)
    _assert_load_error(caught.value, "noncanonical")


def test_persistence_is_idempotent_and_conflicting_bytes_are_unchanged(
    tmp_path: Path,
) -> None:
    preparation = _preparation()
    path = tmp_path / "preparation.json"
    persist_external_publication_recovery_resume_decision_preparation(path, preparation)
    before = path.read_bytes()
    persist_external_publication_recovery_resume_decision_preparation(path, preparation)
    assert path.read_bytes() == before

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationConflictError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation(
            path, _preparation(recovery_resume_decision_sha256="9" * 64)
        )
    assert caught.value.detail.classification == "conflict"
    assert path.read_bytes() == before

    path.write_bytes(before[:-1])
    partial = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationConflictError
    ):
        persist_external_publication_recovery_resume_decision_preparation(
            path, preparation
        )
    assert path.read_bytes() == partial


def test_persistence_rejects_parent_directory_and_symlink(tmp_path: Path) -> None:
    preparation = _preparation()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation(
            tmp_path / "missing" / "preparation.json", preparation
        )
    _assert_persistence_error(caught.value, "parent")

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation(
            directory, preparation
        )
    _assert_persistence_error(caught.value, "target")

    real = tmp_path / "real.json"
    persist_external_publication_recovery_resume_decision_preparation(real, preparation)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation(
            link, preparation
        )
    _assert_persistence_error(caught.value, "target")


class _FaultyHandle:
    """Use an unbuffered real file while faulting one durability stage."""

    def __init__(self, real: object, stage: str) -> None:
        self._real = real
        self._stage = stage

    def __enter__(self) -> _FaultyHandle:
        return self

    def __exit__(self, *exc: object) -> bool:
        self._real.close()  # type: ignore[attr-defined]
        return False

    def write(self, contents: bytes) -> int:
        if self._stage == "write_error":
            raise OSError("write")
        if self._stage == "short_write":
            self._real.write(contents[:-1])  # type: ignore[attr-defined]
            return len(contents) - 1
        self._real.write(contents)  # type: ignore[attr-defined]
        return len(contents)

    def flush(self) -> None:
        if self._stage == "flush_error":
            raise OSError("flush")
        self._real.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self._real.fileno()  # type: ignore[attr-defined]


class _CloseFaultHandle:
    def __init__(self, real: object) -> None:
        self._real = real

    def write(self, contents: bytes) -> int:
        self._real.write(contents)  # type: ignore[attr-defined]
        return len(contents)

    def flush(self) -> None:
        self._real.flush()  # type: ignore[attr-defined]

    def fileno(self) -> int:
        return self._real.fileno()  # type: ignore[attr-defined]

    def close(self) -> None:
        raise OSError("close")


def _install_persistence_fault(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    real_open = Path.open

    def fake_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode == "xb":
            real = real_open(path, mode, buffering=0)
            if stage == "close_failure":
                return _CloseFaultHandle(real)
            return _FaultyHandle(real, stage)
        return real_open(path, mode, *args, **kwargs)

    if stage in {"write_error", "short_write", "flush_error", "close_failure"}:
        monkeypatch.setattr(Path, "open", fake_open)
    else:
        monkeypatch.setattr(preparation_module, "os", _OSShim(os, stage))


class _OSShim:
    def __init__(self, original: object, stage: str) -> None:
        self.original = original
        self.stage = stage
        self.fsync_calls = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self.original, name)

    def fsync(self, descriptor: int) -> None:
        self.fsync_calls += 1
        if (self.stage == "file_fsync" and self.fsync_calls == 1) or (
            self.stage == "dir_fsync" and self.fsync_calls == 2
        ):
            raise OSError("fsync")
        self.original.fsync(descriptor)  # type: ignore[attr-defined]


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
    tmp_path: Path, stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "preparation.json"
    preparation = _preparation()
    _install_persistence_fault(monkeypatch, stage)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationPersistenceError
    ) as caught:
        persist_external_publication_recovery_resume_decision_preparation(
            path, preparation
        )
    _assert_persistence_error(caught.value, "ambiguous")
    assert path.exists()
    retained = path.read_bytes()
    if stage in {"file_fsync", "dir_fsync", "close_failure", "flush_error"}:
        canonical = (
            external_publication_recovery_resume_decision_preparation_canonical_bytes(
                preparation
            )
        )
        assert retained == canonical
    else:
        canonical = (
            external_publication_recovery_resume_decision_preparation_canonical_bytes(
                preparation
            )
        )
        assert retained != canonical


def test_preflight_rejects_invalid_inputs_before_phase299_or_mutation(
    tmp_path: Path,
) -> None:
    phase299 = _CallRecorder(_decision_required())
    target = tmp_path / "binding.json"
    cases = (
        (object(), phase299, _CallRecorder(), _CallRecorder()),
        (target, object(), _CallRecorder(), _CallRecorder()),
        (target, phase299, object(), _CallRecorder()),
        (target, phase299, _CallRecorder(), object()),
    )
    for path, route, loader, digest in cases:
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
        ) as caught:
            prepare_and_persist_external_publication_recovery_resume_decision_lineage(
                resume_intent_binding_path=path,  # type: ignore[arg-type]
                phase299_function=route,
                decision_loader=loader,
                decision_digest_function=digest,
            )
        _assert_preparation_error(
            caught.value, "path_type" if path is not target else "configuration"
        )
    assert phase299.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_phase299_called_once_with_only_exact_path_and_decision_loader_once(
    tmp_path: Path,
) -> None:
    routed = _decision_required()
    decision = _decision()
    phase299 = _CallRecorder(routed)
    loader = _CallRecorder(decision)
    digest = _CallRecorder("8" * 64)
    result = _run(
        tmp_path,
        routed,
        decision,
        phase299=phase299,
        decision_loader=loader,
        decision_digest=digest,
    )
    assert phase299.call_count == 1
    assert phase299.calls[0][0] == ()
    assert phase299.calls[0][1] == {
        "resume_intent_binding_path": tmp_path / "binding.json"
    }
    assert loader.call_count == 1
    assert (
        loader.calls[0][0][0]
        == tmp_path / f"{_DECISION_PREFIX}{routed.recovery_resume_outcome_sha256}.json"
    )
    assert digest.call_count == 1
    assert digest.calls[0][0][0] is decision
    assert isinstance(result, ExternalPublicationRecoveryResumeDecisionPreparation)
    target = tmp_path / f"{_PREPARATION_PREFIX}{'8' * 64}.json"
    assert target.exists()


def test_known_and_unexpected_phase299_errors_are_handled_without_persistence(
    tmp_path: Path,
) -> None:
    known = ExternalPublicationRecoveryResumeOutcomeRoutingError("route_contract")
    route = _CallRecorder(fault=known)
    with pytest.raises(ExternalPublicationRecoveryResumeOutcomeRoutingError) as caught:
        _run(tmp_path, _decision_required(), _decision(), phase299=route)
    assert caught.value is known
    assert list(tmp_path.iterdir()) == []

    unexpected = _CallRecorder(fault=RuntimeError("credential/path secret"))
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        _run(tmp_path, _decision_required(), _decision(), phase299=unexpected)
    _assert_preparation_error(caught.value, "dependency_error")
    assert "credential/path" not in str(caught.value)
    assert list(tmp_path.iterdir()) == []

    for error in (
        ExternalPublicationRecoveryResumeIntentBindingError("configuration"),
        ExternalPublicationRecoveryResumeStartAuthorizationError("configuration"),
        ExternalPublicationRecoveryResumeOutcomeError("configuration"),
        ExternalPublicationOperationStartError("configuration"),
    ):
        with pytest.raises(type(error)) as caught:
            _run(
                tmp_path,
                _decision_required(),
                _decision(),
                phase299=_CallRecorder(fault=error),
            )
        assert caught.value is error


def test_completed_route_has_zero_phase300_calls_and_zero_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="7" * 64
    )
    phase299 = _CallRecorder(completed)
    loader = _CallRecorder(_decision())
    digest = _CallRecorder("8" * 64)
    persistence = _CallRecorder()
    monkeypatch.setattr(
        preparation_module,
        "persist_external_publication_recovery_resume_decision_preparation",
        persistence,
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        _run(
            tmp_path,
            completed,
            _decision(),
            phase299=phase299,
            decision_loader=loader,
            decision_digest=digest,
        )
    _assert_preparation_error(caught.value, "preparation_not_required")
    assert phase299.call_count == 1
    assert loader.call_count == 0
    assert digest.call_count == 0
    assert persistence.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_completed_subclass_and_forged_outcome_fail_closed() -> None:
    class OutcomeChild(ExternalPublicationRecoveryResumeOutcome):
        pass

    source = _outcome(
        state="completed", result_kind="reconciliation", result_sha256="7" * 64
    )
    subclass = _forged_instance(source, cls=OutcomeChild)
    forged = _forged_instance(source, operation_start_sha256="F" * 64)
    for result in (subclass, forged, object()):
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
        ) as caught:
            _run(Path("."), result, _decision())
        _assert_preparation_error(caught.value, "predecessor_contract")


def test_decision_required_subclass_and_lookalike_fail_before_loader(
    tmp_path: Path,
) -> None:
    class DecisionRequiredChild(ExternalPublicationRecoveryResumeDecisionRequired):
        pass

    source = _decision_required()
    for routed in (_forged_instance(source, cls=DecisionRequiredChild), object()):
        loader = _CallRecorder(_decision())
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
        ) as caught:
            _run(tmp_path, routed, _decision(), decision_loader=loader)
        _assert_preparation_error(caught.value, "predecessor_contract")
        assert loader.call_count == 0


def test_loaded_decision_is_revalidated_before_digest_and_lineage(
    tmp_path: Path,
) -> None:
    routed = _decision_required()
    malformed = _forged_instance(_decision(), state="wrong")
    digest = _CallRecorder("8" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        _run(tmp_path, routed, malformed, decision_digest=digest)  # type: ignore[arg-type]
    _assert_preparation_error(caught.value, "predecessor_contract")
    assert digest.call_count == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("recovery_resume_outcome_sha256", "f" * 64),
        ("resume_start_authorization_sha256", "f" * 64),
        ("resume_intent_binding_sha256", "f" * 64),
        ("operation_intent_sha256", "f" * 64),
        ("operation_start_sha256", "f" * 64),
        ("publication_approval_sha256", "f" * 64),
        ("publication_plan_sha256", "f" * 64),
        ("source_operation", "fresh"),
        ("previous_recovery_kind", "reconciliation_mismatch"),
        ("recovery_kind", "already_acquired"),
        ("result_kind", "none"),
        ("result_sha256", "8" * 64),
    ],
)
def test_every_phase299_to_phase300_provenance_mismatch_stops_before_digest(
    tmp_path: Path, field: str, replacement: object
) -> None:
    routed = _decision_required()
    decision_overrides: dict[str, object] = {field: replacement}
    if field == "recovery_kind":
        decision_overrides.update(result_kind="none", result_sha256=None)
    if field == "result_kind":
        decision_overrides["recovery_kind"] = "already_acquired"
        decision_overrides["result_sha256"] = None
    if field == "result_sha256":
        decision_overrides["result_kind"] = "reconciliation"
    mismatched = _decision(**decision_overrides)
    digest = _CallRecorder("8" * 64)
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        _run(tmp_path, routed, mismatched, decision_digest=digest)
    _assert_preparation_error(caught.value, "predecessor_lineage")
    assert digest.call_count == 0
    assert not any(tmp_path.iterdir())


def test_invalid_operation_and_state_are_rejected_before_digest(tmp_path: Path) -> None:
    routed = _decision_required()
    for overrides in (
        {"operation": "fresh"},
        {"state": "not-decided"},
    ):
        malformed = _forged_instance(_decision(), **overrides)
        digest = _CallRecorder("8" * 64)
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
        ) as caught:
            _run(tmp_path, routed, malformed, decision_digest=digest)  # type: ignore[arg-type]
        _assert_preparation_error(caught.value, "predecessor_contract")
        assert digest.call_count == 0


def test_stop_decision_is_terminal_with_zero_digest_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed = _decision_required()
    stop = _decision(decision="stop")
    digest = _CallRecorder("8" * 64)
    persistence = _CallRecorder()
    monkeypatch.setattr(
        preparation_module,
        "persist_external_publication_recovery_resume_decision_preparation",
        persistence,
    )
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        _run(tmp_path, routed, stop, decision_digest=digest)
    _assert_preparation_error(caught.value, "preparation_not_authorized")
    assert digest.call_count == 0
    assert persistence.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_authorize_copies_every_field_and_binds_exact_decision_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed = _decision_required(
        previous_recovery_kind="reconciliation_mismatch",
        recovery_kind="already_acquired",
        result_kind="none",
        result_sha256=None,
    )
    decision = _decision(
        previous_recovery_kind="reconciliation_mismatch",
        recovery_kind="already_acquired",
        result_kind="none",
        result_sha256=None,
    )
    digest = _CallRecorder("8" * 64)
    persistence = _CallRecorder()
    monkeypatch.setattr(
        preparation_module,
        "persist_external_publication_recovery_resume_decision_preparation",
        persistence,
    )
    result = _run(tmp_path, routed, decision, decision_digest=digest)
    assert type(result) is ExternalPublicationRecoveryResumeDecisionPreparation
    assert digest.call_count == 1
    assert digest.calls[0][0][0] is decision
    assert persistence.call_count == 1
    assert (
        persistence.calls[0][0][0] == tmp_path / f"{_PREPARATION_PREFIX}{'8' * 64}.json"
    )
    persisted = persistence.calls[0][0][1]
    assert persisted is result
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
        "result_kind",
        "result_sha256",
    ):
        assert getattr(result, name) == getattr(decision, name)
    assert result.recovery_resume_decision_sha256 == "8" * 64
    assert result.decision == "authorize_resume_preparation"
    assert result.target_operation == "resume"
    assert result.state == "prepared"


def test_malformed_or_known_decision_digest_is_fail_closed(tmp_path: Path) -> None:
    routed = _decision_required()
    decision = _decision()
    for returned in ("not-a-digest", _StringChild("8" * 64), object()):
        digest = _CallRecorder(returned)
        with pytest.raises(
            ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
        ) as caught:
            _run(tmp_path, routed, decision, decision_digest=digest)
        _assert_preparation_error(caught.value, "decision_digest")
        assert not any(tmp_path.iterdir())

    known = ExternalPublicationRecoveryResumeDecisionError("configuration")
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionError) as caught:
        _run(tmp_path, routed, decision, decision_digest=_CallRecorder(fault=known))
    assert caught.value is known

    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        _run(
            tmp_path,
            routed,
            decision,
            decision_digest=_CallRecorder(fault=RuntimeError("sensitive")),
        )
    _assert_preparation_error(caught.value, "dependency_error")
    assert "sensitive" not in str(caught.value)


def test_same_exact_preparation_is_idempotent_and_returns_loaded_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed = _decision_required()
    decision = _decision()
    first = _run(tmp_path, routed, decision, decision_digest=_CallRecorder("8" * 64))
    target = tmp_path / f"{_PREPARATION_PREFIX}{'8' * 64}.json"
    before = target.read_bytes()
    original = load_external_publication_recovery_resume_decision_preparation
    loader = _CallRecorder(delegate=original)
    monkeypatch.setattr(
        preparation_module,
        "load_external_publication_recovery_resume_decision_preparation",
        loader,
    )
    second = _run(tmp_path, routed, decision, decision_digest=_CallRecorder("8" * 64))
    assert second == first
    assert second is loader.results[0]
    assert loader.call_count == 1
    assert target.read_bytes() == before


def test_existing_different_partial_and_noncanonical_artifacts_fail_closed_unchanged(
    tmp_path: Path,
) -> None:
    routed = _decision_required()
    decision = _decision()
    target = tmp_path / f"{_PREPARATION_PREFIX}{'8' * 64}.json"
    stale = _preparation(recovery_resume_decision_sha256="9" * 64)
    target.write_bytes(
        external_publication_recovery_resume_decision_preparation_canonical_bytes(stale)
    )
    before = target.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationConflictError
    ) as caught:
        _run(tmp_path, routed, decision, decision_digest=_CallRecorder("8" * 64))
    assert caught.value.detail.classification == "conflict"
    assert target.read_bytes() == before

    target.write_bytes(before[:-1])
    partial = target.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionPreparationLoadError):
        _run(tmp_path, routed, decision, decision_digest=_CallRecorder("8" * 64))
    assert target.read_bytes() == partial

    target.write_bytes(
        b" "
        + external_publication_recovery_resume_decision_preparation_canonical_bytes(
            _preparation(recovery_resume_decision_sha256="8" * 64)
        )
    )
    noncanonical = target.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumeDecisionPreparationLoadError):
        _run(tmp_path, routed, decision, decision_digest=_CallRecorder("8" * 64))
    assert target.read_bytes() == noncanonical


def test_cross_cycle_combinations_remain_authoritative_and_only_preparation_is_added(
    tmp_path: Path,
) -> None:
    first = _Lineage(tmp_path / "mismatch", previous_recovery_kind="already_acquired")
    first.root.mkdir()
    first.seed(
        state="recovery_required", result_kind="reconciliation", result_sha256="7" * 64
    )
    decision = __import__(
        "ai_office.engine",
        fromlist=["decide_and_persist_external_publication_recovery_resume"],
    ).decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=first.binding_path,
        decision="authorize_resume_preparation",
        decided_by="operator@example.test",
        decision_id="mismatch",
    )
    before = {path.name for path in first.root.iterdir()}
    result = prepare_and_persist_external_publication_recovery_resume_decision_lineage(
        resume_intent_binding_path=first.binding_path
    )
    assert result.previous_recovery_kind == "already_acquired"
    assert result.recovery_kind == "reconciliation_mismatch"
    assert (
        result.recovery_resume_decision_sha256
        == external_publication_recovery_resume_decision_digest(decision)
    )
    assert {path.name for path in first.root.iterdir()} == before | {
        f"{_PREPARATION_PREFIX}{result.recovery_resume_decision_sha256}.json"
    }

    second = _Lineage(
        tmp_path / "already", previous_recovery_kind="reconciliation_mismatch"
    )
    second.root.mkdir()
    second.seed(state="recovery_required", result_kind="none", result_sha256=None)
    decision2 = __import__(
        "ai_office.engine",
        fromlist=["decide_and_persist_external_publication_recovery_resume"],
    ).decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=second.binding_path,
        decision="authorize_resume_preparation",
        decided_by="operator@example.test",
        decision_id="already",
    )
    result2 = prepare_and_persist_external_publication_recovery_resume_decision_lineage(
        resume_intent_binding_path=second.binding_path
    )
    assert result2.previous_recovery_kind == "reconciliation_mismatch"
    assert result2.recovery_kind == "already_acquired"
    assert result2.result_kind == "none"
    assert result2.result_sha256 is None
    assert (
        result2.recovery_resume_decision_sha256
        == external_publication_recovery_resume_decision_digest(decision2)
    )


def test_integration_stop_and_completed_have_zero_phase301_artifacts(
    tmp_path: Path,
) -> None:
    lineage = _Lineage(tmp_path / "stop", previous_recovery_kind="already_acquired")
    lineage.root.mkdir()
    lineage.seed(
        state="recovery_required", result_kind="reconciliation", result_sha256="7" * 64
    )
    from ai_office.engine import decide_and_persist_external_publication_recovery_resume

    decision = decide_and_persist_external_publication_recovery_resume(
        resume_intent_binding_path=lineage.binding_path,
        decision="stop",
        decided_by="operator@example.test",
        decision_id="stop",
    )
    before = {
        path.name: path.read_bytes()
        for path in lineage.root.iterdir()
        if path.is_file()
    }
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        prepare_and_persist_external_publication_recovery_resume_decision_lineage(
            resume_intent_binding_path=lineage.binding_path
        )
    _assert_preparation_error(caught.value, "preparation_not_authorized")
    after = {
        path.name: path.read_bytes()
        for path in lineage.root.iterdir()
        if path.is_file()
    }
    assert after == before
    assert not any(
        path.name.startswith(_PREPARATION_PREFIX) for path in lineage.root.iterdir()
    )
    assert decision.decision == "stop"

    completed = _Lineage(
        tmp_path / "completed", previous_recovery_kind="already_acquired"
    )
    completed.root.mkdir()
    completed.seed(
        state="completed", result_kind="reconciliation", result_sha256="7" * 64
    )
    before_completed = {path.name for path in completed.root.iterdir()}
    with pytest.raises(
        ExternalPublicationRecoveryResumeDecisionPreparationCompatibilityError
    ) as caught:
        prepare_and_persist_external_publication_recovery_resume_decision_lineage(
            resume_intent_binding_path=completed.binding_path
        )
    _assert_preparation_error(caught.value, "preparation_not_required")
    assert {path.name for path in completed.root.iterdir()} == before_completed


def test_source_audit_and_no_cli_change() -> None:
    source_path = Path(preparation_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
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
    forbidden_calls = {
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
        "resolve",
        "realpath",
        "normpath",
        "abspath",
        "samefile",
    }
    assert forbidden_calls.isdisjoint(called)
    source = source_path.read_text(encoding="utf-8")
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
    assert "decision_preparation" not in cli_source
    assert "phase301" not in cli_source.lower()
