"""Phase 60 boundary tests: injected fakes + one real-default regression."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepPreparationBridgeCompatibilityError,
    ApprovedNextStepPreparationPhaseBridgeCompatibilityError,
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_phase_bridge_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "prepare_next_step",
        "workflow",
        "first",
        1,
        "one",
        "second",
        2,
        "two",
        "next_step_available",
    )


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


class StringSubclass(str):
    pass


def event(status: str, index: int) -> RuntimeStepEvent:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        step,
        index,
        person,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]


def targets(
    tmp_path: Path, *, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path]:
    definition = workflow()
    step = definition.steps[index - 1]
    completed = (
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        "workflow",
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = [event("succeeded", 1)] if index == 2 else []
    events.append(event(status, index))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return state_path, events_path


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "second",
        2,
        "two",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "workflow", "first", 1, "one", "api_error"
    )


def test_prepare_delegates_once_with_exact_identity_and_returns_prepared(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied = decision()
    definition, approval_value, person, expected = (
        workflow(),
        approval(),
        employee(),
        prepared(),
    )
    calls = 0

    def phase53(*args: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        assert args == (supplied, definition, state, events, approval_value, person)
        assert all(
            actual is expected_arg
            for actual, expected_arg in zip(
                args,
                (supplied, definition, state, events, approval_value, person),
                strict=True,
            )
        )
        return expected

    before = state.read_bytes(), events.read_bytes()
    assert (
        route_approved_next_step_preparation_phase_bridge_reentry(
            supplied,
            definition,
            state,
            events,
            approval_value,
            person,
            phase53_function=phase53,
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    ("result", "status", "index"),
    [(completion(), "succeeded", 2), (failure(), "failed", 1)],
)
def test_stop_routes_return_supplied_object_without_phase53(
    tmp_path: Path,
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    status: str,
    index: int,
) -> None:
    state, events = targets(tmp_path, status=status, index=index)
    before = state.read_bytes(), events.read_bytes()

    def phase53(*_: object) -> object:
        raise AssertionError("Phase 53 must not run")

    assert (
        route_approved_next_step_preparation_phase_bridge_reentry(
            result, workflow(), state, events, None, None, phase53_function=phase53
        )
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field",
    [
        "decision",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "next_step_id",
        "next_step_index",
        "next_employee_id",
        "reason",
    ],
)
def test_prepare_decision_fields_are_strict_and_prevalidated(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    values = dict(decision().__dict__)
    values[field] = {
        "decision": "workflow_complete",
        "workflow_id": "other",
        "current_step_id": "second",
        "current_step_index": True,
        "current_employee_id": "two",
        "next_step_id": "first",
        "next_step_index": True,
        "next_employee_id": "one",
        "reason": "other",
    }[field]
    calls = 0

    def phase53(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            WorkflowProgressionDecision(**values),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification in {
        "decision_contract",
        "completion_contract",
        "approval_contract",
        "employee_contract",
    }
    assert calls == 0


@pytest.mark.parametrize(
    "field",
    [
        "approved",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "next_step_id",
        "next_step_index",
        "next_employee_id",
    ],
)
def test_approval_fields_are_strict_and_prevalidated(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    values = dict(approval().__dict__)
    values[field] = {
        "approved": False,
        "workflow_id": "other",
        "current_step_id": "second",
        "current_step_index": True,
        "next_step_id": "first",
        "next_step_index": True,
        "next_employee_id": "one",
    }[field]
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            NextStepPreparationApproval(**values),
            employee(),
        )
    assert caught.value.detail.classification == "approval_contract"


def test_prepare_rejects_wrong_employee_before_phase53(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    wrong = employee().model_copy(update={"id": "one"})
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(), workflow(), state, events, approval(), wrong
        )
    assert caught.value.detail.classification == "employee_contract"


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "employee_instructions",
        "step_instructions",
        "model",
        "allowed_tool_names",
    ],
)
def test_prepared_return_fields_are_strict_without_retry(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    expected = prepared()
    calls = 0

    def phase53(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(
            expected,
            **{
                field: {"step_index": 1, "allowed_tool_names": ["tool"]}.get(
                    field, "other"
                )
            },
        )

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert calls == 1


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "step_id",
        "employee_id",
        "employee_instructions",
        "step_instructions",
        "model",
    ],
)
def test_prepared_return_string_subclasses_are_rejected_without_retry(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    expected = prepared()
    calls = 0

    def phase53(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(expected, **{field: StringSubclass(getattr(expected, field))})

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert calls == 1


@pytest.mark.parametrize("value", [True, False, 2.0, "2"])
def test_prepared_return_step_index_requires_exact_int_without_retry(
    tmp_path: Path, value: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase53(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(prepared(), step_index=value)  # type: ignore[arg-type]

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert calls == 1


@pytest.mark.parametrize("value", [["tool"], (StringSubclass("tool"),)])
def test_prepared_return_tool_names_require_exact_tuple_and_string_types(
    tmp_path: Path, value: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase53(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(prepared(), allowed_tool_names=value)  # type: ignore[arg-type]

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert calls == 1


@pytest.mark.parametrize(
    "result",
    [
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "first", 1, "one", None
        ),
        object(),
    ],
)
def test_only_exact_phase59_result_routes_and_never_calls_phase53(
    tmp_path: Path, result: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase53(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            result, workflow(), state, events, None, None, phase53_function=phase53
        )
    assert caught.value.detail.classification in {"result_type", "failure_contract"}
    assert calls == 0


@pytest.mark.parametrize("kind", ["completion", "failure"])
def test_stop_routes_reject_approval_or_employee(tmp_path: Path, kind: str) -> None:
    result = completion() if kind == "completion" else failure()
    state, events = targets(
        tmp_path,
        status="succeeded" if kind == "completion" else "failed",
        index=2 if kind == "completion" else 1,
    )
    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            result, workflow(), state, events, approval(), employee()
        )
    assert caught.value.detail.classification in {
        "completion_contract",
        "failure_contract",
    }


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected", "malformed"])
def test_dependency_mutations_errors_and_malformed_returns_are_compensated(
    tmp_path: Path, operation: str, target: str, kind: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = ApprovedNextStepPreparationBridgeCompatibilityError("preparation_contract")

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"x")
        else:
            path.write_bytes(b"changed")

    def phase53(*_: object) -> object:
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("secret dependency detail")
        if kind == "malformed":
            return object()
        return prepared()

    expected = (
        ApprovedNextStepPreparationBridgeCompatibilityError
        if kind == "safe"
        else ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert "secret" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_rollback_failures_attempt_both_targets_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str, kind: str
) -> None:
    state, events = targets(tmp_path)
    originals = {state: state.read_bytes(), events: events.read_bytes()}
    original_write = Path.write_bytes
    attempts: list[Path] = []
    calls = 0
    safe = ApprovedNextStepPreparationBridgeCompatibilityError("preparation_contract")

    def fail_restore(path: Path, contents: bytes) -> int:
        if contents == originals[path]:
            attempts.append(path)
            if (
                failed == "both"
                or (failed == "state" and path == state)
                or (failed == "events" and path == events)
            ):
                raise OSError("secret dependency detail")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail_restore)

    def phase53(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"changed")
        original_write(events, b"changed")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("secret dependency detail")
        return object()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert attempts[:2] == [state, events]
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected_before_phase53(
    tmp_path: Path, target: str, kind: str
) -> None:
    state, events = targets(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    if kind == "directory":
        selected.mkdir()
    calls = 0

    def phase53(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ApprovedNextStepPreparationPhaseBridgeCompatibilityError
    ) as caught:
        route_approved_next_step_preparation_phase_bridge_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            phase53_function=phase53,
        )
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )
    assert calls == 0


def test_real_default_phase53_phase32_phase26_chain_returns_prepared(
    tmp_path: Path,
) -> None:
    """Real-default regression: Phase 60 -> Phase 53 -> Phase 32 -> Phase 26.

    No injected fake: real public Phase 60 uses its real default phase53_function
    and must return an exact valid PreparedWorkflowStep without touching targets.
    """
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    result = route_approved_next_step_preparation_phase_bridge_reentry(
        decision(),
        workflow(),
        state,
        events,
        approval(),
        employee(),
    )
    assert type(result) is PreparedWorkflowStep
    assert result == prepared()
    assert (state.read_bytes(), events.read_bytes()) == before
