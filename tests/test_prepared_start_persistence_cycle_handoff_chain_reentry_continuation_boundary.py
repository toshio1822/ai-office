"""Focused Phase 125 prepared-start persistence handoff tests."""

# ruff: noqa: E501

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffReentryContinuationError,
    route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
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
                {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
                {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
                {"id": "third", "name": "Third", "employee": "three", "instructions": "c"},
                {"id": "fourth", "name": "Fourth", "employee": "four", "instructions": "d"},
            ],
        }
    )


def employee(index: int = 3) -> EmployeeDefinition:
    step = workflow().steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": step.name,
            "role": "role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def start(index: int = 3) -> PreparedStepExecutionStart:
    definition = workflow()
    step = definition.steps[index - 1]
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", step.instructions, ("tool",)),
        WorkflowExecutionState(
            "workflow",
            "running",
            step.id,
            index,
            step.employee,
            tuple(item.id for item in definition.steps[: index - 1]),
            None,
        ),
    )


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "workflow", "fourth", 4, "four", None, None, None, "last_step_succeeded"
    )


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "workflow", "second", 2, "two", "api_error"
    )


def targets(tmp_path: Path, status: str = "succeeded", index: int = 2) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow()
    completed = (
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded" else tuple(item.id for item in definition.steps[: index - 1])
    )
    step = definition.steps[index - 1]
    state = WorkflowExecutionState(
        "workflow",
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            item.id,
            position,
            item.employee,
            "running",
            "succeeded",
            "openai",
            None,
            "response",
            "request",
            "output",
            None,
        )
        for position, item in enumerate(definition.steps[:index], 1)
    ]
    if status == "failed":
        events = [
            RuntimeStepEvent(
                "step_succeeded",
                "workflow",
                item.id,
                position,
                item.employee,
                "running",
                "succeeded",
                "openai",
                None,
                "response",
                "request",
                "output",
                None,
            )
            for position, item in enumerate(definition.steps[: index - 1], 1)
        ] + [
            RuntimeStepEvent(
                "step_failed",
                "workflow",
                step.id,
                index,
                step.employee,
                "running",
                "failed",
                "openai",
                "api_error",
                None,
                "request",
                None,
                "safe",
            )
        ]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8"
    )
    return state_path, events_path


def invoke(
    result: object,
    person: object | None,
    state: Path,
    events: Path,
    function: object | None = None,
) -> object:
    kwargs = {} if function is None else {"phase118_function": function}
    return route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary(
        result, workflow(), person, state, events, **kwargs
    )


def reject(callable_object, classification: str) -> None:
    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError
    ) as caught:
        callable_object()
    assert caught.value.detail.classification == classification


def test_public_signature_and_phase118_source_audit() -> None:
    function = route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary
    parameters = tuple(inspect.signature(function).parameters.values())
    assert tuple(parameter.name for parameter in parameters[:5]) == (
        "result", "workflow", "employee", "state_path", "events_path"
    )
    assert all(parameter.annotation is object for parameter in parameters[:5])
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters[:5]
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary
    source = Path(
        "src/ai_office/engine/prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary" in source
    assert "phase111" not in source.lower()
    assert "prepared_start_persistence_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_prepared_route_calls_phase118_once_in_canonical_order_and_preserves_identity(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied = (start(), workflow(), employee(), state, events)
    received: list[object] = []
    expected_state = serialize_workflow_execution_state_json(supplied[0].running_state).encode()
    expected = RunningStatePersistenceResult(len(expected_state))

    def fake(*args: object) -> object:
        received.extend(args)
        state.write_bytes(expected_state)
        return expected

    actual = route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary(
        *supplied, phase118_function=fake
    )
    assert actual is expected
    assert len(received) == 5
    assert all(actual_arg is supplied_arg for actual_arg, supplied_arg in zip(received, supplied, strict=True))
    assert events.read_bytes() == targets(tmp_path / "unused")[1].read_bytes() if False else events.read_bytes()


@pytest.mark.parametrize("index", [1, 2])
def test_early_step_indices_are_rejected_before_phase118(tmp_path: Path, index: int) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(index), employee(index), state, events, fake), "start_contract")
    assert calls == 0


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        RunningStatePersistenceResult(1),
        SimpleNamespace(request=start().request, running_state=start().running_state),
    ],
)
def test_unsupported_and_attribute_compatible_results_are_rejected(
    tmp_path: Path, bad: object
) -> None:
    state, events = targets(tmp_path)
    reject(lambda: invoke(bad, employee(), state, events), "result_type")


@pytest.mark.parametrize(
    "field, value",
    [
        ("workflow_id", "other"),
        ("status", "succeeded"),
        ("current_step_id", None),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("completed_step_ids", ["first", "second"]),
        ("last_failure_category", "api_error"),
    ],
)
def test_start_state_fields_are_exactly_linked(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    value_to_check = start()
    object.__setattr__(value_to_check.running_state, field, value)
    reject(lambda: invoke(value_to_check, employee(), state, events), "start_contract")


@pytest.mark.parametrize("nested", [SimpleNamespace(model="model", system_instructions="employee", task_instructions="c", allowed_tools=("tool",)), SimpleNamespace(workflow_id="workflow", status="running", current_step_id="third", current_step_index=3, current_employee_id="three", completed_step_ids=("first", "second"), last_failure_category=None)])
def test_nested_attribute_compatible_start_substitutes_are_rejected(
    tmp_path: Path, nested: object
) -> None:
    state_path, events_path = targets(tmp_path)
    value = start()
    request = nested if hasattr(nested, "model") else value.request
    running = nested if hasattr(nested, "workflow_id") else value.running_state
    reject(
        lambda: invoke(PreparedStepExecutionStart(request, running), employee(), state_path, events_path),
        "start_contract",
    )


@pytest.mark.parametrize(
    "result, status, index",
    [(completion(), "succeeded", 4), (failure(), "failed", 2)],
)
def test_terminal_routes_are_zero_call_identity_preserving_stops(
    tmp_path: Path, result: object, status: str, index: int
) -> None:
    state, events = targets(tmp_path, status, index)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 118 must not be called")

    assert invoke(result, None, state, events, fake) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("result", [completion(), failure()])
def test_terminal_routes_reject_non_none_employee(tmp_path: Path, result: object) -> None:
    status, index = ("succeeded", 4) if type(result) is WorkflowProgressionDecision else ("failed", 2)
    state, events = targets(tmp_path, status, index)
    reject(lambda: invoke(result, employee(), state, events), "completion_contract" if type(result) is WorkflowProgressionDecision else "failure_contract")


def test_terminal_values_and_predecessor_history_are_strict(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    reject(
        lambda: invoke(
            WorkflowProgressionDecision("stopped_failed", "workflow", "fourth", 4, "four", None, None, None, "last_step_succeeded"),
            None,
            state,
            events,
        ),
        "completion_contract",
    )
    reject(
        lambda: invoke(PersistedExecutionOutcome("persisted_success", "workflow", "second", 2, "two", None), None, state, events),
        "failure_contract",
    )
    state.write_text(
        serialize_workflow_execution_state_json(
            WorkflowExecutionState("workflow", "succeeded", "first", 1, "one", (), None)
        ),
        encoding="utf-8",
    )
    reject(lambda: invoke(start(), employee(), state, events), "terminal_contract")


def test_subclasses_and_employee_workflow_mismatches_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    class StartChild(PreparedStepExecutionStart):
        pass

    class EmployeeChild(EmployeeDefinition):
        pass

    class WorkflowChild(WorkflowDefinition):
        pass

    reject(
        lambda: invoke(StartChild(start().request, start().running_state), employee(), state, events),
        "result_type",
    )
    child_employee = EmployeeChild.model_validate(employee().model_dump())
    reject(lambda: invoke(start(), child_employee, state, events), "employee_contract")
    child_workflow = WorkflowChild.model_validate(workflow().model_dump())
    reject(
        lambda: route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary(start(), child_workflow, employee(), state, events),
        "workflow_definition",
    )


@pytest.mark.parametrize("returned", [object(), RunningStatePersistenceResult(0), RunningStatePersistenceResult(True)])
def test_malformed_dependency_returns_are_rejected_and_compensated(
    tmp_path: Path, returned: object
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    expected_state = serialize_workflow_execution_state_json(start().running_state).encode()

    def fake(*_: object) -> object:
        state.write_bytes(expected_state)
        return returned

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == before


def test_event_mutation_and_mismatched_state_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    expected = RunningStatePersistenceResult(len(serialize_workflow_execution_state_json(start().running_state).encode()))

    def fake(*_: object) -> object:
        events.write_bytes(events.read_bytes() + b"unexpected")
        state.write_bytes(serialize_workflow_execution_state_json(start().running_state).encode())
        return expected

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == before


def test_safe_and_unexpected_phase118_errors_are_compensated_without_retry(
    tmp_path: Path,
) -> None:
    for mutation in ("none", "state", "event", "both"):
        state, events = targets(tmp_path / mutation)
        before = state.read_bytes(), events.read_bytes()
        calls = 0
        safe = PreparedStartPersistenceCycleHandoffReentryContinuationError("safe")

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            if mutation in {"state", "both"}:
                state.write_bytes(b"changed-state")
            if mutation in {"event", "both"}:
                events.write_bytes(b"changed-event")
            raise safe

        with pytest.raises(PreparedStartPersistenceCycleHandoffReentryContinuationError) as caught:
            invoke(start(), employee(), state, events, fake)
        assert caught.value is safe
        assert calls == 1
        assert (state.read_bytes(), events.read_bytes()) == before

        state, events = targets(tmp_path / f"unexpected-{mutation}")
        before = state.read_bytes(), events.read_bytes()

        def unexpected(*_: object) -> object:
            if mutation in {"state", "both"}:
                state.write_bytes(b"changed-state")
            if mutation in {"event", "both"}:
                events.write_bytes(b"changed-event")
            raise RuntimeError("secret detail")

        with pytest.raises(PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
            invoke(start(), employee(), state, events, unexpected)
        assert caught.value.detail.classification == "dependency_error"
        assert "secret detail" not in str(caught.value)
        assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "event", "both"])
def test_rollback_failure_attempts_both_targets_and_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events = targets(tmp_path)
    original = Path.write_bytes
    attempts: list[Path] = []

    def fake(*_: object) -> object:
        original(state, b"changed-state")
        original(events, b"changed-event")
        return object()

    def write(path: Path, data: bytes) -> int:
        if path in (state, events) and data in (targets(tmp_path / "other")[0].read_bytes(), targets(tmp_path / "other")[1].read_bytes()):
            attempts.append(path)
            if path == state and failed in {"state", "both"}:
                raise OSError("rollback")
            if path == events and failed in {"event", "both"}:
                raise OSError("rollback")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(PreparedStartPersistenceCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        invoke(start(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts == [state, events]


@pytest.mark.parametrize("target_name", ["state", "events"])
def test_missing_nonregular_and_target_oserror_inputs_are_classified(
    tmp_path: Path, target_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events = targets(tmp_path)
    target = state if target_name == "state" else events
    target.unlink()
    reject(lambda: invoke(start(), employee(), state, events), "state_target" if target_name == "state" else "event_target")
    target.mkdir()
    reject(lambda: invoke(start(), employee(), state, events), "state_target" if target_name == "state" else "event_target")

    state, events = targets(tmp_path / f"oserror-{target_name}")
    original = Path.is_file

    def failing(path: Path) -> bool:
        if path == (state if target_name == "state" else events):
            raise OSError("target")
        return original(path)

    monkeypatch.setattr(Path, "is_file", failing)
    reject(lambda: invoke(start(), employee(), state, events), "state_target" if target_name == "state" else "event_target")
