"""Focused tests for the Phase 135 persisted-transition outer bridge."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError,
    PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationError,
    WorkflowProgressionDecision,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary as public_phase135,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationError as Phase135Error,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationFailureDetail as Phase135FailureDetail,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError as PublicPhase135CompatibilityError,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationCompatibilityError as Phase128CompatibilityError,
)
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary as public_phase128,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.storage.running_state_persistence import RunningStatePersistenceResult


class PersistenceChild(WorkflowExecutionPersistenceResult):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


class DecisionChild(WorkflowProgressionDecision):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class IntChild(int):
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
            ],
        }
    )


def predecessor_event(
    step_id: str, step_index: int, employee: str, provider: object = "other", **changes: object
) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded", "w", step_id, step_index, employee, "running", "succeeded",
            provider, None, f"response-{step_id}", f"request-{step_id}", f"output-{step_id}", None,
        ),
        **changes,
    )


def terminal_event(status: str, provider: object = "openai", **changes: object) -> RuntimeStepEvent:
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w", "four", 4, "d", "running", status, provider,
        None if status == "succeeded" else "api_error",
        "response-four" if status == "succeeded" else None,
        "request-four",
        "output-four" if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )
    return replace(event, **changes)


def runtime_success(*, output_text: str = "output-four") -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w", "four", 4, "d",
        ModelInvocationSuccess("openai", "response-four", "request-four", "completed", ("output",), output_text),
    )


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w", "four", 4, "d",
        ModelInvocationFailure("openai", "api_error", "safe failure", "request-four", 500, None, None),
    )


def write_terminal_targets(tmp_path: Path, status: str, *, provider: object = "openai") -> tuple[Path, Path, bytes, bytes]:
    state = WorkflowExecutionState(
        "w", status, "four", 4, "d",
        ("one", "two", "three", "four") if status == "succeeded" else ("one", "two", "three"),
        None if status == "succeeded" else "api_error",
    )
    predecessors = tuple(
        predecessor_event(step.id, index, step.employee, "other" if index < 3 else "openai")
        for index, step in enumerate(workflow().steps[:3], 1)
    )
    terminal = terminal_event(status, provider)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def persistence_result(state: Path, events: Path) -> WorkflowExecutionPersistenceResult:
    terminal_line = events.read_bytes().splitlines(keepends=True)[-1]
    return WorkflowExecutionPersistenceResult(state, events, len(state.read_bytes()), len(terminal_line))


def values(tmp_path: Path, status: str = "succeeded") -> dict[str, object]:
    state, events, before_state, before_events = write_terminal_targets(tmp_path, status)
    return {
        "result": persistence_result(state, events),
        "workflow": workflow(),
        "state_path": state,
        "events_path": events,
        "before_state": before_state,
        "before_events": before_events,
    }


def expected_outcome(status: str = "succeeded") -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "four", 4, "d", None if status == "succeeded" else "api_error",
    )


def _arguments(data: dict[str, object]) -> dict[str, object]:
    return {key: data[key] for key in ("result", "workflow", "state_path", "events_path")}


def call(data: dict[str, object], dependency: object) -> object:
    supplied = _arguments(data)
    supplied["phase128_function"] = dependency
    return public_phase135(**supplied)  # type: ignore[arg-type]


def reject(data: dict[str, object], classification: str, **changes: object) -> None:
    supplied = _arguments(data)
    supplied.update(changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    supplied["phase128_function"] = dependency
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        public_phase135(**supplied)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    assert caught.value.detail.classification == classification
    assert calls == 0


def reject_after_call(data: dict[str, object], dependency: object, classification: str) -> None:
    before = data["state_path"].read_bytes(), data["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)  # type: ignore[operator]

    supplied = _arguments(data)
    supplied["phase128_function"] = counted
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        public_phase135(**supplied)  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification
    assert calls == 1
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def reject_after_phase128_error(data: dict[str, object], dependency: object, classification: str) -> None:
    before = data["state_path"].read_bytes(), data["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*args)  # type: ignore[operator]

    with pytest.raises(Phase128CompatibilityError) as caught:
        public_phase135(**_arguments(data), phase128_function=counted)  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification
    assert calls == 1
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def valid_dependency(*args: object) -> PersistedExecutionOutcome:
    result = args[0]
    return expected_outcome("succeeded" if result is not None else "succeeded")


def stop_values(tmp_path: Path, kind: str, provider: object = "other") -> tuple[dict[str, object], object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    state, events, before_state, before_events = write_terminal_targets(
        tmp_path, "succeeded" if kind == "complete" else "failed", provider=provider
    )
    result: object = (
        WorkflowProgressionDecision("workflow_complete", "w", "four", 4, "d", None, None, None, "last_step_succeeded")
        if kind == "complete"
        else PersistedExecutionOutcome("persisted_failure", "w", "four", 4, "d", "api_error")
    )
    return {
        "result": result,
        "workflow": workflow(),
        "state_path": state,
        "events_path": events,
        "before_state": before_state,
        "before_events": before_events,
    }, result


def assert_unchanged(data: dict[str, object]) -> None:
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == (  # type: ignore[union-attr]
        data["before_state"], data["before_events"]
    )


def test_public_signature_default_and_source_audit() -> None:
    signature = inspect.signature(public_phase135)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [p.kind for p in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert all(p.annotation is object for p in parameters[:4])
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase128_function"
    assert parameters[4].default is public_phase128
    source = Path("src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary.py").read_text(encoding="utf-8")
    assert "route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary" in source
    assert "phase121" not in source.lower()
    assert "route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source and "._top" not in source and "._raise" not in source


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_classification_routes_call_phase128_once_canonically_and_preserve_identity(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    expected = expected_outcome(status)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        assert tuple(args) == (data["result"], data["workflow"], data["state_path"], data["events_path"])
        return expected

    assert call(data, dependency) is expected
    assert calls == [(data["result"], data["workflow"], data["state_path"], data["events_path"])]
    assert_unchanged(data)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_early_continuation_indices_delegate_once(tmp_path: Path, index: int) -> None:
    supplied_workflow = workflow()
    for current_index in (index, len(supplied_workflow.steps)):
        for status in ("succeeded", "failed"):
            case_dir = tmp_path / f"step-{current_index}-{status}"
            case_dir.mkdir()
            selected = supplied_workflow.steps[current_index - 1]
            state_model = WorkflowExecutionState(
                "w",
                status,
                selected.id,
                current_index,
                selected.employee,
                tuple(
                    step.id
                    for step in supplied_workflow.steps[
                        : current_index if status == "succeeded" else current_index - 1
                    ]
                ),
                None if status == "succeeded" else "api_error",
            )
            predecessors = tuple(
                predecessor_event(
                    step.id,
                    position,
                    step.employee,
                    "openai" if position == current_index - 1 else "other",
                )
                for position, step in enumerate(
                    supplied_workflow.steps[: current_index - 1], 1
                )
            )
            terminal = terminal_event(
                status,
                step_id=selected.id,
                step_index=current_index,
                employee_id=selected.employee,
                request_id=f"request-{selected.id}",
                response_id=(
                    f"response-{selected.id}" if status == "succeeded" else None
                ),
                output_text=(
                    f"output-{selected.id}" if status == "succeeded" else None
                ),
                failure_category=(None if status == "succeeded" else "api_error"),
                message=(None if status == "succeeded" else "safe failure"),
            )
            state_bytes = serialize_workflow_execution_state_json(state_model).encode()
            event_bytes = b"".join(
                serialize_runtime_step_event_jsonl(event).encode()
                for event in (*predecessors, terminal)
            )
            terminal_bytes = serialize_runtime_step_event_jsonl(terminal).encode()
            state_path, events_path = case_dir / "state", case_dir / "events"
            state_path.write_bytes(state_bytes)
            events_path.write_bytes(event_bytes)
            data = {
                "result": WorkflowExecutionPersistenceResult(
                    state_path, events_path, len(state_bytes), len(terminal_bytes)
                ),
                "workflow": supplied_workflow,
                "state_path": state_path,
                "events_path": events_path,
                "before_state": state_bytes,
                "before_events": event_bytes,
            }
            expected = PersistedExecutionOutcome(
                "persisted_success"
                if status == "succeeded"
                else "persisted_failure",
                "w",
                selected.id,
                current_index,
                selected.employee,
                None if status == "succeeded" else "api_error",
            )
            calls: list[tuple[object, ...]] = []

            def dependency(*args: object) -> object:
                calls.append(args)
                assert all(
                    actual is wanted
                    for actual, wanted in zip(
                        args,
                        (data["result"], data["workflow"], data["state_path"], data["events_path"]),
                        strict=True,
                    )
                )
                return expected

            assert call(data, dependency) is expected
            assert len(calls) == 1
            assert_unchanged(data)


@pytest.mark.parametrize("bad", [object(), SimpleNamespace(), WorkflowProgressionDecision("prepare_next_step", "w", "four", 4, "d", None, None, None, "next_step_available"), PersistedExecutionOutcome("persisted_success", "w", "four", 4, "d", None), StepRuntimeExecutionSuccess("w", "four", 4, "d", ModelInvocationSuccess("openai", "r", None, "completed", (), "")), StepRuntimeExecutionFailure("w", "four", 4, "d", ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None)), RunningStatePersistenceResult(1), PreparedStepExecutionStart(SimpleNamespace(), SimpleNamespace())])
def test_unsupported_direct_inputs_are_result_type_zero_call(tmp_path: Path, bad: object) -> None:
    classification = (
        "completion_contract" if type(bad) is WorkflowProgressionDecision
        else "failure_contract" if type(bad) is PersistedExecutionOutcome
        else "result_type"
    )
    reject(values(tmp_path), classification, result=bad)


def test_exact_workflow_and_persistence_models_are_required(tmp_path: Path) -> None:
    data = values(tmp_path)
    result = data["result"]
    p = result  # type: ignore[assignment]
    substitute = SimpleNamespace(state_path=p.state_path, events_path=p.events_path, state_bytes_written=p.state_bytes_written, event_bytes_appended=p.event_bytes_appended)
    persistence_child = PersistenceChild(p.state_path, p.events_path, p.state_bytes_written, p.event_bytes_appended)
    for bad in (persistence_child, substitute):
        reject(data, "result_type", result=bad)
    child = WorkflowChild.model_validate(workflow().model_dump())
    compatible = SimpleNamespace(id="w", name="W", description="D", steps=workflow().steps)
    for bad in (child, compatible):
        reject(data, "workflow_definition", workflow=bad)


def test_workflow_step_subclass_and_compatible_substitute_are_rejected(tmp_path: Path) -> None:
    data = values(tmp_path)
    for step in (StepChild(id="one", name="One", employee="a", instructions="one"), SimpleNamespace(id="one", name="One", employee="a", instructions="one")):
        candidate = WorkflowDefinition.model_construct(id="w", name="W", description="D", steps=[step, *workflow().steps[1:]])
        reject(data, "workflow_definition", workflow=candidate)


@pytest.mark.parametrize("field,value", [("id", 1), ("name", 1), ("description", 1), ("steps", tuple())])
def test_workflow_fields_are_exact(tmp_path: Path, field: str, value: object) -> None:
    candidate = WorkflowDefinition.model_construct(**(workflow().model_dump() | {field: value}))
    reject(values(tmp_path), "workflow_definition", workflow=candidate)


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_persistence_target_identity_is_exact(tmp_path: Path, field: str) -> None:
    data = values(tmp_path)
    result = data["result"]
    bad = replace(result, **{field: Path("different")})
    reject(data, "persistence_contract", result=bad)


@pytest.mark.parametrize("field,value", [("state_bytes_written", True), ("state_bytes_written", IntChild(1)), ("state_bytes_written", 0), ("state_bytes_written", -1), ("state_bytes_written", 1.0), ("event_bytes_appended", True), ("event_bytes_appended", IntChild(1)), ("event_bytes_appended", 0), ("event_bytes_appended", -1), ("event_bytes_appended", 1.0)])
def test_persistence_counts_require_exact_positive_builtin_int_before_phase128(tmp_path: Path, field: str, value: object) -> None:
    data = values(tmp_path)
    reject(data, "persistence_contract", result=replace(data["result"], **{field: value}))


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_positive_but_wrong_persistence_counts_are_rejected_before_phase128(tmp_path: Path, field: str) -> None:
    data = values(tmp_path)
    reject(data, "persistence_contract", result=replace(data["result"], **{field: getattr(data["result"], field) + 1}))


def _history_matrix_case(tmp_path: Path, mode: str) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_text().splitlines(keepends=True)
    if mode == "duplicate":
        content = lines[0] + lines[0] + lines[1] + lines[2] + lines[3]
    elif mode == "missing":
        content = lines[0] + lines[1] + lines[3]
    elif mode == "reordered":
        content = lines[1] + lines[0] + lines[2] + lines[3]
    elif mode == "unrelated":
        content = serialize_runtime_step_event_jsonl(predecessor_event("wrong", 99, "x")) + "".join(lines[1:])
    elif mode == "malformed":
        content = "{malformed}\n"
    else:
        content = "".join(lines) + lines[3]
    events.write_text(content)
    reject(data, "persistence_contract")


@pytest.mark.parametrize("mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_history_matrix_cases(tmp_path: Path, mode: str) -> None:
    _history_matrix_case(tmp_path, mode)


def _replace_terminal(data: dict[str, object], event: RuntimeStepEvent) -> None:
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    appended = serialize_runtime_step_event_jsonl(event).encode("utf-8")
    events.write_bytes(b"".join(lines[:-1]) + appended)  # type: ignore[union-attr]


def _replace_state(data: dict[str, object], state: WorkflowExecutionState) -> None:
    data["state_path"].write_bytes(serialize_workflow_execution_state_json(state).encode())  # type: ignore[union-attr]


@pytest.mark.parametrize("provider", ["other", 4])
def test_immediate_predecessor_provider_contract_is_strict(tmp_path: Path, provider: object) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(predecessor_event("three", 3, "c", provider)).encode()
    events.write_bytes(b"".join(lines[:2]) + replacement + lines[3])  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("provider", ["", 4])
def test_earlier_predecessor_provider_must_be_nonempty_builtin_string(tmp_path: Path, provider: object) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(predecessor_event("one", 1, "a", provider)).encode()
    events.write_bytes(replacement + b"".join(lines[1:]))  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("request_id", [None, "", 4])
def test_predecessor_request_id_contract_is_strict(tmp_path: Path, request_id: object) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(predecessor_event("three", 3, "c", request_id=request_id)).encode()
    events.write_bytes(b"".join(lines[:2]) + replacement + lines[3])  # type: ignore[union-attr]
    reject(data, "persistence_contract")


def test_earlier_non_openai_predecessor_is_accepted(tmp_path: Path) -> None:
    data = values(tmp_path)
    expected = expected_outcome()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(data, dependency) is expected
    assert calls == 1


def test_empty_success_terminal_output_is_valid_on_phase135_route(tmp_path: Path) -> None:
    data = values(tmp_path)
    lines = data["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    empty = serialize_runtime_step_event_jsonl(terminal_event("succeeded", output_text="")).encode()
    event_bytes = b"".join(lines[:3]) + empty
    data["events_path"].write_bytes(event_bytes)  # type: ignore[union-attr]
    data["result"] = replace(data["result"], event_bytes_appended=len(empty))
    data["before_events"] = event_bytes
    expected = expected_outcome()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(data, dependency) is expected
    assert calls == 1
    assert_unchanged(data)


@pytest.mark.parametrize(
    ("state_field", "event_field"),
    [("current_step_id", "step_id"), ("current_employee_id", "employee_id")],
)
def test_terminal_state_and_event_must_link_to_workflow_current_step(
    tmp_path: Path, state_field: str, event_field: str
) -> None:
    data = values(tmp_path)
    state_payload = json.loads(data["state_path"].read_text())  # type: ignore[union-attr]
    state_payload[state_field] = "wrong"
    data["state_path"].write_text(  # type: ignore[union-attr]
        json.dumps(state_payload, separators=(",", ":")) + "\n"
    )
    lines = data["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    terminal_payload = json.loads(lines[-1])
    terminal_payload[event_field] = "wrong"
    lines[-1] = (json.dumps(terminal_payload, separators=(",", ":")) + "\n").encode()
    data["events_path"].write_bytes(b"".join(lines))  # type: ignore[union-attr]
    data["result"] = replace(
        data["result"],
        state_bytes_written=len(data["state_path"].read_bytes()),  # type: ignore[union-attr]
        event_bytes_appended=len(lines[-1]),
    )
    before = data["state_path"].read_bytes(), data["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected_outcome()

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 0
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def test_workflow_complete_stop_rejects_empty_success_output_without_phase128_call(
    tmp_path: Path,
) -> None:
    data, result = stop_values(tmp_path, "complete", provider="other")
    lines = data["events_path"].read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    empty_terminal = serialize_runtime_step_event_jsonl(
        terminal_event("succeeded", provider="other", output_text="")
    ).encode()
    lines[-1] = empty_terminal
    data["events_path"].write_bytes(b"".join(lines))  # type: ignore[union-attr]
    before = data["state_path"].read_bytes(), data["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("field,value", [("provider", "other"), ("provider", 4), ("request_id", ""), ("request_id", 4), ("response_id", ""), ("response_id", None), ("output_text", None), ("message", "bad")])
def test_success_terminal_contract_is_strict(tmp_path: Path, field: str, value: object) -> None:
    data = values(tmp_path)
    _replace_terminal(data, terminal_event("succeeded", **{field: value}))
    reject(data, "persistence_contract")


@pytest.mark.parametrize("field,value", [("provider", "other"), ("provider", 4), ("request_id", ""), ("request_id", 4), ("failure_category", None), ("failure_category", "invalid_request"), ("response_id", "response"), ("output_text", "output"), ("message", "")])
def test_failure_terminal_contract_is_strict(tmp_path: Path, field: str, value: object) -> None:
    data = values(tmp_path, "failed")
    _replace_terminal(data, terminal_event("failed", **{field: value}))
    reject(data, "persistence_contract")


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"}, {"step_id": "other"}, {"step_index": 3},
        {"employee_id": "other"}, {"status": "running"}, {"current_step_id": "other"},
        {"current_step_index": True},
        {"current_employee_id": "other"},
        {"completed_step_ids": ["one", "two"]}, {"completed_step_ids": ["one", "two", "wrong", "four"]},
        {"last_failure_category": "api_error"},
    ],
)
def test_succeeded_persisted_state_contract_is_strict(tmp_path: Path, changes: dict[str, object]) -> None:
    data = values(tmp_path, "succeeded")
    payload = json.loads(data["state_path"].read_text())  # type: ignore[union-attr]
    payload.update(changes)
    data["state_path"].write_text(json.dumps(payload, separators=(",", ":")) + "\n")  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"}, {"status": "succeeded"}, {"current_step_id": "other"},
        {"current_step_index": True}, {"current_employee_id": "other"},
        {"completed_step_ids": ["one", "two", "wrong"]},
        {"last_failure_category": None}, {"last_failure_category": "invalid_request"},
    ],
)
def test_failed_persisted_state_contract_is_strict(tmp_path: Path, changes: dict[str, object]) -> None:
    data = values(tmp_path, "failed")
    payload = json.loads(data["state_path"].read_text())  # type: ignore[union-attr]
    payload.update(changes)
    data["state_path"].write_text(json.dumps(payload, separators=(",", ":")) + "\n")  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("index", [True, IntChild(4)])
def test_loaded_persisted_state_index_requires_exact_builtin_int(tmp_path: Path, index: object, monkeypatch: pytest.MonkeyPatch) -> None:
    data = values(tmp_path)
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(data["state_path"], data["events_path"])  # type: ignore[arg-type]
    )
    malformed = replace(loaded.state, current_step_index=index)

    def fake_loader(_targets: object) -> LoadedWorkflowExecutionHistory:
        return LoadedWorkflowExecutionHistory(malformed, loaded.events)

    import ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary as phase135_module

    monkeypatch.setattr(phase135_module, "load_workflow_execution_history", fake_loader)
    reject(data, "persistence_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_malformed_persisted_state_bytes_are_rejected_before_phase128(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    data["state_path"].write_bytes(b"not-json\n")  # type: ignore[union-attr]
    reject(data, "persistence_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_invalid_persisted_terminal_state_side_effect_is_compensated(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    before = data["state_path"].read_bytes(), data["events_path"].read_bytes()  # type: ignore[union-attr]

    def dependency(*_: object) -> object:
        bad = WorkflowExecutionState("other", status, "four", 4, "d", ("one", "two", "three", "four") if status == "succeeded" else ("one", "two", "three"), None if status == "succeeded" else "api_error")
        data["state_path"].write_bytes(serialize_workflow_execution_state_json(bad).encode())  # type: ignore[union-attr]
        raise Phase128CompatibilityError("persistence_contract")

    reject_after_phase128_error(data, dependency, "persistence_contract")
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("status", ["succeeded", "failed"])
@pytest.mark.parametrize("field", ["workflow_id", "current_step_id", "current_step_index", "current_employee_id", "completed_step_ids", "last_failure_category"])
def test_invalid_persisted_state_side_effect_matrix_is_compensated(tmp_path: Path, status: str, field: str) -> None:
    data = values(tmp_path, status)
    state = WorkflowExecutionState(
        "w", status, "four", 4, "d",
        ("one", "two", "three", "four") if status == "succeeded" else ("one", "two", "three"),
        None if status == "succeeded" else "api_error",
    )
    bad_values = {
        "workflow_id": "other", "current_step_id": "other", "current_step_index": 3, "current_employee_id": "other",
        "completed_step_ids": ("one", "two", "wrong", "four") if status == "succeeded" else ("one", "two", "wrong"),
        "last_failure_category": "api_error" if status == "succeeded" else "invalid_request",
    }

    def dependency(*_: object) -> object:
        data["state_path"].write_bytes(serialize_workflow_execution_state_json(replace(state, **{field: bad_values[field]})).encode())  # type: ignore[union-attr]
        raise Phase128CompatibilityError("persistence_contract")

    reject_after_phase128_error(data, dependency, "persistence_contract")


@pytest.mark.parametrize("mutation", ["remove", "duplicate", "reorder", "rewrite", "missing_terminal", "two_terminal", "malformed_terminal", "extra"])
def test_event_prefix_and_append_invariants_are_compensated(tmp_path: Path, mutation: str) -> None:
    data = values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    unrelated = serialize_runtime_step_event_jsonl(predecessor_event("unrelated", 99, "x")).encode()

    def dependency(*_: object) -> object:
        if mutation == "remove":
            content = lines[0] + lines[2] + lines[3]
        elif mutation == "duplicate":
            content = lines[0] + lines[0] + lines[1] + lines[2] + lines[3]
        elif mutation == "reorder":
            content = lines[1] + lines[0] + lines[2] + lines[3]
        elif mutation == "rewrite":
            content = serialize_runtime_step_event_jsonl(predecessor_event("wrong", 2, "b")).encode() + lines[1] + lines[2] + lines[3]
        elif mutation == "missing_terminal":
            content = b"".join(lines[:3])
        elif mutation == "two_terminal":
            content = b"".join(lines) + lines[3]
        elif mutation == "malformed_terminal":
            content = b"".join(lines[:3]) + b"{bad-json}\n"
        else:
            content = b"".join(lines) + unrelated
        events.write_bytes(content)
        raise Phase128CompatibilityError("persistence_contract")

    reject_after_phase128_error(data, dependency, "persistence_contract")


def test_event_bytes_wrong_positive_is_rejected_after_dependency(tmp_path: Path) -> None:
    data = values(tmp_path)

    def dependency(*_: object) -> object:
        raise Phase128CompatibilityError("persistence_contract")

    reject_after_phase128_error(data, dependency, "persistence_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_dependency_returns_exact_outcome_by_identity(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    expected = expected_outcome(status)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(data, dependency) is expected
    assert calls == 1
    assert_unchanged(data)


@pytest.mark.parametrize(
    "bad_factory",
    [
        lambda expected: OutcomeChild(expected.outcome, expected.workflow_id, expected.current_step_id, expected.current_step_index, expected.current_employee_id, expected.failure_category),
        lambda expected: SimpleNamespace(outcome=expected.outcome, workflow_id=expected.workflow_id, current_step_id=expected.current_step_id, current_step_index=expected.current_step_index, current_employee_id=expected.current_employee_id, failure_category=expected.failure_category),
        lambda expected: object(),
    ],
)
def test_dependency_outcome_must_be_exact_model(tmp_path: Path, bad_factory: object) -> None:
    data = values(tmp_path)
    expected = expected_outcome()

    def dependency(*_: object) -> object:
        return bad_factory(expected)  # type: ignore[operator]

    reject_after_call(data, dependency, "outcome_contract")


@pytest.mark.parametrize(
    "field,value",
    [("outcome", "persisted_failure"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", 3), ("current_step_index", True), ("current_employee_id", "other"), ("failure_category", "api_error")],
)
def test_dependency_outcome_linkage_and_exact_fields_are_revalidated(tmp_path: Path, field: str, value: object) -> None:
    data = values(tmp_path)
    bad = replace(expected_outcome(), **{field: value})
    reject_after_call(data, lambda *_: bad, "outcome_contract")


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase128_error_identity_is_preserved_after_compensation(tmp_path: Path, mutation: str) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    supplied_error = PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationError("safe detail")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")  # type: ignore[union-attr]
        raise supplied_error

    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainReentryContinuationError) as caught:
        call(data, dependency)
    assert caught.value is supplied_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase128_error_is_sanitized_and_compensated(tmp_path: Path, mutation: str) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")  # type: ignore[union-attr]
        raise RuntimeError("secret detail")

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_outcome_after_target_mutation_is_rejected_and_compensated(tmp_path: Path, mutation: str) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")  # type: ignore[union-attr]
        return expected_outcome()

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_return_with_target_mutation_is_compensated_without_retry(tmp_path: Path, mutation: str) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    before = state.read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")  # type: ignore[union-attr]
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")  # type: ignore[union-attr]
        return object()

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "outcome_contract"
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    data = values(tmp_path)
    state, events = data["state_path"], data["events_path"]
    original_write = Path.write_bytes
    restore_calls = {"state": 0, "events": 0}
    dependency_calls = 0

    def restore(path: Path, content: bytes) -> int:
        key = "state" if path == state else "events"
        restore_calls[key] += 1
        if failed_target in (key, "both"):
            raise OSError("rollback")
        return original_write(path, content)

    monkeypatch.setattr(Path, "write_bytes", restore)

    def dependency(*_: object) -> object:
        nonlocal dependency_calls
        dependency_calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert restore_calls == {"state": 1, "events": 1}
    assert dependency_calls == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_preserve_identity_allow_nonopenai_and_call_zero(tmp_path: Path, kind: str) -> None:
    data, result = stop_values(tmp_path, kind, provider="other")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert call(data, dependency) is result
    assert calls == 0
    assert_unchanged(data)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_result_subclasses_are_rejected_zero_call(tmp_path: Path, kind: str) -> None:
    data, result = stop_values(tmp_path, kind)
    child: object = (
        DecisionChild(result.decision, result.workflow_id, result.current_step_id, result.current_step_index, result.current_employee_id, result.next_step_id, result.next_step_index, result.next_employee_id, result.reason)  # type: ignore[union-attr]
        if kind == "complete"
        else OutcomeChild(result.outcome, result.workflow_id, result.current_step_id, result.current_step_index, result.current_employee_id, result.failure_category)  # type: ignore[union-attr]
    )
    reject(data, "result_type", result=child)
    assert_unchanged(data)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_fully_compatible_substitutes_are_rejected_zero_call(tmp_path: Path, kind: str) -> None:
    data, result = stop_values(tmp_path, kind)
    substitute = SimpleNamespace(**result.__dict__)
    reject(data, "result_type", result=substitute)
    assert_unchanged(data)


@pytest.mark.parametrize("kind", ["complete", "failure"])
@pytest.mark.parametrize("index", [True, IntChild(4)])
def test_stop_index_bool_and_int_subclass_are_zero_call(tmp_path: Path, kind: str, index: object) -> None:
    data, result = stop_values(tmp_path, kind)
    classification = "completion_contract" if kind == "complete" else "failure_contract"
    reject(data, classification, result=replace(result, current_step_index=index))
    assert_unchanged(data)


def test_stop_malformed_and_unsupported_values_are_zero_call(tmp_path: Path) -> None:
    data, result = stop_values(tmp_path, "complete")
    reject(data, "completion_contract", result=replace(result, reason="wrong"))
    data, result = stop_values(tmp_path / "failure", "failure")
    reject(data, "terminal_contract", result=replace(result, failure_category="invalid_request"))
    data, _ = stop_values(tmp_path / "unsupported", "complete")
    reject(data, "completion_contract", result=WorkflowProgressionDecision("prepare_next_step", "w", "four", 4, "d", None, None, None, "next_step_available"))


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_terminal_provider_nonstring_is_rejected_without_dependency(tmp_path: Path, kind: str) -> None:
    data, result = stop_values(tmp_path, kind, provider=4)
    reject(data, "terminal_contract", result=result)
    assert_unchanged(data)


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_zero_call(tmp_path: Path, target: str) -> None:
    data = values(tmp_path)
    path = data[target]
    path.unlink()  # type: ignore[union-attr]
    reject(data, "state_target" if target == "state_path" else "event_target")
    path.mkdir()  # type: ignore[union-attr]
    reject(data, "state_target" if target == "state_path" else "event_target")


def test_target_conflict_noncallable_and_path_type_are_zero_call(tmp_path: Path) -> None:
    data = values(tmp_path)
    reject(data, "target_conflict", events_path=data["state_path"])
    reject(data, "state_target", state_path="state")
    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        public_phase135(**_arguments(data), phase128_function=object())  # type: ignore[arg-type]
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_target_oserrors_are_classified_before_phase128(tmp_path: Path, operation: str, target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    data = values(tmp_path)
    selected = data[target]
    original = getattr(Path, operation)

    def raising(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, raising)
    reject(data, "state_target" if target == "state_path" else "event_target")


def _replace_predecessor(data: dict[str, object], position: int, event: RuntimeStepEvent) -> None:
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    lines[position - 1] = serialize_runtime_step_event_jsonl(event).encode()
    events.write_bytes(b"".join(lines))  # type: ignore[union-attr]
    data["before_events"] = events.read_bytes()  # type: ignore[union-attr]


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_empty_output_text_delegates_once_canonical_order(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    _replace_predecessor(data, 3, predecessor_event("three", 3, "c", "openai", output_text=""))
    expected = expected_outcome(status)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    assert call(data, dependency) is expected
    assert calls == [(data["result"], data["workflow"], data["state_path"], data["events_path"])]
    assert_unchanged(data)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_earlier_empty_output_text_survives_later_succeeded_predecessor(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    _replace_predecessor(data, 1, predecessor_event("one", 1, "a", "other", output_text=""))
    expected = expected_outcome(status)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(data, dependency) is expected
    assert calls == 1
    assert_unchanged(data)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_predecessor_nonempty_output_text_remains_accepted(tmp_path: Path, status: str) -> None:
    data = values(tmp_path, status)
    expected = expected_outcome(status)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(data, dependency) is expected
    assert calls == 1
    assert_unchanged(data)


@pytest.mark.parametrize("output_text", [4, None, ["output"]])
def test_predecessor_output_text_non_string_is_rejected_before_phase128(tmp_path: Path, output_text: object) -> None:
    data = values(tmp_path)
    _replace_predecessor(data, 3, predecessor_event("three", 3, "c", "openai", output_text=output_text))
    reject(data, "persistence_contract")


@pytest.mark.parametrize("response_id", ["", None])
def test_predecessor_empty_output_text_still_requires_response_id(tmp_path: Path, response_id: object) -> None:
    data = values(tmp_path)
    _replace_predecessor(data, 3, predecessor_event("three", 3, "c", "openai", output_text="", response_id=response_id))
    reject(data, "persistence_contract")


@pytest.mark.parametrize("request_id", [None, ""])
def test_predecessor_empty_output_text_still_requires_request_id(tmp_path: Path, request_id: object) -> None:
    data = values(tmp_path)
    _replace_predecessor(data, 3, predecessor_event("three", 3, "c", "openai", output_text="", request_id=request_id))
    if request_id is None:
        expected = expected_outcome()
        calls: list[tuple[object, ...]] = []

        def dependency(*args: object) -> object:
            calls.append(args)
            return expected

        assert call(data, dependency) is expected
        assert calls == [
            (data["result"], data["workflow"], data["state_path"], data["events_path"])
        ]
        assert_unchanged(data)
    else:
        reject(data, "persistence_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_none_request_id_empty_output_delegates(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    _replace_predecessor(
        data, 3, predecessor_event("three", 3, "c", "openai", output_text="", request_id=None)
    )
    expected = expected_outcome(status)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    assert call(data, dependency) is expected
    assert calls == [
        (data["result"], data["workflow"], data["state_path"], data["events_path"])
    ]
    assert_unchanged(data)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_immediate_predecessor_none_request_id_nonempty_output_delegates(
    tmp_path: Path, status: str
) -> None:
    data = values(tmp_path, status)
    _replace_predecessor(
        data, 3, predecessor_event("three", 3, "c", "openai", request_id=None)
    )
    expected = expected_outcome(status)
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    assert call(data, dependency) is expected
    assert calls == [
        (data["result"], data["workflow"], data["state_path"], data["events_path"])
    ]
    assert_unchanged(data)


def test_earlier_predecessor_none_request_id_is_rejected_before_phase128(
    tmp_path: Path,
) -> None:
    data = values(tmp_path)
    _replace_predecessor(
        data,
        2,
        predecessor_event("two", 2, "b", "openai", request_id=None),
    )
    reject(data, "persistence_contract")


def test_immediate_predecessor_empty_request_id_is_rejected(tmp_path: Path) -> None:
    data = values(tmp_path)
    _replace_predecessor(
        data, 3, predecessor_event("three", 3, "c", "openai", request_id="")
    )
    reject(data, "persistence_contract")


@pytest.mark.parametrize("provider", ["other", 4])
def test_immediate_predecessor_empty_output_text_still_requires_openai_provider(tmp_path: Path, provider: object) -> None:
    data = values(tmp_path)
    _replace_predecessor(data, 3, predecessor_event("three", 3, "c", provider, output_text=""))
    reject(data, "persistence_contract")


def test_earlier_empty_output_text_keeps_non_openai_provider_allowed(tmp_path: Path) -> None:
    data = values(tmp_path)
    _replace_predecessor(data, 1, predecessor_event("one", 1, "a", "other", output_text=""))
    expected = expected_outcome()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert call(data, dependency) is expected
    assert calls == 1


def test_stop_route_empty_predecessor_output_text_remains_rejected(tmp_path: Path) -> None:
    data, result = stop_values(tmp_path, "complete", provider="other")
    _replace_predecessor(data, 3, predecessor_event("three", 3, "c", "openai", output_text=""))
    before = data["state_path"].read_bytes(), data["events_path"].read_bytes()  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeReentryContinuationCompatibilityError) as caught:
        call(data, dependency)
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (data["state_path"].read_bytes(), data["events_path"].read_bytes()) == before  # type: ignore[union-attr]


def accumulated_workflow() -> WorkflowDefinition:
    """Eight-step workflow exposing positions 5 and 6 as accumulated history."""
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": "e",
                    "instructions": f"step-{index}",
                }
                for index in range(1, 9)
            ],
        }
    )


def write_accumulated_targets(
    tmp_path: Path,
    status: str,
    *,
    five_provider: object = "openai",
    six_provider: object = "openai",
    current: int = 7,
    six_request_id: object = None,
) -> tuple[Path, Path, bytes, bytes]:
    """Terminal step-``current`` targets with ``current-1`` predecessors.

    Positions 1-4 use the default non-openai predecessors; position 5 and
    the immediate predecessor (``current-1``) carry accumulated None request
    ids with the openai provider by default.  For ``current=8`` the
    non-contiguous Issue #380 case 2 is built: step 5 None, step 6 a
    non-empty request id, immediate step 7 None.
    """
    wf = accumulated_workflow()
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = WorkflowExecutionState(
        "w",
        status,
        f"step-{current}",
        current,
        "e",
        (
            tuple(step.id for step in wf.steps[:current])
            if status == "succeeded"
            else tuple(step.id for step in wf.steps[: current - 1])
        ),
        None if status == "succeeded" else "api_error",
    )
    predecessors = []
    for index, step in enumerate(wf.steps[: current - 1], 1):
        provider = "other"
        changes: dict[str, object] = {}
        if index == 5:
            provider = five_provider
            changes["request_id"] = None
        elif index == 6 and current == 8:
            provider = six_provider
            changes["request_id"] = six_request_id
        elif index >= 5:
            provider = six_provider
            changes["request_id"] = None
        predecessors.append(
            predecessor_event(step.id, index, "e", provider, **changes)
        )
    terminal_changes: dict[str, object] = {
        "step_id": f"step-{current}",
        "step_index": current,
        "employee_id": "e",
        "request_id": f"request-step-{current}",
    }
    if status == "succeeded":
        terminal_changes.update(
            response_id=f"response-step-{current}",
            output_text=f"output-step-{current}",
        )
    terminal = terminal_event(status, **terminal_changes)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def accumulated_values(
    tmp_path: Path,
    status: str = "succeeded",
    *,
    five_provider: object = "openai",
    six_provider: object = "openai",
    current: int = 7,
    six_request_id: object = None,
) -> dict[str, object]:
    state, events, before_state, before_events = write_accumulated_targets(
        tmp_path,
        status,
        five_provider=five_provider,
        six_provider=six_provider,
        current=current,
        six_request_id=six_request_id,
    )
    return {
        "result": persistence_result(state, events),
        "workflow": accumulated_workflow(),
        "state_path": state,
        "events_path": events,
        "before_state": before_state,
        "before_events": before_events,
    }


def test_accumulated_none_request_id_positions_five_six_delegates_once(
    tmp_path: Path,
) -> None:
    data = accumulated_values(tmp_path)
    expected = PersistedExecutionOutcome(
        "persisted_success",
        "w",
        "step-7",
        7,
        "e",
        None,
    )
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    assert call(data, dependency) is expected
    assert calls == [
        (
            data["result"],
            data["workflow"],
            data["state_path"],
            data["events_path"],
        )
    ]
    assert_unchanged(data)


def test_accumulated_none_step8_noncontiguous_six_request_id_delegates_once(
    tmp_path: Path,
) -> None:
    """Issue #380 case 2: step8 with step5=None, step6 non-empty, step7=None.

    The non-contiguous accumulated None provenance (step 5 None, step 6 a
    non-empty request id, immediate step 7 None, all openai) classifies
    exactly once; the failed terminal status over the same provenance
    classifies exactly once as an inline subcase.
    """
    data = accumulated_values(tmp_path, current=8, six_request_id="req-6")
    expected = PersistedExecutionOutcome(
        "persisted_success",
        "w",
        "step-8",
        8,
        "e",
        None,
    )
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    assert call(data, dependency) is expected
    assert calls == [
        (
            data["result"],
            data["workflow"],
            data["state_path"],
            data["events_path"],
        )
    ]
    assert_unchanged(data)

    failed = accumulated_values(
        tmp_path / "failed", "failed", current=8, six_request_id="req-6"
    )
    failed_expected = PersistedExecutionOutcome(
        "persisted_failure",
        "w",
        "step-8",
        8,
        "e",
        "api_error",
    )
    failed_calls: list[tuple[object, ...]] = []

    def failed_dependency(*args: object) -> object:
        failed_calls.append(args)
        return failed_expected

    assert call(failed, failed_dependency) is failed_expected
    assert failed_calls == [
        (
            failed["result"],
            failed["workflow"],
            failed["state_path"],
            failed["events_path"],
        )
    ]
    assert_unchanged(failed)


def test_accumulated_none_position_five_non_openai_provider_is_rejected_before_phase128(
    tmp_path: Path,
) -> None:
    data = accumulated_values(tmp_path, five_provider="other")
    reject(data, "persistence_contract")


def test_accumulated_none_position_four_remains_rejected_before_phase128(
    tmp_path: Path,
) -> None:
    data = accumulated_values(tmp_path)
    events = data["events_path"]
    lines = events.read_bytes().splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("step-4", 4, "e", "other", request_id=None)
    ).encode()
    events.write_bytes(b"".join(lines[:3]) + replacement + b"".join(lines[4:]))  # type: ignore[union-attr]
    reject(data, "persistence_contract")
