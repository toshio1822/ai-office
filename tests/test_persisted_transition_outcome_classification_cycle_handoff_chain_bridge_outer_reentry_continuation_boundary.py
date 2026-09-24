"""Behavioral tests for the Phase 143 persisted-transition outcome-classification facade.

The facade now owns the persisted-result classification route directly: it
validates the supplied ``WorkflowExecutionPersistenceResult`` against the actual
state/events bytes and terminal history, then classifies once through the
existing persisted-execution-outcome classification responsibility.  These tests
assert observable classification, read-only, compensation and stop-route
behavior, not the removed historical wrapper topology.
"""

# ruff: noqa: E501

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase143_module
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import PersistedExecutionOutcome, WorkflowProgressionDecision
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeError,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as OuterCompatibilityError,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as public_phase143,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_MODULE_NAME = (
    "persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_"
    "reentry_continuation_boundary"
)
_MODULE_PATH = Path(phase143_module.__file__)
_UNSET = object()


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class PersistenceChild(WorkflowExecutionPersistenceResult):
    pass


class IntChild(int):
    pass


def workflow(count: int = 6) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "persisted-transition outcome classification behavior",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": f"employee-{index}",
                    "instructions": f"instructions-{index}",
                }
                for index in range(1, count + 1)
            ],
        }
    )


def predecessor_event(
    definition: WorkflowDefinition,
    index: int,
    *,
    provider: object = "openai",
    request_id: object = _UNSET,
    output_text: object = _UNSET,
    response_id: object = _UNSET,
) -> RuntimeStepEvent:
    step = definition.steps[index - 1]
    return RuntimeStepEvent(
        "step_succeeded",
        definition.id,
        step.id,
        index,
        step.employee,
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"predecessor-response-{index}" if response_id is _UNSET else response_id,  # type: ignore[arg-type]
        f"predecessor-request-{index}" if request_id is _UNSET else request_id,  # type: ignore[arg-type]
        f"predecessor-output-{index}" if output_text is _UNSET else output_text,  # type: ignore[arg-type]
        None,
    )


def terminal_event(
    definition: WorkflowDefinition,
    index: int,
    status: str,
    *,
    provider: object = "openai",
) -> RuntimeStepEvent:
    step = definition.steps[index - 1]
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded",
            definition.id,
            step.id,
            index,
            step.employee,
            "running",
            "succeeded",
            provider,  # type: ignore[arg-type]
            None,
            f"terminal-response-{index}",
            f"terminal-request-{index}",
            f"terminal-output-{index}",
            None,
        )
    return RuntimeStepEvent(
        "step_failed",
        definition.id,
        step.id,
        index,
        step.employee,
        "running",
        "failed",
        provider,  # type: ignore[arg-type]
        "api_error",
        None,
        f"terminal-request-{index}",
        None,
        "safe failure",
    )


def write_targets(
    tmp_path: Path,
    definition: WorkflowDefinition,
    *,
    current: int = 6,
    status: str = "succeeded",
    events: list[RuntimeStepEvent] | None = None,
    terminal_provider: object = "openai",
) -> tuple[Path, Path, bytes, bytes]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    step = definition.steps[current - 1]
    completed = (
        tuple(item.id for item in definition.steps[:current])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: current - 1])
    )
    state = WorkflowExecutionState(
        definition.id,
        status,  # type: ignore[arg-type]
        step.id,
        current,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    if events is None:
        events = [predecessor_event(definition, index) for index in range(1, current)]
        events.append(
            terminal_event(definition, current, status, provider=terminal_provider)
        )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def persistence_result(
    state_path: Path, events_path: Path
) -> WorkflowExecutionPersistenceResult:
    terminal_line = events_path.read_bytes().splitlines(keepends=True)[-1]
    return WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_path.read_bytes()), len(terminal_line)
    )


def values(
    tmp_path: Path,
    status: str = "succeeded",
    *,
    current: int = 6,
    count: int | None = None,
    events: list[RuntimeStepEvent] | None = None,
    terminal_provider: object = "openai",
) -> dict[str, object]:
    definition = workflow(count or 6)
    state_path, events_path, state_bytes, event_bytes = write_targets(
        tmp_path,
        definition,
        current=current,
        status=status,
        events=events,
        terminal_provider=terminal_provider,
    )
    return {
        "result": persistence_result(state_path, events_path),
        "workflow": definition,
        "state": state_path,
        "events": events_path,
        "before": (state_bytes, event_bytes),
        "current": current,
        "status": status,
    }


def expected_outcome(
    definition: WorkflowDefinition, current: int, status: str
) -> PersistedExecutionOutcome:
    step = definition.steps[current - 1]
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        definition.id,
        step.id,
        current,
        step.employee,
        None if status == "succeeded" else "api_error",
    )


def stop_values(
    tmp_path: Path, kind: str, *, current: int = 6, provider: object = "other"
) -> tuple[dict[str, object], object]:
    definition = workflow()
    status = "succeeded" if kind == "complete" else "failed"
    state_path, events_path, state_bytes, event_bytes = write_targets(
        tmp_path,
        definition,
        current=current,
        status=status,
        terminal_provider=provider,
    )
    step = definition.steps[current - 1]
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete",
            definition.id,
            step.id,
            current,
            step.employee,
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure",
            definition.id,
            step.id,
            current,
            step.employee,
            "api_error",
        )
    )
    return {
        "result": result,
        "workflow": definition,
        "state": state_path,
        "events": events_path,
        "before": (state_bytes, event_bytes),
        "current": current,
        "status": status,
    }, result


def call_route(**kwargs: object) -> object:
    return public_phase143(**kwargs)  # type: ignore[arg-type]


def route_case(case: dict[str, object]) -> object:
    return call_route(
        result=case["result"],
        workflow=case["workflow"],
        state_path=case["state"],
        events_path=case["events"],
    )


def assert_classification(callable_object: object, expected: str) -> None:
    with pytest.raises(OuterCompatibilityError) as caught:
        callable_object()  # type: ignore[operator]
    assert caught.value.detail.classification == expected


def assert_unchanged(case: dict[str, object]) -> None:
    assert (
        case["state"].read_bytes(),  # type: ignore[union-attr]
        case["events"].read_bytes(),  # type: ignore[union-attr]
    ) == case["before"]


def reject(case: dict[str, object], classification: str, **changes: object) -> None:
    supplied = {
        "result": case["result"],
        "workflow": case["workflow"],
        "state_path": case["state"],
        "events_path": case["events"],
    }
    supplied.update(changes)
    assert_classification(lambda: call_route(**supplied), classification)
    assert_unchanged(case)


def committed_history(case: dict[str, object]) -> object:
    return load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            case["state"],
            case["events"],  # type: ignore[arg-type]
        )
    )


# 1. public signature / no new layer / source audit


def test_public_signature_and_no_new_layer_source_audit() -> None:
    parameters = list(inspect.signature(public_phase143).parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "result",
        "workflow",
        "state_path",
        "events_path",
    ]
    assert all(parameter.annotation is object for parameter in parameters)
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters
    )
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)

    source = _MODULE_PATH.read_text(encoding="utf-8")
    # The historical lower wrapper chain and its dependency-injection seam are gone.
    assert "phase135" not in source.lower()
    assert "phase128" not in source.lower()
    assert (
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary"
        not in source
    )
    assert (
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary"
        not in source
    )
    # Exact Phase 142 result subclasses/compat shims are not referenced either.
    assert "Callable" not in source
    # The existing persisted-outcome classification responsibility is reused.
    assert "classify_persisted_execution_outcome_reentry" in source
    # No new public Phase / wrapper / bridge / adapter / compatibility layer:
    # the only public top-level function is the retained Phase 143 facade route.
    tree = ast.parse(source)
    public_functions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]
    assert public_functions == [
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary"
    ]
    classes = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    ]
    assert classes == [
        "PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationFailureDetail",
        "PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError",
        "PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError",
    ]
    # No provider / network / clock / environment access and no new CLI command.
    for forbidden in (
        "argparse",
        "click",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "import random",
        "import time",
        "os.environ",
        "getenv",
    ):
        assert forbidden not in source
    assert "add_parser" not in source
    assert "ai_office.cli" not in source


# 2. exact persisted success / failure outcome semantics


@pytest.mark.parametrize("status", ["succeeded", "failed"])
@pytest.mark.parametrize("current", [1, 2, 3, 5, 6])
def test_valid_persisted_result_returns_exact_outcome(
    tmp_path: Path, status: str, current: int
) -> None:
    case = values(tmp_path, status, current=current)
    outcome = route_case(case)
    assert type(outcome) is PersistedExecutionOutcome
    assert outcome == expected_outcome(case["workflow"], current, status)  # type: ignore[arg-type]
    assert outcome.outcome == (
        "persisted_success" if status == "succeeded" else "persisted_failure"
    )
    assert outcome.failure_category == (None if status == "succeeded" else "api_error")
    assert_unchanged(case)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_normal_classification_leaves_state_and_events_unchanged(
    tmp_path: Path, status: str
) -> None:
    case = values(tmp_path, status)
    before = case["before"]
    route_case(case)
    assert (
        case["state"].read_bytes(),  # type: ignore[union-attr]
        case["events"].read_bytes(),  # type: ignore[union-attr]
    ) == before
    assert case["events"].read_bytes() == before[1]  # type: ignore[union-attr]


# 3. persistence-result / actual-file consistency


def test_persistence_result_type_is_exact(tmp_path: Path) -> None:
    case = values(tmp_path)
    result = case["result"]
    substitute = SimpleNamespace(
        state_path=result.state_path,  # type: ignore[union-attr]
        events_path=result.events_path,  # type: ignore[union-attr]
        state_bytes_written=result.state_bytes_written,  # type: ignore[union-attr]
        event_bytes_appended=result.event_bytes_appended,  # type: ignore[union-attr]
    )
    child = PersistenceChild(
        result.state_path,  # type: ignore[union-attr]
        result.events_path,  # type: ignore[union-attr]
        result.state_bytes_written,  # type: ignore[union-attr]
        result.event_bytes_appended,  # type: ignore[union-attr]
    )
    for bad in (child, substitute):
        reject(case, "result_type", result=bad)


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_persistence_target_identity_is_exact(tmp_path: Path, field: str) -> None:
    case = values(tmp_path)
    reject(
        case,
        "persistence_contract",
        result=replace(case["result"], **{field: Path("different")}),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("state_bytes_written", True),
        ("state_bytes_written", IntChild(1)),
        ("state_bytes_written", 0),
        ("state_bytes_written", -1),
        ("state_bytes_written", 1.0),
        ("event_bytes_appended", True),
        ("event_bytes_appended", IntChild(1)),
        ("event_bytes_appended", 0),
        ("event_bytes_appended", -1),
        ("event_bytes_appended", 1.0),
    ],
)
def test_persistence_counts_require_exact_positive_int(
    tmp_path: Path, field: str, value: object
) -> None:
    case = values(tmp_path)
    reject(
        case, "persistence_contract", result=replace(case["result"], **{field: value})
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_positive_but_wrong_persistence_counts_fail_closed(
    tmp_path: Path, field: str
) -> None:
    case = values(tmp_path)
    reject(
        case,
        "persistence_contract",
        result=replace(
            case["result"],
            **{field: getattr(case["result"], field) + 1},  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize("which", ["state", "events", "appended"])
def test_persistence_bytes_mismatch_fails_closed(tmp_path: Path, which: str) -> None:
    case = values(tmp_path)
    if which == "state":
        case["state"].write_bytes(b"different-state-bytes")  # type: ignore[union-attr]
    elif which == "events":
        case["events"].write_bytes(b"{}\n")  # type: ignore[union-attr]
    else:
        case["result"] = replace(  # type: ignore[assignment]
            case["result"],
            event_bytes_appended=1,  # type: ignore[arg-type]
        )
    assert_classification(lambda: route_case(case), "persistence_contract")


# 4. terminal history / identity fail-closed matrix


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_history_matrix_fails_closed(tmp_path: Path, mode: str) -> None:
    case = values(tmp_path)
    definition = case["workflow"]
    events_path = case["events"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    if mode == "duplicate":
        content = lines[0] + "".join(lines)
    elif mode == "missing":
        content = "".join(lines[1:])
    elif mode == "reordered":
        content = "".join([lines[1], lines[0], *lines[2:]])
    elif mode == "unrelated":
        unrelated = serialize_runtime_step_event_jsonl(
            replace(
                predecessor_event(definition, 1),  # type: ignore[arg-type]
                step_id="unrelated-step",
            )
        )
        content = unrelated + "".join(lines[1:])
    elif mode == "malformed":
        content = "{malformed}\n"
    else:
        content = "".join(lines) + lines[-1]
    events_path.write_text(content, encoding="utf-8")  # type: ignore[union-attr]
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "other-workflow"),
        ("current_step_id", "step-4"),
        ("current_step_index", 4),
        ("current_employee_id", "employee-4"),
        ("completed_step_ids", ("step-1", "step-2")),
        ("last_failure_category", "transport_error"),
        ("status", "running"),
    ],
)
def test_terminal_state_mismatch_fails_closed(
    tmp_path: Path, field: str, value: object
) -> None:
    case = values(tmp_path)
    state = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            case["state"],
            case["events"],  # type: ignore[arg-type]
        )
    ).state
    case["state"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(
            replace(state, **{field: value})
        ).encode()
    )
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_type", "step_failed"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("response_id", None),
        ("output_text", None),
        ("message", "unexpected message"),
        ("provider", "other"),
        ("provider", 4),
        ("request_id", ""),
    ],
)
def test_success_terminal_event_semantics_fail_closed(
    tmp_path: Path, field: str, value: object
) -> None:
    case = values(tmp_path)
    definition = case["workflow"]
    events = [predecessor_event(definition, index) for index in range(1, 6)]  # type: ignore[arg-type]
    events.append(
        replace(terminal_event(definition, 6, "succeeded"), **{field: value})  # type: ignore[arg-type]
    )
    state_path, events_path, state_bytes, event_bytes = write_targets(
        tmp_path / "replace",
        definition,
        events=events,  # type: ignore[arg-type]
    )
    case["state"] = state_path
    case["events"] = events_path
    case["result"] = persistence_result(state_path, events_path)
    case["before"] = (state_bytes, event_bytes)
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize(
    "field,value",
    [
        ("failure_category", "transport_error"),
        ("message", None),
        ("request_id", ""),
        ("response_id", "unexpected-response"),
        ("output_text", "unexpected-output"),
        ("provider", "other"),
        ("provider", 4),
        ("next_status", "succeeded"),
    ],
)
def test_failed_terminal_event_semantics_fail_closed(
    tmp_path: Path, field: str, value: object
) -> None:
    case = values(tmp_path, "failed")
    definition = case["workflow"]
    events = [predecessor_event(definition, index) for index in range(1, 6)]  # type: ignore[arg-type]
    events.append(
        replace(terminal_event(definition, 6, "failed"), **{field: value})  # type: ignore[arg-type]
    )
    state_path, events_path, state_bytes, event_bytes = write_targets(
        tmp_path / "replace",
        definition,
        status="failed",
        events=events,  # type: ignore[arg-type]
    )
    case["state"] = state_path
    case["events"] = events_path
    case["result"] = persistence_result(state_path, events_path)
    case["before"] = (state_bytes, event_bytes)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_workflow_and_step_models_are_exact(tmp_path: Path) -> None:
    case = values(tmp_path)
    child = WorkflowChild.model_validate(workflow().model_dump())
    compatible = SimpleNamespace(
        id="workflow", name="Workflow", description="D", steps=workflow().steps
    )
    for bad in (child, compatible):
        reject(case, "workflow_definition", workflow=bad)
    original = workflow()
    for step in (
        StepChild(id="step-6", name="Step 6", employee="employee-6", instructions="i"),
        SimpleNamespace(
            id="step-6", name="Step 6", employee="employee-6", instructions="i"
        ),
    ):
        steps = [*original.steps[:-1], step]
        candidate = WorkflowDefinition.model_construct(
            id="workflow", name="Workflow", description="D", steps=steps
        )
        reject(case, "workflow_definition", workflow=candidate)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 1),
        ("name", 1),
        ("description", 1),
        ("steps", tuple()),
        ("steps", SimpleNamespace()),
    ],
)
def test_workflow_fields_are_exact(tmp_path: Path, field: str, value: object) -> None:
    case = values(tmp_path)
    candidate = WorkflowDefinition.model_construct(
        **(workflow().model_dump() | {field: value})
    )
    reject(case, "workflow_definition", workflow=candidate)


# 5. provider / request-id / empty-output compatibility


@pytest.mark.parametrize("provider", ["openai", "omniroute"])
def test_immediate_and_terminal_provider_compatibility_is_preserved(
    tmp_path: Path, provider: str
) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(definition, 5, provider=provider)
    events.append(terminal_event(definition, 6, "succeeded", provider=provider))
    case = values(tmp_path, events=events)
    assert route_case(case) == expected_outcome(definition, 6, "succeeded")


def test_non_openai_immediate_predecessor_is_rejected(tmp_path: Path) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(definition, 5, provider="other")
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_earlier_non_openai_predecessor_remains_allowed(tmp_path: Path) -> None:
    definition = workflow()
    events = [
        predecessor_event(
            definition,
            index,
            provider="omniroute" if index == 5 else "other",
        )
        for index in range(1, 6)
    ]
    events.append(terminal_event(definition, 6, "succeeded", provider="omniroute"))
    case = values(tmp_path, events=events)
    assert route_case(case) == expected_outcome(definition, 6, "succeeded")


def test_non_openai_terminal_provider_is_rejected(tmp_path: Path) -> None:
    case = values(tmp_path, terminal_provider="other")
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize("provider", ["other", 4])
def test_terminal_provider_contract_is_strict(tmp_path: Path, provider: object) -> None:
    case = values(tmp_path, terminal_provider=provider)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_bounded_immediate_none_request_id_is_accepted(tmp_path: Path) -> None:
    """Current bounded compatibility: the step-5 immediate predecessor may carry None."""
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(definition, 5, request_id=None, provider="openai")
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert route_case(case) == expected_outcome(definition, 6, "succeeded")


def test_immediate_none_request_id_below_step_five_is_rejected(tmp_path: Path) -> None:
    definition = workflow(count=5)
    events = [predecessor_event(definition, index) for index in range(1, 4)]
    events.append(predecessor_event(definition, 4, request_id=None, provider="openai"))
    events.append(terminal_event(definition, 5, "succeeded"))
    case = values(tmp_path, current=5, count=5, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_earlier_none_request_id_is_rejected(tmp_path: Path) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[1] = predecessor_event(definition, 2, request_id=None, provider="openai")
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_accumulated_none_request_id_at_step_seven_is_accepted(tmp_path: Path) -> None:
    definition = workflow(count=7)
    events = [predecessor_event(definition, index) for index in range(1, 7)]
    for position in (5, 6):
        events[position - 1] = predecessor_event(
            definition, position, request_id=None, provider="openai"
        )
    events.append(terminal_event(definition, 7, "succeeded"))
    case = values(tmp_path, current=7, count=7, events=events)
    assert route_case(case) == expected_outcome(definition, 7, "succeeded")


def test_accumulated_none_provider_outside_openai_family_is_rejected(
    tmp_path: Path,
) -> None:
    definition = workflow(count=7)
    events = [predecessor_event(definition, index) for index in range(1, 7)]
    events[4] = predecessor_event(definition, 5, request_id=None, provider="other")
    events[5] = predecessor_event(definition, 6, request_id=None, provider="openai")
    events.append(terminal_event(definition, 7, "succeeded"))
    case = values(tmp_path, current=7, count=7, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize("request_id", ["", 4])
def test_empty_request_id_is_rejected(tmp_path: Path, request_id: object) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(definition, 5, request_id=request_id)
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize("output_text", [4, None, ["output"]])
def test_non_string_predecessor_output_is_rejected(
    tmp_path: Path, output_text: object
) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(definition, 5, output_text=output_text)
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


@pytest.mark.parametrize("missing", ["response_id", "request_id"])
def test_empty_output_with_empty_provenance_is_rejected(
    tmp_path: Path, missing: str
) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    changes: dict[str, object] = {"output_text": "", missing: ""}
    events[4] = predecessor_event(definition, 5, **changes)
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_empty_output_with_missing_response_id_is_rejected(tmp_path: Path) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(definition, 5, output_text="", response_id=None)
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert_classification(lambda: route_case(case), "persistence_contract")


def test_empty_output_with_bounded_none_request_id_remains_accepted(
    tmp_path: Path,
) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[4] = predecessor_event(
        definition, 5, output_text="", request_id=None, provider="openai"
    )
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert route_case(case) == expected_outcome(definition, 6, "succeeded")


def test_empty_predecessor_output_remains_accepted(tmp_path: Path) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events[0] = predecessor_event(definition, 1, output_text="")
    events[4] = predecessor_event(definition, 5, output_text="")
    events.append(terminal_event(definition, 6, "succeeded"))
    case = values(tmp_path, events=events)
    assert route_case(case) == expected_outcome(definition, 6, "succeeded")


def test_empty_terminal_success_output_remains_accepted(tmp_path: Path) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events.append(replace(terminal_event(definition, 6, "succeeded"), output_text=""))
    case = values(tmp_path, events=events)
    outcome = route_case(case)
    assert outcome == expected_outcome(definition, 6, "succeeded")
    assert_unchanged(case)


# 6. stop routes: identity-preserving / read-only


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_are_identity_preserving_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    case, stop_result = stop_values(tmp_path, kind)
    calls = 0

    def unexpected_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("stop route must not reclassify")

    monkeypatch.setattr(
        phase143_module,
        "classify_persisted_execution_outcome_reentry",
        unexpected_owner,
    )
    assert route_case(case) is stop_result
    assert calls == 0
    assert_unchanged(case)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_accept_non_openai_terminal_provider(
    tmp_path: Path, kind: str
) -> None:
    case, stop_result = stop_values(tmp_path, kind, provider="other")
    assert route_case(case) is stop_result
    assert_unchanged(case)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_route_malformed_values_are_rejected(tmp_path: Path, kind: str) -> None:
    case, _ = stop_values(tmp_path, kind)
    classification = "completion_contract" if kind == "complete" else "failure_contract"
    malformed = (
        replace(case["result"], reason="wrong")
        if kind == "complete"
        else replace(case["result"], failure_category="not-a-category")
    )
    reject(case, classification, result=malformed)


def test_stop_route_empty_success_terminal_output_is_rejected(tmp_path: Path) -> None:
    definition = workflow()
    events = [predecessor_event(definition, index) for index in range(1, 6)]
    events.append(replace(terminal_event(definition, 6, "succeeded"), output_text=""))
    state_path, events_path, state_bytes, event_bytes = write_targets(
        tmp_path, definition, events=events
    )
    step = definition.steps[5]
    result = WorkflowProgressionDecision(
        "workflow_complete",
        definition.id,
        step.id,
        6,
        step.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )
    case = {
        "result": result,
        "workflow": definition,
        "state": state_path,
        "events": events_path,
        "before": (state_bytes, event_bytes),
    }
    assert_classification(lambda: route_case(case), "terminal_contract")


def test_stop_route_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    case, _ = stop_values(tmp_path, "failure")
    history = committed_history(case)
    case["state"].write_bytes(  # type: ignore[union-attr]
        serialize_workflow_execution_state_json(
            replace(history.state, current_step_index=5)
        ).encode()
    )
    assert_classification(lambda: route_case(case), "terminal_contract")


# 7. input / target safety and unsupported results


@pytest.mark.parametrize("kind", ["str", "int", "none"])
def test_unsupported_result_is_rejected_without_side_effects(
    tmp_path: Path, kind: str
) -> None:
    case = values(tmp_path)
    replacement: object = {"str": "step-6", "int": 6, "none": None}[kind]
    reject(case, "result_type", result=replacement)


def test_target_conflict_is_rejected(tmp_path: Path) -> None:
    case = values(tmp_path)
    reject(case, "target_conflict", events_path=case["state"])


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_target_is_rejected(tmp_path: Path, target: str) -> None:
    case = values(tmp_path)
    absent = tmp_path / "absent"
    supplied = {
        "result": case["result"],
        "workflow": case["workflow"],
        "state_path": case["state"],
        "events_path": case["events"],
    }
    supplied[target] = absent
    assert_classification(
        lambda: call_route(**supplied),
        "state_target" if target == "state_path" else "event_target",
    )
    assert_unchanged(case)


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_directory_target_is_rejected(tmp_path: Path, target: str) -> None:
    case = values(tmp_path)
    directory = tmp_path / "directory"
    directory.mkdir()
    supplied = {
        "result": case["result"],
        "workflow": case["workflow"],
        "state_path": case["state"],
        "events_path": case["events"],
    }
    supplied[target] = directory
    assert_classification(
        lambda: call_route(**supplied),
        "state_target" if target == "state_path" else "event_target",
    )


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_target_oserror_is_classified_by_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, target: str
) -> None:
    case = values(tmp_path)
    expected = "state_target" if target == "state" else "event_target"
    victim = case[target]
    original = getattr(Path, operation)

    def failing(path: Path, *args: object, **kwargs: object) -> object:
        if path == victim:
            raise OSError("target failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, failing)
    assert_classification(lambda: route_case(case), expected)
    monkeypatch.undo()
    assert_unchanged(case)


# 8. compensation / rollback


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_owner_safe_error_is_sanitized_and_compensated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    case = values(tmp_path)
    state, events = case["state"], case["events"]
    before = case["before"]
    write = Path.write_bytes
    calls = 0

    def failing_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            write(state, b"mutated-state")  # type: ignore[arg-type]
        if mutation in ("events", "both"):
            write(events, b"mutated-events")  # type: ignore[arg-type]
        raise PersistedExecutionOutcomeCompatibilityError("history_data")

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", failing_owner
    )
    assert_classification(lambda: route_case(case), "dependency_error")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_unexpected_owner_error_is_sanitized_and_compensated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    case = values(tmp_path)
    state, events = case["state"], case["events"]
    before = case["before"]
    write = Path.write_bytes
    calls = 0

    def failing_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            write(state, b"mutated-state")  # type: ignore[arg-type]
        if mutation in ("events", "both"):
            write(events, b"mutated-events")  # type: ignore[arg-type]
        raise RuntimeError("secret owner detail")

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", failing_owner
    )
    with pytest.raises(OuterCompatibilityError) as caught:
        route_case(case)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret owner detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_owner_history_rollback_maps_to_dependency_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    case = values(tmp_path)
    state, events = case["state"], case["events"]
    before = case["before"]
    write = Path.write_bytes
    calls = 0

    def rollback_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            write(state, b"mutated-state")  # type: ignore[arg-type]
        if mutation in ("events", "both"):
            write(events, b"mutated-events")  # type: ignore[arg-type]
        raise PersistedExecutionOutcomeCompatibilityError("history_rollback")

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", rollback_owner
    )
    assert_classification(lambda: route_case(case), "dependency_rollback")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_is_safe_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_target: str
) -> None:
    case = values(tmp_path)
    state, events = case["state"], case["events"]
    write = Path.write_bytes
    restore_calls = {"state": 0, "events": 0}
    owner_calls = 0

    def restore(path: Path, payload: bytes) -> int:
        key = "state" if path == state else "events"
        restore_calls[key] += 1
        if failed_target in (key, "both"):
            raise OSError("rollback")
        return write(path, payload)

    monkeypatch.setattr(Path, "write_bytes", restore)

    def mutating_owner(*_: object, **__: object) -> object:
        nonlocal owner_calls
        owner_calls += 1
        write(state, b"mutated-state")  # type: ignore[arg-type]
        write(events, b"mutated-events")  # type: ignore[arg-type]
        raise PersistedExecutionOutcomeCompatibilityError("history_rollback")

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", mutating_owner
    )
    assert_classification(lambda: route_case(case), "dependency_rollback")
    assert restore_calls == {"state": 1, "events": 1}
    assert owner_calls == 1


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_owner_mutation_with_valid_outcome_is_compensated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    case = values(tmp_path)
    state, events = case["state"], case["events"]
    before = case["before"]
    write = Path.write_bytes
    calls = 0
    valid = expected_outcome(case["workflow"], 6, "succeeded")  # type: ignore[arg-type]

    def mutating_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            write(state, b"mutated-state")  # type: ignore[arg-type]
        if mutation in ("events", "both"):
            write(events, b"mutated-events")  # type: ignore[arg-type]
        return valid

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", mutating_owner
    )
    assert_classification(lambda: route_case(case), "outcome_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "malformed",
    [
        object(),
        "persisted_success",
        SimpleNamespace(),
    ],
)
def test_malformed_owner_result_is_rejected_and_compensated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed: object
) -> None:
    case = values(tmp_path)
    before = case["before"]
    calls = 0

    def malformed_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return malformed

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", malformed_owner
    )
    assert_classification(lambda: route_case(case), "outcome_contract")
    assert calls == 1
    assert_unchanged(case)
    assert case["before"] == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome", "persisted_failure"),
        ("workflow_id", "other-workflow"),
        ("current_step_id", "step-4"),
        ("current_step_index", 4),
        ("current_employee_id", "employee-4"),
        ("failure_category", "api_error"),
    ],
)
def test_owner_result_field_mismatch_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    case = values(tmp_path)
    calls = 0
    malformed = replace(
        expected_outcome(case["workflow"], 6, "succeeded"), **{field: value}
    )  # type: ignore[arg-type]

    def malformed_owner(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        return malformed

    monkeypatch.setattr(
        phase143_module, "classify_persisted_execution_outcome_reentry", malformed_owner
    )
    assert_classification(lambda: route_case(case), "outcome_contract")
    assert calls == 1
    assert_unchanged(case)


# 9. real composition (no injected owner)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_real_default_classification_returns_committed_outcome(
    tmp_path: Path, status: str
) -> None:
    """No injection: the whole default route classifies through the real owner."""
    case = values(tmp_path, status)
    outcome = route_case(case)
    assert outcome == expected_outcome(case["workflow"], 6, status)  # type: ignore[arg-type]
    history = committed_history(case)
    assert history.state.status == status
    assert len(history.events) == 6
    assert_unchanged(case)


def test_default_owner_identity_is_the_existing_responsibility() -> None:
    from ai_office.engine.persisted_execution_outcome_reentry import (
        classify_persisted_execution_outcome_reentry,
    )

    assert (
        phase143_module.classify_persisted_execution_outcome_reentry
        is classify_persisted_execution_outcome_reentry
    )
    assert issubclass(
        PersistedExecutionOutcomeCompatibilityError, PersistedExecutionOutcomeError
    )
