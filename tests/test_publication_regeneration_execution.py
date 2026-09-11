"""Synthetic provider tests for the Phase 266 regeneration execution boundary."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.engine.publication_regeneration as publication_regeneration_module
import ai_office.providers.openai.responses_execution as responses_execution_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.post_terminal_facts import (
    build_post_terminal_facts,
    build_publication_readiness_audit_record,
    load_persisted_terminal_snapshot,
    persist_publication_readiness_audit,
)
from ai_office.engine.publication_regeneration import (
    PublicationRegenerationAttemptAlreadyConsumedError,
    PublicationRegenerationAttemptClaimPersistenceError,
    approve_publication_regeneration,
    build_publication_regeneration_plan,
    load_publication_regeneration_attempt_claim,
    publication_regeneration_attempt_claim_path,
    publication_regeneration_consumption_key,
)
from ai_office.engine.publication_regeneration_execution import (
    PublicationRegenerationExecutionError,
    execute_approved_publication_regeneration,
)
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    LOCAL_OMNIROUTE_EXECUTION_TARGET,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
    RuntimeFactsSnapshot,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
    OpenAIResponsesTransportError,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition

FakeTransport = Callable[
    [OpenAIResponsesAuthenticatedHttpRequest], OpenAIResponsesRawHttpResponse
]


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase266-workflow",
            "name": "Phase 266 workflow",
            "description": "provider execution fixture",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "employee": "researcher",
                    "instructions": "Research.",
                },
                {
                    "id": "publish",
                    "name": "Publish",
                    "employee": "editor",
                    "instructions": "Prepare.",
                },
            ],
        }
    )


def write_history(path: Path) -> WorkflowExecutionPersistenceTargets:
    path.mkdir(parents=True)
    definition = workflow()
    state = WorkflowExecutionState(
        workflow_id=definition.id,
        status="succeeded",
        current_step_id="publish",
        current_step_index=2,
        current_employee_id="editor",
        completed_step_ids=("research", "publish"),
        last_failure_category=None,
    )
    events = (
        RuntimeStepEvent(
            event_type="step_succeeded",
            workflow_id=definition.id,
            step_id="research",
            step_index=1,
            employee_id="researcher",
            previous_status="running",
            next_status="succeeded",
            provider="research-provider",
            failure_category=None,
            response_id="response-one",
            request_id="request-one",
            output_text="intermediate",
            message=None,
        ),
        RuntimeStepEvent(
            event_type="step_succeeded",
            workflow_id=definition.id,
            step_id="publish",
            step_index=2,
            employee_id="editor",
            previous_status="running",
            next_status="succeeded",
            provider="terminal-provider",
            failure_category=None,
            response_id="response-terminal",
            request_id="request-terminal",
            output_text="ORIGINAL BUSINESS OUTPUT 日本語",
            message=None,
        ),
    )
    targets = WorkflowExecutionPersistenceTargets(
        state_path=path / "state.json",
        events_path=path / "events.jsonl",
    )
    targets.state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return targets


def request() -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model="future-model",
        system_instructions="SYSTEM SECRET INSTRUCTIONS",
        task_instructions="TASK SECRET INSTRUCTIONS",
        allowed_tools=("search",),
        upstream_inputs=(
            UpstreamStepOutput(
                workflow_id="upstream-workflow",
                step_id="research",
                step_index=1,
                employee_id="researcher",
                output_text="explicit upstream output",
            ),
        ),
        runtime_facts=RuntimeFactsSnapshot(),
    )


def tool(name: str = "search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="tool description",
        parameters=(
            ToolParameterDefinition(
                name="query",
                description="query description",
                type="string",
                required=True,
            ),
        ),
    )


def raw_response(status_code: int, payload: object) -> OpenAIResponsesRawHttpResponse:
    return OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="synthetic",
        headers=(("x-request-id", "synthetic-request"),),
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def success_response() -> OpenAIResponsesRawHttpResponse:
    return raw_response(
        200,
        {
            "id": "synthetic-response",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": " regenerated 日本語"},
                    ],
                },
            ],
        },
    )


class ExecutionFixture:
    def __init__(self, tmp_path: Path) -> None:
        history_targets = write_history(tmp_path / "history")
        facts = build_post_terminal_facts(
            load_persisted_terminal_snapshot(workflow(), history_targets)
        )
        audit = build_publication_readiness_audit_record(
            facts,
            "ORIGINAL BUSINESS OUTPUT 日本語",
        )
        self.audit_path = tmp_path / "audit.json"
        persist_publication_readiness_audit(self.audit_path, audit)
        self.ledger_directory = tmp_path / "ledger"
        self.ledger_directory.mkdir()
        self.history_targets = history_targets
        self.request = request()
        self.tools = (tool(),)
        self.plan = build_publication_regeneration_plan(
            audit,
            "regen-20260912-01",
            self.request,
            self.tools,
            DIRECT_OPENAI_EXECUTION_TARGET,
        )
        self.outer_approval = approve_publication_regeneration(
            self.plan,
            approved_by="outer-human",
            approval_id="outer-approval-266",
        )
        self.inner_approval = approve_model_invocation_execution(
            self.request,
            self.tools,
            provider="openai",
            approved_by="inner-human",
            approval_id="inner-approval-266",
        )
        self.environment = {"OPENAI_API_KEY": "synthetic-key"}

    def execute(
        self,
        transport: FakeTransport,
        *,
        audit_path: Path | None = None,
        plan=None,
        outer_approval=None,
        request_value=None,
        resolved_tools=None,
        inner_approval=None,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
        environment=None,
    ):
        return execute_approved_publication_regeneration(
            audit_path=self.audit_path if audit_path is None else audit_path,
            ledger_directory=self.ledger_directory,
            plan=self.plan if plan is None else plan,
            outer_approval=(
                self.outer_approval if outer_approval is None else outer_approval
            ),
            request=self.request if request_value is None else request_value,
            resolved_tools=(self.tools if resolved_tools is None else resolved_tools),
            inner_approval=(
                self.inner_approval if inner_approval is None else inner_approval
            ),
            execution_target=execution_target,
            environment=(self.environment if environment is None else environment),
            transport=transport,
        )


def test_valid_inputs_claim_before_transport_and_return_existing_success(
    tmp_path: Path,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    observed: list[str] = []

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        observed.append("transport")
        marker = publication_regeneration_attempt_claim_path(
            fixture.ledger_directory,
            publication_regeneration_consumption_key(fixture.outer_approval),
        )
        assert marker.exists()
        loaded = load_publication_regeneration_attempt_claim(marker)
        assert loaded.approval_id == fixture.outer_approval.approval_id
        assert request_value.headers[-1][0] == "Authorization"
        return success_response()

    result = fixture.execute(transport)

    assert isinstance(result, ModelInvocationSuccess)
    assert result == ModelInvocationSuccess(
        provider="openai",
        response_id="synthetic-response",
        request_id="synthetic-request",
        status="completed",
        text_parts=(" regenerated 日本語",),
        text=" regenerated 日本語",
    )
    assert observed == ["transport"]
    marker = publication_regeneration_attempt_claim_path(
        fixture.ledger_directory,
        publication_regeneration_consumption_key(fixture.outer_approval),
    )
    assert marker.exists()


def test_transport_failure_returns_existing_failure_and_replay_is_consumed(
    tmp_path: Path,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise OpenAIResponsesTransportError("synthetic transport failure")

    result = fixture.execute(transport)
    assert result.category == "transport_error"  # type: ignore[union-attr]
    assert result.message == "synthetic transport failure"  # type: ignore[union-attr]
    assert calls == 1

    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError) as error:
        fixture.execute(transport)
    assert error.value.detail.classification == "already_consumed"
    assert calls == 1


def test_existing_corrupt_marker_blocks_transport(tmp_path: Path) -> None:
    fixture = ExecutionFixture(tmp_path)
    marker = publication_regeneration_attempt_claim_path(
        fixture.ledger_directory,
        publication_regeneration_consumption_key(fixture.outer_approval),
    )
    marker.write_bytes(b'{"truncated"')
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        fixture.execute(transport)
    assert calls == 0
    assert marker.read_bytes() == b'{"truncated"'


def test_different_plan_with_same_outer_approval_id_is_consumed(
    tmp_path: Path,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        return success_response()

    fixture.execute(transport)
    changed_plan = build_publication_regeneration_plan(
        build_publication_readiness_audit_record(
            build_post_terminal_facts(
                load_persisted_terminal_snapshot(workflow(), fixture.history_targets)
            ),
            "ORIGINAL BUSINESS OUTPUT 日本語",
        ),
        "regen-20260912-02",
        fixture.request,
        fixture.tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    changed_outer = approve_publication_regeneration(
        changed_plan,
        approved_by="outer-human",
        approval_id=fixture.outer_approval.approval_id,
    )

    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        fixture.execute(
            transport,
            plan=changed_plan,
            outer_approval=changed_outer,
        )
    assert calls == 1


def test_stale_fresh_audit_stops_before_claim_and_transport(tmp_path: Path) -> None:
    fixture = ExecutionFixture(tmp_path)
    stale_audit_path = tmp_path / "stale-audit.json"
    history_facts = build_post_terminal_facts(
        load_persisted_terminal_snapshot(workflow(), fixture.history_targets)
    )
    stale_audit = build_publication_readiness_audit_record(
        history_facts,
        "CHANGED BUSINESS OUTPUT",
    )
    persist_publication_readiness_audit(stale_audit_path, stale_audit)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport, audit_path=stale_audit_path)
    assert error.value.detail.classification == "plan"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


@pytest.mark.parametrize("mismatch", ["request", "tools", "target"])
def test_plan_request_tools_or_target_mismatch_stops_before_claim(
    tmp_path: Path,
    mismatch: str,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    request_value = fixture.request
    resolved_tools = fixture.tools
    execution_target = DIRECT_OPENAI_EXECUTION_TARGET
    if mismatch == "request":
        request_value = replace(
            fixture.request,
            task_instructions="different task instructions",
        )
    elif mismatch == "tools":
        resolved_tools = (tool("different"),)
    else:
        execution_target = LOCAL_OMNIROUTE_EXECUTION_TARGET

    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(
            transport,
            request_value=request_value,
            resolved_tools=resolved_tools,
            execution_target=execution_target,
        )
    assert error.value.detail.classification == "plan"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_invalid_outer_approval_stops_before_claim(tmp_path: Path) -> None:
    fixture = ExecutionFixture(tmp_path)
    other_plan = build_publication_regeneration_plan(
        build_publication_readiness_audit_record(
            build_post_terminal_facts(
                load_persisted_terminal_snapshot(workflow(), fixture.history_targets)
            ),
            "ORIGINAL BUSINESS OUTPUT 日本語",
        ),
        "regen-20260912-other",
        fixture.request,
        fixture.tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    other_outer = approve_publication_regeneration(
        other_plan,
        approved_by="outer-human",
        approval_id="other-outer-approval",
    )
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport, outer_approval=other_outer)
    assert error.value.detail.classification == "outer_approval"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_invalid_inner_approval_stops_before_claim(tmp_path: Path) -> None:
    fixture = ExecutionFixture(tmp_path)
    changed_request = replace(fixture.request, task_instructions="stale request")
    stale_inner = approve_model_invocation_execution(
        changed_request,
        fixture.tools,
        provider="openai",
        approved_by="inner-human",
        approval_id="stale-inner-approval",
    )
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport, inner_approval=stale_inner)
    assert error.value.detail.classification == "inner_approval"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_missing_credential_stops_before_claim(tmp_path: Path) -> None:
    fixture = ExecutionFixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport, environment={})
    assert error.value.detail.classification == "credential"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_invalid_execution_target_stops_before_claim(tmp_path: Path) -> None:
    fixture = ExecutionFixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport, execution_target=object())  # type: ignore[arg-type]
    assert error.value.detail.classification == "plan"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_request_construction_failure_stops_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ExecutionFixture(tmp_path)

    def fail_build(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic construction failure")

    monkeypatch.setattr(
        responses_execution_module,
        "build_openai_responses_request",
        fail_build,
    )
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport)
    assert error.value.detail.classification == "provider_execution"
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_ambiguous_claim_persistence_never_calls_transport_and_marker_consumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    original_fsync = publication_regeneration_module.os.fsync

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(publication_regeneration_module.os, "fsync", fail_fsync)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationAttemptClaimPersistenceError) as error:
        fixture.execute(transport)
    assert error.value.detail.classification == "ambiguous"
    assert calls == 0
    marker = publication_regeneration_attempt_claim_path(
        fixture.ledger_directory,
        publication_regeneration_consumption_key(fixture.outer_approval),
    )
    assert marker.exists()
    assert marker.read_bytes()

    monkeypatch.setattr(publication_regeneration_module.os, "fsync", original_fsync)
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        fixture.execute(transport)
    assert calls == 0


def test_guarded_transport_rejects_second_delegation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        return success_response()

    def invoke_twice(*args: object, **kwargs: object):
        guarded = kwargs["transport"]
        request_value = args[0]
        first = guarded(
            # The actual type is irrelevant to the guard test; the synthetic
            # transport receives the same object after the first claim.
            request_value
        )
        guarded(request_value)
        return first

    monkeypatch.setattr(
        "ai_office.engine.publication_regeneration_execution.execute_openai_model_invocation",
        invoke_twice,
    )
    with pytest.raises(PublicationRegenerationExecutionError) as error:
        fixture.execute(transport)
    assert error.value.detail.classification == "transport_reuse"
    assert calls == 1


def test_original_artifacts_and_audit_remain_byte_for_byte_unchanged(
    tmp_path: Path,
) -> None:
    fixture = ExecutionFixture(tmp_path)
    before_state = fixture.history_targets.state_path.read_bytes()
    before_events = fixture.history_targets.events_path.read_bytes()
    before_audit = fixture.audit_path.read_bytes()

    fixture.execute(lambda _: success_response())

    assert fixture.history_targets.state_path.read_bytes() == before_state
    assert fixture.history_targets.events_path.read_bytes() == before_events
    assert fixture.audit_path.read_bytes() == before_audit


def test_synthetic_transport_never_opens_a_network_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = ExecutionFixture(tmp_path)

    def forbidden_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real network must not be used")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    result = fixture.execute(lambda _: success_response())
    assert isinstance(result, ModelInvocationSuccess)
