"""Focused Phase 123 progression-to-approved-preparation handoff chain reentry continuation tests."""

# ruff: noqa: E501, E702, F401

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError,
    WorkflowProgressionDecision,
    route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffReentryContinuationError,
    route_progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def wf() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
        {"id": "third", "name": "Third", "employee": "three", "instructions": "b"},
    ]})


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "workflow", "second", 2, "two", "third", 3, "three", "next_step_available")


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "second", 2, "third", 3, "three")


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "three", "name": "Three", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "third", 3, "three", "employee", "b", "model", ("tool",))


def targets(tmp_path: Path, status: str = "succeeded", index: int = 2) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = wf(); step = definition.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee,
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error")
    event = RuntimeStepEvent("step_succeeded", "workflow", step.id, index, step.employee, "running", "succeeded", "openai", None, "response", "request", "output", None)
    events = [event]
    if status == "succeeded" and index >= 2:
        if index == 2:
            events.insert(0, RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
        else:
            events.insert(0, RuntimeStepEvent("step_succeeded", "workflow", "second", 2, "two", "running", "succeeded", "openai", None, "response", "request", "output", None))
            events.insert(0, RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
    if status == "failed":
        events = [RuntimeStepEvent("step_failed", "workflow", step.id, index, step.employee, "running", "failed", "openai", "api_error", None, "request", None, "safe")]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events))
    return state_path, events_path


def call(*args: object, phase116_function=None) -> object:
    kwargs = {} if phase116_function is None else {"phase116_function": phase116_function}
    return route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary(*args, **kwargs)


def test_implementation_uses_only_public_phase116_dependency() -> None:
    source = Path(
        "src/ai_office/engine/"
        "progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary.py"
    ).read_text()
    assert "route_progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary" in source
    assert "phase109" not in source.lower()
    assert "progression_to_approved_preparation_cycle_reentry_continuation_boundary" not in source
    assert "route_approved_next_step_cycle_continuation_boundary" not in source
    assert "approved_next_step_cycle_continuation_boundary" not in source
    assert "approved_next_step_preparation" not in source
    assert "phase95" not in source.lower()
    assert "._top" not in source
    assert "._validate_" not in source
    assert "._raise" not in source


def test_public_signature_and_exact_six_argument_identity(tmp_path: Path) -> None:
    parameters = list(inspect.signature(
        route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary
    ).parameters.values())
    assert tuple(parameter.name for parameter in parameters[:6]) == (
        "result", "workflow", "approval", "employee", "state_path", "events_path",
    )
    assert all(parameter.annotation is object for parameter in parameters[:6])
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters[:6]
    )
    state, events = targets(tmp_path)
    supplied = (decision(), wf(), approval(), employee(), state, events); received: list[object] = []
    def fake(*args: object) -> object:
        received.extend(args); return prepared()
    assert call(*supplied, phase116_function=fake) is not None
    assert all(left is right for left, right in zip(received, supplied, strict=True))


def test_prepare_delegates_once_and_returns_exact_dependency_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path); value = prepared(); calls = 0
    def fake(*_: object) -> object:
        nonlocal calls; calls += 1; return value
    assert call(decision(), wf(), approval(), employee(), state, events, phase116_function=fake) is value
    assert calls == 1


def test_completion_and_failure_are_identity_preserving_zero_call_stops(tmp_path: Path) -> None:
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls; calls += 1; raise AssertionError
    complete = WorkflowProgressionDecision("workflow_complete", "workflow", "third", 3, "three", None, None, None, "last_step_succeeded")
    state, events = targets(tmp_path / "complete", "succeeded", 3)
    assert call(complete, wf(), None, None, state, events, phase116_function=fake) is complete
    failed = PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")
    state, events = targets(tmp_path / "failed", "failed", 1)
    assert call(failed, wf(), None, None, state, events, phase116_function=fake) is failed
    assert calls == 0


@pytest.mark.parametrize("bad", [object(), "substitute"])
def test_exact_models_and_stop_context_rejected(tmp_path: Path, bad: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(bad, wf(), approval(), employee(), state, events)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), None, employee(), state, events)


def test_safe_error_identity_and_unexpected_error_are_sanitized(tmp_path: Path) -> None:
    state, events = targets(tmp_path); safe = ProgressionToApprovedPreparationCycleHandoffReentryContinuationError("safe")
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffReentryContinuationError) as caught:
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=lambda *_: (_ for _ in ()).throw(safe))
    assert caught.value is safe
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")))
    assert caught.value.detail.classification == "dependency_error"


def test_mutation_is_compensated_and_malformed_return_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path); before = (state.read_bytes(), events.read_bytes())
    def fake(*_: object) -> object:
        state.write_bytes(b"changed"); events.write_bytes(b"changed"); return object()
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=fake)
    assert (state.read_bytes(), events.read_bytes()) == before


def test_signature_has_keyword_only_dependency() -> None:
    parameters = inspect.signature(
        route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary
    ).parameters
    assert parameters["phase116_function"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["phase116_function"].default is route_progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary


@pytest.mark.parametrize("field", [
    "decision", "workflow_id", "current_step_id", "current_step_index",
    "current_employee_id", "next_step_id", "next_step_index", "next_employee_id", "reason",
])
def test_prepare_decision_field_matrix(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    invalid = {
        "decision": "unsupported", "workflow_id": 1, "current_step_id": 1,
        "current_step_index": True, "current_employee_id": 1, "next_step_id": 1,
        "next_step_index": True, "next_employee_id": 1, "reason": 1,
    }
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(replace(decision(), **{field: invalid[field]}), wf(), approval(), employee(), state, events)


@pytest.mark.parametrize("field", [
    "approved", "workflow_id", "current_step_id", "current_step_index",
    "next_step_id", "next_step_index", "next_employee_id",
])
def test_approval_field_matrix(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    invalid = {
        "approved": 1, "workflow_id": 1, "current_step_id": 1,
        "current_step_index": True, "next_step_id": 1, "next_step_index": True,
        "next_employee_id": 1,
    }
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), replace(approval(), **{field: invalid[field]}), employee(), state, events)


@pytest.mark.parametrize("field", ["id", "name", "role", "instructions", "model", "allowed_tools"])
def test_employee_field_matrix(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    invalid = {"id": 1, "name": 1, "role": 1, "instructions": 1, "model": 1, "allowed_tools": ("tool",)}
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee().model_copy(update={field: invalid[field]}), state, events)


@pytest.mark.parametrize("kind", ["result", "workflow", "approval", "employee"])
def test_subclass_models_are_rejected_before_dependency(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path)
    class DecisionChild(WorkflowProgressionDecision):
        pass
    class WorkflowChild(WorkflowDefinition):
        pass
    class ApprovalChild(NextStepPreparationApproval):
        pass
    class EmployeeChild(EmployeeDefinition):
        pass
    values = {
        "result": DecisionChild(**decision().__dict__),
        "workflow": WorkflowChild.model_validate(wf().model_dump()),
        "approval": ApprovalChild(**approval().__dict__),
        "employee": EmployeeChild.model_validate(employee().model_dump()),
    }
    supplied = {"result": decision(), "workflow": wf(), "approval": approval(), "employee": employee()}
    supplied[kind] = values[kind]
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(supplied["result"], supplied["workflow"], supplied["approval"], supplied["employee"], state, events)


@pytest.mark.parametrize("kind", ["result", "workflow", "approval", "employee"])
def test_attribute_compatible_substitutes_are_rejected(tmp_path: Path, kind: str) -> None:
    state, events = targets(tmp_path)
    class Substitute:
        pass
    supplied = {"result": decision(), "workflow": wf(), "approval": approval(), "employee": employee()}
    substitute = Substitute()
    for name, value in supplied[kind].__dict__.items():
        setattr(substitute, name, value)
    supplied[kind] = substitute
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(supplied["result"], supplied["workflow"], supplied["approval"], supplied["employee"], state, events)


def test_path_type_and_target_conflict(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee(), str(state), events)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee(), state, state)


@pytest.mark.parametrize("field", [
    "workflow_id", "step_id", "step_index", "employee_id", "employee_instructions",
    "step_instructions", "model", "allowed_tool_names",
])
def test_prepared_return_field_matrix(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    invalid = {
        "workflow_id": 1, "step_id": 1, "step_index": True, "employee_id": 1,
        "employee_instructions": 1, "step_instructions": 1, "model": 1,
        "allowed_tool_names": ["tool"],
    }
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee(), state, events,
             phase116_function=lambda *_: replace(prepared(), **{field: invalid[field]}))


def test_prepared_subclass_and_substitute_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    class PreparedChild(PreparedWorkflowStep):
        pass
    child = PreparedChild(**prepared().__dict__)
    for value in (child, object()):
        with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
            call(decision(), wf(), approval(), employee(), state, events, phase116_function=lambda *_: value)


def test_unsupported_progression_and_direct_persisted_success_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    unsupported = replace(
        decision(), decision="stopped_failed", next_step_id=None,
        next_step_index=None, next_employee_id=None, reason="latest_step_failed",
    )
    success = PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None)
    for value in (unsupported, success):
        with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
            call(value, wf(), approval(), employee(), state, events)


def test_stop_routes_reject_extra_context_and_terminal_mismatch(tmp_path: Path) -> None:
    state, events = targets(tmp_path, "succeeded", 3)
    complete = WorkflowProgressionDecision(
        "workflow_complete", "workflow", "third", 3, "three", None, None, None,
        "last_step_succeeded",
    )
    for extra in ((approval(), None), (None, employee()), (approval(), employee())):
        with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
            call(complete, wf(), *extra, state, events)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(replace(complete, current_step_id="second"), wf(), None, None, state, events)


def test_terminal_prefix_duplicate_missing_reordered_unrelated_malformed_and_extra_events(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path, "succeeded", 3)
    original = events.read_text()
    lines = original.splitlines(keepends=True)
    variants = (
        lines + [lines[-1],],
        lines[:1],
        list(reversed(lines)),
        [lines[0].replace('"first"', '"other"', 1), lines[1]],
        lines[:1] + ["{malformed}\n"] + lines[1:],
        lines + [lines[0]],
    )
    complete = WorkflowProgressionDecision(
        "workflow_complete", "workflow", "third", 3, "three", None, None, None,
        "last_step_succeeded",
    )
    for variant in variants:
        events.write_text("".join(variant))
        with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
            call(complete, wf(), None, None, state, events)
    events.write_text(original)


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets(tmp_path: Path, target: str, kind: str) -> None:
    state, events = targets(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    if kind == "directory":
        selected.mkdir()
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(decision(), wf(), approval(), employee(), state, events)
    assert caught.value.detail.classification == ("state_target" if target == "state" else "event_target")


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("method", ["is_file", "read_bytes"])
def test_state_and_event_oserror_are_separately_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, method: str,
) -> None:
    state, events = targets(tmp_path)
    selected = state if target == "state" else events
    original = getattr(Path, method)
    def fail(path: Path, *args: object, **kwargs: object):
        if path == selected:
            raise OSError("unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, method, fail)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(decision(), wf(), approval(), employee(), state, events)
    assert caught.value.detail.classification == ("state_target" if target == "state" else "event_target")


def _mutating_error_fake(state: Path, events: Path, target_kind: str, error: BaseException):
    def fake(*_: object) -> object:
        if target_kind in ("state", "both"):
            state.open("wb").write(b"changed-state")
        if target_kind in ("events", "both"):
            events.open("wb").write(b"changed-events")
        raise error
    return fake


@pytest.mark.parametrize("target_kind", ["unchanged", "state", "events", "both"])
def test_safe_error_identity_after_each_mutation(tmp_path: Path, target_kind: str) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    safe = ProgressionToApprovedPreparationCycleHandoffReentryContinuationError("safe")
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffReentryContinuationError) as caught:
        call(decision(), wf(), approval(), employee(), state, events,
             phase116_function=_mutating_error_fake(state, events, target_kind, safe))
    assert caught.value is safe
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("target_kind", ["unchanged", "state", "events", "both"])
def test_unexpected_error_sanitized_after_each_mutation(tmp_path: Path, target_kind: str) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    fake = _mutating_error_fake(state, events, target_kind, RuntimeError("secret"))
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("target_kind", ["unchanged", "state", "events", "both"])
def test_malformed_return_rejected_after_each_mutation(tmp_path: Path, target_kind: str) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    def fake(*_: object) -> object:
        if target_kind in ("state", "both"):
            state.open("wb").write(b"changed-state")
        if target_kind in ("events", "both"):
            events.open("wb").write(b"changed-events")
        return object()
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError):
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=fake)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("target_kind", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_second_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str,
) -> None:
    state, events = targets(tmp_path)
    attempts: list[Path] = []
    def fake(*_: object) -> object:
        state.open("wb").write(b"changed-state")
        events.open("wb").write(b"changed-events")
        return object()
    original = Path.write_bytes
    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if target_kind in ("state", "both") and path == state:
            raise OSError("state rollback")
        if target_kind in ("events", "both") and path == events:
            raise OSError("event rollback")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts.count(state) == 1 and attempts.count(events) == 1


@pytest.mark.parametrize("outcome", ["safe", "unexpected", "malformed"])
def test_dependency_call_count_one_no_retry_for_every_error_path(tmp_path: Path, outcome: str) -> None:
    state, events = targets(tmp_path)
    calls = 0
    safe = ProgressionToApprovedPreparationCycleHandoffReentryContinuationError("safe")
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if outcome == "safe":
            raise safe
        if outcome == "unexpected":
            raise RuntimeError("unexpected")
        return object()
    with pytest.raises((
        ProgressionToApprovedPreparationCycleHandoffReentryContinuationError,
        ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationCompatibilityError,
        )):
        call(decision(), wf(), approval(), employee(), state, events, phase116_function=fake)
    assert calls == 1
