"""Issue #394 Phase 181 prerequisite prepared-start persistence compatibility.

The real Phase 180 boundary can produce an exact PreparedStepExecutionStart for
step 8 while the durable step-7 success history retains an aged OpenAI
request_id=None at position 5.  This prerequisite repairs only the prepared
routes of Phase 147 and Phase 139; Phase 181 itself is not implemented.

Synthetic transport only.  The eight tests use inline matrices to pin the
source layering, Stage 0-3 preflight seam, bounded rule, strictness, and
unchanged stop/read-only contracts.
"""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase147,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase139,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary as phase132,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase147Error,
    _valid_history as phase147_valid_history,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase139Error,
    _valid_history as phase139_valid_history,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError as Phase132Error,
)
from ai_office.invocation import ModelInvocationRequest, UpstreamStepOutput
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_HARNESS = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py"
)
_PHASE147_SOURCE = Path(
    "src/ai_office/engine/"
    "prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
)
_PHASE139_SOURCE = Path(
    "src/ai_office/engine/"
    "prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
)
_PHASE132_SOURCE = Path(
    "src/ai_office/engine/"
    "prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
)
_PHASE125_SOURCE = Path(
    "src/ai_office/engine/"
    "prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary.py"
)
_SHARED_HISTORY_SOURCE = Path("src/ai_office/engine/terminal_history_contract.py")


def _load_harness():
    spec = importlib.util.spec_from_file_location("phase180_issue394_harness", _HARNESS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(tmp_path: Path) -> dict[str, object]:
    """Return the exact real Phase 180 step-8 start and post-Phase 180 snapshot."""
    harness = _load_harness()
    case = harness._case(tmp_path)
    calls: list[object] = []
    start = harness._call(case, transport=case["h"].success_transport(calls))
    assert type(start) is PreparedStepExecutionStart
    assert start.running_state.current_step_index == 8
    assert start.running_state.current_step_id == "step-8"
    assert start.running_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    assert len(calls) == 1
    return {
        "harness": case["h"],
        "workflow": case["workflow"],
        "start": start,
        "employee": case["next_employee"],
        "state_path": case["state_path"],
        "events_path": case["events_path"],
        "committed": (case["state_path"].read_bytes(), case["events_path"].read_bytes()),
        "transport_calls": calls,
    }


def _workflow(steps: int = 20) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": f"e{index}",
                    "instructions": f"step-{index}",
                }
                for index in range(1, steps + 1)
            ],
        }
    )


def _employee(workflow: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = workflow.steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def _start(workflow: WorkflowDefinition, index: int) -> PreparedStepExecutionStart:
    person = _employee(workflow, index)
    step = workflow.steps[index - 1]
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            person.model,
            person.instructions,
            step.instructions,
            tuple(person.allowed_tools),
            (
                UpstreamStepOutput(
                    workflow.id,
                    workflow.steps[index - 2].id,
                    index - 1,
                    workflow.steps[index - 2].employee,
                    f"output-{workflow.steps[index - 2].id}",
                ),
            ),
        ),
        WorkflowExecutionState(
            workflow.id,
            "running",
            step.id,
            index,
            person.id,
            tuple(item.id for item in workflow.steps[: index - 1]),
            None,
        ),
    )


def _event(workflow: WorkflowDefinition, index: int, **changes: object) -> RuntimeStepEvent:
    step = workflow.steps[index - 1]
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            workflow.id,
            step.id,
            index,
            step.employee,
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step.id}",
            f"request-{step.id}",
            f"output-{step.id}",
            None,
        ),
        **changes,
    )


def _scenario(
    tmp_path: Path,
    *,
    prepared_index: int = 8,
    current: int = 7,
    none_positions: tuple[int, ...] = (),
    non_openai_positions: tuple[int, ...] = (),
    empty_positions: tuple[int, ...] = (),
    wrong_type_positions: tuple[int, ...] = (),
) -> dict[str, object]:
    workflow = _workflow()
    current_step = workflow.steps[current - 1]
    events: list[RuntimeStepEvent] = []
    for index in range(1, current):
        changes: dict[str, object] = {}
        if index in none_positions:
            changes["request_id"] = None
        if index in non_openai_positions:
            changes["provider"] = "anthropic"
        if index in empty_positions:
            changes["request_id"] = ""
        if index in wrong_type_positions:
            changes["request_id"] = 12345
        events.append(_event(workflow, index, **changes))
    events.append(_event(workflow, current, request_id="request-terminal"))
    state = WorkflowExecutionState(
        workflow.id,
        "succeeded",
        current_step.id,
        current,
        current_step.employee,
        tuple(step.id for step in workflow.steps[:current]),
        None,
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(
        b"".join(serialize_runtime_step_event_jsonl(event).encode() for event in events)
    )
    return {
        "workflow": workflow,
        "start": _start(workflow, prepared_index),
        "employee": _employee(workflow, prepared_index),
        "state_path": state_path,
        "events_path": events_path,
        "before": (state_path.read_bytes(), events_path.read_bytes()),
    }


def _persisted_result(start: PreparedStepExecutionStart) -> RunningStatePersistenceResult:
    return RunningStatePersistenceResult(
        len(serialize_workflow_execution_state_json(start.running_state).encode())
    )


def _persistence_stub(scenario: dict[str, object], calls: list[tuple]) -> object:
    start = scenario["start"]
    state_path = scenario["state_path"]
    events_path = scenario["events_path"]
    assert type(start) is PreparedStepExecutionStart
    assert isinstance(state_path, Path) and isinstance(events_path, Path)

    def dependency(*args: object, **kwargs: object) -> RunningStatePersistenceResult:
        calls.append((args, kwargs))
        state_path.write_text(serialize_workflow_execution_state_json(start.running_state))
        return _persisted_result(start)

    return dependency


def _assert_unchanged(scenario: dict[str, object]) -> None:
    state_path = scenario["state_path"]
    events_path = scenario["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    before = scenario["before"]
    assert (state_path.read_bytes(), events_path.read_bytes()) == before


def _stop_scenario(tmp_path: Path, *, failed: bool = False) -> dict[str, object]:
    workflow = _workflow(8)
    last = workflow.steps[-1]
    state = WorkflowExecutionState(
        workflow.id,
        "failed" if failed else "succeeded",
        last.id,
        8,
        last.employee,
        tuple(step.id for step in workflow.steps[:7 if failed else 8]),
        "api_error" if failed else None,
    )
    events = [_event(workflow, index) for index in range(1, 8)]
    events.append(
        RuntimeStepEvent(
            "step_failed" if failed else "step_succeeded",
            workflow.id,
            last.id,
            8,
            last.employee,
            "running",
            "failed" if failed else "succeeded",
            "openai",
            "api_error" if failed else None,
            None if failed else "response-step-8",
            None if failed else "request-step-8",
            None if failed else "output-step-8",
            "safe failure" if failed else None,
        )
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(
        b"".join(serialize_runtime_step_event_jsonl(event).encode() for event in events)
    )
    result = (
        PersistedExecutionOutcome(
            "persisted_failure", workflow.id, last.id, 8, last.employee, "api_error"
        )
        if failed
        else WorkflowProgressionDecision(
            "workflow_complete", workflow.id, last.id, 8, last.employee,
            None, None, None, "last_step_succeeded"
        )
    )
    return {
        "workflow": workflow,
        "result": result,
        "state_path": state_path,
        "events_path": events_path,
        "before": (state_path.read_bytes(), events_path.read_bytes()),
    }


def _inject_aged_none(events_path: Path) -> bytes:
    lines = events_path.read_text().splitlines()
    payload = json.loads(lines[4])
    payload["request_id"] = None
    payload["provider"] = "openai"
    lines[4] = json.dumps(payload, separators=(",", ":"))
    value = ("\n".join(lines) + "\n").encode()
    events_path.write_bytes(value)
    return value


# 1. source/default dependency audit and unchanged lower/shared contract.
def test_01_source_default_dependency_audit() -> None:
    sig147 = inspect.signature(phase147)
    sig139 = inspect.signature(phase139)
    assert sig147.parameters["phase139_function"].default is phase139
    assert sig139.parameters["phase132_function"].default is phase132
    source147 = _PHASE147_SOURCE.read_text()
    source139 = _PHASE139_SOURCE.read_text()
    assert "phase139_function(result, workflow, employee, state_path, events_path)" in source147
    assert "phase132_function(result, workflow, employee, state_path, events_path)" in source139
    assert "phase132" not in source147.lower()
    assert "phase147" not in source139.lower()
    assert "allow_accumulated_openai_none" not in _PHASE132_SOURCE.read_text()
    assert "allow_accumulated_openai_none" not in _PHASE125_SOURCE.read_text()
    assert "allow_accumulated_openai_none" not in _SHARED_HISTORY_SOURCE.read_text()
    assert "phase181" not in source147.lower()


# 2. real Phase 180 canonical provenance.
def test_02_canonical_provenance_real_phase180(tmp_path: Path) -> None:
    case = _canonical(tmp_path)
    harness = case["harness"]
    assert isinstance(harness, object)
    state_path = case["state_path"]
    events_path = case["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(state_path, events_path)
    )
    assert [event.step_id for event in loaded.events] == [f"step-{i}" for i in range(1, 8)]
    assert loaded.events[4].request_id is None
    assert loaded.events[4].provider == "openai"
    assert loaded.state.current_step_index == 7
    assert loaded.state.status == "succeeded"
    assert case["transport_calls"] and len(case["transport_calls"]) == 1
    assert (state_path.read_bytes(), events_path.read_bytes()) == case["committed"]


# 3. Stage 0: Phase 132 -> Phase 125 -> unchanged lower chain.
def test_03_stage0_lower_chain_proof(tmp_path: Path) -> None:
    case = _canonical(tmp_path)
    state_path = case["state_path"]
    events_path = case["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    output = phase132(case["start"], case["workflow"], case["employee"], state_path, events_path)
    assert type(output) is RunningStatePersistenceResult
    assert state_path.read_bytes() == serialize_workflow_execution_state_json(
        case["start"].running_state
    ).encode()
    assert events_path.read_bytes() == case["committed"][1]


# 4. Stage 1/2/3 localization and Phase 147 bounded rule.
def test_04_phase147_bounded_accumulated_rule(tmp_path: Path) -> None:
    # The source-change-free Stage 1/2 localization is recorded by
    # /tmp/preflight394_stage0_3.py: the unmodified Phase147 rejects before
    # Phase139, an isolated Phase147 copy repair reaches unchanged Phase139,
    # and only the two-copy repair reaches the lower chain.  The implemented
    # route is now exercised here with its bounded acceptance and strictness.
    accepted = _scenario(tmp_path / "accepted", none_positions=(5,))
    calls: list[tuple] = []
    output = phase147(
        accepted["start"], accepted["workflow"], accepted["employee"],
        accepted["state_path"], accepted["events_path"],
        phase139_function=_persistence_stub(accepted, calls),
    )
    assert type(output) is RunningStatePersistenceResult
    assert len(calls) == 1
    assert calls[0][0] == (
        accepted["start"], accepted["workflow"], accepted["employee"],
        accepted["state_path"], accepted["events_path"],
    )

    for label, scenario in (
        ("position4", _scenario(tmp_path / "position4", none_positions=(4,))),
        ("non-openai", _scenario(tmp_path / "non-openai", none_positions=(5,), non_openai_positions=(5,))),
        ("empty", _scenario(tmp_path / "empty", empty_positions=(5,))),
        ("wrong-type", _scenario(tmp_path / "wrong-type", wrong_type_positions=(5,))),
    ):
        calls: list[tuple] = []
        with pytest.raises(Phase147Error) as error:
            phase147(
                scenario["start"], scenario["workflow"], scenario["employee"],
                scenario["state_path"], scenario["events_path"],
                phase139_function=lambda *args, _calls=calls, **kwargs: _calls.append((args, kwargs)),
            )
        assert error.value.detail.classification == "terminal_contract", label
        assert calls == []

    # Pin the accumulated threshold independently of immediate-predecessor
    # compatibility: position 5 None is false at persisted index 6 and true
    # at persisted index 7 when immediate None is disabled.
    lower = _scenario(tmp_path / "lower-index", prepared_index=7, current=6, none_positions=(5,))
    lower_loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(lower["state_path"], lower["events_path"])
    )
    assert not phase147_valid_history(
        lower["workflow"], lower_loaded.state, lower_loaded.events,
        "succeeded", None, None,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_immediate_none_request_id=False,
        allow_accumulated_openai_none=True,
    )
    threshold = _scenario(tmp_path / "threshold", prepared_index=8, current=7, none_positions=(5,))
    threshold_loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(threshold["state_path"], threshold["events_path"])
    )
    assert phase147_valid_history(
        threshold["workflow"], threshold_loaded.state, threshold_loaded.events,
        "succeeded", None, None,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_immediate_none_request_id=False,
        allow_accumulated_openai_none=True,
    )

    immediate = _scenario(tmp_path / "immediate", prepared_index=7, current=6, none_positions=(5,))
    calls = []
    phase147(
        immediate["start"], immediate["workflow"], immediate["employee"],
        immediate["state_path"], immediate["events_path"],
        phase139_function=_persistence_stub(immediate, calls),
    )
    assert len(calls) == 1


# 5. Phase 139 bounded rule and exact Phase 139 -> Phase 132 shape.
def test_05_phase139_bounded_accumulated_rule(tmp_path: Path) -> None:
    accepted = _scenario(tmp_path / "accepted", none_positions=(5,))
    calls: list[tuple] = []
    output = phase139(
        accepted["start"], accepted["workflow"], accepted["employee"],
        accepted["state_path"], accepted["events_path"],
        phase132_function=_persistence_stub(accepted, calls),
    )
    assert type(output) is RunningStatePersistenceResult
    assert len(calls) == 1
    assert calls[0][0] == (
        accepted["start"], accepted["workflow"], accepted["employee"],
        accepted["state_path"], accepted["events_path"],
    )
    for label, scenario in (
        ("position4", _scenario(tmp_path / "position4", none_positions=(4,))),
        ("non-openai", _scenario(tmp_path / "non-openai", none_positions=(5,), non_openai_positions=(5,))),
        ("empty", _scenario(tmp_path / "empty", empty_positions=(5,))),
        ("wrong-type", _scenario(tmp_path / "wrong-type", wrong_type_positions=(5,))),
    ):
        with pytest.raises(Phase139Error) as error:
            phase139(
                scenario["start"], scenario["workflow"], scenario["employee"],
                scenario["state_path"], scenario["events_path"],
                phase132_function=lambda *args, **kwargs: pytest.fail("Phase132 must not run"),
            )
        assert error.value.detail.classification == "terminal_contract", label

    # Pin the accumulated threshold independently of immediate-predecessor
    # compatibility: position 5 None is false at persisted index 6 and true
    # at persisted index 7 when immediate None is disabled.
    lower = _scenario(tmp_path / "lower-index", prepared_index=7, current=6, none_positions=(5,))
    lower_loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(lower["state_path"], lower["events_path"])
    )
    assert not phase139_valid_history(
        lower["workflow"], lower_loaded.state, lower_loaded.events,
        "succeeded", None,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_immediate_none_request_id=False,
        allow_accumulated_openai_none=True,
    )
    threshold = _scenario(tmp_path / "threshold", prepared_index=8, current=7, none_positions=(5,))
    threshold_loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(threshold["state_path"], threshold["events_path"])
    )
    assert phase139_valid_history(
        threshold["workflow"], threshold_loaded.state, threshold_loaded.events,
        "succeeded", None,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_immediate_none_request_id=False,
        allow_accumulated_openai_none=True,
    )

    immediate = _scenario(tmp_path / "immediate", prepared_index=7, current=6, none_positions=(5,))
    calls = []
    phase139(
        immediate["start"], immediate["workflow"], immediate["employee"],
        immediate["state_path"], immediate["events_path"],
        phase132_function=_persistence_stub(immediate, calls),
    )
    assert len(calls) == 1


# 6. full real Phase 180 -> repaired Phase147 -> repaired Phase139 -> lower chain.
def test_06_full_repaired_real_chain_success(tmp_path: Path) -> None:
    case = _canonical(tmp_path)
    output = phase147(
        case["start"], case["workflow"], case["employee"],
        case["state_path"], case["events_path"],
    )
    assert type(output) is RunningStatePersistenceResult
    assert output.state_bytes_written > 0
    assert case["state_path"].read_bytes() == serialize_workflow_execution_state_json(
        case["start"].running_state
    ).encode()
    assert case["events_path"].read_bytes() == case["committed"][1]
    assert len(case["transport_calls"]) == 1
    assert b"step-8" not in case["events_path"].read_bytes()


# 7. multiple valid None / non-contiguous provenance and strict boundary.
def test_07_non_contiguous_multiple_valid_none_provenance(tmp_path: Path) -> None:
    case = _canonical(tmp_path / "non-contiguous")
    events_path = case["events_path"]
    assert isinstance(events_path, Path)
    lines = events_path.read_text().splitlines()
    payload = json.loads(lines[5])
    payload["request_id"] = "request-step-6"
    lines[5] = json.dumps(payload, separators=(",", ":"))
    events_path.write_text("\n".join(lines) + "\n")
    output = phase147(
        case["start"], case["workflow"], case["employee"],
        case["state_path"], events_path,
    )
    assert type(output) is RunningStatePersistenceResult
    assert case["state_path"].read_bytes() == serialize_workflow_execution_state_json(
        case["start"].running_state
    ).encode()
    assert len(case["transport_calls"]) == 1

    # Canonical step-5 aged None + step-6 immediate None both remain valid.
    both = _canonical(tmp_path / "both-none")
    assert isinstance(both["events_path"], Path)
    lines = both["events_path"].read_text().splitlines()
    payload = json.loads(lines[5])
    payload["request_id"] = None
    lines[5] = json.dumps(payload, separators=(",", ":"))
    both["events_path"].write_text("\n".join(lines) + "\n")
    assert type(phase147(
        both["start"], both["workflow"], both["employee"],
        both["state_path"], both["events_path"],
    )) is RunningStatePersistenceResult

    # Earlier position 4 None and non-openai position 5 None stay rejected.
    for label, scenario in (
        ("position4", _scenario(tmp_path / "early-none", none_positions=(4,))),
        ("non-openai", _scenario(tmp_path / "provider", none_positions=(5,), non_openai_positions=(5,))),
    ):
        with pytest.raises(Phase147Error) as error:
            phase147(
                scenario["start"], scenario["workflow"], scenario["employee"],
                scenario["state_path"], scenario["events_path"],
                phase139_function=lambda *args, **kwargs: pytest.fail("not delegated"),
            )
        assert error.value.detail.classification == "terminal_contract", label


# 8. stop/read-only/error identity, compensation, and no retry are unchanged.
def test_08_unchanged_stop_readonly_error_behavior(tmp_path: Path) -> None:
    for failed in (False, True):
        stop = _stop_scenario(tmp_path / f"stop-{failed}", failed=failed)
        calls: list[tuple] = []
        output = phase147(
            stop["result"], stop["workflow"], None,
            stop["state_path"], stop["events_path"],
            phase139_function=lambda *args, _calls=calls, **kwargs: _calls.append((args, kwargs)),
        )
        assert output is stop["result"] and calls == []
        _assert_unchanged(stop)

    # Stop routes do not gain the accumulated exception.
    aged = _stop_scenario(tmp_path / "aged-stop")
    injected = _inject_aged_none(aged["events_path"])
    with pytest.raises(Phase147Error) as error:
        phase147(
            aged["result"], aged["workflow"], None,
            aged["state_path"], aged["events_path"],
            phase139_function=lambda *args: pytest.fail("stop dependency called"),
        )
    assert error.value.detail.classification == "terminal_contract"
    assert aged["events_path"].read_bytes() == injected

    # Prepared-route safe dependency identity and both-target compensation.
    case = _canonical(tmp_path / "safe")
    safe = Phase139Error("dependency_error")
    attempts: list[tuple] = []
    before = case["committed"]

    def failing_dependency(*args: object, **kwargs: object) -> object:
        attempts.append((args, kwargs))
        case["state_path"].write_bytes(b"mutated-state")
        case["events_path"].write_bytes(b"mutated-events")
        raise safe

    with pytest.raises(Phase139Error) as caught:
        phase147(
            case["start"], case["workflow"], case["employee"],
            case["state_path"], case["events_path"], phase139_function=failing_dependency,
        )
    assert caught.value is safe
    assert len(attempts) == 1
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before

    # Phase139 does the same for its Phase132 dependency.
    lower = _scenario(tmp_path / "lower-safe", none_positions=(5,))
    safe132 = Phase132Error("dependency_error")
    lower_attempts: list[tuple] = []

    def failing_lower(*args: object, **kwargs: object) -> object:
        lower_attempts.append((args, kwargs))
        lower["state_path"].write_bytes(b"mutated-state")
        lower["events_path"].write_bytes(b"mutated-events")
        raise safe132

    with pytest.raises(Phase132Error) as caught:
        phase139(
            lower["start"], lower["workflow"], lower["employee"],
            lower["state_path"], lower["events_path"], phase132_function=failing_lower,
        )
    assert caught.value is safe132
    assert len(lower_attempts) == 1
    _assert_unchanged(lower)

    # Persistence-contract strictness: malformed dependency results and invalid
    # persisted state are rejected once, then both targets are restored.
    phase147_case = _canonical(tmp_path / "phase147-persistence-contract")
    phase147_before = phase147_case["committed"]
    phase147_calls = 0

    def malformed_phase147(*args: object, **kwargs: object) -> object:
        nonlocal phase147_calls
        phase147_calls += 1
        phase147_case["state_path"].write_bytes(b"invalid persisted state")
        return object()

    with pytest.raises(Phase147Error) as caught:
        phase147(
            phase147_case["start"], phase147_case["workflow"], phase147_case["employee"],
            phase147_case["state_path"], phase147_case["events_path"],
            phase139_function=malformed_phase147,
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert phase147_calls == 1
    assert (phase147_case["state_path"].read_bytes(), phase147_case["events_path"].read_bytes()) == phase147_before

    phase139_case = _scenario(tmp_path / "phase139-persistence-contract", none_positions=(5,))
    phase139_calls = 0

    def malformed_phase139(*args: object, **kwargs: object) -> object:
        nonlocal phase139_calls
        phase139_calls += 1
        phase139_case["state_path"].write_bytes(b"invalid persisted state")
        return object()

    with pytest.raises(Phase139Error) as caught:
        phase139(
            phase139_case["start"], phase139_case["workflow"], phase139_case["employee"],
            phase139_case["state_path"], phase139_case["events_path"],
            phase132_function=malformed_phase139,
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert phase139_calls == 1
    _assert_unchanged(phase139_case)
