"""Focused Phase 132 prepared-start persistence bridge tests."""

# ruff: noqa: E501

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainReentryContinuationError as Phase125Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary as phase125_route,
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


def employee(index: int = 4) -> EmployeeDefinition:
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


def start(index: int = 4) -> PreparedStepExecutionStart:
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


def targets(
    tmp_path: Path,
    status: str = "succeeded",
    index: int = 3,
    provider: object = "openai",
) -> tuple[Path, Path]:
    definition = workflow()
    tmp_path.mkdir(parents=True, exist_ok=True)
    completed = (
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
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
    if status == "succeeded":
        events[-1] = RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            step.id,
            index,
            step.employee,
            "running",
            "succeeded",
            provider,
            None,
            "response",
            "request",
            "output",
            None,
        )
    else:
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
                provider,
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
        "".join(serialize_runtime_step_event_jsonl(item) for item in events),
        encoding="utf-8",
    )
    return state_path, events_path


def invoke(
    result: object,
    person: object | None,
    state: Path,
    events: Path,
    function: object | None = None,
    workflow_value: object | None = None,
) -> object:
    kwargs = {} if function is None else {"phase125_function": function}
    return route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
        result,
        workflow() if workflow_value is None else workflow_value,
        person,
        state,
        events,
        **kwargs,
    )


def reject(callable_object, classification: str) -> None:
    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        callable_object()
    assert caught.value.detail.classification == classification


def test_public_signature_default_and_phase125_source_audit() -> None:
    function = route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary
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
    assert parameters[5].default is phase125_route
    source = Path(
        "src/ai_office/engine/prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary" in source
    assert "phase118" not in source.lower()
    assert "route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary" not in source
    assert ". _validate_" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_prepared_route_uses_canonical_identity_once_and_persists_state_only(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    original_events = events.read_bytes()
    supplied = (start(), workflow(), employee(), state, events)
    expected_state = serialize_workflow_execution_state_json(
        supplied[0].running_state
    ).encode()
    expected = RunningStatePersistenceResult(len(expected_state))
    received: list[tuple[object, ...]] = []

    def fake(*args: object) -> object:
        received.append(args)
        state.write_bytes(expected_state)
        return expected

    actual = route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
        *supplied, phase125_function=fake
    )
    assert actual is expected
    assert len(received) == 1
    assert len(received[0]) == 5
    assert all(
        actual_arg is supplied_arg
        for actual_arg, supplied_arg in zip(received[0], supplied, strict=True)
    )
    assert state.read_bytes() == expected_state
    assert events.read_bytes() == original_events


@pytest.mark.parametrize("index", [1, 2, 3])
def test_continuation_indices_below_four_are_rejected_before_phase125(
    tmp_path: Path, index: int
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(index), employee(index), state, events, fake), "start_contract")
    assert calls == 0


@pytest.mark.parametrize("bad", [object(), RunningStatePersistenceResult(1)])
def test_unsupported_direct_results_are_rejected_before_phase125(
    tmp_path: Path, bad: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(bad, employee(), state, events, fake), "result_type")
    assert calls == 0


def test_workflow_and_employee_attribute_compatible_substitutes_are_rejected(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    workflow_substitute = SimpleNamespace(
        id="workflow", name="Workflow", description="test", steps=workflow().steps
    )
    employee_substitute = SimpleNamespace(
        id="four", name="Fourth", role="role", instructions="employee", model="model", allowed_tools=["tool"]
    )
    reject(
        lambda: invoke(start(), employee(), state, events, fake, workflow_substitute),
        "workflow_definition",
    )
    reject(
        lambda: invoke(start(), employee_substitute, state, events, fake),
        "employee_contract",
    )
    assert calls == 0


def test_subclass_models_are_rejected_before_phase125(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    class StartChild(PreparedStepExecutionStart):
        pass

    class WorkflowChild(WorkflowDefinition):
        pass

    class StepChild(WorkflowStepDefinition):
        pass

    class EmployeeChild(EmployeeDefinition):
        pass

    child_start = StartChild(start().request, start().running_state)
    child_workflow = WorkflowChild.model_validate(workflow().model_dump())
    child_step = StepChild.model_validate(workflow().steps[3].model_dump())
    step_workflow = workflow()
    step_workflow.steps[3] = child_step
    child_employee = EmployeeChild.model_validate(employee().model_dump())
    reject(lambda: invoke(child_start, employee(), state, events, fake), "result_type")
    reject(lambda: invoke(start(), employee(), state, events, fake, child_workflow), "workflow_definition")
    reject(lambda: invoke(start(), child_employee, state, events, fake), "employee_contract")
    reject(lambda: invoke(start(), employee(), state, events, fake, step_workflow), "workflow_definition")
    assert calls == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("instructions", "wrong"),
        ("model", "wrong"),
        ("allowed_tools", ["tool", "wrong-tool"]),
    ],
)
def test_employee_semantic_linkage_is_rejected_before_phase125(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    person = employee()
    object.__setattr__(person, field, value)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(), person, state, events, fake), "start_contract")
    assert calls == 0


@pytest.mark.parametrize("field", ["instructions"])
def test_workflow_step_semantic_linkage_is_rejected_before_phase125(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    definition = workflow()
    object.__setattr__(definition.steps[3], field, "wrong")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), employee(), state, events, fake, definition),
        "start_contract",
    )
    assert calls == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("model", 4),
        ("system_instructions", "wrong"),
        ("task_instructions", "wrong"),
        ("allowed_tools", ["tool"]),
        ("allowed_tools", ("tool", 4)),
        ("model", "wrong-model"),
        ("allowed_tools", ("tool", "wrong-tool")),
    ],
)
def test_request_exact_type_and_semantic_mismatches_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    value_to_check = start()
    object.__setattr__(value_to_check.request, field, value)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(value_to_check, employee(), state, events, fake), "start_contract")
    assert calls == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("workflow_id", "other"),
        ("status", "succeeded"),
        ("current_step_id", "wrong"),
        ("current_step_index", True),
        ("current_step_index", type("IndexChild", (int,), {})(4)),
        ("current_employee_id", "other"),
        ("completed_step_ids", ["first", "second", "third"]),
        ("completed_step_ids", ("first", "second", "wrong")),
        ("last_failure_category", "api_error"),
    ],
)
def test_running_state_exact_type_and_semantic_linkage_is_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    value_to_check = start()
    object.__setattr__(value_to_check.running_state, field, value)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    classification = "employee_contract" if field == "current_employee_id" else "start_contract"
    reject(lambda: invoke(value_to_check, employee(), state, events, fake), classification)
    assert calls == 0


@pytest.mark.parametrize("provider", ["other", 4])
def test_predecessor_terminal_provider_must_be_exact_openai(
    tmp_path: Path, provider: object
) -> None:
    state, events = targets(tmp_path, provider=provider)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(), employee(), state, events, fake), "terminal_contract")
    assert calls == 0


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_predecessor_history_matrix_is_rejected_before_phase125(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    lines = events.read_bytes().splitlines(keepends=True)
    if mutation == "duplicate":
        mutated = lines[0] + lines[0] + lines[2]
    elif mutation == "missing":
        mutated = b"".join(lines[:2])
    elif mutation == "reordered":
        mutated = lines[1] + lines[0] + lines[2]
    elif mutation == "unrelated":
        unrelated = RuntimeStepEvent(
            "step_succeeded", "workflow", "unrelated", 99, "unrelated-employee",
            "running", "succeeded", "openai", None, "response", "request", "output", None
        )
        mutated = lines[0] + lines[1] + serialize_runtime_step_event_jsonl(unrelated).encode()
    elif mutation == "malformed":
        mutated = b"not-json\n"
    else:
        mutated = b"".join(lines + [lines[0]])
    events.write_bytes(mutated)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(), employee(), state, events, fake), "terminal_contract")
    assert calls == 0


@pytest.mark.parametrize("result, status, index", [(completion(), "succeeded", 4), (failure(), "failed", 2)])
def test_stop_routes_are_identity_preserving_zero_call_stops(
    tmp_path: Path, result: object, status: str, index: int
) -> None:
    state, events = targets(tmp_path, status, index)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 125 must not be called")

    assert invoke(result, None, state, events, fake) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("result", [completion(), failure()])
def test_stop_routes_reject_non_none_employee_with_zero_calls(
    tmp_path: Path, result: object
) -> None:
    status, index = ("succeeded", 4) if type(result) is WorkflowProgressionDecision else ("failed", 2)
    state, events = targets(tmp_path, status, index)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    classification = "completion_contract" if type(result) is WorkflowProgressionDecision else "failure_contract"
    reject(lambda: invoke(result, employee(), state, events, fake), classification)
    assert calls == 0


def test_stop_route_subclasses_and_attribute_substitutes_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    completion_value = completion()
    failure_value = failure()

    class DecisionChild(WorkflowProgressionDecision):
        pass

    class OutcomeChild(PersistedExecutionOutcome):
        pass

    decision_child = DecisionChild(*tuple(completion_value.__dict__.values()))
    outcome_child = OutcomeChild(*tuple(failure_value.__dict__.values()))
    decision_substitute = SimpleNamespace(**completion_value.__dict__)
    outcome_substitute = SimpleNamespace(**failure_value.__dict__)
    for value, classification in (
        (decision_child, "result_type"),
        (outcome_child, "result_type"),
        (decision_substitute, "result_type"),
        (outcome_substitute, "result_type"),
    ):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        before = state.read_bytes(), events.read_bytes()
        reject(lambda value=value: invoke(value, None, state, events, fake), classification)
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result, classification",
    [
        (WorkflowProgressionDecision("bad", "workflow", "fourth", 4, "four", None, None, None, "last_step_succeeded"), "completion_contract"),
        (PersistedExecutionOutcome("persisted_success", "workflow", "second", 2, "two", None), "failure_contract"),
    ],
)
def test_malformed_stop_values_are_zero_call_rejected(
    tmp_path: Path, result: object, classification: str
) -> None:
    state, events = targets(tmp_path, "succeeded" if type(result) is WorkflowProgressionDecision else "failed", 4 if type(result) is WorkflowProgressionDecision else 2)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(result, None, state, events, fake), classification)
    assert calls == 0


def _expected_persistence(start_value: PreparedStepExecutionStart) -> tuple[bytes, RunningStatePersistenceResult]:
    contents = serialize_workflow_execution_state_json(start_value.running_state).encode()
    return contents, RunningStatePersistenceResult(len(contents))


@pytest.mark.parametrize(
    "returned",
    [object(), RunningStatePersistenceResult(0), RunningStatePersistenceResult(-1), RunningStatePersistenceResult(True)],
)
def test_malformed_persistence_results_are_rejected_and_compensated(
    tmp_path: Path, returned: object
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    expected_state, _ = _expected_persistence(start())
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return returned

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_persistence_result_subclass_and_attribute_substitute_are_rejected(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    expected_state, expected = _expected_persistence(start())

    class ResultChild(RunningStatePersistenceResult):
        pass

    for returned in (
        ResultChild(expected.state_bytes_written),
        SimpleNamespace(state_bytes_written=expected.state_bytes_written),
    ):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            state.write_bytes(expected_state)
            return returned

        reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
        assert calls == 1
        assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "count",
    [0, -1, True, type("CountChild", (int,), {})(1), 999999],
)
def test_persistence_byte_count_requires_positive_exact_built_in_int(
    tmp_path: Path, count: object
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    expected_state, _ = _expected_persistence(start())
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return RunningStatePersistenceResult(count)  # type: ignore[arg-type]

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_mismatched_valid_persisted_running_state_is_rejected_and_compensated(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    mismatched = WorkflowExecutionState(
        "workflow", "running", "third", 3, "three", ("first", "second"), None
    )
    mismatched_bytes = serialize_workflow_execution_state_json(mismatched).encode()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(mismatched_bytes)
        return RunningStatePersistenceResult(len(mismatched_bytes))

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_unrelated_target_mutation_is_rejected_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    expected_state, expected = _expected_persistence(start())
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        if mutation in {"events", "both"}:
            events.write_bytes(events.read_bytes() + b"unexpected")
        if mutation == "state":
            state.write_bytes(expected_state + b"unexpected")
        return expected

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase125_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0
    supplied_error = Phase125Error("safe")

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        raise supplied_error

    with pytest.raises(Phase125Error) as caught:
        invoke(start(), employee(), state, events, fake)
    assert caught.value is supplied_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase125_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        raise RuntimeError("secret detail")

    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(start(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_return_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        return object()

    reject(lambda: invoke(start(), employee(), state, events, fake), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events = targets(tmp_path)
    original_state, original_events = state.read_bytes(), events.read_bytes()
    original_write = Path.write_bytes
    attempts: list[Path] = []
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    def write(path: Path, data: bytes) -> int:
        if path == state and data == original_state or path == events and data == original_events:
            attempts.append(path)
            if path == state and failed in {"state", "both"}:
                raise OSError("rollback")
            if path == events and failed in {"events", "both"}:
                raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(start(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert attempts == [state, events]


@pytest.mark.parametrize("target_name", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_missing_directory_and_target_oserror_are_classified(
    tmp_path: Path,
    target_name: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, events = targets(tmp_path)
    target = state if target_name == "state" else events
    classification = "state_target" if target_name == "state" else "event_target"
    target.unlink()
    reject(lambda: invoke(start(), employee(), state, events), classification)
    target.mkdir()
    reject(lambda: invoke(start(), employee(), state, events), classification)

    state, events = targets(tmp_path / f"oserror-{operation}-{target_name}")
    selected = state if target_name == "state" else events
    original = getattr(Path, operation)

    def failing(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("target")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, failing)
    reject(lambda: invoke(start(), employee(), state, events), classification)


def test_same_target_and_non_callable_dependency_are_rejected(tmp_path: Path) -> None:
    state, _ = targets(tmp_path)
    reject(lambda: invoke(start(), employee(), state, state), "target_conflict")
    _, events = targets(tmp_path / "events")
    state, _ = targets(tmp_path / "state")
    reject(lambda: invoke(start(), employee(), state, events, object()), "persistence_contract")
