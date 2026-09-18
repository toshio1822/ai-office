"""Focused provider-free tests for the Phase 294 resume preparation boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_resume_preparation as prep_module
from ai_office.engine import (
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationOperationLifecycleOutcomeError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartError,
    ExternalPublicationPlan,
    ExternalPublicationRecoveryDecision,
    ExternalPublicationRecoveryDecisionError,
    ExternalPublicationRecoveryDecisionLoadError,
    ExternalPublicationRecoveryResumePreparation,
    ExternalPublicationRecoveryResumePreparationCompatibilityError,
    ExternalPublicationRecoveryResumePreparationConflictError,
    ExternalPublicationRecoveryResumePreparationError,
    ExternalPublicationRecoveryResumePreparationFailureDetail,
    ExternalPublicationRecoveryResumePreparationLoadError,
    ExternalPublicationRecoveryResumePreparationPersistenceError,
    acquire_external_publication_operation_start,
    approve_external_publication,
    build_external_publication_operation_intent,
    decide_and_persist_external_publication_recovery,
    external_publication_operation_lifecycle_outcome_digest,
    external_publication_operation_start_digest,
    external_publication_recovery_decision_digest,
    external_publication_recovery_resume_preparation_canonical_bytes,
    external_publication_recovery_resume_preparation_digest,
    load_external_publication_operation_lifecycle_outcome,
    load_external_publication_operation_start,
    load_external_publication_recovery_decision,
    load_external_publication_recovery_resume_preparation,
    persist_external_publication_operation_intent,
    persist_external_publication_operation_lifecycle_outcome,
    persist_external_publication_recovery_resume_preparation,
    prepare_and_persist_external_publication_recovery_resume_lineage,
    serialize_external_publication_recovery_resume_preparation_canonical,
)

_PREPARATION_SCHEMA = "external-publication-recovery-resume-preparation.v1"
_LIFECYCLE_SCHEMA = "external-publication-operation-lifecycle-outcome.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_DECISION_SCHEMA = "external-publication-recovery-decision.v1"
_MESSAGE = "external publication recovery resume preparation is invalid"
_PERSIST_MESSAGE = "external publication recovery resume preparation persistence failed"
_LOAD_MESSAGE = "external publication recovery resume preparation could not be loaded"
_SOURCE = Path(prep_module.__file__).read_text(encoding="utf-8")
_KEYS = tuple(
    sorted(
        (
            "lifecycle_outcome_sha256",
            "operation_start_sha256",
            "publication_approval_sha256",
            "publication_plan_sha256",
            "recovery_decision_sha256",
            "recovery_kind",
            "schema_version",
            "source_operation",
            "state",
            "target_operation",
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


class _StringChild(str):
    pass


def _forged_instance(cls: type[object], source: object, **overrides: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    for name, replacement in overrides.items():
        object.__setattr__(value, name, replacement)
    return value


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumePreparationCompatibilityError
    assert isinstance(error, ExternalPublicationRecoveryResumePreparationError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert type(error.detail) is (
        ExternalPublicationRecoveryResumePreparationFailureDetail
    )
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_persistence_error(error: ValueError, classification: str) -> None:
    assert type(error) is (ExternalPublicationRecoveryResumePreparationPersistenceError)
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_load_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationRecoveryResumePreparationLoadError
    assert str(error) == _LOAD_MESSAGE
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _assert_conflict_error(error: ValueError) -> None:
    assert type(error) is ExternalPublicationRecoveryResumePreparationConflictError
    assert str(error) == _PERSIST_MESSAGE
    assert error.detail.classification == "conflict"
    assert error.__cause__ is None


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-294",
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
        approved_by="human-reviewer-294",
        approval_id="approval-294",
    )


def _start(
    operation: str = "fresh",
    *,
    approval_digest: str = "b" * 64,
    plan_digest: str = "c" * 64,
    intent_digest: str = "a" * 64,
) -> ExternalPublicationOperationStart:
    return ExternalPublicationOperationStart(
        schema_version=_START_SCHEMA,  # type: ignore[arg-type]
        operation_intent_sha256=intent_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        operation=operation,  # type: ignore[arg-type]
        state="started",
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


def _install_persistence_fault(scope: pytest.MonkeyPatch, stage: str) -> None:
    """Fault-inject exactly one step of the append-only persistence sequence."""

    def _fsync_boom(*args: object, **kwargs: object) -> None:
        raise OSError("fsync failed")

    if stage == "file_fsync":
        scope.setattr(prep_module.os, "fsync", _fsync_boom)
        return
    if stage == "dir_fsync":
        scope.setattr(prep_module, "_fsync_preparation_directory", _fsync_boom)
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
    intent_path = root / "intent.json"
    start_path = root / "start.json"
    persist_external_publication_operation_intent(intent_path, intent)
    acquire_external_publication_operation_start(
        intent_path=intent_path, start_path=start_path
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
        decided_by="operator-294",
        decision_id="decision-294",
    )
    return decision_path, lifecycle_path, start_path


def _prepare(
    decision_path: Path,
    lifecycle_path: Path,
    start_path: Path,
    preparation_path: Path,
    **kwargs: object,
) -> ExternalPublicationRecoveryResumePreparation:
    return prepare_and_persist_external_publication_recovery_resume_lineage(
        recovery_decision_path=decision_path,
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        resume_preparation_path=preparation_path,
        **kwargs,  # type: ignore[arg-type]
    )


# --- public surface -------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert (
        ExternalPublicationRecoveryResumePreparationError.__name__
        == "ExternalPublicationRecoveryResumePreparationError"
    )
    assert issubclass(ExternalPublicationRecoveryResumePreparationError, ValueError)
    assert issubclass(
        ExternalPublicationRecoveryResumePreparationCompatibilityError,
        ExternalPublicationRecoveryResumePreparationError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumePreparationPersistenceError,
        ExternalPublicationRecoveryResumePreparationError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumePreparationConflictError,
        ExternalPublicationRecoveryResumePreparationPersistenceError,
    )
    assert issubclass(
        ExternalPublicationRecoveryResumePreparationLoadError,
        ExternalPublicationRecoveryResumePreparationError,
    )
    for name in (
        "serialize_external_publication_recovery_resume_preparation_canonical",
        "external_publication_recovery_resume_preparation_canonical_bytes",
        "external_publication_recovery_resume_preparation_digest",
        "load_external_publication_recovery_resume_preparation",
        "persist_external_publication_recovery_resume_preparation",
        "prepare_and_persist_external_publication_recovery_resume_lineage",
    ):
        assert name in prep_module.__all__, name
        assert callable(getattr(prep_module, name))


def test_signature_defaults_and_no_caller_authority_arguments() -> None:
    signature = inspect.signature(
        prepare_and_persist_external_publication_recovery_resume_lineage
    )
    parameters = signature.parameters
    for forbidden in (
        "decision",
        "decision_object",
        "lifecycle",
        "lifecycle_object",
        "start",
        "start_object",
        "recovery_kind",
        "target_operation",
        "operation_intent",
        "approval",
        "predecessor_digests",
    ):
        assert forbidden not in parameters, forbidden
    expected = {
        "recovery_decision_path": inspect.Parameter.empty,
        "lifecycle_outcome_path": inspect.Parameter.empty,
        "start_path": inspect.Parameter.empty,
        "resume_preparation_path": inspect.Parameter.empty,
        "decision_loader": load_external_publication_recovery_decision,
        "decision_digest_function": (external_publication_recovery_decision_digest),
        "lifecycle_loader": (load_external_publication_operation_lifecycle_outcome),
        "lifecycle_digest_function": (
            external_publication_operation_lifecycle_outcome_digest
        ),
        "start_loader": load_external_publication_operation_start,
        "start_digest_function": external_publication_operation_start_digest,
    }
    assert set(parameters) == set(expected)
    for name, default in expected.items():
        assert parameters[name].default is default, name
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_model_field_order_and_frozen() -> None:
    assert [
        field.name
        for field in dataclasses.fields(ExternalPublicationRecoveryResumePreparation)
    ] == [
        "schema_version",
        "recovery_decision_sha256",
        "lifecycle_outcome_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "recovery_kind",
        "target_operation",
        "state",
    ]
    assert type(_preparation()).__dataclass_params__.frozen is True
    with pytest.raises(Exception):
        _preparation().state = "prepared"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"source_operation": "resume"},
        {"recovery_kind": "reconciliation_mismatch", "source_operation": "resume"},
        {"target_operation": "resume", "state": "prepared"},
    ],
)
def test_model_accepts_exact_valid_combinations(overrides: dict[str, object]) -> None:
    preparation = _preparation(**overrides)
    assert preparation.state == "prepared"
    assert preparation.target_operation == "resume"
    assert preparation.schema_version == _PREPARATION_SCHEMA


def test_model_rejects_reconciliation_mismatch_with_fresh_source() -> None:
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _preparation(source_operation="fresh", recovery_kind="reconciliation_mismatch")
    _assert_error(info.value, "configuration")


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "external-publication-recovery-resume-preparation.v2"},
        {"decision_digest": "A" * 64},
        {"decision_digest": "7" * 63},
        {"decision_digest": None},
        {"lifecycle_digest": "not-a-digest"},
        {"start_digest": 1234},
        {"approval_digest": "b" * 63},
        {"plan_digest": None},
        {"source_operation": "replay"},
        {"recovery_kind": "mismatch"},
        {"target_operation": "fresh"},
        {"target_operation": "RESUME"},
        {"state": "decided"},
        {"state": "executed"},
    ],
)
def test_model_rejects_non_exact_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _preparation(**overrides)
    _assert_error(info.value, "configuration")


def test_model_rejects_non_exact_runtime_types() -> None:
    for overrides in (
        {"decision_digest": _StringChild("7" * 64)},
        {"source_operation": _StringChild("fresh")},
        {"recovery_kind": _StringChild("already_acquired")},
        {"target_operation": _StringChild("resume")},
        {"state": _StringChild("prepared")},
    ):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _preparation(**overrides)
        _assert_error(info.value, "configuration")


def test_model_subclass_rejected_by_helpers(tmp_path: Path) -> None:
    class _Child(ExternalPublicationRecoveryResumePreparation):
        pass

    child = object.__new__(_Child)
    for field in dataclasses.fields(ExternalPublicationRecoveryResumePreparation):
        object.__setattr__(child, field.name, getattr(_preparation(), field.name))
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        serialize_external_publication_recovery_resume_preparation_canonical(
            child  # type: ignore[arg-type]
        )
    _assert_error(info.value, "configuration")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        persist_external_publication_recovery_resume_preparation(
            tmp_path / "prep.json",
            child,  # type: ignore[arg-type]
        )
    _assert_error(info.value, "configuration")
    assert not (tmp_path / "prep.json").exists()


def test_forged_model_is_rejected_by_helpers(tmp_path: Path) -> None:
    forged = _forged_instance(
        ExternalPublicationRecoveryResumePreparation,
        _preparation(),
        target_operation="fresh",
    )
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        external_publication_recovery_resume_preparation_canonical_bytes(
            forged  # type: ignore[arg-type]
        )
    _assert_error(info.value, "configuration")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        persist_external_publication_recovery_resume_preparation(
            tmp_path / "prep.json",
            forged,  # type: ignore[arg-type]
        )
    _assert_error(info.value, "configuration")


# --- canonical form and loader --------------------------------------------


def test_canonical_json_exact_keys_and_deterministic_digest() -> None:
    import json

    preparation = _preparation()
    text = serialize_external_publication_recovery_resume_preparation_canonical(
        preparation
    )
    parsed = json.loads(text)
    assert tuple(parsed) == _KEYS
    assert text == json.dumps(
        parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    assert external_publication_recovery_resume_preparation_canonical_bytes(
        preparation
    ) == text.encode("utf-8")
    assert external_publication_recovery_resume_preparation_digest(
        preparation
    ) == external_publication_recovery_resume_preparation_digest(_preparation())


def test_loader_round_trips_exact_bytes(tmp_path: Path) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    path.write_bytes(
        external_publication_recovery_resume_preparation_canonical_bytes(preparation)
    )
    loaded = load_external_publication_recovery_resume_preparation(path)
    assert loaded == preparation
    assert type(loaded) is ExternalPublicationRecoveryResumePreparation


def test_loader_rejects_noncanonical_whitespace(tmp_path: Path) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    canonical = external_publication_recovery_resume_preparation_canonical_bytes(
        preparation
    )
    path.write_bytes(canonical.replace(b'":"', b'": "', 1))
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        load_external_publication_recovery_resume_preparation(path)
    _assert_load_error(info.value, "noncanonical")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"[]",
        b'{"schema_version":NaN}',
        b'{"a":1,"a":2}',
        b"not json at all",
        b'{"schema_version":"x","schema_version":"y"}',
    ],
)
def test_loader_rejects_malformed_payloads(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "prep.json"
    path.write_bytes(payload)
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        load_external_publication_recovery_resume_preparation(path)
    assert info.value.detail.classification in {"parse", "keys"}
    assert str(info.value) == _LOAD_MESSAGE


def test_loader_rejects_oversized_payload(tmp_path: Path) -> None:
    path = tmp_path / "prep.json"
    path.write_bytes(b"{" + b" " * 5000 + b"}")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        load_external_publication_recovery_resume_preparation(path)
    _assert_load_error(info.value, "size")


def test_loader_rejects_extra_and_missing_keys(tmp_path: Path) -> None:
    import json

    base = json.loads(
        external_publication_recovery_resume_preparation_canonical_bytes(
            _preparation()
        ).decode("utf-8")
    )
    extra = dict(base)
    extra["unexpected"] = "x"
    missing = dict(base)
    del missing["target_operation"]
    for candidate in (extra, missing):
        path = tmp_path / "prep.json"
        path.write_bytes(
            json.dumps(
                candidate, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        )
        with pytest.raises(
            ExternalPublicationRecoveryResumePreparationLoadError
        ) as info:
            load_external_publication_recovery_resume_preparation(path)
        _assert_load_error(info.value, "keys")


def test_loader_rejects_non_regular_target(tmp_path: Path) -> None:
    directory = tmp_path / "adir"
    directory.mkdir()
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        load_external_publication_recovery_resume_preparation(directory)
    _assert_load_error(info.value, "target")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        load_external_publication_recovery_resume_preparation(tmp_path / "absent.json")
    _assert_load_error(info.value, "target")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        load_external_publication_recovery_resume_preparation("not-a-path")  # type: ignore[arg-type]
    _assert_load_error(info.value, "path_type")


# --- persistence ----------------------------------------------------------


def test_persistence_idempotent_for_identical_bytes(tmp_path: Path) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    persist_external_publication_recovery_resume_preparation(path, preparation)
    before = path.read_bytes()
    assert before == external_publication_recovery_resume_preparation_canonical_bytes(
        preparation
    )
    persist_external_publication_recovery_resume_preparation(path, preparation)
    assert path.read_bytes() == before


def test_persistence_conflict_for_different_bytes(tmp_path: Path) -> None:
    path = tmp_path / "prep.json"
    persist_external_publication_recovery_resume_preparation(path, _preparation())
    before = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationConflictError
    ) as info:
        persist_external_publication_recovery_resume_preparation(
            path, _preparation(decision_digest="6" * 64)
        )
    _assert_conflict_error(info.value)
    assert path.read_bytes() == before


def test_persistence_conflict_for_partial_bytes(tmp_path: Path) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    canonical = external_publication_recovery_resume_preparation_canonical_bytes(
        preparation
    )
    path.write_bytes(canonical[:-1])
    before = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationConflictError
    ) as info:
        persist_external_publication_recovery_resume_preparation(path, preparation)
    _assert_conflict_error(info.value)
    assert path.read_bytes() == before


def test_persistence_rejects_bad_parent_and_target(tmp_path: Path) -> None:
    preparation = _preparation()
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_preparation(
            tmp_path / "absent" / "prep.json", preparation
        )
    _assert_persistence_error(info.value, "parent")

    directory = tmp_path / "adir"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_preparation(directory, preparation)
    _assert_persistence_error(info.value, "target")

    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_preparation(
            "not-a-path",
            preparation,  # type: ignore[arg-type]
        )
    _assert_persistence_error(info.value, "path_type")


def test_persistence_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    persist_external_publication_recovery_resume_preparation(real, _preparation())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        persist_external_publication_recovery_resume_preparation(link, _preparation())
    _assert_persistence_error(info.value, "target")


_FULL_WRITE_STAGES = ("flush_error", "file_fsync", "close_failure", "dir_fsync")
_PARTIAL_WRITE_STAGES = ("write_error", "short_write")


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_persistence_ambiguity_per_stage_retains_artifact_no_retry(
    tmp_path: Path, stage: str
) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumePreparationPersistenceError
        ) as info:
            persist_external_publication_recovery_resume_preparation(path, preparation)
    _assert_persistence_error(info.value, "ambiguous")
    # the artifact is always retained, never cleaned up, rewritten, or retried
    assert path.exists()
    canonical = external_publication_recovery_resume_preparation_canonical_bytes(
        preparation
    )
    if stage in _FULL_WRITE_STAGES:
        assert path.read_bytes() == canonical
    else:
        assert path.read_bytes() != canonical


@pytest.mark.parametrize("stage", _FULL_WRITE_STAGES)
def test_persistence_ambiguity_then_exact_retained_bytes_idempotent(
    tmp_path: Path, stage: str
) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumePreparationPersistenceError
        ):
            persist_external_publication_recovery_resume_preparation(path, preparation)
    persist_external_publication_recovery_resume_preparation(path, preparation)
    assert load_external_publication_recovery_resume_preparation(path) == preparation

    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationConflictError
    ) as info:
        persist_external_publication_recovery_resume_preparation(
            path, _preparation(plan_digest="a" * 64)
        )
    _assert_conflict_error(info.value)


@pytest.mark.parametrize("stage", _PARTIAL_WRITE_STAGES)
def test_persistence_partial_retained_bytes_fail_closed(
    tmp_path: Path, stage: str
) -> None:
    preparation = _preparation()
    path = tmp_path / "prep.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, stage)
        with pytest.raises(
            ExternalPublicationRecoveryResumePreparationPersistenceError
        ):
            persist_external_publication_recovery_resume_preparation(path, preparation)
    of_size = path.stat().st_size
    before = path.read_bytes()
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationConflictError
    ) as info:
        persist_external_publication_recovery_resume_preparation(path, preparation)
    _assert_conflict_error(info.value)
    assert path.read_bytes() == before
    assert path.stat().st_size == of_size


# --- preflight ------------------------------------------------------------


def test_preflight_rejects_bad_paths_and_dependencies(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    for kwargs in (
        {"recovery_decision_path": "not-a-path"},
        {"lifecycle_outcome_path": 5},
        {"start_path": None},
        {"resume_preparation_path": b"bytes"},
    ):
        args = {
            "recovery_decision_path": decision_path,
            "lifecycle_outcome_path": lifecycle_path,
            "start_path": start_path,
            "resume_preparation_path": preparation_path,
        }
        args.update(kwargs)
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            prepare_and_persist_external_publication_recovery_resume_lineage(
                **args  # type: ignore[arg-type]
            )
        _assert_error(info.value, "path_type")
    assert not preparation_path.exists()

    for name in (
        "decision_loader",
        "decision_digest_function",
        "lifecycle_loader",
        "lifecycle_digest_function",
        "start_loader",
        "start_digest_function",
    ):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                **{name: object()},
            )
        _assert_error(info.value, "configuration")
    assert not preparation_path.exists()


def test_preflight_rejects_bad_preparation_target(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    directory = tmp_path / "adir"
    directory.mkdir()
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        _prepare(decision_path, lifecycle_path, start_path, directory)
    _assert_persistence_error(info.value, "target")

    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            tmp_path / "absent" / "prep.json",
        )
    _assert_persistence_error(info.value, "parent")


# --- decision (Phase 293) provenance --------------------------------------


def test_decision_loader_exactly_once_with_exact_path_identity(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    decision_loader = _LoaderRecorder(load_external_publication_recovery_decision)
    decision_digest = _CallRecorder(external_publication_recovery_decision_digest)

    preparation = _prepare(
        decision_path,
        lifecycle_path,
        start_path,
        preparation_path,
        decision_loader=decision_loader,
        decision_digest_function=decision_digest,
    )

    assert len(decision_loader.calls) == 1
    assert decision_loader.calls[0] is decision_path
    assert len(decision_digest.calls) == 1
    assert decision_digest.calls[0][0][0] is decision_loader.results[0]
    assert preparation.recovery_decision_sha256 == (
        external_publication_recovery_decision_digest(decision_loader.results[0])
    )


def test_decision_subclass_or_lookalike_rejected(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_recovery_decision(decision_path)

    class _Child(ExternalPublicationRecoveryDecision):
        pass

    for value in (_forged_instance(_Child, real), object()):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                decision_loader=lambda path, value=value: value,
            )
        _assert_error(info.value, "decision_contract")
    assert not preparation_path.exists()


def test_decision_contract_rejects_forged_state(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(ExternalPublicationRecoveryDecision, real, state="open")
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=lambda path: forged,
        )
    _assert_error(info.value, "decision_state")
    assert not preparation_path.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "external-publication-recovery-decision.v2"},
        {"lifecycle_outcome_sha256": "1" * 63},
        {"operation_start_sha256": None},
        {"publication_approval_sha256": "A" * 64},
        {"publication_plan_sha256": 7},
        {"source_operation": "replay"},
        {"recovery_kind": "mismatch"},
    ],
)
def test_decision_contract_rejects_forged_fields(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(ExternalPublicationRecoveryDecision, real, **overrides)
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=lambda path: forged,
        )
    _assert_error(info.value, "decision_contract")
    assert not preparation_path.exists()


_METADATA_INVALID_CASES = [
    pytest.param(1234, id="non-string-int"),
    pytest.param(None, id="non-string-none"),
    pytest.param(_StringChild("operator-294"), id="non-string-str-child"),
    pytest.param("", id="empty"),
    pytest.param(" operator-294", id="leading-whitespace"),
    pytest.param("operator-294 ", id="trailing-whitespace"),
    pytest.param("a" * 257, id="over-max-length"),
    pytest.param("oper\x00ator", id="cc-character"),
    pytest.param("oper\ud800ator", id="cs-surrogate"),
]


@pytest.mark.parametrize("field", ["decided_by", "decision_id"])
@pytest.mark.parametrize("value", _METADATA_INVALID_CASES)
def test_decision_metadata_revalidated_locally_before_any_dependency(
    tmp_path: Path, field: str, value: object
) -> None:
    """Phase 294 rejects forged Phase 293 operator metadata on its own.

    The injected decision digest helper would return a valid lowercase 64-hex
    digest for this operand, so only the Phase 294 local boundary contract can
    reject it.  The rejection must happen before the digest helper, the
    lifecycle loader, or the start loader is ever called.
    """
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    before = (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    )
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(
        ExternalPublicationRecoveryDecision, real, **{field: value}
    )
    digest_function = _CallRecorder(lambda *args, **kwargs: "a" * 64)
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    start_loader = _LoaderRecorder(load_external_publication_operation_start)

    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=lambda path: forged,
            decision_digest_function=digest_function,
            lifecycle_loader=lifecycle_loader,
            start_loader=start_loader,
        )
    _assert_error(info.value, "decision_contract")
    assert len(digest_function.calls) == 0
    assert len(lifecycle_loader.calls) == 0
    assert len(start_loader.calls) == 0
    assert not preparation_path.exists()
    assert (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    ) == before


@pytest.mark.parametrize("field", ["decided_by", "decision_id"])
def test_decision_metadata_exactly_max_length_still_accepted(
    tmp_path: Path, field: str
) -> None:
    """The Phase 293 ``<= 256`` length contract is preserved exactly.

    Exactly 256 characters with no surrounding or control characters stays
    valid; the rejection boundary begins at 257.
    """
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(
        ExternalPublicationRecoveryDecision, real, **{field: "a" * 256}
    )
    preparation = _prepare(
        decision_path,
        lifecycle_path,
        start_path,
        preparation_path,
        decision_loader=lambda path: forged,
    )
    assert preparation.state == "prepared"
    assert preparation.target_operation == "resume"


def test_stop_decision_rejected_before_target_mutation(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path, chosen="stop")
    preparation_path = tmp_path / "prep.json"
    before = (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    )
    decision_loader = _LoaderRecorder(load_external_publication_recovery_decision)
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=decision_loader,
        )
    _assert_error(info.value, "decision_state")
    assert not preparation_path.exists()
    assert (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    ) == before
    # the authorizing check happens before lifecycle/start are touched.
    assert len(decision_loader.calls) == 1


def test_stop_decision_is_rejected_even_when_provenance_is_valid(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path, chosen="stop")
    preparation_path = tmp_path / "prep.json"
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    start_loader = _LoaderRecorder(load_external_publication_operation_start)
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            lifecycle_loader=lifecycle_loader,
            start_loader=start_loader,
        )
    _assert_error(info.value, "decision_state")
    assert len(lifecycle_loader.calls) == 0
    assert len(start_loader.calls) == 0
    assert not preparation_path.exists()


def test_decision_digest_malformed_helper_return_rejected(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    for value in ("zzz", "A" * 64, 1234, None):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                decision_digest_function=lambda decision, value=value: value,
            )
        _assert_error(info.value, "decision_digest")
    assert not preparation_path.exists()


def test_decision_known_error_same_object_identity(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    known = ExternalPublicationRecoveryDecisionLoadError("target")
    with pytest.raises(ExternalPublicationRecoveryDecisionError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=_CallRecorder(fault=known),
        )
    assert info.value is known
    assert not preparation_path.exists()


def test_decision_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=_CallRecorder(fault=RuntimeError("secret detail")),
        )
    _assert_error(info.value, "dependency_error")
    assert "secret detail" not in str(info.value)
    assert not preparation_path.exists()


def test_decision_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    known = ExternalPublicationRecoveryDecisionError("decision_contract")
    with pytest.raises(ExternalPublicationRecoveryDecisionError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_digest_function=_CallRecorder(fault=known),
        )
    assert info.value is known


def test_decision_digest_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_digest_function=_CallRecorder(fault=RuntimeError("boom")),
        )
    _assert_error(info.value, "dependency_error")


# --- lifecycle (Phase 292) provenance -------------------------------------


def test_lifecycle_loader_and_digest_exactly_once_exact_identities(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    lifecycle_digest = _CallRecorder(
        external_publication_operation_lifecycle_outcome_digest
    )

    preparation = _prepare(
        decision_path,
        lifecycle_path,
        start_path,
        preparation_path,
        lifecycle_loader=lifecycle_loader,
        lifecycle_digest_function=lifecycle_digest,
    )

    assert len(lifecycle_loader.calls) == 1
    assert lifecycle_loader.calls[0] is lifecycle_path
    assert len(lifecycle_digest.calls) == 1
    assert lifecycle_digest.calls[0][0][0] is lifecycle_loader.results[0]
    assert preparation.lifecycle_outcome_sha256 == lifecycle_digest.results[0]


def test_lifecycle_subclass_or_lookalike_rejected(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_operation_lifecycle_outcome(lifecycle_path)

    class _Child(ExternalPublicationOperationLifecycleOutcome):
        pass

    for value in (_forged_instance(_Child, real), object()):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                lifecycle_loader=lambda path, value=value: value,
            )
        _assert_error(info.value, "lifecycle_contract")
    assert not preparation_path.exists()


def test_completed_lifecycle_rejected_before_write(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    start = load_external_publication_operation_start(start_path)
    completed = _lifecycle(
        start_digest=external_publication_operation_start_digest(start),
        operation="fresh",
        state="completed",
        result_kind="execution_result",
        result_sha256="d" * 64,
    )
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            lifecycle_loader=lambda path: completed,
        )
    _assert_error(info.value, "lifecycle_state")
    assert not preparation_path.exists()


def test_lifecycle_digest_mismatch_rejected_before_write(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(
        ExternalPublicationRecoveryDecision,
        real,
        lifecycle_outcome_sha256="1" * 64,
    )
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=lambda path: forged,
        )
    _assert_error(info.value, "lifecycle_digest")
    assert not preparation_path.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"publication_approval_sha256": "a" * 64},
        {"publication_plan_sha256": "a" * 64},
        {"source_operation": "resume"},
    ],
)
def test_lifecycle_decision_lineage_mismatch_rejected_before_write(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(ExternalPublicationRecoveryDecision, real, **overrides)
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=lambda path: forged,
        )
    _assert_error(info.value, "lifecycle_lineage")
    assert not preparation_path.exists()


def test_lifecycle_derived_recovery_kind_mismatch_rejected_before_write(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    start = load_external_publication_operation_start(start_path)
    reconciliation = _lifecycle(
        start_digest=external_publication_operation_start_digest(start),
        operation="resume",
        state="recovery_required",
        result_kind="reconciliation",
        result_sha256="d" * 64,
        approval_digest=start.publication_approval_sha256,
        plan_digest=start.publication_plan_sha256,
    )
    real = load_external_publication_recovery_decision(decision_path)
    forged = _forged_instance(
        ExternalPublicationRecoveryDecision,
        real,
        lifecycle_outcome_sha256=(
            external_publication_operation_lifecycle_outcome_digest(reconciliation)
        ),
    )
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=lambda path: forged,
            lifecycle_loader=lambda path: reconciliation,
        )
    _assert_error(info.value, "lifecycle_lineage")
    assert not preparation_path.exists()


def test_lifecycle_digest_malformed_helper_return_rejected(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    for value in ("zzz", "A" * 64, None):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                lifecycle_digest_function=lambda lifecycle, value=value: value,
            )
        _assert_error(info.value, "lifecycle_digest")
    assert not preparation_path.exists()


def test_lifecycle_known_error_same_object_identity(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    known = ExternalPublicationOperationLifecycleOutcomeError("lifecycle_contract")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            lifecycle_loader=_CallRecorder(fault=known),
        )
    assert info.value is known
    assert not preparation_path.exists()


def test_lifecycle_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    known = ExternalPublicationOperationLifecycleOutcomeError("lifecycle_digest")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            lifecycle_digest_function=_CallRecorder(fault=known),
        )
    assert info.value is known


def test_lifecycle_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    for kwargs in (
        {"lifecycle_loader": _CallRecorder(fault=RuntimeError("secret"))},
        {"lifecycle_digest_function": _CallRecorder(fault=RuntimeError("secret"))},
    ):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                **kwargs,
            )
        _assert_error(info.value, "dependency_error")
        assert "secret" not in str(info.value)
    assert not preparation_path.exists()


# --- start (Phase 290) provenance -----------------------------------------


def test_start_loader_and_digest_exactly_once_exact_identities(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    start_loader = _LoaderRecorder(load_external_publication_operation_start)
    start_digest = _CallRecorder(external_publication_operation_start_digest)

    preparation = _prepare(
        decision_path,
        lifecycle_path,
        start_path,
        preparation_path,
        start_loader=start_loader,
        start_digest_function=start_digest,
    )

    assert len(start_loader.calls) == 1
    assert start_loader.calls[0] is start_path
    assert len(start_digest.calls) == 1
    assert start_digest.calls[0][0][0] is start_loader.results[0]
    assert preparation.operation_start_sha256 == start_digest.results[0]


def test_start_subclass_or_lookalike_rejected(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_operation_start(start_path)

    class _Child(ExternalPublicationOperationStart):
        pass

    for value in (_forged_instance(_Child, real), object()):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                start_loader=lambda path, value=value: value,
            )
        _assert_error(info.value, "start_contract")
    assert not preparation_path.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"operation": "resume"},
        {"publication_approval_sha256": "a" * 64},
        {"publication_plan_sha256": "a" * 64},
        {"state": "acquired"},
    ],
)
def test_start_contract_or_lineage_mismatch_rejected_before_write(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    real = load_external_publication_operation_start(start_path)
    forged = _forged_instance(ExternalPublicationOperationStart, real, **overrides)
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            start_loader=lambda path: forged,
        )
    expected = "start_contract" if "state" in overrides else "start_lineage"
    _assert_error(info.value, expected)
    assert not preparation_path.exists()


def test_start_digest_mismatch_rejected_before_write(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    for value in ("f" * 64, "zzz", None):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                start_digest_function=lambda start, value=value: value,
            )
        _assert_error(info.value, "start_lineage")
    assert not preparation_path.exists()


def test_start_known_error_same_object_identity(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    known = ExternalPublicationOperationStartError("start_contract")
    with pytest.raises(ExternalPublicationOperationStartError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            start_loader=_CallRecorder(fault=known),
        )
    assert info.value is known
    assert not preparation_path.exists()


def test_start_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    known = ExternalPublicationOperationStartError("start_lineage")
    with pytest.raises(ExternalPublicationOperationStartError) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            start_digest_function=_CallRecorder(fault=known),
        )
    assert info.value is known


def test_start_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    for kwargs in (
        {"start_loader": _CallRecorder(fault=RuntimeError("secret"))},
        {"start_digest_function": _CallRecorder(fault=RuntimeError("secret"))},
    ):
        with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
            _prepare(
                decision_path,
                lifecycle_path,
                start_path,
                preparation_path,
                **kwargs,
            )
        _assert_error(info.value, "dependency_error")
        assert "secret" not in str(info.value)
    assert not preparation_path.exists()


# --- construction, existing fast path, and no-retry ---------------------


@pytest.mark.parametrize(
    "scenario",
    [
        {
            "operation": "fresh",
            "result_kind": "none",
            "recovery_kind": "already_acquired",
        },
        {
            "operation": "resume",
            "result_kind": "none",
            "recovery_kind": "already_acquired",
        },
        {
            "operation": "resume",
            "result_kind": "reconciliation",
            "recovery_kind": "reconciliation_mismatch",
        },
    ],
)
def test_constructed_target_operation_and_state_are_exact(
    tmp_path: Path, scenario: dict[str, str]
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(
        tmp_path,
        operation=scenario["operation"],
        result_kind=scenario["result_kind"],
        result_sha256="d" * 64 if scenario["result_kind"] == "reconciliation" else None,
    )
    preparation_path = tmp_path / "prep.json"
    preparation = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    assert preparation.target_operation == "resume"
    assert preparation.state == "prepared"
    assert preparation.recovery_kind == scenario["recovery_kind"]
    assert preparation.source_operation == scenario["operation"]
    assert (
        load_external_publication_recovery_resume_preparation(preparation_path)
        == preparation
    )


def test_existing_preparation_loader_exactly_once_returns_loaded_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    first = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    before = preparation_path.read_bytes()

    preparation_loader = _CallRecorder(
        load_external_publication_recovery_resume_preparation
    )
    monkeypatch.setattr(
        prep_module,
        "load_external_publication_recovery_resume_preparation",
        preparation_loader,
    )
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    start_loader = _LoaderRecorder(load_external_publication_operation_start)

    second = _prepare(
        decision_path,
        lifecycle_path,
        start_path,
        preparation_path,
        lifecycle_loader=lifecycle_loader,
        start_loader=start_loader,
    )

    assert len(preparation_loader.calls) == 1
    assert preparation_loader.first_positional is preparation_path
    assert len(preparation_loader.results) == 1
    assert second is preparation_loader.results[0]
    assert second == first
    assert len(lifecycle_loader.calls) == 1
    assert len(start_loader.calls) == 1
    assert preparation_path.read_bytes() == before


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"decision_digest": "1" * 64},
        {"lifecycle_digest": "2" * 64},
        {"start_digest": "3" * 64},
        {"approval_digest": "4" * 64},
        {"plan_digest": "5" * 64},
        {"source_operation": "resume"},
        {"recovery_kind": "reconciliation_mismatch", "source_operation": "resume"},
        {"plan_digest": "0" * 64},
    ],
)
def test_existing_preparation_mismatch_conflicts_unchanged(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    stale = _preparation(**overrides)
    persist_external_publication_recovery_resume_preparation(preparation_path, stale)
    before = preparation_path.read_bytes()
    assert before == (
        external_publication_recovery_resume_preparation_canonical_bytes(stale)
    )

    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationConflictError
    ) as info:
        _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    _assert_conflict_error(info.value)
    assert preparation_path.read_bytes() == before


def test_existing_preparation_corrupt_bytes_is_load_error_unchanged(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    preparation_path.write_bytes(b"{not canonical")
    before = preparation_path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumePreparationLoadError) as info:
        _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    _assert_load_error(info.value, "parse")
    assert preparation_path.read_bytes() == before


def test_persistence_failure_never_retries_predecessor_or_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    decision_loader = _LoaderRecorder(load_external_publication_recovery_decision)
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    start_loader = _LoaderRecorder(load_external_publication_operation_start)
    decision_digest = _CallRecorder(external_publication_recovery_decision_digest)
    persist_recorder = _CallRecorder(
        persist_external_publication_recovery_resume_preparation
    )
    monkeypatch.setattr(
        prep_module,
        "persist_external_publication_recovery_resume_preparation",
        persist_recorder,
    )
    _install_persistence_fault(monkeypatch, "file_fsync")
    with pytest.raises(
        ExternalPublicationRecoveryResumePreparationPersistenceError
    ) as info:
        _prepare(
            decision_path,
            lifecycle_path,
            start_path,
            preparation_path,
            decision_loader=decision_loader,
            decision_digest_function=decision_digest,
            lifecycle_loader=lifecycle_loader,
            start_loader=start_loader,
        )
    _assert_persistence_error(info.value, "ambiguous")
    assert len(persist_recorder.calls) == 1
    assert persist_recorder.first_positional is preparation_path
    assert len(decision_loader.calls) == 1
    assert len(decision_digest.calls) == 1
    assert len(lifecycle_loader.calls) == 1
    assert len(start_loader.calls) == 1
    assert preparation_path.exists()


def test_ambiguous_exact_artifact_later_idempotent_and_partial_conflicts(
    tmp_path: Path,
) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path)
    preparation_path = tmp_path / "prep.json"
    with pytest.MonkeyPatch.context() as scope:
        _install_persistence_fault(scope, "dir_fsync")
        with pytest.raises(
            ExternalPublicationRecoveryResumePreparationPersistenceError
        ) as info:
            _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    _assert_persistence_error(info.value, "ambiguous")
    retained = preparation_path.read_bytes()

    # the retained exact bytes are accepted idempotently without a rewrite.
    second = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    assert (
        external_publication_recovery_resume_preparation_canonical_bytes(second)
        == retained
    )
    assert (
        load_external_publication_recovery_resume_preparation(preparation_path)
        == second
    )

    # a partial retained artifact fails closed without being repaired.
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_decision, other_lifecycle, other_start = _seed_lineage(other_root)
    partial_path = other_root / "prep.json"
    partial_path.write_bytes(retained[: len(retained) // 2])
    before = partial_path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(other_decision, other_lifecycle, other_start, partial_path)
    assert info.value.detail.classification in {"conflict", "load", "parse"}
    assert partial_path.read_bytes() == before


# --- integration regressions ---------------------------------------------


def test_integration_fresh_already_acquired_authorization(
    tmp_path: Path,
) -> None:
    predecessor_root = tmp_path / "predecessor"
    predecessor_root.mkdir()
    preparation_root = tmp_path / "preparation-root"
    preparation_root.mkdir()
    decision_path, lifecycle_path, start_path = _seed_lineage(predecessor_root)
    before = (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    )
    preparation_path = preparation_root / "preparation.json"

    first = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    assert first.recovery_kind == "already_acquired"
    assert first.source_operation == "fresh"
    assert first.target_operation == "resume"
    assert first.state == "prepared"
    assert first.recovery_decision_sha256 == (
        external_publication_recovery_decision_digest(
            load_external_publication_recovery_decision(decision_path)
        )
    )
    assert first.lifecycle_outcome_sha256 == (
        external_publication_operation_lifecycle_outcome_digest(
            load_external_publication_operation_lifecycle_outcome(lifecycle_path)
        )
    )
    assert first.operation_start_sha256 == (
        external_publication_operation_start_digest(
            load_external_publication_operation_start(start_path)
        )
    )

    # no Phase 289 intent and no new Phase 290 start marker were created.
    assert sorted(path.name for path in preparation_root.iterdir()) == [
        "preparation.json"
    ]

    second = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    assert second == first
    assert (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    ) == before
    assert sorted(path.name for path in preparation_root.iterdir()) == [
        "preparation.json"
    ]


def test_integration_stop_route_zero_preparation(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(tmp_path, chosen="stop")
    before = (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    )
    preparation_path = tmp_path / "preparation.json"
    with pytest.raises(ExternalPublicationRecoveryResumePreparationError) as info:
        _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    _assert_error(info.value, "decision_state")
    assert not preparation_path.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "decision.json",
        "intent.json",
        "lifecycle.json",
        "start.json",
    ]
    assert (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    ) == before


def test_integration_resume_reconciliation_mismatch(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(
        tmp_path,
        operation="resume",
        state="recovery_required",
        result_kind="reconciliation",
        result_sha256="d" * 64,
    )
    before = (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    )
    preparation_path = tmp_path / "preparation.json"
    preparation = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    assert preparation.recovery_kind == "reconciliation_mismatch"
    assert preparation.source_operation == "resume"
    assert preparation.target_operation == "resume"
    assert preparation.state == "prepared"
    assert (
        load_external_publication_recovery_resume_preparation(preparation_path)
        == preparation
    )
    assert (
        decision_path.read_bytes(),
        lifecycle_path.read_bytes(),
        start_path.read_bytes(),
    ) == before


def test_integration_resume_already_acquired(tmp_path: Path) -> None:
    decision_path, lifecycle_path, start_path = _seed_lineage(
        tmp_path, operation="resume"
    )
    preparation_path = tmp_path / "preparation.json"
    preparation = _prepare(decision_path, lifecycle_path, start_path, preparation_path)
    assert preparation.recovery_kind == "already_acquired"
    assert preparation.source_operation == "resume"
    assert preparation.target_operation == "resume"


# --- source audit ---------------------------------------------------------


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
        ".external_publication_operation_lifecycle_outcome",
        ".external_publication_operation_start",
        ".external_publication_recovery_decision",
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
        "unicodedata",
    }
    assert imports[".external_publication_recovery_decision"] == {
        "ExternalPublicationRecoveryDecision",
        "ExternalPublicationRecoveryDecisionError",
        "external_publication_recovery_decision_digest",
        "load_external_publication_recovery_decision",
    }
    assert imports[".external_publication_operation_lifecycle_outcome"] == {
        "ExternalPublicationOperationLifecycleOutcome",
        "ExternalPublicationOperationLifecycleOutcomeError",
        "external_publication_operation_lifecycle_outcome_digest",
        "load_external_publication_operation_lifecycle_outcome",
    }
    assert imports[".external_publication_operation_start"] == {
        "ExternalPublicationOperationStart",
        "ExternalPublicationOperationStartError",
        "external_publication_operation_start_digest",
        "load_external_publication_operation_start",
    }
    for forbidden in (
        "decide_and_persist_external_publication_recovery",
        "run_and_persist_external_publication_operation_lifecycle_outcome",
        "acquire_external_publication_operation_start",
        "build_external_publication_operation_intent",
        "persist_external_publication_operation_intent",
        "run_external_publication_operation_start_handoff",
        "execute_approved_external_publication",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
    ):
        assert not any(
            name == forbidden for names in imports.values() for name in names
        ), forbidden
    for module in imports:
        assert "orchestration" not in module
        assert "acquisition" not in module
        assert "operation_intent" not in module


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
        "decide_and_persist_external_publication_recovery",
        "run_and_persist_external_publication_operation_lifecycle_outcome",
        "acquire_external_publication_operation_start",
        "build_external_publication_operation_intent",
        "persist_external_publication_operation_intent",
        "run_external_publication_operation_start_handoff",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
    ):
        assert forbidden not in called, forbidden
    assert "load_external_publication_recovery_decision" not in called
    assert "load_external_publication_operation_lifecycle_outcome" not in called
    assert "load_external_publication_operation_start" not in called
    assert "loader" in called
    assert "digest_function" in called


def test_no_cli_change_and_no_phase294_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "resume_preparation" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "resume_preparation" not in workflows.output.lower()
    assert "phase294" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "resume_preparation" not in cli_source
    assert "phase294" not in cli_source.lower().replace(" ", "")
