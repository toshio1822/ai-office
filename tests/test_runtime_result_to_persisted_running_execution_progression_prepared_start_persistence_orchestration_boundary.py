"""Focused regressions for Issue #396 Phase 181."""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
import inspect
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase147Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase139Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase180Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179CompatibilityError,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
)

phase181 = route_runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary

_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py"
)


def _harness():
    spec = importlib.util.spec_from_file_location("phase180_harness_for_181", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _authoritative_execution_approval(case: dict) -> object:
    workflow = case["workflow"]
    employee = case["employee"]
    step = workflow.steps[6]
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        step.instructions,
        tuple(employee.allowed_tools),
        (
            UpstreamStepOutput(
                workflow.id,
                workflow.steps[5].id,
                6,
                workflow.steps[5].employee,
                "output",
            ),
        ),
    )
    return approve_model_invocation_execution(
        request,
        case["tools"],
        provider="openai",
        approved_by="step-7",
        approval_id="approval-7",
    )


def _prepared_case(tmp_path: Path) -> tuple[dict, PreparedStepExecutionStart]:
    h = _harness()
    case = h._case(tmp_path)
    case["execution_approval"] = _authoritative_execution_approval(case)
    calls: list[object] = []
    start = h._call(case, transport=case["h"].success_transport(calls))
    assert type(start) is PreparedStepExecutionStart
    assert len(calls) == 1
    case["phase180_state_bytes"] = case["state_path"].read_bytes()
    case["phase180_event_bytes"] = case["events_path"].read_bytes()
    return case, start


def _args(case: dict, **overrides: object) -> list[object]:
    return [
        overrides.get("result", case["result"]),
        overrides.get("workflow", case["workflow"]),
        overrides.get("preparation_approval", case.get("approval")),
        overrides.get("employee", case.get("employee")),
        overrides.get("state_path", case["state_path"]),
        overrides.get("events_path", case["events_path"]),
        overrides.get("resolved_tools", case.get("tools")),
        overrides.get("api_key", case.get("api_key")),
        overrides.get("execution_approval", case.get("execution_approval")),
        overrides.get("transport", None),
        overrides.get("next_preparation_approval", case.get("next_approval")),
        overrides.get("next_employee", case.get("next_employee")),
    ]


def _classification(fn, expected: str) -> None:
    with pytest.raises(Phase181Error) as caught:
        fn()
    assert caught.value.detail.classification == expected
    assert "secret" not in str(caught.value)


def _stop_case(tmp_path: Path, *, failed: bool = False) -> tuple[dict, object]:
    spec = importlib.util.spec_from_file_location(
        "phase179_harness_for_181",
        Path(__file__).with_name(
            "test_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py"
        ),
    )
    assert spec is not None and spec.loader is not None
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    values = h.terminal_setup(tmp_path, steps=6)
    result = h.failure_outcome(values["workflow"], 6) if failed else h.complete_decision(values["workflow"], 6)
    case = {
        "result": result,
        "workflow": values["workflow"],
        "state_path": values["state_path"],
        "events_path": values["events_path"],
        "before": (values["state_path"].read_bytes(), values["events_path"].read_bytes()),
    }
    return case, result


def test_01_public_signature_defaults_exports_and_no_lower_calls() -> None:
    sig = inspect.signature(phase181)
    params = list(sig.parameters.values())
    assert [p.name for p in params[:12]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path",
        "events_path", "resolved_tools", "api_key", "execution_approval", "transport",
        "next_preparation_approval", "next_employee",
    ]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params[:12])
    assert [p.name for p in params[12:]] == ["phase180_function", "phase147_function"]
    assert params[12].default.__name__ == "route_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary"
    assert params[13].default is route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    assert issubclass(Phase181Error, RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryFailureDetail
    source = Path(phase181.__code__.co_filename).read_text()
    assert source.count("phase180_function(") == 1
    assert source.count("phase147_function(") == 1
    assert "phase139_function(" not in source and "phase132_function(" not in source
    assert "route_persisted_running_execution" not in source


def test_02_phase180_exactly_once_twelve_positional_no_kwargs(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "calls")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    def p180(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return start
    def p147(*args: object) -> object:
        data = serialize_workflow_execution_state_json(start.running_state).encode()
        args[3].write_bytes(data)
        return RunningStatePersistenceResult(len(data))
    phase181(*_args(case), phase180_function=p180, phase147_function=p147)
    assert len(calls) == 1 and len(calls[0][0]) == 12 and calls[0][1] == {}
    assert calls[0][0][0] is case["result"] and calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["approval"] and calls[0][0][3] is case["employee"]
    assert calls[0][0][10] is case["next_approval"] and calls[0][0][11] is case["next_employee"]


def test_03_phase147_exactly_once_five_positional_next_employee(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "phase147")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    def p147(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        state = args[0].running_state
        path = args[3]
        path.write_bytes(serialize_workflow_execution_state_json(state).encode())
        return RunningStatePersistenceResult(len(path.read_bytes()))
    out = phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147)
    assert type(out) is RunningStatePersistenceResult
    assert len(calls) == 1 and len(calls[0][0]) == 5 and calls[0][1] == {}
    assert calls[0][0] == (start, case["workflow"], case["next_employee"], case["state_path"], case["events_path"])


def test_04_real_phase180_to_phase147_accumulated_provenance_success(tmp_path: Path) -> None:
    h = _harness()
    case = h._case(tmp_path / "real")
    calls: list[object] = []
    out = phase181(*_args(case, transport=case["h"].success_transport(calls)))
    assert type(out) is RunningStatePersistenceResult
    assert len(calls) == 1
    assert load_workflow_execution_state(case["state_path"]).current_step_index == 8
    assert load_workflow_execution_state(case["state_path"]).status == "running"
    assert b'"current_step_index":8' in case["state_path"].read_bytes()
    assert b'"step_id":"step-8"' not in case["events_path"].read_bytes()


def test_05_next_employee_ownership_rejects_first_and_substitute(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "employee")
    seen: list[object] = []
    def p147(*args: object) -> object:
        seen.append(args[2])
        return RunningStatePersistenceResult(1)
    # Make the result valid without writing because the rejection occurs before
    # the dependency; the real Phase147-style employee contract is asserted by
    # the public dependency in the two negative cases below.
    _classification(lambda: phase181(*_args(case, next_employee=case["employee"]), phase180_function=lambda *a: start, phase147_function=p147), "phase180_contract")
    assert seen == []
    _classification(lambda: phase181(*_args(case, next_employee=object()), phase180_function=lambda *a: start, phase147_function=p147), "phase180_contract")
    assert seen == []


def test_06_original_workflow_complete_identity_zero_phase147(tmp_path: Path) -> None:
    case, stop = _stop_case(tmp_path / "complete")
    calls: list[object] = []
    out = phase181(*_args(case), phase180_function=lambda *a: stop, phase147_function=lambda *a: calls.append(a))
    assert out is stop and calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == case["before"]


def test_07_original_persisted_failure_identity_zero_phase147(tmp_path: Path) -> None:
    case, stop = _stop_case(tmp_path / "failure", failed=True)
    calls: list[object] = []
    out = phase181(*_args(case), phase180_function=lambda *a: stop, phase147_function=lambda *a: calls.append(a))
    assert out is stop and calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == case["before"]


def test_08_runtime_to_workflow_complete_stop_direct_return(tmp_path: Path) -> None:
    h = _harness()
    case = h._case(tmp_path / "runtime-complete", steps=7, current=6)
    stop, _ = h._real_phase179(case)
    assert type(stop) is WorkflowProgressionDecision
    before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    calls: list[object] = []
    out = phase181(*_args(case), phase180_function=lambda *a: stop, phase147_function=lambda *a: calls.append(a))
    assert out is stop and calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before


def test_09_runtime_to_persisted_failure_stop_direct_return(tmp_path: Path) -> None:
    h = _harness()
    case = h._case(tmp_path / "runtime-failure")
    stop, _ = h._real_phase179(case, transport=case["h"].failure_transport([]))
    assert type(stop) is PersistedExecutionOutcome
    before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    calls: list[object] = []
    out = phase181(*_args(case), phase180_function=lambda *a: stop, phase147_function=lambda *a: calls.append(a))
    assert out is stop and calls == []
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == before


def test_10_strict_initial_result_and_workflow_domain(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "domain")
    class Sub(type(case["result"])):
        pass
    sub = Sub(case["result"].workflow_id, case["result"].step_id, case["result"].step_index, case["result"].employee_id, case["result"].invocation_result)
    for value in (sub, object(), WorkflowProgressionDecision("prepare_next_step", "w", "step-6", 6, "e6", "step-7", 7, "e7", "next_step_available"), PersistedExecutionOutcome("persisted_success", "w", "step-6", 6, "e6", "api_error")):
        _classification(lambda value=value: phase181(*_args(case, result=value), phase180_function=lambda *a: pytest.fail("Phase180 called"), phase147_function=lambda *a: pytest.fail("Phase147 called")), "result_type")
    _classification(lambda: phase181(*_args(case, workflow=object()), phase180_function=lambda *a: pytest.fail("called"), phase147_function=lambda *a: pytest.fail("called")), "workflow_definition")


def test_11_target_and_configuration_contract_precedes_dependencies(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "configuration")
    calls: list[object] = []
    def p180(*args: object) -> object:
        calls.append(args)
        return None
    _classification(lambda: phase181(*_args(case, state_path="bad"), phase180_function=p180, phase147_function=p180), "state_target")
    _classification(lambda: phase181(*_args(case, events_path=object()), phase180_function=p180, phase147_function=p180), "event_target")
    _classification(lambda: phase181(*_args(case, events_path=case["state_path"]), phase180_function=p180, phase147_function=p180), "target_conflict")
    _classification(lambda: phase181(*_args(case), phase180_function=None, phase147_function=p180), "configuration")
    missing = case["state_path"].parent / "missing"
    _classification(lambda: phase181(*_args(case, state_path=missing), phase180_function=p180, phase147_function=p180), "state_target")
    assert calls == []


def test_12_later_context_is_not_prevalidated_before_phase180(tmp_path: Path) -> None:
    case, stop = _stop_case(tmp_path / "non-prevalidation")
    calls: list[tuple[object, ...]] = []
    def p180(*args: object) -> object:
        calls.append(args)
        return stop
    out = phase181(*_args(case, preparation_approval=None, employee=None, resolved_tools=None, api_key=None, execution_approval=None, transport=None, next_preparation_approval=None, next_employee=None), phase180_function=p180, phase147_function=lambda *a: pytest.fail("called"))
    assert out is stop and len(calls) == 1


def test_13_phase180_safe_error_identity_no_pre_rollback(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "safe")
    before = case["state_path"].read_bytes()
    for safe in (Phase180Error("dependency_error"), Phase179CompatibilityError("dependency_error")):
        def p180(*args: object, safe=safe) -> object:
            case["state_path"].write_bytes(before + b"mutated")
            raise safe
        with pytest.raises(type(safe)) as caught:
            phase181(*_args(case), phase180_function=p180, phase147_function=lambda *a: pytest.fail("called"))
        assert caught.value is safe
        case["state_path"].write_bytes(before)


def test_14_unexpected_phase180_error_is_sanitized_without_rollback(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "unexpected")
    before = case["events_path"].read_bytes()
    def p180(*args: object) -> object:
        case["events_path"].write_bytes(before + b"secret")
        raise RuntimeError("secret dependency detail")
    _classification(lambda: phase181(*_args(case), phase180_function=p180, phase147_function=lambda *a: pytest.fail("called")), "dependency_error")
    assert case["events_path"].read_bytes() == before + b"secret"


def test_15_malformed_phase180_output_has_no_phase180_rollback(tmp_path: Path) -> None:
    case, _ = _prepared_case(tmp_path / "malformed")
    before = case["state_path"].read_bytes()
    def p180(*args: object) -> object:
        case["state_path"].write_bytes(before + b"owned")
        return object()
    _classification(lambda: phase181(*_args(case), phase180_function=p180, phase147_function=lambda *a: pytest.fail("called")), "phase180_contract")
    assert case["state_path"].read_bytes() == before + b"owned"


def test_16_phase147_and_phase139_safe_errors_compensate_exactly(tmp_path: Path) -> None:
    for error_type in (Phase147Error, Phase139Error):
        case, start = _prepared_case(tmp_path / error_type.__name__)
        committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        safe = error_type("terminal_contract")
        def p147(*args: object, safe=safe) -> object:
            case["state_path"].write_bytes(b"state-mutated")
            case["events_path"].write_bytes(b"events-mutated")
            raise safe
        with pytest.raises(type(safe)) as caught:
            phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147)
        assert caught.value is safe
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_17_unexpected_phase147_error_sanitizes_and_compensates(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "unexpected147")
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    def p147(*args: object) -> object:
        case["state_path"].write_bytes(b"bad")
        case["events_path"].write_bytes(b"bad-events")
        raise RuntimeError("phase147 secret")
    _classification(lambda: phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147), "dependency_error")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_18_malformed_phase147_result_compensates_to_post_phase180_bytes(tmp_path: Path) -> None:
    for malformed in (object(), RunningStatePersistenceResult(0), RunningStatePersistenceResult(-1), RunningStatePersistenceResult(True)):
        case, start = _prepared_case(tmp_path / f"malformed-{len(str(malformed))}")
        committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        def p147(*args: object, malformed=malformed) -> object:
            case["state_path"].write_bytes(b"wrong")
            return malformed
        _classification(lambda: phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147), "phase147_contract")
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_19_mutation_and_rollback_failure_attempt_both_targets_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case, start = _prepared_case(tmp_path / "mutation")
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    def p147(*args: object) -> object:
        case["state_path"].write_bytes(b"wrong-state")
        case["events_path"].write_bytes(b"wrong-events")
        return RunningStatePersistenceResult(1)
    _classification(lambda: phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147), "committed_mutation")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed
    case, start = _prepared_case(tmp_path / "rollback")
    attempts: list[Path] = []
    state = case["state_path"]
    events = case["events_path"]
    def fail_restore(path: Path, contents: bytes) -> None:
        attempts.append(path)
        raise OSError("restore blocked")
    def p147_fail(*args: object) -> object:
        state.unlink(); state.mkdir()
        events.unlink()
        with events.open("wb") as handle:
            handle.write(b"mutated")
        raise Phase139Error("terminal_contract")
    monkeypatch.setattr(Path, "write_bytes", fail_restore)
    _classification(lambda: phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147_fail), "rollback_failure")
    assert attempts == [state, events]


def test_20_success_establishes_running_commit_and_stops_without_execution(tmp_path: Path) -> None:
    case, start = _prepared_case(tmp_path / "ownership")
    calls: list[tuple[object, ...]] = []
    def p147(*args: object) -> object:
        calls.append(args)
        state = args[0].running_state
        path = args[3]
        data = serialize_workflow_execution_state_json(state).encode()
        path.write_bytes(data)
        return RunningStatePersistenceResult(len(data))
    out = phase181(*_args(case), phase180_function=lambda *a: start, phase147_function=p147)
    assert type(out) is RunningStatePersistenceResult
    assert out is not None and len(calls) == 1
    assert load_workflow_execution_state(case["state_path"]) == start.running_state
    assert start.running_state.current_step_index == 8
    assert case["events_path"].read_bytes() == case["phase180_event_bytes"]
    assert start.running_state.current_step_index == 8
