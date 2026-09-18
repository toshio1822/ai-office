"""Focused provider-free tests for the Phase 293 recovery decision boundary."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import ai_office.engine.external_publication_recovery_decision as decision_module  # noqa: E501
from ai_office.engine import (
    ExternalPublicationOperationLifecycleOutcome,
    ExternalPublicationOperationLifecycleOutcomeLoadError,
    ExternalPublicationOperationStart,
    ExternalPublicationOperationStartLoadError,
    ExternalPublicationPlan,
    ExternalPublicationRecoveryDecision,
    ExternalPublicationRecoveryDecisionCompatibilityError,
    ExternalPublicationRecoveryDecisionConflictError,
    ExternalPublicationRecoveryDecisionError,
    ExternalPublicationRecoveryDecisionFailureDetail,
    ExternalPublicationRecoveryDecisionLoadError,
    ExternalPublicationRecoveryDecisionPersistenceError,
    acquire_external_publication_operation_start,
    approve_external_publication,
    build_external_publication_operation_intent,
    decide_and_persist_external_publication_recovery,
    external_publication_operation_lifecycle_outcome_digest,
    external_publication_operation_start_digest,
    external_publication_recovery_decision_canonical_bytes,
    external_publication_recovery_decision_digest,
    load_external_publication_operation_lifecycle_outcome,
    load_external_publication_operation_start,
    load_external_publication_recovery_decision,
    persist_external_publication_operation_intent,
    persist_external_publication_operation_lifecycle_outcome,
    persist_external_publication_recovery_decision,
    serialize_external_publication_recovery_decision_canonical,
)

_DECISION_SCHEMA = "external-publication-recovery-decision.v1"
_LIFECYCLE_SCHEMA = "external-publication-operation-lifecycle-outcome.v1"
_START_SCHEMA = "external-publication-operation-start.v1"
_MESSAGE = "external publication recovery decision is invalid"
_PERSIST_MESSAGE = "external publication recovery decision persistence failed"
_LOAD_MESSAGE = "external publication recovery decision could not be loaded"
_SOURCE = Path(decision_module.__file__).read_text(encoding="utf-8")
_KEYS = tuple(
    sorted(
        (
            "decision",
            "decided_by",
            "decision_id",
            "lifecycle_outcome_sha256",
            "operation_start_sha256",
            "publication_approval_sha256",
            "publication_plan_sha256",
            "recovery_kind",
            "schema_version",
            "source_operation",
            "state",
        )
    )
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
    assert type(error) is ExternalPublicationRecoveryDecisionCompatibilityError
    assert isinstance(error, ExternalPublicationRecoveryDecisionError)
    assert isinstance(error, ValueError)
    assert str(error) == _MESSAGE
    assert type(error.detail) is ExternalPublicationRecoveryDecisionFailureDetail
    assert error.detail.classification == classification
    assert error.__cause__ is None


def _plan() -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id="regen-293",
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
        approved_by="human-reviewer-293",
        approval_id="approval-293",
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


def _matched_pair(
    *,
    operation: str = "fresh",
    state: str = "recovery_required",
    result_kind: str = "none",
    result_sha256: object = None,
) -> tuple[
    ExternalPublicationOperationStart, ExternalPublicationOperationLifecycleOutcome
]:
    start = _start(operation)
    digest = external_publication_operation_start_digest(start)
    lifecycle = _lifecycle(
        start_digest=digest,
        operation=operation,
        state=state,
        result_kind=result_kind,
        result_sha256=result_sha256,
    )
    return start, lifecycle


def _decision(
    *,
    schema_version: object = _DECISION_SCHEMA,
    lifecycle_digest: str = "9" * 64,
    start_digest: str = "8" * 64,
    approval_digest: str = "b" * 64,
    plan_digest: str = "c" * 64,
    source_operation: str = "fresh",
    recovery_kind: str = "already_acquired",
    chosen: str = "stop",
    decided_by: str = "operator-293",
    decision_id: str = "decision-293",
    state: str = "decided",
) -> ExternalPublicationRecoveryDecision:
    return ExternalPublicationRecoveryDecision(
        schema_version=schema_version,  # type: ignore[arg-type]
        lifecycle_outcome_sha256=lifecycle_digest,
        operation_start_sha256=start_digest,
        publication_approval_sha256=approval_digest,
        publication_plan_sha256=plan_digest,
        source_operation=source_operation,  # type: ignore[arg-type]
        recovery_kind=recovery_kind,  # type: ignore[arg-type]
        decision=chosen,  # type: ignore[arg-type]
        decided_by=decided_by,
        decision_id=decision_id,
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


class _ValueCallRecorder:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls: list[object] = []

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append(args[0] if args else None)
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


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
        scope.setattr(decision_module.os, "fsync", _fsync_boom)
        return
    if stage == "dir_fsync":
        scope.setattr(decision_module, "_fsync_decision_directory", _fsync_boom)
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


def _seed_predecessors(
    root: Path,
    *,
    operation: str = "fresh",
    state: str = "recovery_required",
    result_kind: str = "none",
    result_sha256: object = None,
) -> tuple[Path, Path]:
    """Create a real exact Phase 292 lifecycle outcome plus matching start."""
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
    return lifecycle_path, start_path


# --- public surface -------------------------------------------------------


def test_public_exports_and_error_family() -> None:
    assert (
        ExternalPublicationRecoveryDecisionError.__name__
        == "ExternalPublicationRecoveryDecisionError"
    )
    assert issubclass(ExternalPublicationRecoveryDecisionError, ValueError)
    assert issubclass(
        ExternalPublicationRecoveryDecisionCompatibilityError,
        ExternalPublicationRecoveryDecisionError,
    )
    assert issubclass(
        ExternalPublicationRecoveryDecisionPersistenceError,
        ExternalPublicationRecoveryDecisionError,
    )
    assert issubclass(
        ExternalPublicationRecoveryDecisionConflictError,
        ExternalPublicationRecoveryDecisionPersistenceError,
    )
    assert issubclass(
        ExternalPublicationRecoveryDecisionLoadError,
        ExternalPublicationRecoveryDecisionError,
    )
    for name in (
        "serialize_external_publication_recovery_decision_canonical",
        "external_publication_recovery_decision_canonical_bytes",
        "external_publication_recovery_decision_digest",
        "load_external_publication_recovery_decision",
        "persist_external_publication_recovery_decision",
        "decide_and_persist_external_publication_recovery",
    ):
        assert name in decision_module.__all__, name
        assert callable(getattr(decision_module, name))


def test_signature_defaults_and_no_recovery_kind_argument() -> None:
    signature = inspect.signature(decide_and_persist_external_publication_recovery)
    parameters = signature.parameters
    for forbidden in ("recovery_kind", "lifecycle", "start", "decision_object"):
        assert forbidden not in parameters, forbidden
    expected = {
        "lifecycle_outcome_path": inspect.Parameter.empty,
        "start_path": inspect.Parameter.empty,
        "recovery_decision_path": inspect.Parameter.empty,
        "decision": inspect.Parameter.empty,
        "decided_by": inspect.Parameter.empty,
        "decision_id": inspect.Parameter.empty,
        "lifecycle_loader": load_external_publication_operation_lifecycle_outcome,
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
        field.name for field in dataclasses.fields(ExternalPublicationRecoveryDecision)
    ] == [
        "schema_version",
        "lifecycle_outcome_sha256",
        "operation_start_sha256",
        "publication_approval_sha256",
        "publication_plan_sha256",
        "source_operation",
        "recovery_kind",
        "decision",
        "decided_by",
        "decision_id",
        "state",
    ]
    assert type(_decision()).__dataclass_params__.frozen is True
    with pytest.raises(Exception):
        _decision().decision = "authorize_resume_preparation"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"chosen": "authorize_resume_preparation"},
        {"source_operation": "resume"},
        {
            "source_operation": "resume",
            "recovery_kind": "reconciliation_mismatch",
        },
    ],
)
def test_model_accepts_exact_valid_combinations(overrides: dict[str, object]) -> None:
    decision = _decision(**overrides)  # type: ignore[arg-type]
    assert decision.state == "decided"
    assert decision.schema_version == _DECISION_SCHEMA


def test_model_rejects_non_exact_runtime_types() -> None:
    for overrides in (
        {"source_operation": _StringChild("fresh")},
        {"recovery_kind": _StringChild("already_acquired")},
        {"chosen": _StringChild("stop")},
        {"state": _StringChild("decided")},
        {"schema_version": _StringChild(_DECISION_SCHEMA)},
        {"decided_by": _StringChild("operator-293")},
        {"decision_id": _StringChild("decision-293")},
        {"lifecycle_digest": "A" * 64},
        {"lifecycle_digest": "a" * 63},
        {"start_digest": None},
        {"approval_digest": "b" * 63},
        {"plan_digest": "c" * 65},
    ):
        with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
            _decision(**overrides)  # type: ignore[arg-type]

    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
        _decision(chosen="execute_resume")
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
        _decision(recovery_kind="provider_error")
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
        _decision(source_operation="fresh", recovery_kind="reconciliation_mismatch")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        " padded",
        "padded ",
        "x" * 257,
        "bad\u0000value",
        "bad\ud800value",
    ],
)
def test_model_rejects_invalid_operator_metadata(value: str) -> None:
    for overrides in ({"decided_by": value}, {"decision_id": value}):
        with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
            _decision(**overrides)  # type: ignore[arg-type]


def test_model_accepts_max_length_metadata() -> None:
    decision = _decision(decided_by="o" * 256, decision_id="d" * 256)
    assert len(decision.decided_by) == 256
    assert len(decision.decision_id) == 256


def test_forged_model_is_rejected_by_helpers() -> None:
    real = _decision()
    forged = _forged_instance(
        ExternalPublicationRecoveryDecision, real, decision="execute_resume"
    )
    for helper in (
        serialize_external_publication_recovery_decision_canonical,
        external_publication_recovery_decision_canonical_bytes,
        external_publication_recovery_decision_digest,
    ):
        with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
            helper(forged)  # type: ignore[arg-type]


# --- canonical serialization ---------------------------------------------


def test_canonical_json_exact_keys_and_deterministic_digest() -> None:
    decision = _decision()
    payload = serialize_external_publication_recovery_decision_canonical(decision)
    import json

    parsed = json.loads(payload)
    assert tuple(sorted(parsed)) == _KEYS
    assert payload == payload.strip()
    assert " " not in payload
    assert external_publication_recovery_decision_digest(
        decision
    ) == external_publication_recovery_decision_digest(_decision())
    assert external_publication_recovery_decision_canonical_bytes(
        decision
    ) == payload.encode("utf-8")


def test_loader_round_trips_exact_bytes(tmp_path: Path) -> None:
    decision = _decision()
    path = tmp_path / "decision.json"
    persist_external_publication_recovery_decision(path, decision)
    assert path.read_bytes() == external_publication_recovery_decision_canonical_bytes(
        decision
    )
    assert load_external_publication_recovery_decision(path) == decision


def test_loader_rejects_noncanonical_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "decision.json"
    canonical = external_publication_recovery_decision_canonical_bytes(_decision())
    path.write_bytes(canonical.replace(b"{", b"{ ", 1))
    with pytest.raises(ExternalPublicationRecoveryDecisionLoadError) as info:
        load_external_publication_recovery_decision(path)
    assert type(info.value) is ExternalPublicationRecoveryDecisionLoadError
    assert str(info.value) == _LOAD_MESSAGE


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"null",
        b"[]",
        b'{"decided_by":NaN}',
        b'{"decision":"stop","decision":"stop"}',
        b"\xff\xfe",
    ],
)
def test_loader_rejects_malformed_payloads(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "decision.json"
    path.write_bytes(payload)
    with pytest.raises(ExternalPublicationRecoveryDecisionLoadError):
        load_external_publication_recovery_decision(path)


def test_loader_rejects_extra_and_missing_keys(tmp_path: Path) -> None:
    import json

    canonical = json.loads(
        external_publication_recovery_decision_canonical_bytes(_decision())
    )
    extra = dict(canonical, extra_key="x")
    missing = {key: value for key, value in canonical.items() if key != "state"}
    for payload in (
        json.dumps(extra, sort_keys=True, separators=(",", ":")),
        json.dumps(missing, sort_keys=True, separators=(",", ":")),
    ):
        path = tmp_path / "decision.json"
        path.write_bytes(payload.encode("utf-8"))
        with pytest.raises(ExternalPublicationRecoveryDecisionLoadError) as info:
            load_external_publication_recovery_decision(path)
        assert info.value.detail.classification == "keys"
        path.unlink()


def test_loader_rejects_non_regular_target(tmp_path: Path) -> None:
    directory = tmp_path / "dir.json"
    directory.mkdir()
    with pytest.raises(ExternalPublicationRecoveryDecisionLoadError):
        load_external_publication_recovery_decision(directory)
    with pytest.raises(ExternalPublicationRecoveryDecisionLoadError):
        load_external_publication_recovery_decision(tmp_path / "absent.json")


# --- persistence ----------------------------------------------------------


def test_persistence_idempotent_for_identical_bytes(tmp_path: Path) -> None:
    path = tmp_path / "decision.json"
    decision = _decision()
    persist_external_publication_recovery_decision(path, decision)
    before = path.read_bytes()
    persist_external_publication_recovery_decision(path, decision)
    assert path.read_bytes() == before


def test_persistence_conflict_for_different_bytes(tmp_path: Path) -> None:
    path = tmp_path / "decision.json"
    persist_external_publication_recovery_decision(path, _decision())
    before = path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryDecisionConflictError) as info:
        persist_external_publication_recovery_decision(
            path, _decision(chosen="authorize_resume_preparation")
        )
    assert type(info.value) is ExternalPublicationRecoveryDecisionConflictError
    assert isinstance(info.value, ExternalPublicationRecoveryDecisionPersistenceError)
    assert str(info.value) == _PERSIST_MESSAGE
    assert path.read_bytes() == before


def test_persistence_conflict_for_partial_bytes(tmp_path: Path) -> None:
    path = tmp_path / "decision.json"
    canonical = external_publication_recovery_decision_canonical_bytes(_decision())
    path.write_bytes(canonical[:-5])
    with pytest.raises(ExternalPublicationRecoveryDecisionConflictError):
        persist_external_publication_recovery_decision(path, _decision())


def test_persistence_rejects_bad_parent_and_target(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError) as info:
        persist_external_publication_recovery_decision(
            tmp_path / "absent" / "decision.json", _decision()
        )
    assert info.value.detail.classification == "parent"

    directory = tmp_path / "dir.json"
    directory.mkdir()
    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError) as info:
        persist_external_publication_recovery_decision(directory, _decision())
    assert info.value.detail.classification == "target"


def test_persistence_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    persist_external_publication_recovery_decision(real, _decision())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError):
        persist_external_publication_recovery_decision(link, _decision())


_AMBIGUOUS_STAGES = (
    "write_error",
    "short_write",
    "flush_error",
    "file_fsync",
    "close_failure",
    "dir_fsync",
)


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_persistence_ambiguity_per_stage_retains_artifact_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / "decision.json"
    decision = _decision()
    _install_persistence_fault(monkeypatch, stage)
    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError) as info:
        persist_external_publication_recovery_decision(path, decision)
    assert type(info.value) is ExternalPublicationRecoveryDecisionPersistenceError
    assert info.value.detail.classification == "ambiguous"
    monkeypatch.undo()
    assert path.exists(), stage


@pytest.mark.parametrize("stage", _AMBIGUOUS_STAGES)
def test_persistence_ambiguity_then_exact_retained_bytes_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / "decision.json"
    decision = _decision()
    _install_persistence_fault(monkeypatch, stage)
    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError):
        persist_external_publication_recovery_decision(path, decision)
    monkeypatch.undo()
    if path.exists() and path.read_bytes() == (
        external_publication_recovery_decision_canonical_bytes(decision)
    ):
        persist_external_publication_recovery_decision(path, decision)
        assert path.read_bytes() == (
            external_publication_recovery_decision_canonical_bytes(decision)
        )
    else:
        with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError):
            persist_external_publication_recovery_decision(path, decision)


# --- orchestration preflight ---------------------------------------------


def test_preflight_rejects_bad_paths_and_dependencies(tmp_path: Path) -> None:
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path="not-a-path",  # type: ignore[arg-type]
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
        )
    assert info.value.detail.classification == "path_type"

    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader="not-callable",  # type: ignore[arg-type]
        )
    assert info.value.detail.classification == "configuration"

    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "absent" / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
        )
    assert info.value.detail.classification == "parent"


@pytest.mark.parametrize(
    "value",
    ["", " stop", "stop ", "resume", "execute_resume", 1, None, _StringChild("stop")],
)
def test_preflight_rejects_bad_decision(tmp_path: Path, value: object) -> None:
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision=value,  # type: ignore[arg-type]
            decided_by="operator-293",
            decision_id="decision-293",
        )
    assert info.value.detail.classification == "decision"
    assert not (tmp_path / "decision.json").exists()


def test_preflight_rejects_bad_operator_metadata(tmp_path: Path) -> None:
    for overrides in (
        {"decided_by": ""},
        {"decided_by": " padded"},
        {"decision_id": "x" * 257},
        {"decision_id": "bad\u0000"},
    ):
        kwargs = {"decided_by": "operator-293", "decision_id": "decision-293"}
        kwargs.update(overrides)
        with pytest.raises(
            ExternalPublicationRecoveryDecisionCompatibilityError
        ) as info:
            decide_and_persist_external_publication_recovery(
                lifecycle_outcome_path=tmp_path / "lifecycle.json",
                start_path=tmp_path / "start.json",
                recovery_decision_path=tmp_path / "decision.json",
                decision="stop",
                **kwargs,  # type: ignore[arg-type]
            )
        assert info.value.detail.classification == "operator_metadata"
    assert not (tmp_path / "decision.json").exists()


# --- lifecycle consumption ------------------------------------------------


def test_lifecycle_loader_exactly_once_with_exact_path_identity(
    tmp_path: Path,
) -> None:
    start, lifecycle = _matched_pair()
    lifecycle_path = tmp_path / "lifecycle.json"
    start_path = tmp_path / "start.json"
    persist_external_publication_operation_lifecycle_outcome(lifecycle_path, lifecycle)

    loader = _LoaderRecorder(lambda path: lifecycle)
    start_loader = _LoaderRecorder(lambda path: start)
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=tmp_path / "decision.json",
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=loader,
        start_loader=start_loader,
    )
    assert len(loader.calls) == 1
    assert loader.calls[0] is lifecycle_path
    assert loader.results[0] is lifecycle
    assert len(start_loader.calls) == 1
    assert start_loader.calls[0] is start_path
    assert start_loader.results[0] is start
    assert decision.recovery_kind == "already_acquired"


def test_lifecycle_subclass_or_lookalike_rejected(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    forged = _forged_instance(
        ExternalPublicationOperationLifecycleOutcome, lifecycle, state="completed"
    )
    loader = _LoaderRecorder(lambda path: forged)
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=loader,
        )
    assert info.value.detail.classification == "lifecycle_contract"
    assert not (tmp_path / "decision.json").exists()

    class _Lookalike(ExternalPublicationOperationLifecycleOutcome):
        pass

    lookalike = object.__new__(_Lookalike)
    for field in dataclasses.fields(lifecycle):
        object.__setattr__(lookalike, field.name, getattr(lifecycle, field.name))
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lookalike),
        )
    assert info.value.detail.classification == "lifecycle_contract"


def test_lifecycle_digest_exactly_once_with_exact_object_identity(
    tmp_path: Path,
) -> None:
    start, lifecycle = _matched_pair()
    digest_recorder = _ValueCallRecorder(
        external_publication_operation_lifecycle_outcome_digest(lifecycle)
    )
    start_digest_recorder = _ValueCallRecorder(
        external_publication_operation_start_digest(start)
    )
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=tmp_path / "lifecycle.json",
        start_path=tmp_path / "start.json",
        recovery_decision_path=tmp_path / "decision.json",
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
        lifecycle_digest_function=digest_recorder,
        start_loader=_LoaderRecorder(lambda path: start),
        start_digest_function=start_digest_recorder,
    )
    assert len(digest_recorder.calls) == 1
    assert digest_recorder.calls[0] is lifecycle
    assert len(start_digest_recorder.calls) == 1
    assert start_digest_recorder.calls[0] is start
    assert decision.lifecycle_outcome_sha256 == (
        external_publication_operation_lifecycle_outcome_digest(lifecycle)
    )


def test_completed_lifecycle_rejected_and_no_decision_file(tmp_path: Path) -> None:
    for operation, result_kind in (
        ("fresh", "execution_result"),
        ("resume", "reconciliation"),
    ):
        start, lifecycle = _matched_pair(
            operation=operation,
            state="completed",
            result_kind=result_kind,
            result_sha256="d" * 64,
        )
        decision_path = tmp_path / f"decision-{operation}.json"
        with pytest.raises(
            ExternalPublicationRecoveryDecisionCompatibilityError
        ) as info:
            decide_and_persist_external_publication_recovery(
                lifecycle_outcome_path=tmp_path / "lifecycle.json",
                start_path=tmp_path / "start.json",
                recovery_decision_path=decision_path,
                decision="stop",
                decided_by="operator-293",
                decision_id="decision-293",
                lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
                start_loader=_LoaderRecorder(lambda path: start),
            )
        assert info.value.detail.classification == "lifecycle_state"
        assert not decision_path.exists()


def test_fresh_recovery_required_none_derives_already_acquired(
    tmp_path: Path,
) -> None:
    start, lifecycle = _matched_pair()
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=tmp_path / "lifecycle.json",
        start_path=tmp_path / "start.json",
        recovery_decision_path=tmp_path / "decision.json",
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
        start_loader=_LoaderRecorder(lambda path: start),
    )
    assert decision.recovery_kind == "already_acquired"
    assert decision.source_operation == "fresh"


def test_resume_recovery_required_none_derives_already_acquired(
    tmp_path: Path,
) -> None:
    start, lifecycle = _matched_pair(operation="resume")
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=tmp_path / "lifecycle.json",
        start_path=tmp_path / "start.json",
        recovery_decision_path=tmp_path / "decision.json",
        decision="authorize_resume_preparation",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
        start_loader=_LoaderRecorder(lambda path: start),
    )
    assert decision.recovery_kind == "already_acquired"
    assert decision.source_operation == "resume"


def test_resume_recovery_required_reconciliation_derives_mismatch(
    tmp_path: Path,
) -> None:
    start, lifecycle = _matched_pair(
        operation="resume",
        state="recovery_required",
        result_kind="reconciliation",
        result_sha256="d" * 64,
    )
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=tmp_path / "lifecycle.json",
        start_path=tmp_path / "start.json",
        recovery_decision_path=tmp_path / "decision.json",
        decision="authorize_resume_preparation",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
        start_loader=_LoaderRecorder(lambda path: start),
    )
    assert decision.recovery_kind == "reconciliation_mismatch"
    assert decision.source_operation == "resume"


def test_malformed_lifecycle_rejected(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    for overrides in (
        {"result_kind": "none", "result_sha256": "d" * 64},
        {"result_kind": "execution_result", "result_sha256": None},
        {"operation": "execution_result"},
        {"operation_start_sha256": "Z" * 64},
        {"publication_approval_sha256": None},
    ):
        forged = _forged_instance(
            ExternalPublicationOperationLifecycleOutcome, lifecycle, **overrides
        )
        with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError):
            decide_and_persist_external_publication_recovery(
                lifecycle_outcome_path=tmp_path / "lifecycle.json",
                start_path=tmp_path / "start.json",
                recovery_decision_path=tmp_path / "decision.json",
                decision="stop",
                decided_by="operator-293",
                decision_id="decision-293",
                lifecycle_loader=_LoaderRecorder(lambda path: forged),
                start_loader=_LoaderRecorder(lambda path: start),
            )


def test_lifecycle_known_error_same_object_identity(tmp_path: Path) -> None:
    error = ExternalPublicationOperationLifecycleOutcomeLoadError("target")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_ValueCallRecorder(error),
        )
    assert info.value is error


def test_lifecycle_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    def _boom(path: object) -> object:
        raise RuntimeError("secret detail")

    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_boom,
        )
    assert info.value.detail.classification == "dependency_error"
    assert "secret" not in str(info.value)


def test_lifecycle_malformed_digest_return_rejected(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            lifecycle_digest_function=_ValueCallRecorder("not-hex"),
            start_loader=_LoaderRecorder(lambda path: start),
        )
    assert info.value.detail.classification == "lifecycle_digest"


# --- start lineage --------------------------------------------------------


def test_start_loader_exactly_once_exact_path_identity(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    start_path = tmp_path / "start.json"
    start_loader = _LoaderRecorder(lambda path: start)
    decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=tmp_path / "lifecycle.json",
        start_path=start_path,
        recovery_decision_path=tmp_path / "decision.json",
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
        start_loader=start_loader,
    )
    assert len(start_loader.calls) == 1
    assert start_loader.calls[0] is start_path


def test_start_contract_rejects_lookalike(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    forged = _forged_instance(ExternalPublicationOperationStart, start, state="closed")
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_LoaderRecorder(lambda path: forged),
        )
    assert info.value.detail.classification == "start_contract"
    assert not (tmp_path / "decision.json").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"overrides": {"operation": "resume"}},
        {"overrides": {"publication_approval_sha256": "7" * 64}},
        {"overrides": {"publication_plan_sha256": "6" * 64}},
    ],
)
def test_start_lineage_mismatch_rejected_before_write(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    start, lifecycle = _matched_pair()
    mutated = _forged_instance(
        ExternalPublicationOperationStart,
        start,
        **overrides["overrides"],  # type: ignore[arg-type]
    )
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_LoaderRecorder(lambda path: mutated),
        )
    assert info.value.detail.classification == "start_lineage"
    assert not (tmp_path / "decision.json").exists()


def test_start_digest_mismatch_rejected_before_write(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_LoaderRecorder(lambda path: start),
            start_digest_function=_ValueCallRecorder("5" * 64),
        )
    assert info.value.detail.classification == "start_lineage"
    assert not (tmp_path / "decision.json").exists()


def test_start_digest_malformed_helper_return_rejected(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_LoaderRecorder(lambda path: start),
            start_digest_function=_ValueCallRecorder("not-hex"),
        )
    assert info.value.detail.classification == "start_lineage"


def test_start_known_error_same_object_identity(tmp_path: Path) -> None:
    _, lifecycle = _matched_pair()
    error = ExternalPublicationOperationStartLoadError("target")
    with pytest.raises(ExternalPublicationOperationStartLoadError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_ValueCallRecorder(error),
        )
    assert info.value is error


def test_start_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    _, lifecycle = _matched_pair()

    def _boom(path: object) -> object:
        raise RuntimeError("secret start detail")

    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_boom,
        )
    assert info.value.detail.classification == "dependency_error"
    assert "secret" not in str(info.value)


def test_start_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    error = ExternalPublicationOperationStartLoadError("target")
    with pytest.raises(ExternalPublicationOperationStartLoadError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_LoaderRecorder(lambda path: start),
            start_digest_function=_ValueCallRecorder(error),
        )
    assert info.value is error


def test_lifecycle_digest_known_error_same_object_identity(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()
    error = ExternalPublicationOperationLifecycleOutcomeLoadError("target")
    with pytest.raises(ExternalPublicationOperationLifecycleOutcomeLoadError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            lifecycle_digest_function=_ValueCallRecorder(error),
            start_loader=_LoaderRecorder(lambda path: start),
        )
    assert info.value is error


def test_lifecycle_digest_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()

    def _boom(value: object) -> object:
        raise RuntimeError("secret lifecycle digest detail")

    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            lifecycle_digest_function=_boom,
            start_loader=_LoaderRecorder(lambda path: start),
        )
    assert info.value.detail.classification == "dependency_error"
    assert "secret" not in str(info.value)
    assert info.value.__cause__ is None
    assert not (tmp_path / "decision.json").exists()


def test_start_digest_unexpected_error_is_detail_safe(tmp_path: Path) -> None:
    start, lifecycle = _matched_pair()

    def _boom(value: object) -> object:
        raise RuntimeError("secret start digest detail")

    with pytest.raises(ExternalPublicationRecoveryDecisionCompatibilityError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=tmp_path / "lifecycle.json",
            start_path=tmp_path / "start.json",
            recovery_decision_path=tmp_path / "decision.json",
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=_LoaderRecorder(lambda path: lifecycle),
            start_loader=_LoaderRecorder(lambda path: start),
            start_digest_function=_boom,
        )
    assert info.value.detail.classification == "dependency_error"
    assert "secret" not in str(info.value)
    assert info.value.__cause__ is None
    assert not (tmp_path / "decision.json").exists()


# --- decision recording ---------------------------------------------------


@pytest.mark.parametrize("chosen", ["stop", "authorize_resume_preparation"])
def test_decision_persists_exact_decision(tmp_path: Path, chosen: str) -> None:
    lifecycle_path, start_path = _seed_predecessors(tmp_path)
    decision_path = tmp_path / "decision.json"
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision=chosen,  # type: ignore[arg-type]
        decided_by="operator-293",
        decision_id="decision-293",
    )
    assert decision.decision == chosen
    assert decision.state == "decided"
    assert decision.decided_by == "operator-293"
    assert decision.decision_id == "decision-293"
    assert load_external_publication_recovery_decision(decision_path) == decision
    assert decision_path.read_bytes() == (
        external_publication_recovery_decision_canonical_bytes(decision)
    )


def test_existing_decision_loader_exactly_once_exact_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle_path, start_path = _seed_predecessors(tmp_path)
    decision_path = tmp_path / "decision.json"
    first = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
    )
    before = decision_path.read_bytes()

    decision_loader = _CallRecorder(load_external_publication_recovery_decision)
    monkeypatch.setattr(
        decision_module,
        "load_external_publication_recovery_decision",
        decision_loader,
    )
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    start_loader = _LoaderRecorder(load_external_publication_operation_start)

    second = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
        lifecycle_loader=lifecycle_loader,
        start_loader=start_loader,
    )

    # existing decision loader: exactly once, exact caller path identity, and
    # the exact loader-returned object is what orchestration returns.
    assert len(decision_loader.calls) == 1
    assert decision_loader.first_positional is decision_path
    assert len(decision_loader.results) == 1
    assert second is decision_loader.results[0]
    assert second == first

    # predecessor lifecycle/start validation still runs normally, once each.
    assert len(lifecycle_loader.calls) == 1
    assert lifecycle_loader.calls[0] is lifecycle_path
    assert len(start_loader.calls) == 1
    assert start_loader.calls[0] is start_path

    assert decision_path.read_bytes() == before


@pytest.mark.parametrize(
    "existing_overrides",
    [
        {"lifecycle_digest": "1" * 64},
        {"start_digest": "2" * 64},
        {"approval_digest": "3" * 64},
        {"plan_digest": "4" * 64},
        {"source_operation": "resume"},
        {"source_operation": "resume", "recovery_kind": "reconciliation_mismatch"},
    ],
)
def test_existing_decision_lineage_mismatch_conflicts_unchanged(
    tmp_path: Path, existing_overrides: dict[str, object]
) -> None:
    lifecycle_path, start_path = _seed_predecessors(tmp_path)
    decision_path = tmp_path / "decision.json"
    stale = _decision(**existing_overrides)  # type: ignore[arg-type]
    persist_external_publication_recovery_decision(decision_path, stale)
    before = decision_path.read_bytes()
    assert before == external_publication_recovery_decision_canonical_bytes(stale)

    with pytest.raises(ExternalPublicationRecoveryDecisionConflictError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=lifecycle_path,
            start_path=start_path,
            recovery_decision_path=decision_path,
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
        )
    assert type(info.value) is ExternalPublicationRecoveryDecisionConflictError
    assert isinstance(info.value, ExternalPublicationRecoveryDecisionPersistenceError)
    assert str(info.value) == _PERSIST_MESSAGE
    # no overwrite, repair, or replacement of the existing decision bytes.
    assert decision_path.read_bytes() == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"chosen": "authorize_resume_preparation"},
        {"decided_by": "operator-other"},
        {"decision_id": "decision-other"},
    ],
)
def test_existing_decision_requested_field_mismatch_conflicts(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    lifecycle_path, start_path = _seed_predecessors(tmp_path)
    decision_path = tmp_path / "decision.json"
    decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
    )
    before = decision_path.read_bytes()
    with pytest.raises(ExternalPublicationRecoveryDecisionConflictError):
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=lifecycle_path,
            start_path=start_path,
            recovery_decision_path=decision_path,
            decision=overrides.get("chosen", "stop"),  # type: ignore[arg-type]
            decided_by=overrides.get("decided_by", "operator-293"),  # type: ignore[arg-type]
            decision_id=overrides.get("decision_id", "decision-293"),  # type: ignore[arg-type]
        )
    assert decision_path.read_bytes() == before


def test_persistence_failure_never_retries_predecessor_or_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle_path, start_path = _seed_predecessors(tmp_path)
    decision_path = tmp_path / "decision.json"
    lifecycle_loader = _LoaderRecorder(
        load_external_publication_operation_lifecycle_outcome
    )
    start_loader = _LoaderRecorder(load_external_publication_operation_start)
    persist_recorder = _CallRecorder(persist_external_publication_recovery_decision)
    monkeypatch.setattr(
        decision_module,
        "persist_external_publication_recovery_decision",
        persist_recorder,
    )
    _install_persistence_fault(monkeypatch, "file_fsync")
    with pytest.raises(ExternalPublicationRecoveryDecisionPersistenceError) as info:
        decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=lifecycle_path,
            start_path=start_path,
            recovery_decision_path=decision_path,
            decision="stop",
            decided_by="operator-293",
            decision_id="decision-293",
            lifecycle_loader=lifecycle_loader,
            start_loader=start_loader,
        )
    assert info.value.detail.classification == "ambiguous"
    # persistence helper is called exactly once with the exact caller path and
    # never retried after the failure.
    assert len(persist_recorder.calls) == 1
    assert persist_recorder.first_positional is decision_path
    # predecessor loading is not retried either.
    assert len(lifecycle_loader.calls) == 1
    assert len(start_loader.calls) == 1
    # artifact retained; no cleanup, rewrite, or repair.
    assert decision_path.exists()


# --- integration regressions ---------------------------------------------


def test_integration_fresh_already_acquired_stop_idempotent(
    tmp_path: Path,
) -> None:
    lifecycle_path, start_path = _seed_predecessors(tmp_path)
    lifecycle_before = lifecycle_path.read_bytes()
    start_before = start_path.read_bytes()
    decision_path = tmp_path / "decision.json"

    first = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
    )
    assert first.recovery_kind == "already_acquired"
    assert first.source_operation == "fresh"
    assert first.decision == "stop"

    second = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="stop",
        decided_by="operator-293",
        decision_id="decision-293",
    )
    assert second == first
    assert lifecycle_path.read_bytes() == lifecycle_before
    assert start_path.read_bytes() == start_before


def test_integration_authorize_creates_no_intent_or_start(tmp_path: Path) -> None:
    predecessor_root = tmp_path / "predecessor"
    decision_root = tmp_path / "decision-root"
    predecessor_root.mkdir()
    decision_root.mkdir()
    lifecycle_path, start_path = _seed_predecessors(predecessor_root)
    start_before = start_path.read_bytes()

    decision_path = decision_root / "decision.json"
    decision = decide_and_persist_external_publication_recovery(
        lifecycle_outcome_path=lifecycle_path,
        start_path=start_path,
        recovery_decision_path=decision_path,
        decision="authorize_resume_preparation",
        decided_by="operator-293",
        decision_id="decision-293",
    )
    assert decision.decision == "authorize_resume_preparation"
    assert decision.recovery_kind == "already_acquired"
    assert sorted(path.name for path in decision_root.iterdir()) == ["decision.json"]
    assert start_path.read_bytes() == start_before


def test_integration_resume_reconciliation_mismatch(tmp_path: Path) -> None:
    for chosen, root_name in (
        ("stop", "stop-root"),
        ("authorize_resume_preparation", "authorize-root"),
    ):
        root = tmp_path / root_name
        root.mkdir()
        lifecycle_path, start_path = _seed_predecessors(
            root,
            operation="resume",
            state="recovery_required",
            result_kind="reconciliation",
            result_sha256="d" * 64,
        )
        lifecycle_before = lifecycle_path.read_bytes()
        decision = decide_and_persist_external_publication_recovery(
            lifecycle_outcome_path=lifecycle_path,
            start_path=start_path,
            recovery_decision_path=root / "decision.json",
            decision=chosen,  # type: ignore[arg-type]
            decided_by="operator-293",
            decision_id=f"decision-{chosen}",
        )
        assert decision.recovery_kind == "reconciliation_mismatch"
        assert decision.source_operation == "resume"
        assert decision.decision == chosen
        assert lifecycle_path.read_bytes() == lifecycle_before


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
        "execute_approved_external_publication",
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "reconcile_and_persist_external_publication_execution",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "run_and_persist_external_publication_operation_lifecycle_outcome",
        "run_external_publication_operation_start_handoff",
        "build_external_publication_operation_intent",
        "persist_external_publication_operation_intent",
    ):
        assert not any(
            name == forbidden for names in imports.values() for name in names
        ), forbidden
    for module in imports:
        assert "orchestration" not in module
        assert "reconciliation_resume" not in module
        assert "attempt_claim" not in module


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
        "execute_and_persist_approved_external_publication",
        "resume_external_publication_reconciliation_closure",
        "acquire_external_publication_operation_start",
        "run_external_publication_operation",
        "reconcile_and_persist_external_publication_execution",
        "run_and_persist_external_publication_operation_lifecycle_outcome",
        "run_external_publication_operation_start_handoff",
        "build_external_publication_operation_intent",
        "persist_external_publication_operation_intent",
    ):
        assert forbidden not in called, forbidden
    assert "load_external_publication_operation_lifecycle_outcome" not in called
    assert "load_external_publication_operation_start" not in called
    assert "loader" in called
    assert "digest_function" in called


def test_no_cli_change_and_no_phase293_command() -> None:
    from typer.testing import CliRunner

    from ai_office.cli import app

    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "recovery_decision" not in root.output.lower()

    workflows = runner.invoke(app, ["workflows", "--help"])
    assert workflows.exit_code == 0
    assert "recovery" not in workflows.output.lower()
    assert "phase293" not in workflows.output.lower().replace(" ", "")

    cli_source = Path("src/ai_office/cli.py").read_text(encoding="utf-8")
    assert "recovery_decision" not in cli_source
