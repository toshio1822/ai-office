"""Focused Issue #402 Phase 184 composition contract tests."""

# ruff: noqa: E501,I001

import importlib.util
import inspect
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase184Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase145,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary as phase184,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary as phase183,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase183Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179Error,
)
from ai_office.runtime import StepRuntimeExecutionSuccess
from ai_office.storage import load_workflow_execution_state

_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py"
)


def _harness():
    spec = importlib.util.spec_from_file_location("phase179_helpers_for_184", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _approval_employee(wf: object, index: int) -> tuple[NextStepPreparationApproval, EmployeeDefinition]:
    h = _harness()
    decision = h.prepare_decision(wf, index)  # type: ignore[arg-type]
    return h.approval_for(decision), h.employee_for(decision)


def _execution_approval(wf: object, index: int) -> object:
    h = _harness()
    step = wf.steps[index - 1]  # type: ignore[union-attr]
    employee = EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": f"Employee {index}",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )
    request = h.ModelInvocationRequest(
        employee.model,
        employee.instructions,
        step.instructions,
        tuple(employee.allowed_tools),
    )
    return h.approve_model_invocation_execution(
        request,
        h.TOOLS,
        provider="openai",
        approved_by=f"step-{index}",
        approval_id=f"approval-{index}",
    )


def _scenario(tmp_path: Path, *, steps: int = 9) -> dict[str, object]:
    h = _harness()
    values = h.running_setup(tmp_path, steps=steps, current=6)
    wf = values["workflow"]
    approval7, employee7 = _approval_employee(wf, 6) if steps >= 7 else (None, None)
    approval8, employee8 = _approval_employee(wf, 7) if steps >= 8 else (None, None)
    following = _approval_employee(wf, 8) if steps >= 9 else (None, None)
    return {
        **values,
        "h": h,
        "result": h.runtime_success(wf, 6, request_id_none=True),
        "approval": approval7,
        "employee": employee7,
        "next_approval": approval8,
        "next_employee": employee8,
        "following_approval": following[0],
        "following_employee": following[1],
        "tools": h.TOOLS,
        "api_key": h.OpenAIApiKey(value=SecretStr("synthetic")),
        "execution_approval": _execution_approval(wf, 7) if steps >= 7 else None,
        "next_execution_approval": _execution_approval(wf, 8) if steps >= 8 else None,
    }


def _args(case: dict[str, object], **overrides: object) -> list[object]:
    names = (
        "result", "workflow", "approval", "employee", "state_path", "events_path",
        "tools", "api_key", "execution_approval", "transport", "next_approval",
        "next_employee", "tools", "api_key", "next_execution_approval", "next_transport",
        "following_approval", "following_employee",
    )
    values = (
        case["result"], case["workflow"], case["approval"], case["employee"],
        case["state_path"], case["events_path"], case["tools"], case["api_key"],
        case["execution_approval"], None, case["next_approval"], case["next_employee"],
        case["tools"], case["api_key"], case["next_execution_approval"], None,
        case["following_approval"], case["following_employee"],
    )
    return [overrides.get(name, value) for name, value in zip(names, values, strict=True)]


def _committed(tmp_path: Path, *, steps: int = 9) -> dict[str, object]:
    case = _scenario(tmp_path, steps=steps)
    h = case["h"]
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = phase183(
        *_args(
            case,
            transport=h.success_transport(first_calls),  # type: ignore[attr-defined]
            next_transport=h.success_transport(second_calls),  # type: ignore[attr-defined]
        )[:16]
    )
    case["progressed"] = out
    case["committed"] = (
        case["state_path"].read_bytes(),
        case["events_path"].read_bytes(),
    )
    case["transport_calls"] = (first_calls, second_calls)
    return case


def _call(case: dict[str, object], **overrides: object) -> object:
    dependencies = {
        "phase183_function": overrides.pop("phase183_function", phase183),
        "phase145_function": overrides.pop("phase145_function", phase145),
    }
    return phase184(*_args(case, **overrides), **dependencies)


def _classification(fn, expected: str) -> None:
    with pytest.raises(Phase184Error) as caught:
        fn()
    assert caught.value.detail.classification == expected
    assert "secret" not in str(caught.value)


def _prepared(case: dict[str, object], decision: WorkflowProgressionDecision, employee: EmployeeDefinition) -> PreparedWorkflowStep:
    step = case["workflow"].steps[decision.next_step_index - 1]  # type: ignore[union-attr]
    return PreparedWorkflowStep(
        case["workflow"].id, step.id, decision.next_step_index, employee.id,
        employee.instructions, step.instructions, employee.model, tuple(employee.allowed_tools),
    )


def test_01_public_api_signature_exports_and_source_boundary() -> None:
    params = list(inspect.signature(phase184).parameters.values())
    assert [p.name for p in params[:18]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval",
        "next_employee", "next_resolved_tools", "next_api_key", "next_execution_approval",
        "next_transport", "following_preparation_approval", "following_employee",
    ]
    assert [p.name for p in params[18:]] == ["phase183_function", "phase145_function"]
    assert params[18].default is phase183 and params[19].default is phase145
    assert issubclass(Phase184Error, RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail
    source = Path(phase184.__code__.co_filename).read_text()
    assert source.count("phase183_function(") == 1
    assert source.count("phase145_function(") == 1
    assert "phase179_function" not in source and "phase178_function" not in source
    assert "route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary(" not in source
    assert "route_runtime_result_to_progression_orchestration_boundary(" not in source
    assert "while " not in source[source.index("def route_") : source.index("def _check_inputs")]


def test_02_phase183_exactly_once_canonical_sixteen_positional_no_kwargs(tmp_path: Path) -> None:
    case = _committed(tmp_path / "call")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    decision = case["progressed"]

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return decision

    out = _call(case, phase183_function=dependency, phase145_function=lambda *args: _prepared(case, decision, case["following_employee"]))
    assert out.step_id == "step-9"  # type: ignore[union-attr]
    assert len(calls) == 1 and len(calls[0][0]) == 16 and calls[0][1] == {}
    assert calls[0][0][0] is case["result"] and calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["approval"] and calls[0][0][3] is case["employee"]
    assert calls[0][0][10] is case["next_approval"] and calls[0][0][11] is case["next_employee"]
    assert calls[0][0][12] is case["tools"] and calls[0][0][14] is case["next_execution_approval"]


def test_03_phase145_exactly_once_six_positional_following_pair_no_kwargs(tmp_path: Path) -> None:
    case = _committed(tmp_path / "phase145")
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    decision = case["progressed"]

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return _prepared(case, decision, case["following_employee"])

    out = _call(case, phase183_function=lambda *args: decision, phase145_function=dependency)
    assert out.step_id == "step-9"  # type: ignore[union-attr]
    assert len(calls) == 1 and len(calls[0][0]) == 6 and calls[0][1] == {}
    assert calls[0][0][0] is decision and calls[0][0][1] is case["workflow"]
    assert calls[0][0][2] is case["following_approval"]
    assert calls[0][0][3] is case["following_employee"]
    assert calls[0][0][4] is case["state_path"] and calls[0][0][5] is case["events_path"]


def test_04_three_contexts_are_distinct_and_following_pair_not_prevalidated(tmp_path: Path) -> None:
    h = _harness()
    values = h.terminal_setup(tmp_path / "contexts", steps=6)
    stop = h.complete_decision(values["workflow"], 6)
    seen: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        seen.append(args)
        return stop

    out = phase184(
        stop, values["workflow"], object(), object(), values["state_path"], values["events_path"],
        object(), object(), object(), object(), object(), object(), object(), object(), object(), object(),
        object(), object(), phase183_function=dependency,
    )
    assert out is stop and len(seen) == 1 and len(seen[0]) == 16
    assert seen[0][2] is not seen[0][10] and seen[0][3] is not seen[0][11]


def test_05_original_workflow_complete_is_exact_identity_and_phase145_zero(tmp_path: Path) -> None:
    h = _harness()
    values = h.terminal_setup(tmp_path / "complete", steps=6)
    stop = h.complete_decision(values["workflow"], 6)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    out = phase184(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None, None, None,
        phase183_function=lambda *args: stop, phase145_function=lambda *args: calls.append(args),
    )
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_06_original_persisted_failure_is_exact_identity_and_phase145_zero(tmp_path: Path) -> None:
    h = _harness()
    values = h.terminal_setup(tmp_path / "failure", steps=6, fail=True)
    stop = h.failure_outcome(values["workflow"], 6)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    out = phase184(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None, None, None,
        phase183_function=lambda *args: stop, phase145_function=lambda *args: calls.append(args),
    )
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_07_runtime_first_continuation_workflow_complete_bypasses_phase145(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "first-complete", steps=7)
    calls: list[object] = []
    h = case["h"]
    out = _call(case, transport=h.success_transport([]), next_transport=h.success_transport([]), phase145_function=lambda *args: calls.append(args))
    assert type(out) is WorkflowProgressionDecision and out.decision == "workflow_complete"
    assert out.current_step_index == 7 and calls == []


def test_08_runtime_first_continuation_failure_bypasses_phase145(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "first-failure", steps=8)
    h = case["h"]
    calls: list[object] = []
    out = _call(case, transport=h.failure_transport([]), next_transport=h.success_transport([]), phase145_function=lambda *args: calls.append(args))
    assert type(out) is PersistedExecutionOutcome and out.outcome == "persisted_failure"
    assert out.current_step_index == 7 and calls == []


def test_09_runtime_second_continuation_workflow_complete_bypasses_phase145(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "second-complete", steps=8)
    h = case["h"]
    calls: list[object] = []
    out = _call(case, transport=h.success_transport([]), next_transport=h.success_transport([]), phase145_function=lambda *args: calls.append(args))
    assert type(out) is WorkflowProgressionDecision and out.decision == "workflow_complete"
    assert out.current_step_index == 8 and calls == []


def test_10_runtime_second_continuation_failure_bypasses_phase145(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "second-failure", steps=9)
    h = case["h"]
    calls: list[object] = []
    out = _call(case, transport=h.success_transport([]), next_transport=h.failure_transport([]), phase145_function=lambda *args: calls.append(args))
    assert type(out) is PersistedExecutionOutcome and out.outcome == "persisted_failure"
    assert out.current_step_index == 8 and calls == []


def test_11_real_nine_step_success_returns_prepared_step9_and_no_readvance(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "real-nine")
    h = case["h"]
    first_calls: list[object] = []
    second_calls: list[object] = []
    before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    out = _call(case, transport=h.success_transport(first_calls), next_transport=h.success_transport(second_calls))
    assert type(out) is PreparedWorkflowStep
    assert out.step_id == "step-9" and out.step_index == 9 and out.employee_id == case["following_employee"].id
    assert len(first_calls) == 1 and len(second_calls) == 1
    assert load_workflow_execution_state(case["state_path"]).current_step_index == 8
    assert "step-9" not in case["events_path"].read_text()
    # Phase145 is read-only on success; the post-Phase183 bytes are the final bytes.
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) != before


def test_12_existing_provenance_contract_is_reused_for_aged_none_and_older_provider(tmp_path: Path) -> None:
    for label, changes in (
        ("older-provider", {"provider": "other", "request_id": "older-request"}),
        ("aged-none", {"provider": "openai", "request_id": None}),
    ):
        case = _committed(tmp_path / label)
        lines = [json.loads(line) for line in case["events_path"].read_text().splitlines()]
        position = 2 if changes["request_id"] is not None else 5
        lines[position - 1].update(changes)
        case["events_path"].write_text("".join(json.dumps(line) + "\n" for line in lines))
        out = _call(case, phase183_function=lambda *args: case["progressed"])
        assert type(out) is PreparedWorkflowStep and out.step_id == "step-9"
    invalid = _committed(tmp_path / "invalid")
    lines = [json.loads(line) for line in invalid["events_path"].read_text().splitlines()]
    lines[4]["request_id"] = None
    lines[4]["provider"] = "other"
    invalid["events_path"].write_text("".join(json.dumps(line) + "\n" for line in lines))
    with pytest.raises(Phase145Error):
        _call(invalid, phase183_function=lambda *args: invalid["progressed"])


def test_13_phase183_safe_error_identity_zero_phase145_and_no_outer_rollback(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "safe")
    before = case["state_path"].read_bytes()
    safe = Phase183Error("phase182_contract")
    calls: list[object] = []

    def dependency(*args: object) -> object:
        case["state_path"].write_bytes(before + b"owned")
        raise safe

    with pytest.raises(Phase183Error) as caught:
        _call(case, phase183_function=dependency, phase145_function=lambda *args: calls.append(args))
    assert caught.value is safe and calls == [] and case["state_path"].read_bytes() == before + b"owned"


def test_14_unexpected_phase183_error_is_sanitized_without_outer_rollback(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "unexpected")
    before = case["events_path"].read_bytes()

    def dependency(*args: object) -> object:
        case["events_path"].write_bytes(before + b"secret")
        raise RuntimeError("secret provider payload")

    _classification(lambda: _call(case, phase183_function=dependency), "dependency_error")
    assert case["events_path"].read_bytes() == before + b"secret"


def test_15_malformed_phase183_output_and_snapshot_mismatch_are_contract_errors(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "malformed")
    calls: list[object] = []
    _classification(lambda: _call(case, phase183_function=lambda *args: object(), phase145_function=lambda *args: calls.append(args)), "phase183_contract")
    assert calls == []
    case = _committed(tmp_path / "mismatch")
    decision = case["progressed"]
    state_text = case["state_path"].read_text()
    case["state_path"].write_text(
        state_text.replace('"current_step_index":8', '"current_step_index":7')
        .replace('"current_step_index": 8', '"current_step_index": 7')
    )
    _classification(lambda: _call(case, phase183_function=lambda *args: decision, phase145_function=lambda *args: calls.append(args)), "phase183_contract")
    assert calls == []


def test_16_invalid_following_pair_is_checked_only_after_phase183_commit(tmp_path: Path) -> None:
    case = _committed(tmp_path / "following")
    committed = case["committed"]
    for approval, employee in (
        (case["next_approval"], case["next_employee"]),
        (case["approval"], case["employee"]),
        (None, None),
    ):
        with pytest.raises(Phase145Error):
            phase184(
                *_args(case, following_approval=approval, following_employee=employee)[:16],
                approval, employee,
                phase183_function=lambda *args: case["progressed"],
            )
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_17_malformed_phase145_return_compensates_to_post_phase183_snapshot(tmp_path: Path) -> None:
    case = _committed(tmp_path / "malformed145")
    committed = case["committed"]

    def dependency(*args: object) -> object:
        case["events_path"].write_bytes(committed[1] + b"mutation")
        return object()

    _classification(lambda: _call(case, phase183_function=lambda *args: case["progressed"], phase145_function=dependency), "phase145_contract")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_18_phase145_safe_unexpected_mutation_and_rollback_failure_matrices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _committed(tmp_path / "phase145-errors")
    committed = case["committed"]
    safe = Phase145Error("employee_contract")

    def safe_dependency(*args: object) -> object:
        case["state_path"].write_bytes(committed[0] + b"safe")
        raise safe

    with pytest.raises(Phase145Error) as caught:
        _call(case, phase183_function=lambda *args: case["progressed"], phase145_function=safe_dependency)
    assert caught.value is safe
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed

    def unexpected(*args: object) -> object:
        case["events_path"].write_bytes(committed[1] + b"unexpected")
        raise RuntimeError("secret")

    _classification(lambda: _call(case, phase183_function=lambda *args: case["progressed"], phase145_function=unexpected), "dependency_error")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed

    def mutate(*args: object) -> object:
        case["state_path"].write_bytes(committed[0] + b"mutation")
        return _prepared(case, case["progressed"], case["following_employee"])

    _classification(lambda: _call(case, phase183_function=lambda *args: case["progressed"], phase145_function=mutate), "committed_mutation")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed

    production = __import__(phase184.__module__, fromlist=["_restore_if_changed"])
    attempts: list[Path] = []
    original_write = Path.write_bytes

    mutation_written = False

    def failing_write(path: Path, data: bytes) -> int:
        nonlocal mutation_written
        attempts.append(path)
        if path == case["events_path"] and mutation_written:
            raise OSError("restore failure")
        if path == case["events_path"]:
            mutation_written = True
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    _classification(lambda: _call(case, phase183_function=lambda *args: case["progressed"], phase145_function=unexpected), "rollback_failure")
    assert case["state_path"] in attempts and case["events_path"] in attempts
    assert production is not None


def test_19_no_readvance_no_lower_call_and_no_retry_after_prepared_step(tmp_path: Path) -> None:
    case = _committed(tmp_path / "stop")
    calls = {"p183": 0, "p145": 0}
    decision = case["progressed"]
    prepared = _prepared(case, decision, case["following_employee"])

    def p183(*args: object) -> object:
        calls["p183"] += 1
        return decision

    def p145(*args: object) -> object:
        calls["p145"] += 1
        return prepared

    out = _call(case, phase183_function=p183, phase145_function=p145)
    assert out is prepared and calls == {"p183": 1, "p145": 1}
    assert load_workflow_execution_state(case["state_path"]).current_step_index == 8
    source = Path(phase184.__code__.co_filename).read_text()
    assert "route_prepared_step_start" not in source
    assert "route_persisted_running_execution_cycle" not in source


def test_20_strict_input_targets_dependencies_and_safe_error_surface(tmp_path: Path) -> None:
    case = _scenario(tmp_path / "domain")
    h = case["h"]
    class SubSuccess(StepRuntimeExecutionSuccess):
        pass
    result = case["result"]
    substitute = SubSuccess(result.workflow_id, result.step_id, result.step_index, result.employee_id, result.invocation_result)
    for value in (object(), substitute, h.prepare_decision(case["workflow"], 6), PersistedExecutionOutcome("persisted_success", "w", "step-6", 6, "e6", None)):
        _classification(lambda value=value: _call(case, result=value), "result_type")
    _classification(lambda: _call(case, workflow=object()), "workflow_definition")
    _classification(lambda: _call(case, state_path="not-path"), "state_target")
    _classification(lambda: _call(case, events_path=case["state_path"]), "target_conflict")
    _classification(lambda: _call(case, phase183_function=None), "configuration")
    _classification(lambda: _call(case, phase145_function=object()), "configuration")
    assert "secret" not in str(Phase179Error("dependency_error"))
