"""Focused tests for the Phase 145 outer-chain progression-to-preparation bridge."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_phase145,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as public_phase137,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    WorkflowExecutionPersistenceResult,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class DecisionChild(WorkflowProgressionDecision):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class ApprovalChild(NextStepPreparationApproval):
    pass


class EmployeeChild(EmployeeDefinition):
    pass


class PreparedChild(PreparedWorkflowStep):
    pass


class StartChild(PreparedStepExecutionStart):
    pass


class IntChild(int):
    pass


class TupleChild(tuple):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "c", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
                {"id": "six", "name": "Six", "employee": "f", "instructions": "six"},
            ],
        }
    )


def progression(supplied_workflow: WorkflowDefinition, index: int = 5) -> WorkflowProgressionDecision:
    current = supplied_workflow.steps[index - 1]
    following = supplied_workflow.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        supplied_workflow.id,
        current.id,
        index,
        current.employee,
        following.id,
        index + 1,
        following.employee,
        "next_step_available",
    )


def approval(value: WorkflowProgressionDecision) -> NextStepPreparationApproval:
    return NextStepPreparationApproval(
        True,
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.next_step_id,
        value.next_step_index,
        value.next_employee_id,
    )


def employee(value: WorkflowProgressionDecision) -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": value.next_employee_id,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def prepared(
    supplied_workflow: WorkflowDefinition,
    value: WorkflowProgressionDecision,
    supplied_employee: EmployeeDefinition,
) -> PreparedWorkflowStep:
    step = supplied_workflow.steps[value.next_step_index - 1]
    return PreparedWorkflowStep(
        supplied_workflow.id,
        step.id,
        value.next_step_index,
        supplied_employee.id,
        supplied_employee.instructions,
        step.instructions,
        supplied_employee.model,
        tuple(supplied_employee.allowed_tools),
    )


def predecessor_event(
    step: WorkflowStepDefinition,
    index: int,
    *,
    provider: object = "other",
    request_id: object = "request",
    response_id: object = "response",
    output_text: object = "output",
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step.id,
        index,
        step.employee,
        "running",
        "succeeded",
        provider,
        None,
        response_id,
        request_id,
        output_text,
        None,
    )


def terminal_event(
    step: WorkflowStepDefinition,
    index: int,
    *,
    status: str = "succeeded",
    provider: object = "openai",
    output_text: object = "output",
    request_id: object = "request",
    response_id: object = "response",
    failure_category: object = None,
    message: object = None,
) -> RuntimeStepEvent:
    failed = status == "failed"
    return RuntimeStepEvent(
        "step_failed" if failed else "step_succeeded",
        "w",
        step.id,
        index,
        step.employee,
        "running",
        status,
        provider,
        "api_error" if failed else failure_category,
        None if failed else response_id,
        request_id,
        None if failed else output_text,
        "safe failure" if failed else message,
    )


def data(
    tmp_path: Path,
    *,
    index: int = 5,
    status: str = "succeeded",
    terminal_provider: object = "openai",
    terminal_output: object = "output",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
    predecessor_providers: dict[int, object] | None = None,
    predecessor_outputs: dict[int, object] | None = None,
) -> dict[str, object]:
    supplied_workflow = workflow()
    step = supplied_workflow.steps[index - 1]
    predecessor_providers = predecessor_providers or {}
    predecessor_outputs = predecessor_outputs or {}
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider=predecessor_providers.get(
                position, "openai" if position == index - 1 else "other"
            ),
            output_text=predecessor_outputs.get(position, "output"),
        )
        for position, prior in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        tuple(item.id for item in supplied_workflow.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in supplied_workflow.steps[: index - 1]),
        None if status == "succeeded" else "api_error",
    )
    terminal = terminal_event(
        step,
        index,
        status=status,
        provider=terminal_provider,
        output_text=terminal_output,
        request_id=terminal_request_id,
        response_id=terminal_response_id,
    )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result: object = (
        progression(supplied_workflow, index)
        if status == "succeeded" and index < len(supplied_workflow.steps)
        else PersistedExecutionOutcome(
            "persisted_failure",
            "w",
            step.id,
            index,
            step.employee,
            "api_error",
        )
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "approval": approval(result) if type(result) is WorkflowProgressionDecision else None,
        "employee": employee(result) if type(result) is WorkflowProgressionDecision else None,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def completion_data(tmp_path: Path, *, provider: object = "other", output: object = "output") -> dict[str, object]:
    value = data(tmp_path, index=6, terminal_provider=provider, terminal_output=output)
    value["result"] = WorkflowProgressionDecision(
        "workflow_complete", "w", "six", 6, "f", None, None, None, "last_step_succeeded"
    )
    value["approval"] = None
    value["employee"] = None
    return value


def failure_data(tmp_path: Path, *, provider: object = "other") -> dict[str, object]:
    return data(tmp_path, index=5, status="failed", terminal_provider=provider)


def invoke(value: dict[str, object], dependency: object) -> object:
    return public_phase145(
        value["result"],
        value["workflow"],
        value["approval"],
        value["employee"],
        value["state_path"],
        value["events_path"],
        phase137_function=dependency,
    )


def before(value: dict[str, object]) -> tuple[bytes, bytes]:
    return value["state_path"].read_bytes(), value["events_path"].read_bytes()


def unchanged(value: dict[str, object]) -> None:
    assert before(value) == (value["before_state"], value["before_events"])


def target_snapshot(
    value: dict[str, object],
) -> tuple[tuple[str, bytes | None], tuple[str, bytes | None]]:
    def snapshot(path: Path) -> tuple[str, bytes | None]:
        if path.is_file():
            return "file", path.read_bytes()
        if path.is_dir():
            return "directory", None
        return "missing", None

    return snapshot(value["state_path"]), snapshot(value["events_path"])


def rewrite_state(path: Path, **changes: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def rewrite_events(path: Path, mutate: object) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    result = mutate(lines)
    path.write_text("\n".join(result) + "\n", encoding="utf-8")


def rewrite_event(path: Path, index: int, **changes: object) -> None:
    def mutate(lines: list[str]) -> list[str]:
        payload = json.loads(lines[index])
        payload.update(changes)
        lines[index] = json.dumps(payload, separators=(",", ":"))
        return lines

    rewrite_events(path, mutate)


def assert_rejected(value: dict[str, object], expected: str, dependency: object) -> None:
    expected_targets = target_snapshot(value)
    calls = 0

    def counted(*_: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*_) if callable(dependency) else object()

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, counted)
    assert type(caught.value) is Phase145CompatibilityError
    assert caught.value.detail.classification == expected
    assert calls == 0
    assert target_snapshot(value) == expected_targets


def patch_loaded_event(
    value: dict[str, object], monkeypatch: pytest.MonkeyPatch, index: int, **changes: object
) -> None:
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(value["state_path"], value["events_path"])
    )
    events = list(loaded.events)
    events[index] = replace(events[index], **changes)

    def fake_loader(_targets: object) -> LoadedWorkflowExecutionHistory:
        return LoadedWorkflowExecutionHistory(loaded.state, tuple(events))

    import ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase145_module

    monkeypatch.setattr(phase145_module, "load_workflow_execution_history", fake_loader)


def test_public_signature_default_and_source_audit() -> None:
    function = public_phase145
    parameters = list(inspect.signature(function).parameters.values())
    assert [item.name for item in parameters] == [
        "result", "workflow", "approval", "employee", "state_path", "events_path", "phase137_function"
    ]
    assert all(item.annotation is object for item in parameters[:6])
    assert [item.kind for item in parameters[:6]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 6
    assert parameters[6].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[6].default is public_phase137
    source = Path(
        "src/ai_office/engine/"
        "progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary" in source
    assert "phase130" not in source.lower()
    assert "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary" not in source
    assert "route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary" not in source
    assert "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_prepare_delegates_canonical_six_arguments_once_and_returns_identity(tmp_path: Path) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return expected

    assert invoke(value, fake) is expected
    assert calls == [
        (
            value["result"], value["workflow"], value["approval"], value["employee"],
            value["state_path"], value["events_path"],
        )
    ]
    unchanged(value)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_prepare_indices_one_two_three_four_delegate_once_with_identity(
    tmp_path: Path, index: int
) -> None:
    value = data(tmp_path, index=index)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return expected

    assert invoke(value, fake) is expected
    assert calls == [
        (
            value["result"], value["workflow"], value["approval"], value["employee"],
            value["state_path"], value["events_path"],
        )
    ]
    assert expected.workflow_id == value["workflow"].id
    assert expected.step_id == value["result"].next_step_id
    assert expected.step_index == index + 1
    assert expected.employee_id == value["result"].next_employee_id
    unchanged(value)

    default_result = public_phase145(
        value["result"],
        value["workflow"],
        value["approval"],
        value["employee"],
        value["state_path"],
        value["events_path"],
    )
    assert type(default_result) is PreparedWorkflowStep
    assert default_result.workflow_id == value["workflow"].id
    assert default_result.step_id == value["result"].next_step_id
    assert default_result.step_index == index + 1
    assert default_result.employee_id == value["result"].next_employee_id
    unchanged(value)


def test_immediate_predecessor_empty_output_text_delegates_once_canonical_order(
    tmp_path: Path,
) -> None:
    value = data(tmp_path, predecessor_outputs={4: ""})
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return expected

    assert invoke(value, fake) is expected
    assert calls == [
        (
            value["result"], value["workflow"], value["approval"], value["employee"],
            value["state_path"], value["events_path"],
        )
    ]
    unchanged(value)


def test_earlier_empty_output_text_survives_later_succeeded_predecessor(
    tmp_path: Path,
) -> None:
    value = data(tmp_path, predecessor_outputs={2: ""})
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


def test_predecessor_nonempty_output_text_remains_accepted(tmp_path: Path) -> None:
    value = data(tmp_path, predecessor_outputs={1: "first", 2: "second", 3: "third", 4: "fourth"})
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("value_field", [None, 4])
def test_predecessor_output_text_non_string_is_rejected_before_phase137(
    tmp_path: Path, value_field: object
) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 2, output_text=value_field)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_predecessor_output_text_bytes_is_rejected_before_phase137(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = data(tmp_path)
    patch_loaded_event(value, monkeypatch, 2, output_text=b"bytes")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field,value_field", [("request_id", ""), ("request_id", 4), ("response_id", ""), ("response_id", 4)])
def test_predecessor_response_request_contract_is_strict(tmp_path: Path, field: str, value_field: object) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 2, **{field: value_field})
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_immediate_predecessor_provider_is_strict(tmp_path: Path, provider: object) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 3, provider=provider)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("provider", ["", 4])
def test_earlier_predecessor_provider_requires_nonempty_exact_string(tmp_path: Path, provider: object) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 1, provider=provider)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_earlier_nonopenai_predecessor_remains_valid(tmp_path: Path) -> None:
    value = data(tmp_path, predecessor_providers={1: "anthropic", 2: "other", 3: "azure"})
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


def test_succeeded_terminal_exact_empty_output_remains_accepted(tmp_path: Path) -> None:
    value = data(tmp_path, terminal_output="")
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


def test_succeeded_terminal_exact_nonempty_output_remains_accepted(tmp_path: Path) -> None:
    value = data(tmp_path, terminal_output="done")
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("field,value", [("provider", "other"), ("provider", ""), ("provider", 4), ("response_id", ""), ("response_id", 4), ("response_id", None), ("output_text", 4), ("output_text", None), ("failure_category", "api_error"), ("message", "bad")])
def test_prepare_terminal_event_contract_is_strict(tmp_path: Path, field: str, value: object) -> None:
    value_set = data(tmp_path)
    rewrite_event(value_set["events_path"], 4, **{field: value})
    assert_rejected(value_set, "terminal_contract", lambda *_: pytest.fail("called"))


def test_terminal_request_id_none_is_accepted(tmp_path: Path) -> None:
    value = data(tmp_path, terminal_request_id=None)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("value", ["", 4])
def test_terminal_request_id_non_none_requires_nonempty_exact_string(tmp_path: Path, value: object) -> None:
    data_set = data(tmp_path)
    rewrite_event(data_set["events_path"], 4, request_id=value)
    assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


def test_predecessor_provenance_matrix_is_zero_call(tmp_path: Path) -> None:
    for position in (1, 2, 3, 4):
        for field, value in (
            ("event_type", "step_failed"),
            ("workflow_id", "wrong"),
            ("step_id", "wrong"),
            ("step_index", 99),
            ("employee_id", "wrong"),
            ("previous_status", "succeeded"),
            ("next_status", "failed"),
            ("failure_category", "api_error"),
            ("message", "bad"),
        ):
            data_set = data(tmp_path)
            rewrite_event(data_set["events_path"], position - 1, **{field: value})
            assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("replacement", [True, IntChild(4)])
def test_workflow_step_index_requires_exact_builtin_int_zero_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: object,
) -> None:
    for position in (1, 2, 3, 4):
        data_set = data(tmp_path)
        if type(replacement) is bool:
            rewrite_event(data_set["events_path"], position - 1, step_index=replacement)
        else:
            patch_loaded_event(data_set, monkeypatch, position - 1, step_index=replacement)
        assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    "changes",
    [
        {"current_step_index": True},
        {"current_step_index": 4},
        {"current_step_id": "wrong"},
        {"current_employee_id": "wrong"},
        {"status": "failed"},
        {"last_failure_category": "api_error"},
        {"completed_step_ids": ["one", "two", "three"]},
    ],
)
def test_persisted_terminal_state_contract_is_strict(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    value = data(tmp_path)
    if changes.get("status") == "failed":
        rewrite_event(
            value["events_path"],
            4,
            event_type="step_failed",
            next_status="failed",
            failure_category="api_error",
            response_id=None,
            output_text=None,
            message="safe failure",
        )
    rewrite_state(value["state_path"], **changes)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_history_matrix_is_rejected_before_phase137(tmp_path: Path, mode: str) -> None:
    value = data(tmp_path)

    def mutate(lines: list[str]) -> list[str]:
        if mode == "duplicate":
            return [lines[0], lines[0], *lines[1:]]
        if mode == "missing":
            return lines[1:]
        if mode == "reordered":
            return [lines[1], lines[0], *lines[2:]]
        if mode == "unrelated":
            payload = json.loads(lines[0])
            payload["workflow_id"] = "other"
            return [*lines[:-1], json.dumps(payload, separators=(",", ":")), lines[-1]]
        if mode == "malformed":
            return ["{", *lines[1:]]
        return [*lines, lines[-1]]

    rewrite_events(value["events_path"], mutate)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["state", "events", "both"])
def test_normal_dependency_mutation_is_compensated_without_retry(tmp_path: Path, kind: str) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        if kind in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if kind in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        return expected

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("kind", ["malformed", "substitute"])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_dependency_return_and_mutation_are_compensated(
    tmp_path: Path, kind: str, mutation: str
) -> None:
    value = data(tmp_path)

    def fake(*_: object) -> object:
        if mutation in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if mutation in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        if kind == "substitute":
            return SimpleNamespace(**prepared(value["workflow"], value["result"], value["employee"]).__dict__)
        return object()

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"
    unchanged(value)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase137_error_identity_is_preserved_after_compensation(tmp_path: Path, mutation: str) -> None:
    value = data(tmp_path)
    safe_error = Phase137Error("safe")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if mutation in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        raise safe_error

    with pytest.raises(Phase137Error) as caught:
        invoke(value, fake)
    assert caught.value is safe_error
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase137_error_is_sanitized_and_compensated(tmp_path: Path, mutation: str) -> None:
    value = data(tmp_path)

    def fake(*_: object) -> object:
        if mutation in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if mutation in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        raise RuntimeError("secret detail")

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    unchanged(value)


@pytest.mark.parametrize("failed", [{"state"}, {"events"}, {"state", "events"}])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, failed: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0
    writes: dict[Path, int] = {value["state_path"]: 0, value["events_path"]: 0}
    original_write = Path.write_bytes

    def write(path: Path, contents: bytes) -> int:
        writes[path] += 1
        if path.stem in failed and writes[path] >= 2:
            raise OSError("rollback")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write)

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        value["state_path"].write_bytes(b"mutated")
        value["events_path"].write_bytes(b"mutated\n")
        return expected

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert writes[value["state_path"]] == 2
    assert writes[value["events_path"]] == 2


@pytest.mark.parametrize("target", ["state_path", "events_path"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_nonregular_targets_are_zero_call(tmp_path: Path, target: str, kind: str) -> None:
    value = data(tmp_path)
    path = value[target]
    if kind == "missing":
        path.unlink()
    else:
        path.unlink()
        path.mkdir()
    assert_rejected(value, "state_target" if target == "state_path" else "event_target", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("target", ["state_path", "events_path"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_separately_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    operation: str,
) -> None:
    value = data(tmp_path)
    selected = value[target]
    original = getattr(Path, operation)
    original_state = Path.read_bytes(value["state_path"])
    original_events = Path.read_bytes(value["events_path"])

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("target-secret")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    calls = 0
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if target == "state_path" else "event_target"
    )
    assert calls == 0
    monkeypatch.setattr(Path, operation, original)
    assert Path.read_bytes(value["state_path"]) == original_state
    assert Path.read_bytes(value["events_path"]) == original_events


def test_target_conflict_and_noncallable_dependency_are_zero_call(tmp_path: Path) -> None:
    value = data(tmp_path)
    value["events_path"] = value["state_path"]
    assert_rejected(value, "target_conflict", lambda *_: pytest.fail("called"))
    value = data(tmp_path)
    with pytest.raises(Phase145CompatibilityError) as caught:
        public_phase145(
            value["result"],
            value["workflow"],
            value["approval"],
            value["employee"],
            value["state_path"],
            value["events_path"],
            phase137_function=object(),
        )
    assert caught.value.detail.classification == "dependency_error"
    unchanged(value)


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
def test_prepare_decision_fields_are_strict(tmp_path: Path, field: str) -> None:
    value = data(tmp_path)
    original = value["result"]
    value["result"] = replace(
        original,
        **{field: "wrong" if field != "current_step_index" else True},
    )
    assert_rejected(value, "decision_contract", lambda *_: pytest.fail("called"))


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
def test_approval_linkage_is_strict(tmp_path: Path, field: str) -> None:
    value = data(tmp_path)
    original = value["approval"]
    value["approval"] = replace(original, **{field: "wrong"})
    assert_rejected(value, "approval_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field", ["id", "name", "role", "instructions", "model", "allowed_tools"])
def test_employee_contract_is_strict(tmp_path: Path, field: str) -> None:
    value = data(tmp_path)
    original = value["employee"]
    bad = ("tool-one", "wrong-tool") if field == "allowed_tools" else (True if field == "id" else 4)
    value["employee"] = original.model_copy(update={field: bad})
    assert_rejected(value, "employee_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", True),
        ("employee_id", "wrong"),
        ("employee_instructions", "wrong"),
        ("step_instructions", "wrong"),
        ("model", "wrong"),
        ("allowed_tool_names", ["tool-one", "tool-two"]),
        ("allowed_tool_names", ("tool-one", 4)),
    ],
)
def test_prepared_return_contract_is_strict_after_one_call(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = data(tmp_path)
    expected = prepared(data_set["workflow"], data_set["result"], data_set["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(expected, **{field: value})

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(data_set, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(data_set)


def test_prepared_allowed_tool_names_tuple_subclass_is_rejected_after_one_call(
    tmp_path: Path,
) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return replace(expected, allowed_tool_names=TupleChild(("tool-one", "tool-two")))

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("bad", [
    WorkflowProgressionDecision("workflow_complete", "w", "six", 6, "f", None, None, None, "last_step_succeeded"),
    PersistedExecutionOutcome("persisted_failure", "w", "four", 4, "d", "api_error"),
    object(),
])
def test_dependency_return_must_be_exact_prepared_step(tmp_path: Path, bad: object) -> None:
    value = data(tmp_path)

    def fake(*_: object) -> object:
        return bad

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"


def test_dependency_return_prepared_subclass_is_rejected_after_one_call(
    tmp_path: Path,
) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return PreparedChild(*expected.__dict__.values())

    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("bad", [
    PreparedWorkflowStep("w", "six", 6, "f", "employee instructions", "six", "model-name", ("tool-one", "tool-two")),
    PreparedStepExecutionStart(
        SimpleNamespace(model="model-name", system_instructions="employee instructions", task_instructions="six", allowed_tools=("tool-one", "tool-two")),
        WorkflowExecutionState("w", "running", "six", 6, "f", ("one", "two", "three", "four", "five"), None),
    ),
    WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1),
    RunningStatePersistenceResult(1),
    StepRuntimeExecutionSuccess("w", "six", 6, "f", ModelInvocationSuccess("openai", "response", "request", "completed", ("text",), "text")),
    StepRuntimeExecutionFailure("w", "six", 6, "f", ModelInvocationFailure("openai", "api_error", "safe", "request", None, None, None)),
    object(),
])
def test_direct_unsupported_inputs_are_result_type_zero_call(tmp_path: Path, bad: object) -> None:
    value = data(tmp_path)
    value["result"] = bad
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["subclass", "substitute"])
def test_prepare_result_subclass_and_fully_compatible_substitute_are_zero_call(
    tmp_path: Path, kind: str
) -> None:
    value = data(tmp_path)
    original = value["result"]
    value["result"] = (
        DecisionChild(*original.__dict__.values())
        if kind == "subclass"
        else SimpleNamespace(**original.__dict__)
    )
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["workflow", "step", "approval", "employee"])
def test_prepare_model_subclasses_are_zero_call(tmp_path: Path, kind: str) -> None:
    value = data(tmp_path)
    if kind == "workflow":
        value["workflow"] = WorkflowChild.model_validate(value["workflow"].model_dump())
    elif kind == "step":
        original = value["workflow"].steps
        value["workflow"] = value["workflow"].model_copy(
            update={"steps": [StepChild.model_validate(item.model_dump()) for item in original]}
        )
    elif kind == "approval":
        value["approval"] = ApprovalChild(*value["approval"].__dict__.values())
    else:
        value["employee"] = EmployeeChild.model_validate(value["employee"].model_dump())
    assert_rejected(value, "workflow_definition" if kind in {"workflow", "step"} else ("approval_contract" if kind == "approval" else "employee_contract"), lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["workflow", "step", "approval", "employee"])
def test_prepare_input_fully_attribute_compatible_substitutes_are_zero_call(
    tmp_path: Path, kind: str
) -> None:
    value = data(tmp_path)
    if kind == "workflow":
        value["workflow"] = SimpleNamespace(**value["workflow"].__dict__)
    elif kind == "step":
        original = value["workflow"]
        value["workflow"] = original.model_copy(
            update={"steps": [SimpleNamespace(**item.__dict__) for item in original.steps]}
        )
    elif kind == "approval":
        value["approval"] = SimpleNamespace(**value["approval"].__dict__)
    else:
        value["employee"] = SimpleNamespace(**value["employee"].__dict__)
    expected = (
        "workflow_definition"
        if kind in {"workflow", "step"}
        else "approval_contract" if kind == "approval" else "employee_contract"
    )
    assert_rejected(value, expected, lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_stop_routes_are_identity_preserving_zero_call_and_allow_nonopenai(
    tmp_path: Path, route: str
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    result = value["result"]
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(value, fake) is result
    assert calls == 0
    unchanged(value)


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("context", ["approval", "employee"])
def test_stop_routes_reject_non_none_context_before_dependency(
    tmp_path: Path, route: str, context: str
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    context_result = progression(value["workflow"])
    value[context] = (
        approval(context_result)
        if context == "approval"
        else employee(context_result)
    )
    assert_rejected(value, "completion_contract" if route == "completion" else "failure_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("kind", ["subclass", "substitute"])
def test_stop_result_subclasses_and_full_substitutes_are_result_type_zero_call(
    tmp_path: Path, route: str, kind: str
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    original = value["result"]
    if route == "completion":
        value["result"] = (
            DecisionChild(*original.__dict__.values())
            if kind == "subclass"
            else SimpleNamespace(**original.__dict__)
        )
    else:
        value["result"] = (
            OutcomeChild(*original.__dict__.values())
            if kind == "subclass"
            else SimpleNamespace(**original.__dict__)
        )
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("index", [True, IntChild(5)])
def test_stop_bool_and_int_subclass_indices_are_zero_call(
    tmp_path: Path, route: str, index: int
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    value["result"] = replace(value["result"], current_step_index=index)
    assert_rejected(
        value,
        "completion_contract" if route == "completion" else "failure_contract",
        lambda *_: pytest.fail("called"),
    )


def test_workflow_complete_stop_empty_terminal_output_is_rejected_zero_call(tmp_path: Path) -> None:
    value = completion_data(tmp_path, output="")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_stop_routes_preserve_empty_predecessor_output_zero_call(tmp_path: Path) -> None:
    for route in ("completion", "failure"):
        value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
        for position in range(1, 5):
            rewrite_event(value["events_path"], position - 1, output_text="")
        value["before_events"] = value["events_path"].read_bytes()
        result = value["result"]
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert invoke(value, fake) is result
        assert calls == 0
        unchanged(value)


def test_stop_routes_allow_non_openai_terminal_provider_zero_call(tmp_path: Path) -> None:
    for route in ("completion", "failure"):
        value = completion_data(tmp_path, provider="anthropic") if route == "completion" else failure_data(tmp_path, provider="anthropic")
        result = value["result"]
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert invoke(value, fake) is result
        assert calls == 0
        unchanged(value)


@pytest.mark.parametrize("field", [
    "decision", "workflow_id", "current_step_id", "current_step_index",
    "current_employee_id", "next_step_id", "next_step_index", "next_employee_id", "reason",
])
def test_completion_stop_result_fields_are_strict_zero_call(
    tmp_path: Path, field: str
) -> None:
    value = completion_data(tmp_path)
    original = value["result"]
    value["result"] = replace(
        original,
        **{field: True if field.endswith("index") else "wrong"},
    )
    expected = "decision_contract" if field == "decision" else "completion_contract"
    assert_rejected(value, expected, lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field", [
    "outcome", "workflow_id", "current_step_id", "current_step_index",
    "current_employee_id", "failure_category",
])
def test_failure_stop_result_fields_are_strict_zero_call(
    tmp_path: Path, field: str
) -> None:
    value = failure_data(tmp_path)
    original = value["result"]
    value["result"] = replace(
        original,
        **{field: "persisted_success" if field == "outcome" else True if field.endswith("index") else "wrong"},
    )
    expected = "result_type" if field == "outcome" else "failure_contract"
    assert_rejected(value, expected, lambda *_: pytest.fail("called"))


def test_direct_start_inputs_are_result_type_zero_call(tmp_path: Path) -> None:
    value = data(tmp_path)
    bad = PreparedStepExecutionStart(
        SimpleNamespace(
            model="model-name",
            system_instructions="employee instructions",
            task_instructions="six",
            allowed_tools=("tool-one", "tool-two"),
        ),
        WorkflowExecutionState("w", "running", "six", 6, "f", ("one", "two", "three", "four", "five"), None),
    )
    value["result"] = bad
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


def test_public_error_detail_has_only_safe_classification(tmp_path: Path) -> None:
    value = data(tmp_path)
    value["result"] = object()
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value, lambda *_: pytest.fail("called"))
    assert caught.value.detail == ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail("result_type")
    assert str(caught.value) == "progression to approved preparation cycle handoff chain bridge outer-chain inputs are incompatible"


def test_prepare_index_six_immediate_predecessor_none_request_id_delegates_once(
    tmp_path: Path,
) -> None:
    # Phase-155 provenance: seven-step workflow, step six succeeded, immediate
    # predecessor (step five) has request_id None.  Phase 145 must accept the
    # None request_id at index >= 6 for the immediate predecessor only.
    supplied_workflow = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{position}",
                    "name": f"Step {position}",
                    "employee": f"e{position}",
                    "instructions": f"step-{position}",
                }
                for position in range(1, 8)
            ],
        }
    )
    index = 6
    step = supplied_workflow.steps[index - 1]
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider="openai" if position == index - 1 else "other",
            request_id=None if position == index - 1 else "request",
        )
        for position, prior in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        step.id,
        index,
        step.employee,
        tuple(item.id for item in supplied_workflow.steps[:index]),
        None,
    )
    terminal = terminal_event(step, index)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = progression(supplied_workflow, index)
    value = {
        "result": result,
        "workflow": supplied_workflow,
        "approval": approval(result),
        "employee": employee(result),
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }
    expected = prepared(supplied_workflow, result, employee(result))
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)

    # --- Review-required narrowness subcases (inline, +0 collected) ---
    # (a) normal non-empty request IDs remain accepted (delegates once).
    rewrite_event(value["events_path"], 4, request_id="request")
    value["before_events"] = value["events_path"].read_bytes()
    calls = 0

    def fake_nonempty(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake_nonempty) is expected
    assert calls == 1
    unchanged(value)

    # (b) terminal request_id=None remains accepted (delegates once).
    rewrite_event(value["events_path"], 5, request_id=None)
    value["before_events"] = value["events_path"].read_bytes()
    calls = 0

    def fake_terminal_none(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake_terminal_none) is expected
    assert calls == 1
    unchanged(value)

    # (c) earlier (non-immediate) predecessor request_id=None is rejected at
    # index >= 6: the relaxation is immediate-predecessor-only.
    rewrite_event(value["events_path"], 0, request_id=None)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # Restore the earlier predecessor to its valid baseline before the next
    # subcase so each rejection proves exactly one independent bad condition.
    rewrite_event(value["events_path"], 0, request_id="request")

    # (d) immediate predecessor empty request_id is rejected at index >= 6 as
    # the sole bad condition (earlier predecessor back to baseline).
    rewrite_event(value["events_path"], 4, request_id="")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # Restore the immediate predecessor to its valid baseline.
    rewrite_event(value["events_path"], 4, request_id="request")

    # (e) immediate predecessor invalid request-id type is rejected as the
    # sole bad condition.
    rewrite_event(value["events_path"], 4, request_id=4)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_prepare_index_five_immediate_predecessor_none_request_id_rejected(
    tmp_path: Path,
) -> None:
    # Below index 6 the immediate-predecessor None request_id relaxation must
    # not apply: the strict nonempty request_id contract is preserved.
    value = data(tmp_path)
    rewrite_event(value["events_path"], 3, request_id=None)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # --- Review-required narrowness subcases (inline, +0 collected) ---
    # (a) workflow_complete stop route at index 6 accepts the immediate
    # predecessor request_id=None: exact identity with zero dependency calls.
    completed = completion_data(tmp_path)
    rewrite_event(completed["events_path"], 4, request_id=None)
    completed["before_events"] = completed["events_path"].read_bytes()
    calls = 0

    def forbidden_complete(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(completed, forbidden_complete) is completed["result"]
    assert calls == 0
    unchanged(completed)

    # (b) persisted_failure stop route at index 6 accepts the immediate
    # predecessor request_id=None: exact identity with zero dependency calls.
    failed = data(tmp_path, index=6, status="failed")
    rewrite_event(failed["events_path"], 4, request_id=None)
    failed["before_events"] = failed["events_path"].read_bytes()
    calls = 0

    def forbidden_failed(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(failed, forbidden_failed) is failed["result"]
    assert calls == 0
    unchanged(failed)


def _accumulated_workflow(steps: int = 9) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{position}",
                    "name": f"Step {position}",
                    "employee": f"e{position}",
                    "instructions": f"step-{position}",
                }
                for position in range(1, steps + 1)
            ],
        }
    )


def _accumulated_value(
    tmp_path: Path,
    *,
    index: int,
    none_positions: tuple[int, ...] = (5, 6),
    none_provider: object = "openai",
) -> dict[str, object]:
    """prepare route at `index` with accumulated openai None predecessors."""
    supplied_workflow = _accumulated_workflow()
    step = supplied_workflow.steps[index - 1]
    immediate = index - 1
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider=(
                "openai"
                if position in none_positions or position == immediate
                else "other"
            ),
            request_id=None if position in none_positions else "request",
        )
        for position, prior in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        step.id,
        index,
        step.employee,
        tuple(item.id for item in supplied_workflow.steps[:index]),
        None,
    )
    terminal = terminal_event(step, index, provider="openai")
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = progression(supplied_workflow, index)
    return {
        "result": result,
        "workflow": supplied_workflow,
        "approval": approval(result),
        "employee": employee(result),
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def _accumulated_stop_value(
    tmp_path: Path,
    *,
    fail: bool,
    none_positions: tuple[int, ...] = (5, 6),
) -> dict[str, object]:
    """A stop-route durable snapshot (workflow_complete or persisted_failure)
    with aged openai None predecessors at the requested positions."""
    supplied_workflow = _accumulated_workflow()
    index = 7 if fail else len(supplied_workflow.steps)
    step = supplied_workflow.steps[index - 1]
    immediate = index - 1
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider=(
                "openai"
                if position in none_positions or position == immediate
                else "other"
            ),
            request_id=None if position in none_positions else "request",
        )
        for position, prior in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    if fail:
        state = WorkflowExecutionState(
            "w",
            "failed",
            step.id,
            index,
            step.employee,
            tuple(item.id for item in supplied_workflow.steps[: index - 1]),
            "api_error",
        )
        terminal = terminal_event(step, index, status="failed", provider="openai")
        result: object = PersistedExecutionOutcome(
            "persisted_failure", "w", step.id, index, step.employee, "api_error"
        )
    else:
        state = WorkflowExecutionState(
            "w",
            "succeeded",
            step.id,
            index,
            step.employee,
            tuple(item.id for item in supplied_workflow.steps[:index]),
            None,
        )
        terminal = terminal_event(step, index, provider="openai")
        result = WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            step.id,
            index,
            step.employee,
            None,
            None,
            None,
            "last_step_succeeded",
        )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return {
        "result": result,
        "workflow": supplied_workflow,
        "approval": None,
        "employee": None,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def test_prepare_accumulated_openai_none_request_id_delegates_once(tmp_path: Path) -> None:
    # Issue #386: the prepared step-8 accepts a prepare route where step 7 is
    # the current succeeded step (index 7) and accumulated predecessors at
    # positions 5 and 6 both carry request_id=None + provider=openai.  The
    # dependency (the real Phase 137) is delegated exactly once with the
    # canonical six positional arguments, returns the exact prepared step, and
    # both durable targets are left byte-identical.
    value = _accumulated_value(tmp_path, index=7)
    result = value["result"]
    # acceptance contract: current step 7 succeeded, positions 5/6 None+openai,
    # prepare step 8.
    assert type(result) is WorkflowProgressionDecision
    assert result.current_step_index == 7
    assert result.next_step_index == 8
    events = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(value["state_path"], value["events_path"])
    ).events
    assert events[4].request_id is None and events[4].provider == "openai"
    assert events[5].request_id is None and events[5].provider == "openai"

    expected = prepared(value["workflow"], result, employee(result))
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake(*args: object, **kwargs: object) -> PreparedWorkflowStep:
        calls.append((args, kwargs))
        return expected

    assert invoke(value, fake) is expected
    # dependency delegated exactly once with the canonical six positional args
    # (result, workflow, approval, employee, state_path, events_path).
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert len(args) == 6
    assert args[0] is value["result"]
    assert args[1] is value["workflow"]
    assert args[2] is value["approval"]
    assert args[3] is value["employee"]
    assert args[4] is value["state_path"]
    assert args[5] is value["events_path"]
    assert kwargs == {}
    unchanged(value)


def test_prepare_accumulated_openai_none_request_id_narrowness(tmp_path: Path) -> None:
    # The accumulated-openai-None relaxation is only enabled on the prepare
    # route (not stop), only for exact request_id=None with provider=="openai"
    # at aged positions (>= 5).  Every rejected case must leave the durable
    # targets byte-identical and never call the dependency (zero-call).
    value = _accumulated_value(tmp_path, index=7)

    # (a) position 5 with provider != openai stays rejected.
    rewrite_event(value["events_path"], 4, provider="anthropic")
    value["before_events"] = value["events_path"].read_bytes()
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))
    rewrite_event(value["events_path"], 4, provider="openai")
    value["before_events"] = value["events_path"].read_bytes()

    # (b) position 5 with request_id="" stays rejected (neither None nor a
    # non-empty string).
    rewrite_event(value["events_path"], 4, request_id="")
    value["before_events"] = value["events_path"].read_bytes()
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))
    rewrite_event(value["events_path"], 4, request_id=None)
    value["before_events"] = value["events_path"].read_bytes()

    # (c) position 5 with a non-string / non-None request_id stays rejected.
    rewrite_event(value["events_path"], 4, request_id=12345)
    value["before_events"] = value["events_path"].read_bytes()
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))
    rewrite_event(value["events_path"], 4, request_id=None)
    value["before_events"] = value["events_path"].read_bytes()

    # (d) accumulated None at position 4 (below the aged threshold >= 5)
    # remains rejected even with an openai provider.
    rewrite_event(value["events_path"], 3, request_id=None)
    value["before_events"] = value["events_path"].read_bytes()
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))
    rewrite_event(value["events_path"], 3, request_id="request")
    value["before_events"] = value["events_path"].read_bytes()

    # (e) the pre-existing immediate-predecessor None (position 6 alone) is
    # still accepted: it does not rely on the accumulated relaxation.
    immediate_only = _accumulated_value(tmp_path, index=7, none_positions=(6,))
    expected_immediate = prepared(
        immediate_only["workflow"],
        immediate_only["result"],
        employee(immediate_only["result"]),
    )
    immediate_calls = 0

    def immediate_fake(*_: object) -> PreparedWorkflowStep:
        nonlocal immediate_calls
        immediate_calls += 1
        return expected_immediate

    assert invoke(immediate_only, immediate_fake) is expected_immediate
    assert immediate_calls == 1
    unchanged(immediate_only)

    # (f) the stop routes remain strict: an aged openai None at positions 5
    # and 6 is still rejected on workflow_complete and persisted_failure
    # (the relaxation is gated ``not stop``), with zero dependency calls.
    complete = _accumulated_stop_value(tmp_path / "complete", fail=False)
    assert_rejected(complete, "terminal_contract", lambda *_: pytest.fail("called"))
    failed = _accumulated_stop_value(tmp_path / "failed", fail=True)
    assert_rejected(failed, "terminal_contract", lambda *_: pytest.fail("called"))
