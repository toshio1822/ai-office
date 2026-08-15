"""Phase-155 prepared-step-start compatibility across Phase 146 -> 138 -> 131.

Issue #361 (Phase 174) compatibility repair: the real Phase 146 -> 138 -> 131
chain must accept the already-proven Phase 155 canonical provenance (persisted
step-6 success with immediate predecessor ``request_id=None`` and empty
``output_text``) and reach the unchanged lower chain (Phase 124 -> ... -> 33)
to return the exact ``PreparedStepExecutionStart`` for step 7.

Requirement-to-test mapping (Issue #361):
- source/default dependency audit Phase146 -> 138 -> 131 -> 124; no bypass /
  private cross-phase helper / shared-contract change
  -> test_source_default_dependency_audit_phase146_138_131_124
- real unchanged Phase 124 -> ... -> 33 canonical baseline succeeds / read-only
  -> test_real_unchanged_phase124_to_33_canonical_baseline
- real repaired full Phase 146 -> ... -> 33 canonical success with exact
  PreparedStepExecutionStart(step7)
  -> test_real_repaired_full_phase146_to_33_canonical_success
- Phase 146 bounded request-ID compatibility + inline narrowness
  -> test_phase146_bounded_request_id_compatibility_and_narrowness
- Phase 138 bounded request-ID compatibility + inline narrowness / unchanged stops
  -> test_phase138_bounded_request_id_compatibility_narrowness_and_stops
- Phase 131 bounded empty-output compatibility + inline narrowness and existing
  request_id=None acceptance
  -> test_phase131_bounded_empty_output_compatibility_and_narrowness
- malformed provider/history/order/completed-prefix/linkage/exact-type cases
  remain strict
  -> test_malformed_provider_history_order_completed_prefix_linkage_strict
- stop / read-only / compensation / no-retry behavior remains unchanged
  -> test_stop_read_only_compensation_no_retry_unchanged
"""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase146Error,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase146_route,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase138Error,
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138SafeError,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase138_route,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError as Phase131Error,
    route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary as phase131_route,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary as phase124_route,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_phase145,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

SOURCE_PHASE146 = (
    "src/ai_office/engine/"
    "prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
)
SOURCE_PHASE138 = (
    "src/ai_office/engine/"
    "prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
)
SOURCE_PHASE131 = (
    "src/ai_office/engine/"
    "prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
)
SOURCE_PHASE124 = (
    "src/ai_office/engine/"
    "prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary.py"
)
SOURCE_SHARED_CONTRACT = "src/ai_office/engine/terminal_history_contract.py"


def workflow(steps: int) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"step-{i}",
                }
                for i in range(1, steps + 1)
            ],
        }
    )


def predecessor_event(step_id: str, index: int, **changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            index,
            f"e{index}",
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def runtime_success(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            f"request-{step.id}",
            "completed",
            ("output",),
            "output",
        ),
    )


def approval_for(decision: WorkflowProgressionDecision) -> NextStepPreparationApproval:
    return NextStepPreparationApproval(
        True,
        decision.workflow_id,
        decision.current_step_id,
        decision.current_step_index,
        decision.next_step_id,
        decision.next_step_index,
        decision.next_employee_id,
    )


def employee_for(decision: WorkflowProgressionDecision) -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": decision.next_employee_id,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def start_for(prepared: PreparedWorkflowStep, wf: WorkflowDefinition) -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
        ),
        WorkflowExecutionState(
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
            tuple(step.id for step in wf.steps[: prepared.step_index - 1]),
            None,
        ),
    )


def canonical(
    base: Path, *, steps: int = 7, current: int = 6
) -> dict[str, object]:
    """Real Phase 172 durable step-``current`` success + real Phase 145 step-7 prepared.

    Reproduces the already-proven Phase 173/Phase 155 continuation with the
    exact canonical provenance: persisted step-6 succeeded, completed prefix
    step-1..step-6, ordered predecessor events steps 1..5 (steps 2/3/4 outputs
    exactly ``""``, immediate step-5 provider ``"openai"`` / output ``""`` /
    ``request_id=None``, earlier request IDs non-empty), and exact valid
    step-7 ``PreparedWorkflowStep``.
    """
    base.mkdir(parents=True, exist_ok=True)
    state_path, events_path = base / "state", base / "events"
    wf = workflow(steps)
    state = WorkflowExecutionState(
        "w",
        "running",
        wf.steps[current - 1].id,
        current,
        wf.steps[current - 1].employee,
        tuple(step.id for step in wf.steps[: current - 1]),
        None,
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = []
    for index in range(1, current):
        if index == current - 1:
            events.append(
                predecessor_event(
                    wf.steps[index - 1].id, index, output_text="", request_id=None
                )
            )
        elif index in (2, 3, 4):
            events.append(predecessor_event(wf.steps[index - 1].id, index, output_text=""))
        else:
            events.append(predecessor_event(wf.steps[index - 1].id, index))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )

    decision = route_runtime_result_to_progression_orchestration_boundary(
        runtime_success(wf, current), wf, state_path, events_path
    )
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "prepare_next_step"
    employee = employee_for(decision)
    prepared = public_phase145(
        decision,
        wf,
        approval_for(decision),
        employee,
        state_path,
        events_path,
    )
    assert type(prepared) is PreparedWorkflowStep
    assert prepared.step_index == current + 1
    return {
        "workflow": wf,
        "employee": employee,
        "prepared": prepared,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def manual_canonical(base: Path, *, steps: int = 6, current: int = 5) -> dict[str, object]:
    """Directly persisted canonical-style targets for index-5-and-below narrowness.

    The real Phase 172 route only persists running contexts at ``current_step_index
    >= 6``, so the bounded strictness check below index 6 is built directly with the
    same canonical shape: succeeded at ``current``, completed prefix, immediate
    predecessor ``request_id=None`` with empty output, and an exact valid prepared
    step for ``current + 1``.
    """
    base.mkdir(parents=True, exist_ok=True)
    state_path, events_path = base / "state", base / "events"
    wf = workflow(steps)
    current_step = wf.steps[current - 1]
    state = WorkflowExecutionState(
        wf.id,
        "succeeded",
        current_step.id,
        current,
        current_step.employee,
        tuple(step.id for step in wf.steps[:current]),
        None,
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = []
    for index in range(1, current):
        if index == current - 1:
            events.append(
                predecessor_event(
                    wf.steps[index - 1].id, index, output_text="", request_id=None
                )
            )
        elif index in (2, 3, 4):
            events.append(predecessor_event(wf.steps[index - 1].id, index, output_text=""))
        else:
            events.append(predecessor_event(wf.steps[index - 1].id, index))
    terminal = RuntimeStepEvent(
        "step_succeeded",
        wf.id,
        current_step.id,
        current,
        current_step.employee,
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{current_step.id}",
        f"request-{current_step.id}",
        f"output-{current_step.id}",
        None,
    )
    events.append(terminal)
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    next_step = wf.steps[current]
    employee = EmployeeDefinition.model_validate(
        {
            "id": next_step.employee,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )
    prepared = PreparedWorkflowStep(
        wf.id,
        next_step.id,
        current + 1,
        employee.id,
        employee.instructions,
        next_step.instructions,
        employee.model,
        tuple(employee.allowed_tools),
    )
    return {
        "workflow": wf,
        "employee": employee,
        "prepared": prepared,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def completion(wf: WorkflowDefinition) -> WorkflowProgressionDecision:
    final = wf.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        wf.id,
        final.id,
        len(wf.steps),
        final.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure",
        wf.id,
        step.id,
        index,
        step.employee,
        "api_error",
    )


def terminal_targets(
    base: Path, *, status: str, index: int
) -> tuple[Path, Path, bytes, bytes]:
    """Plain (non-Phase-155) durable terminal targets for stop-route checks."""
    base.mkdir(parents=True, exist_ok=True)
    state_path, events_path = base / "state", base / "events"
    wf = workflow(6)
    current = wf.steps[index - 1]
    completed = (
        tuple(step.id for step in wf.steps[:index])
        if status == "succeeded"
        else tuple(step.id for step in wf.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        wf.id,
        status,
        current.id,
        index,
        current.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        predecessor_event(step.id, position)
        for position, step in enumerate(wf.steps[: index - 1], 1)
    ]
    if status == "succeeded":
        terminal = RuntimeStepEvent(
            "step_succeeded",
            wf.id,
            current.id,
            index,
            current.employee,
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{current.id}",
            f"request-{current.id}",
            f"output-{current.id}",
            None,
        )
    else:
        terminal = RuntimeStepEvent(
            "step_failed",
            wf.id,
            current.id,
            index,
            current.employee,
            "running",
            "failed",
            "openai",
            "api_error",
            None,
            None,
            None,
            "safe failure",
        )
    events.append(terminal)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def rewrite_state(state: Path, **changes: object) -> None:
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload.update(changes)
    state.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def rewrite_event(events: Path, index: int, **changes: object) -> None:
    lines = events.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assert_terminal_contract(callable_object: object) -> None:
    with pytest.raises(Phase146Error) as caught:
        callable_object()  # type: ignore[operator]
    assert caught.value.detail.classification == "terminal_contract"


def test_source_default_dependency_audit_phase146_138_131_124() -> None:
    # Phase 146 default dependency is the public Phase 138 route.
    parameters = tuple(inspect.signature(phase146_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "phase138_function",
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase138_route

    # Phase 138 default dependency is the public Phase 131 route.
    parameters = tuple(inspect.signature(phase138_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "phase131_function",
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase131_route

    # Phase 131 default dependency is the public Phase 124 route.
    parameters = tuple(inspect.signature(phase131_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "phase124_function",
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase124_route

    # Phase 124 default dependency is the public Phase 117 route.
    parameters = tuple(inspect.signature(phase124_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "phase117_function",
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY

    # No bypass / private cross-phase helper / shared-contract change.
    source146 = Path(SOURCE_PHASE146).read_text(encoding="utf-8")
    source138 = Path(SOURCE_PHASE138).read_text(encoding="utf-8")
    source131 = Path(SOURCE_PHASE131).read_text(encoding="utf-8")
    source124 = Path(SOURCE_PHASE124).read_text(encoding="utf-8")
    shared = Path(SOURCE_SHARED_CONTRACT).read_text(encoding="utf-8")
    # Phase 146 must only depend on the public Phase 138 boundary.
    assert "phase131" not in source146.lower()
    assert "phase124" not in source146.lower()
    assert "terminal_history_contract" not in source146
    assert "._validate_" not in source146 and "._top" not in source146
    # Phase 138 must only depend on the public Phase 131 boundary.
    assert "phase124" not in source138.lower()
    assert "terminal_history_contract" not in source138
    assert "._validate_" not in source138 and "._top" not in source138
    # Phase 131 must only depend on the public Phase 124 boundary; its only
    # shared-contract use is the pre-existing load_strict_terminal_history.
    assert "phase117" not in source131.lower()
    assert source131.count("load_strict_terminal_history") >= 1
    # Phase 124 keeps the shared strict history contract (unchanged).
    assert "load_strict_terminal_history" in source124
    # The shared contract must not gain any new prepared-step gates.
    assert "allow_missing_immediate_request_id" not in shared
    assert "allow_empty_predecessor_output" not in shared
    assert "request_id" not in shared


def test_real_unchanged_phase124_to_33_canonical_baseline(tmp_path: Path) -> None:
    values = canonical(tmp_path / "baseline")
    wf = values["workflow"]
    result = phase124_route(
        values["prepared"],
        wf,
        values["employee"],
        values["state_path"],
        values["events_path"],
    )
    assert type(result) is PreparedStepExecutionStart
    assert result.running_state.current_step_id == "step-7"
    assert result.running_state.current_step_index == 7
    assert result.running_state.current_employee_id == "e7"
    assert result.running_state.completed_step_ids == tuple(
        step.id for step in wf.steps[:6]
    )
    # Read-only: committed bytes stay unchanged.
    assert values["state_path"].read_bytes() == values["state_before"]
    assert values["events_path"].read_bytes() == values["events_before"]


def test_real_repaired_full_phase146_to_33_canonical_success(tmp_path: Path) -> None:
    values = canonical(tmp_path / "full")
    wf = values["workflow"]
    result = phase146_route(
        values["prepared"],
        wf,
        values["employee"],
        values["state_path"],
        values["events_path"],
    )
    assert type(result) is PreparedStepExecutionStart
    request, running = result.request, result.running_state
    assert request.model == "model-name"
    assert request.system_instructions == "employee instructions"
    assert request.task_instructions == "step-7"
    assert request.allowed_tools == ("tool-one", "tool-two")
    assert running.workflow_id == "w"
    assert running.status == "running"
    assert running.current_step_id == "step-7"
    assert running.current_step_index == 7
    assert running.current_employee_id == "e7"
    assert running.completed_step_ids == tuple(step.id for step in wf.steps[:6])
    assert running.last_failure_category is None
    assert values["state_path"].read_bytes() == values["state_before"]
    assert values["events_path"].read_bytes() == values["events_before"]


def test_phase146_bounded_request_id_compatibility_and_narrowness(
    tmp_path: Path,
) -> None:
    values = canonical(tmp_path / "case146")
    wf = values["workflow"]
    returned = start_for(values["prepared"], wf)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    # Canonical immediate step-5 request_id=None is accepted for current index 6.
    assert (
        phase146_route(
            values["prepared"],
            wf,
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=fake,
        )
        is returned
    )
    assert calls == 1

    # Inline narrowness: immediate "" request ID remains rejected.
    values = canonical(tmp_path / "case146-empty")
    rewrite_event(values["events_path"], 4, request_id="")
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=fake,
        )
    )

    # Immediate invalid request-ID type remains rejected.
    values = canonical(tmp_path / "case146-type")
    rewrite_event(values["events_path"], 4, request_id=4)
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=fake,
        )
    )

    # Earlier predecessor request_id=None stays strict (only immediate allowed).
    values = canonical(tmp_path / "case146-earlier")
    rewrite_event(values["events_path"], 0, request_id=None)
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=fake,
        )
    )

    # Immediate predecessor provider must remain exactly "openai".
    values = canonical(tmp_path / "case146-provider")
    rewrite_event(values["events_path"], 4, provider="anthropic")
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=fake,
        )
    )

    # Index 5 and below stay strict: prepared step 6 (persisted current 5)
    # with immediate predecessor request_id=None is still rejected.
    values = manual_canonical(tmp_path / "case146-index5", steps=6, current=5)
    assert values["prepared"].step_index == 6
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=fake,
        )
    )


def test_phase138_bounded_request_id_compatibility_narrowness_and_stops(
    tmp_path: Path,
) -> None:
    values = canonical(tmp_path / "case138")
    wf = values["workflow"]
    returned = start_for(values["prepared"], wf)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    # Canonical immediate step-5 request_id=None is accepted through Phase 138.
    assert (
        phase138_route(
            values["prepared"],
            wf,
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase131_function=fake,
        )
        is returned
    )
    assert calls == 1

    # Inline narrowness: immediate "" / invalid type / earlier None rejected.
    values = canonical(tmp_path / "case138-empty")
    rewrite_event(values["events_path"], 4, request_id="")
    with pytest.raises(Phase138Error) as caught:
        phase138_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase131_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    values = canonical(tmp_path / "case138-type")
    rewrite_event(values["events_path"], 4, request_id=4)
    with pytest.raises(Phase138Error) as caught:
        phase138_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase131_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    values = canonical(tmp_path / "case138-earlier")
    rewrite_event(values["events_path"], 0, request_id=None)
    with pytest.raises(Phase138Error) as caught:
        phase138_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase131_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # Index 5 and below stay strict through Phase 138 as well.
    values = manual_canonical(tmp_path / "case138-index5", steps=6, current=5)
    with pytest.raises(Phase138Error) as caught:
        phase138_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase131_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # Unchanged stops: workflow_complete identity, dependency not called.
    wf6 = workflow(6)
    state, events, before_state, before_events = terminal_targets(
        tmp_path / "stop138", status="succeeded", index=6
    )
    completed = completion(wf6)
    stop_calls = 0

    def stop_fake(*_: object) -> object:
        nonlocal stop_calls
        stop_calls += 1
        return object()

    assert (
        phase138_route(completed, wf6, None, state, events, phase131_function=stop_fake)
        is completed
    )
    assert stop_calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase131_bounded_empty_output_compatibility_and_narrowness(
    tmp_path: Path,
) -> None:
    values = canonical(tmp_path / "case131")
    wf = values["workflow"]
    returned = start_for(values["prepared"], wf)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    # Canonical empty predecessor outputs (steps 2/3/4 and immediate step 5)
    # with step-5 request_id=None are accepted through Phase 131.
    assert (
        phase131_route(
            values["prepared"],
            wf,
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase124_function=fake,
        )
        is returned
    )
    assert calls == 1

    # Inline narrowness: None output remains rejected.
    values = canonical(tmp_path / "case131-none")
    rewrite_event(values["events_path"], 4, output_text=None)
    with pytest.raises(Phase131Error) as caught:
        phase131_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase124_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # Non-string output remains rejected.
    values = canonical(tmp_path / "case131-nonstr")
    rewrite_event(values["events_path"], 4, output_text=4)
    with pytest.raises(Phase131Error) as caught:
        phase131_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase124_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # Index 5 and below keep the old strict predecessor-output contract.
    values = manual_canonical(tmp_path / "case131-index5", steps=6, current=5)
    with pytest.raises(Phase131Error) as caught:
        phase131_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase124_function=fake,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # Existing request_id=None acceptance: earlier predecessor None is fine
    # because Phase 131 already permits optional request IDs.
    values = canonical(tmp_path / "case131-request-none")
    rewrite_event(values["events_path"], 0, request_id=None)
    calls = 0
    assert (
        phase131_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase124_function=fake,
        )
        is returned
    )
    assert calls == 1


def test_malformed_provider_history_order_completed_prefix_linkage_strict(
    tmp_path: Path,
) -> None:
    # Immediate predecessor provider must be exactly "openai".
    values = canonical(tmp_path / "malformed-provider")
    rewrite_event(values["events_path"], 4, provider="anthropic")
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
        )
    )

    # Scrambled history order stays strict.
    values = canonical(tmp_path / "malformed-order")
    lines = values["events_path"].read_text(encoding="utf-8").splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    values["events_path"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
        )
    )

    # Completed-prefix mismatch stays strict.
    values = canonical(tmp_path / "malformed-prefix")
    rewrite_state(
        values["state_path"],
        completed_step_ids=["step-1", "step-2", "step-3", "step-4"],
    )
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
        )
    )

    # Linkage mismatch (event workflow id) stays strict.
    values = canonical(tmp_path / "malformed-linkage")
    rewrite_event(values["events_path"], 0, workflow_id="other")
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
        )
    )

    # Exact-type mismatch (string step index) stays strict.
    values = canonical(tmp_path / "malformed-type")
    rewrite_state(values["state_path"], current_step_index="6")
    assert_terminal_contract(
        lambda: phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
        )
    )


def test_stop_read_only_compensation_no_retry_unchanged(tmp_path: Path) -> None:
    wf6 = workflow(6)

    # Stop: workflow_complete identity, dependency not called, read-only.
    state, events, before_state, before_events = terminal_targets(
        tmp_path / "stop-complete", status="succeeded", index=6
    )
    completed = completion(wf6)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert (
        phase146_route(completed, wf6, None, state, events, phase138_function=fake)
        is completed
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)

    # Stop: persisted_failure identity, dependency not called, read-only.
    state, events, before_state, before_events = terminal_targets(
        tmp_path / "stop-failure", status="failed", index=4
    )
    outcome = failure(wf6, 4)
    assert (
        phase146_route(outcome, wf6, None, state, events, phase138_function=fake)
        is outcome
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)

    # Compensation without retry: dependency mutates targets, then the mutated
    # return is rejected and the original bytes are restored (called once).
    values = canonical(tmp_path / "compensation")
    wf = values["workflow"]
    returned = start_for(values["prepared"], wf)
    calls = 0

    def mutating(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        values["state_path"].write_bytes(b"mutated-state")
        values["events_path"].write_bytes(b"mutated-events")
        return returned

    with pytest.raises(Phase146Error) as caught:
        phase146_route(
            values["prepared"],
            wf,
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=mutating,
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1
    assert values["state_path"].read_bytes() == values["state_before"]
    assert values["events_path"].read_bytes() == values["events_before"]

    # Safe dependency error identity preserved after compensation, no retry.
    values = canonical(tmp_path / "safe-error")
    calls = 0
    safe_error = Phase138SafeError("safe detail")

    def raising(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise safe_error

    with pytest.raises(Phase138SafeError) as caught:
        phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=raising,
        )
    assert caught.value is safe_error
    assert calls == 1
    assert values["state_path"].read_bytes() == values["state_before"]
    assert values["events_path"].read_bytes() == values["events_before"]

    # Unexpected dependency error is sanitized to dependency_error, no retry.
    values = canonical(tmp_path / "unexpected")
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret detail")

    with pytest.raises(Phase146Error) as caught:
        phase146_route(
            values["prepared"],
            values["workflow"],
            values["employee"],
            values["state_path"],
            values["events_path"],
            phase138_function=unexpected,
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert values["state_path"].read_bytes() == values["state_before"]
    assert values["events_path"].read_bytes() == values["events_before"]
