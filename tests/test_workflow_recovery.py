"""Behavioral coverage for explicit Run-bound execution recovery."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
import ai_office.providers.openai.responses_execution as responses_execution_module
from ai_office.cli import app
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    list_run_artifacts,
    load_workflow_run_manifest,
    loaded_employees_from_run_manifest,
    loaded_workflow_from_run_manifest,
    tool_catalog_from_run_manifest,
)
from ai_office.engine.workflow_approval_evidence import (
    build_execution_approval_evidence_for_tools,
    build_recovery_approval_evidence,
    persist_execution_approval_evidence,
    persist_recovery_approval_evidence,
)
from ai_office.engine.workflow_recovery import (
    WorkflowRecoveryError,
    assess_workflow_recovery,
    validate_workflow_recovery_authorization,
)
from ai_office.execution_evidence import (
    ExecutionEvidencePersistenceError,
    build_execution_evidence_context,
    claim_execution_attempt,
    execution_attempt_evidence_path,
    execution_normalized_result_evidence_path,
    execution_raw_response_evidence_path,
    list_run_execution_evidence,
    load_normalized_result_evidence,
    load_raw_response_evidence,
    persist_raw_response_evidence,
)
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
    build_model_invocation_request,
)
from ai_office.planning.execution_plan import build_execution_plan
from ai_office.planning.step_execution_request import build_step_execution_request
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
    build_openai_responses_http_request_from_invocation,
    execute_openai_model_invocation,
)
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.runtime.executed_step_transition_persistence import (
    persist_executed_step_transition,
)
from ai_office.storage import serialize_workflow_execution_state_json
from tests._run_test_support import TestRun as RunFixture
from tests._run_test_support import create_test_run

runner = CliRunner()


@dataclass(frozen=True)
class RecoveryFixture:
    run: RunFixture
    workflow: WorkflowDefinition
    employee: EmployeeDefinition
    request: ModelInvocationRequest
    approval: ModelInvocationExecutionApproval
    execution_context: object


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "recovery-workflow",
            "name": "Recovery Workflow",
            "description": "One immutable recovery step.",
            "steps": [
                {
                    "id": "recoverable-step",
                    "name": "Recoverable Step",
                    "employee": "recovery-employee",
                    "instructions": "Return a short answer.",
                    "business_approval_required": False,
                    "artifact_content_type": "text/plain",
                }
            ],
        }
    )


def _employee() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="recovery-employee",
        name="Recovery Employee",
        role="Recovery test employee",
        instructions="Follow the pinned request exactly.",
        model="recovery-model",
        allowed_tools=[],
    )


def _fixture(tmp_path: Path, run_id: str) -> RecoveryFixture:
    workflow = _workflow()
    employee = _employee()
    store_root = tmp_path / "runs"
    run = create_test_run(
        store_root,
        run_id,
        workflow,
        (employee,),
        with_business_approvals=False,
    )
    manifest = load_workflow_run_manifest(run.store, run_id)
    pinned_workflow = loaded_workflow_from_run_manifest(manifest)
    pinned_employees = loaded_employees_from_run_manifest(manifest)
    plan = build_execution_plan(pinned_workflow, pinned_employees)
    step_request = build_step_execution_request(
        plan,
        1,
        pinned_employees,
        run_id=run.binding.run_id,
        manifest_digest=run.binding.manifest_digest,
    )
    request = build_model_invocation_request(
        step_request,
        run_input=manifest.run_input,
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="initial-operator",
        approval_id=f"initial-execution-{run_id}",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    approval_evidence = build_execution_approval_evidence_for_tools(
        request,
        (),
        approval,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=employee.id,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    persist_execution_approval_evidence(run.store, approval_evidence)
    state = WorkflowExecutionState(
        workflow_id=workflow.id,
        status="running",
        current_step_id=workflow.steps[0].id,
        current_step_index=1,
        current_employee_id=employee.id,
        completed_step_ids=(),
        last_failure_category=None,
        binding=run.binding,
    )
    run.state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    run.events_path.write_text("", encoding="utf-8")
    execution_context = build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=employee.id,
        request=request,
        resolved_tools=(),
        approval=approval,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    return RecoveryFixture(
        run, workflow, employee, request, approval, execution_context
    )


def _success_response(text: str = "recovered output") -> OpenAIResponsesRawHttpResponse:
    body = json.dumps(
        {
            "id": "recovery-response",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        }
    ).encode("utf-8")
    return OpenAIResponsesRawHttpResponse(
        200, "synthetic", (("content-type", "application/json"),), body
    )


def _api_failure_response() -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        500,
        "synthetic",
        (("content-type", "application/json"),),
        b'{"error":{"message":"synthetic failure","type":"server_error",'
        b'"param":null,"code":"server_error"}}',
    )


def _claim_only(fixture: RecoveryFixture) -> None:
    catalog = tool_catalog_from_run_manifest(
        load_workflow_run_manifest(fixture.run.store, fixture.run.binding.run_id)
    )
    request = build_openai_responses_http_request_from_invocation(
        fixture.request,
        catalog,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    claim_execution_attempt(fixture.execution_context, request)


def _persist_result(fixture: RecoveryFixture, result: object) -> None:
    identity = {
        "workflow_id": fixture.workflow.id,
        "step_id": fixture.workflow.steps[0].id,
        "step_index": 1,
        "employee_id": fixture.employee.id,
        "binding": fixture.run.binding,
    }
    if type(result) is ModelInvocationSuccess:
        runtime_result = StepRuntimeExecutionSuccess(
            **identity, invocation_result=result
        )
    else:
        assert type(result) is ModelInvocationFailure
        runtime_result = StepRuntimeExecutionFailure(
            **identity, invocation_result=result
        )
    persist_executed_step_transition(
        runtime_result, fixture.run.state_path, fixture.run.events_path
    )


def _snapshot_run_namespace(
    root: Path,
) -> dict[str, tuple[str, bytes | str | None]]:
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", path.readlink().as_posix())
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
    return snapshot


def _inspect(fixture: RecoveryFixture):
    return runner.invoke(
        app,
        [
            "workflows",
            "recovery",
            fixture.run.binding.run_id,
            "--run-store",
            str(fixture.run.store.root),
        ],
    )


def _recover_args(fixture: RecoveryFixture, decision: dict[str, object]) -> list[str]:
    return [
        "workflows",
        "recover",
        fixture.run.binding.run_id,
        "--run-store",
        str(fixture.run.store.root),
        "--recovery-decision-sha256",
        str(decision["recovery_decision_sha256"]),
        "--approve-recovery",
        "--recovery-approved-by",
        "recovery-operator",
        "--recovery-approval-id",
        f"recovery-approval-{fixture.run.binding.run_id}",
    ]


def _with_execution_approval(
    fixture: RecoveryFixture,
    decision: dict[str, object],
    *,
    recovery_approval_id: str | None = None,
    execution_approval_id: str | None = None,
) -> list[str]:
    recovery_id = recovery_approval_id or (
        f"recovery-approval-{fixture.run.binding.run_id}"
    )
    execution_id = execution_approval_id or (
        f"recovery-execution-{fixture.run.binding.run_id}"
    )
    args = _recover_args(fixture, decision)
    args[args.index(f"recovery-approval-{fixture.run.binding.run_id}")] = recovery_id
    return args + [
        "--approve-execution",
        "--execution-approved-by",
        "new-execution-operator",
        "--execution-approval-id",
        execution_id,
        "--expected-step-id",
        str(decision["step_id"]),
        "--expected-step-index",
        str(decision["step_index"]),
        "--expected-employee-id",
        str(decision["employee_id"]),
        "--expected-request-fingerprint",
        str(decision["invocation_fingerprint"]),
    ]


def _inspect_value(fixture: RecoveryFixture) -> dict[str, object]:
    result = _inspect(fixture)
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def _prepare_new_transport(
    monkeypatch: pytest.MonkeyPatch, calls: list[object]
) -> None:
    def send(request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        return _success_response()

    monkeypatch.setattr(cli_module, "send_openai_responses_http_request", send)
    monkeypatch.setattr(
        cli_module,
        "load_openai_api_key_from_environment",
        lambda: OpenAIApiKey(value=SecretStr("synthetic-recovery-key")),
    )


def test_recovery_inspection_is_read_only_and_terminal_failure_needs_explicit_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, "run-failed-recovery")
    first = execute_openai_model_invocation(
        fixture.request,
        (),
        OpenAIApiKey(value=SecretStr("initial-key")),
        fixture.approval,
        transport=lambda _: _api_failure_response(),
        execution_evidence=fixture.execution_context,
    )
    assert type(first) is ModelInvocationFailure
    _persist_result(fixture, first)
    attempts_before = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert len(attempts_before) == 1
    old_attempt = attempts_before[0]
    old_attempt_bytes = execution_attempt_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, old_attempt.attempt_id
    ).read_bytes()
    old_raw_bytes = execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, old_attempt.attempt_id
    ).read_bytes()
    old_result_bytes = execution_normalized_result_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, old_attempt.attempt_id
    ).read_bytes()
    old_events = fixture.run.events_path.read_bytes()
    before_inspection = _snapshot_run_namespace(fixture.run.store.root)

    inspection = _inspect(fixture)
    assert inspection.exit_code == 0, inspection.stderr
    decision = json.loads(inspection.stdout)
    assert decision["eligible"] is True
    assert decision["action"] == "retry_failed"
    assert _snapshot_run_namespace(fixture.run.store.root) == before_inspection

    calls: list[object] = []
    _prepare_new_transport(monkeypatch, calls)
    ordinary_continue = runner.invoke(
        app,
        [
            "workflows",
            "continue",
            fixture.run.binding.run_id,
            "--run-store",
            str(fixture.run.store.root),
        ],
    )
    assert ordinary_continue.exit_code == 1
    assert calls == []

    recovered = runner.invoke(app, _with_execution_approval(fixture, decision))
    assert recovered.exit_code == 0, recovered.stdout + recovered.stderr
    assert len(calls) == 1
    recovered_value = json.loads(recovered.stdout)
    assert recovered_value["status"] == "workflow_complete"
    assert fixture.run.events_path.read_bytes().startswith(old_events)

    attempts_after = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert len(attempts_after) == 2
    recovered_attempt = next(
        attempt
        for attempt in attempts_after
        if attempt.schema_version == "workflow-execution-attempt.v2"
    )
    assert recovered_attempt.schema_version == "workflow-execution-attempt.v2"
    assert recovered_attempt.previous_attempt_id == old_attempt.attempt_id
    assert recovered_attempt.previous_attempt_evidence_sha256 == old_attempt.digest
    assert recovered_attempt.recovery_approval_id == (
        f"recovery-approval-{fixture.run.binding.run_id}"
    )
    assert recovered_attempt.execution_approval_id != old_attempt.execution_approval_id
    assert execution_attempt_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, old_attempt.attempt_id
    ).read_bytes() == old_attempt_bytes
    assert execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, old_attempt.attempt_id
    ).read_bytes() == old_raw_bytes
    assert execution_normalized_result_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, old_attempt.attempt_id
    ).read_bytes() == old_result_bytes
    events = fixture.run.events_path.read_text(encoding="utf-8")
    assert events.count('"event_type":"step_failed"') == 1
    assert events.count('"event_type":"step_recovery_started"') == 1
    assert events.count('"event_type":"step_succeeded"') == 1

    artifacts = list_run_artifacts(fixture.run.store, fixture.run.binding.run_id)
    assert len(artifacts) == 1
    assert artifacts[0].execution_attempt_id == recovered_attempt.attempt_id
    assert artifacts[0].content == b"recovered output"


def test_durable_normalized_result_is_completed_without_provider_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, "run-result-completion")
    result = execute_openai_model_invocation(
        fixture.request,
        (),
        OpenAIApiKey(value=SecretStr("initial-key")),
        fixture.approval,
        transport=lambda _: _success_response("already durable"),
        execution_evidence=fixture.execution_context,
    )
    assert type(result) is ModelInvocationSuccess
    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    assert load_normalized_result_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    ).result == result
    before_inspection = _snapshot_run_namespace(fixture.run.store.root)
    decision = _inspect_value(fixture)
    assert decision["action"] == "complete_result"
    assert _snapshot_run_namespace(fixture.run.store.root) == before_inspection

    calls: list[object] = []
    _prepare_new_transport(monkeypatch, calls)
    recovered = runner.invoke(app, _recover_args(fixture, decision))
    assert recovered.exit_code == 0, recovered.stdout + recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "workflow_complete"
    assert calls == []
    artifacts = list_run_artifacts(fixture.run.store, fixture.run.binding.run_id)
    assert len(artifacts) == 1
    assert artifacts[0].execution_attempt_id == attempt.attempt_id

    completed_namespace = _snapshot_run_namespace(fixture.run.store.root)
    completed_inspection = _inspect(fixture)
    assert completed_inspection.exit_code != 0
    assert _snapshot_run_namespace(fixture.run.store.root) == completed_namespace
    assert calls == []


def test_durable_raw_response_is_normalized_without_provider_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, "run-raw-completion")

    def interrupted_normalization(*_: object, **__: object) -> object:
        raise ExecutionEvidencePersistenceError("ambiguous")

    with monkeypatch.context() as patch:
        patch.setattr(
            responses_execution_module,
            "persist_normalized_result_evidence",
            interrupted_normalization,
        )
        with pytest.raises(ExecutionEvidencePersistenceError):
            execute_openai_model_invocation(
                fixture.request,
                (),
                OpenAIApiKey(value=SecretStr("initial-key")),
                fixture.approval,
                transport=lambda _: _success_response("raw only"),
                execution_evidence=fixture.execution_context,
            )
    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    raw = load_raw_response_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    assert raw.body == _success_response("raw only").body
    before_inspection = _snapshot_run_namespace(fixture.run.store.root)
    decision = _inspect_value(fixture)
    assert decision["action"] == "complete_raw_response"
    assert _snapshot_run_namespace(fixture.run.store.root) == before_inspection

    calls: list[object] = []
    _prepare_new_transport(monkeypatch, calls)
    recovered = runner.invoke(app, _recover_args(fixture, decision))
    assert recovered.exit_code == 0, recovered.stdout + recovered.stderr
    assert json.loads(recovered.stdout)["status"] == "workflow_complete"
    assert calls == []
    normalized = load_normalized_result_evidence(
        fixture.run.store.root, fixture.run.binding.run_id, attempt.attempt_id
    )
    assert normalized.result.text == "raw only"
    artifacts = list_run_artifacts(fixture.run.store, fixture.run.binding.run_id)
    assert len(artifacts) == 1
    assert artifacts[0].execution_attempt_id == attempt.attempt_id


def test_recovery_decision_is_stale_after_authoritative_response_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, "run-stale-recovery")
    _claim_only(fixture)
    decision = _inspect_value(fixture)
    assessment = assess_workflow_recovery(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert decision["action"] == "retry_ambiguous"
    assert assessment.digest == decision["recovery_decision_sha256"]

    approval = build_recovery_approval_evidence(
        assessment,
        approved_by="recovery-operator",
        approval_id="stale-recovery-approval",
    )
    persist_recovery_approval_evidence(fixture.run.store, approval)
    attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    persist_raw_response_evidence(
        fixture.execution_context,
        attempt,
        _success_response("provider response is now durable"),
    )
    current = assess_workflow_recovery(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert current.action == "complete_raw_response"
    with pytest.raises(WorkflowRecoveryError):
        validate_workflow_recovery_authorization(
            fixture.run.store.root, assessment, approval
        )

    before_recovery = _snapshot_run_namespace(fixture.run.store.root)
    calls: list[object] = []
    _prepare_new_transport(monkeypatch, calls)
    stale_recovery = runner.invoke(app, _recover_args(fixture, decision))
    assert stale_recovery.exit_code == 2
    assert calls == []
    assert _snapshot_run_namespace(fixture.run.store.root) == before_recovery


def test_recovery_decision_cannot_authorize_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _fixture(tmp_path / "first", "run-first-recovery")
    second = _fixture(tmp_path / "second", "run-second-recovery")
    _claim_only(first)
    _claim_only(second)
    first_decision = _inspect_value(first)
    before_second = _snapshot_run_namespace(second.run.store.root)

    calls: list[object] = []
    _prepare_new_transport(monkeypatch, calls)
    cross_run_recovery = runner.invoke(app, _recover_args(second, first_decision))

    assert cross_run_recovery.exit_code == 2
    assert calls == []
    assert _snapshot_run_namespace(second.run.store.root) == before_second


def test_failed_recovery_preserves_both_attempts_and_requires_new_approvals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, "run-failed-retry")
    first_result = execute_openai_model_invocation(
        fixture.request,
        (),
        OpenAIApiKey(value=SecretStr("initial-key")),
        fixture.approval,
        transport=lambda _: _api_failure_response(),
        execution_evidence=fixture.execution_context,
    )
    assert type(first_result) is ModelInvocationFailure
    _persist_result(fixture, first_result)
    first_attempt = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )[0]
    first_attempt_bytes = execution_attempt_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, first_attempt.attempt_id
    ).read_bytes()
    first_raw_bytes = execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, first_attempt.attempt_id
    ).read_bytes()
    first_result_bytes = execution_normalized_result_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, first_attempt.attempt_id
    ).read_bytes()
    first_decision = _inspect_value(fixture)

    calls: list[object] = []

    def fail_retry(request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        return _api_failure_response()

    monkeypatch.setattr(cli_module, "send_openai_responses_http_request", fail_retry)
    monkeypatch.setattr(
        cli_module,
        "load_openai_api_key_from_environment",
        lambda: OpenAIApiKey(value=SecretStr("synthetic-recovery-key")),
    )
    first_retry = runner.invoke(
        app, _with_execution_approval(fixture, first_decision)
    )
    assert first_retry.exit_code == 1
    assert len(calls) == 1
    second_attempt = next(
        attempt
        for attempt in list_run_execution_evidence(
            fixture.run.store.root, fixture.run.binding.run_id
        )
        if attempt.attempt_id != first_attempt.attempt_id
    )
    assert second_attempt.previous_attempt_id == first_attempt.attempt_id
    second_attempt_paths = (
        execution_attempt_evidence_path(
            fixture.run.store.root,
            fixture.run.binding.run_id,
            second_attempt.attempt_id,
        ),
        execution_raw_response_evidence_path(
            fixture.run.store.root,
            fixture.run.binding.run_id,
            second_attempt.attempt_id,
        ),
        execution_normalized_result_evidence_path(
            fixture.run.store.root,
            fixture.run.binding.run_id,
            second_attempt.attempt_id,
        ),
    )
    second_attempt_bytes = tuple(path.read_bytes() for path in second_attempt_paths)
    assert fixture.run.events_path.read_text(encoding="utf-8").count(
        '"event_type":"step_failed"'
    ) == 2
    assert fixture.run.events_path.read_text(encoding="utf-8").count(
        '"event_type":"step_recovery_started"'
    ) == 1
    assert execution_attempt_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, first_attempt.attempt_id
    ).read_bytes() == first_attempt_bytes
    assert execution_raw_response_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, first_attempt.attempt_id
    ).read_bytes() == first_raw_bytes
    assert execution_normalized_result_evidence_path(
        fixture.run.store.root, fixture.run.binding.run_id, first_attempt.attempt_id
    ).read_bytes() == first_result_bytes

    second_decision = _inspect_value(fixture)
    assert second_decision["action"] == "retry_failed"
    assert second_decision["previous_attempt_id"] == second_attempt.attempt_id
    before_reuse = _snapshot_run_namespace(fixture.run.store.root)
    reused_approval = runner.invoke(
        app, _with_execution_approval(fixture, second_decision)
    )
    assert reused_approval.exit_code == 2
    assert len(calls) == 1
    assert _snapshot_run_namespace(fixture.run.store.root) == before_reuse

    def succeed_retry(request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        return _success_response("success after a second explicit recovery")

    monkeypatch.setattr(cli_module, "send_openai_responses_http_request", succeed_retry)
    second_retry = runner.invoke(
        app,
        _with_execution_approval(
            fixture,
            second_decision,
            recovery_approval_id="fresh-recovery-approval",
            execution_approval_id="fresh-execution-approval",
        ),
    )
    assert second_retry.exit_code == 0, second_retry.stdout + second_retry.stderr
    assert len(calls) == 2
    attempts = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert len(attempts) == 3
    third_attempt = next(
        attempt
        for attempt in attempts
        if attempt.attempt_id != first_attempt.attempt_id
        and attempt.attempt_id != second_attempt.attempt_id
    )
    assert third_attempt.previous_attempt_id == second_attempt.attempt_id
    assert fixture.run.events_path.read_text(encoding="utf-8").count(
        '"event_type":"step_failed"'
    ) == 2
    assert fixture.run.events_path.read_text(encoding="utf-8").count(
        '"event_type":"step_recovery_started"'
    ) == 2
    assert fixture.run.events_path.read_text(encoding="utf-8").count(
        '"event_type":"step_succeeded"'
    ) == 1
    assert (
        tuple(path.read_bytes() for path in second_attempt_paths)
        == second_attempt_bytes
    )
    artifacts = list_run_artifacts(fixture.run.store, fixture.run.binding.run_id)
    assert len(artifacts) == 1
    assert artifacts[0].execution_attempt_id == third_attempt.attempt_id
    assert artifacts[0].content == b"success after a second explicit recovery"


def test_ambiguous_claim_requires_both_approvals_and_normal_continue_never_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, "run-ambiguous-recovery")
    _claim_only(fixture)
    before_inspection = _snapshot_run_namespace(fixture.run.store.root)
    decision = _inspect_value(fixture)
    assert decision["action"] == "retry_ambiguous"
    assert _snapshot_run_namespace(fixture.run.store.root) == before_inspection

    calls: list[object] = []
    _prepare_new_transport(monkeypatch, calls)
    ordinary_continue = runner.invoke(
        app,
        [
            "workflows",
            "continue",
            fixture.run.binding.run_id,
            "--run-store",
            str(fixture.run.store.root),
        ],
    )
    assert ordinary_continue.exit_code == 2
    assert calls == []

    missing_execution = runner.invoke(app, _recover_args(fixture, decision))
    assert missing_execution.exit_code == 2
    assert calls == []
    assert _snapshot_run_namespace(fixture.run.store.root) == before_inspection

    execution_only = _with_execution_approval(fixture, decision)
    recovery_options_start = execution_only.index("--approve-recovery")
    del execution_only[recovery_options_start : recovery_options_start + 5]
    missing_recovery = runner.invoke(app, execution_only)
    assert missing_recovery.exit_code == 2
    assert calls == []
    assert _snapshot_run_namespace(fixture.run.store.root) == before_inspection

    recovered = runner.invoke(app, _with_execution_approval(fixture, decision))
    assert recovered.exit_code == 0, recovered.stdout + recovered.stderr
    assert len(calls) == 1
    attempts = list_run_execution_evidence(
        fixture.run.store.root, fixture.run.binding.run_id
    )
    assert len(attempts) == 2
    recovered_attempt = next(
        attempt
        for attempt in attempts
        if attempt.schema_version == "workflow-execution-attempt.v2"
    )
    assert recovered_attempt.previous_attempt_id is not None
    assert recovered_attempt.previous_attempt_id in {
        attempt.attempt_id
        for attempt in attempts
        if attempt.schema_version == "workflow-execution-attempt.v1"
    }
    assert recovered_attempt.recovery_approval_id is not None
    assert '"event_type":"step_recovery_started"' in fixture.run.events_path.read_text(
        encoding="utf-8"
    )
