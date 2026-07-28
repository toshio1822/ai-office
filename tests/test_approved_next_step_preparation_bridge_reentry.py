"""Focused Phase 53 bridge tests using injected Phase 32 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepPreparationBridgeCompatibilityError,
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_bridge_reentry,
)
from ai_office.engine.approved_next_step_reentry import (
    ApprovedNextStepReentryCompatibilityError,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "y"},
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="b",
        name="B",
        role="R",
        instructions="employee",
        model="model",
        allowed_tools=["tool"],
    )


def setup(
    tmp_path: Path, status: str = "succeeded"
) -> tuple[
    Path,
    Path,
    WorkflowProgressionDecision | PersistedExecutionOutcome,
    NextStepPreparationApproval | None,
    EmployeeDefinition | None,
]:
    state = WorkflowExecutionState(
        "w",
        status,
        "one",
        1,
        "a",
        ("one",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        "one",
        1,
        "a",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(serialize_runtime_step_event_jsonl(event))
    if status == "failed":
        return (
            state_path,
            events_path,
            PersistedExecutionOutcome(
                "persisted_failure", "w", "one", 1, "a", "api_error"
            ),
            None,
            None,
        )
    decision = WorkflowProgressionDecision(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    approval = NextStepPreparationApproval(True, "w", "one", 1, "two", 2, "b")
    return state_path, events_path, decision, approval, employee()


def test_prepare_delegates_exact_arguments_and_returns_same_object(
    tmp_path: Path,
) -> None:
    state, events, decision, approval, person = setup(tmp_path)
    definition = workflow()
    calls = 0
    expected = PreparedWorkflowStep(
        "w", "two", 2, "b", "employee", "y", "model", ("tool",)
    )

    def phase32(*args: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        assert (
            args[0] is definition
            and args[1] is state
            and args[2] is events
            and args[3] is decision
            and args[4] is approval
            and args[5] is person
        )
        return expected

    assert (
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            definition,
            state,
            events,
            approval,
            person,
            preparation_function=phase32,
        )
        is expected
    )
    assert calls == 1


def test_persisted_failure_stop_is_unchanged(tmp_path: Path) -> None:
    state, events, failure, _, _ = setup(tmp_path, "failed")
    definition = workflow()
    before = (state.read_bytes(), events.read_bytes())
    assert (
        route_approved_next_step_preparation_bridge_reentry(
            failure,
            definition,
            state,
            events,
            None,
            None,
            preparation_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
        is failure
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_workflow_completion_stop_is_unchanged(tmp_path: Path) -> None:
    definition = workflow()
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    terminal = WorkflowExecutionState(
        "w", "succeeded", "two", 2, "b", ("one", "two"), None
    )
    events = (
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            "one",
            1,
            "a",
            "running",
            "succeeded",
            "openai",
            None,
            "first",
            "request",
            "out",
            None,
        ),
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            "two",
            2,
            "b",
            "running",
            "succeeded",
            "openai",
            None,
            "final",
            "request",
            "out",
            None,
        ),
    )
    state_path.write_text(serialize_workflow_execution_state_json(terminal))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events)
    )
    completion = WorkflowProgressionDecision(
        "workflow_complete", "w", "two", 2, "b", None, None, None, "last_step_succeeded"
    )
    before = state_path.read_bytes(), events_path.read_bytes()
    assert (
        route_approved_next_step_preparation_bridge_reentry(
            completion,
            definition,
            state_path,
            events_path,
            None,
            None,
            preparation_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
        is completion
    )
    assert (state_path.read_bytes(), events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "bad",
    [None, object(), NextStepPreparationApproval(False, "w", "one", 1, "two", 2, "b")],
)
def test_missing_or_bad_approval_has_zero_calls(tmp_path: Path, bad: object) -> None:
    state, events, decision, _, person = setup(tmp_path)
    calls = 0

    def phase32(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ApprovedNextStepPreparationBridgeCompatibilityError):
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            workflow(),
            state,
            events,
            bad,
            person,
            preparation_function=phase32,
        )
    assert calls == 0


@pytest.mark.parametrize(
    "result",
    [
        object(),
        WorkflowProgressionDecision(
            "prepare_next_step",
            "w",
            "one",
            1,
            "a",
            "wrong",
            2,
            "b",
            "next_step_available",
        ),
        WorkflowProgressionDecision(
            "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "wrong_reason"
        ),
    ],
)
def test_malformed_prevalidation_keeps_bytes_and_never_calls_phase32(
    tmp_path: Path, result: object
) -> None:
    state, events, _, approval, person = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase32(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ApprovedNextStepPreparationBridgeCompatibilityError):
        route_approved_next_step_preparation_bridge_reentry(
            result,
            workflow(),
            state,
            events,
            approval,
            person,
            preparation_function=phase32,
        )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("outcome", ["normal", "safe", "unexpected"])
def test_dependency_mutations_are_restored(
    tmp_path: Path, operation: str, target: str, outcome: str
) -> None:
    state, events, decision, approval, person = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def change(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"x")
        else:
            path.write_bytes(b"changed")

    def phase32(*_: object) -> PreparedWorkflowStep:
        if target in {"state", "both"}:
            change(state)
        if target in {"events", "both"}:
            change(events)
        if outcome == "safe":
            raise ApprovedNextStepReentryCompatibilityError("approval_contract")
        if outcome == "unexpected":
            raise RuntimeError("provider secret")
        return PreparedWorkflowStep(
            "w", "two", 2, "b", "employee", "y", "model", ("tool",)
        )

    with pytest.raises(
        (
            ApprovedNextStepPreparationBridgeCompatibilityError,
            ApprovedNextStepReentryCompatibilityError,
        )
    ) as caught:
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            workflow(),
            state,
            events,
            approval,
            person,
            preparation_function=phase32,
        )
    assert (state.read_bytes(), events.read_bytes()) == before
    if outcome == "unexpected":
        assert "secret" not in str(caught.value)


def test_safe_identity_and_unexpected_sanitization(tmp_path: Path) -> None:
    state, events, decision, approval, person = setup(tmp_path)
    definition = workflow()
    safe = ApprovedNextStepReentryCompatibilityError("approval_contract")
    with pytest.raises(ApprovedNextStepReentryCompatibilityError) as caught:
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            definition,
            state,
            events,
            approval,
            person,
            preparation_function=lambda *_: (_ for _ in ()).throw(safe),
        )
    assert caught.value is safe
    with pytest.raises(ApprovedNextStepPreparationBridgeCompatibilityError) as caught:
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            definition,
            state,
            events,
            approval,
            person,
            preparation_function=lambda *_: (_ for _ in ()).throw(
                RuntimeError("provider output")
            ),
        )
    assert (
        caught.value.detail.classification == "dependency_error"
        and "provider" not in str(caught.value)
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", 99),
        ("employee_id", "wrong"),
        ("employee_instructions", "wrong"),
        ("step_instructions", "wrong"),
        ("model", "wrong"),
        ("allowed_tool_names", ("wrong",)),
    ],
)
def test_malformed_phase32_return_is_rejected_without_retry(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, decision, approval, person = setup(tmp_path)
    calls = 0
    expected = PreparedWorkflowStep(
        "w", "two", 2, "b", "employee", "y", "model", ("tool",)
    )

    def phase32(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(expected, **{field: value})

    with pytest.raises(ApprovedNextStepPreparationBridgeCompatibilityError) as caught:
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            workflow(),
            state,
            events,
            approval,
            person,
            preparation_function=phase32,
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert calls == 1


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("outcome", ["normal", "safe", "unexpected"])
def test_rollback_failure_has_priority_and_attempts_both_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, outcome: str
) -> None:
    state, events, decision, approval, person = setup(tmp_path)
    original_write, attempts = Path.write_bytes, []

    def phase32(*_: object) -> PreparedWorkflowStep:
        if target in {"state", "both"}:
            original_write(state, b"changed")
        if target in {"events", "both"}:
            original_write(events, b"changed")
        if outcome == "safe":
            raise ApprovedNextStepReentryCompatibilityError("approval_contract")
        if outcome == "unexpected":
            raise RuntimeError("credential=secret")
        return PreparedWorkflowStep(
            "w", "two", 2, "b", "employee", "y", "model", ("tool",)
        )

    def fail_restore(path: Path, value: bytes) -> int:
        attempts.append(path)
        raise OSError("restore unavailable")

    monkeypatch.setattr(Path, "write_bytes", fail_restore)
    with pytest.raises(ApprovedNextStepPreparationBridgeCompatibilityError) as caught:
        route_approved_next_step_preparation_bridge_reentry(
            decision,
            workflow(),
            state,
            events,
            approval,
            person,
            preparation_function=phase32,
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts == [state, events]
    assert "secret" not in str(caught.value)
