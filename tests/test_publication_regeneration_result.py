"""Synthetic durable-result evidence tests for Phase 267."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import ai_office.engine.publication_regeneration_result as result_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    publication_regeneration_result_execution as result_execution_module,
)
from ai_office.engine.post_terminal_facts import (
    build_post_terminal_facts,
    build_publication_readiness_audit_record,
    load_persisted_terminal_snapshot,
    persist_publication_readiness_audit,
)
from ai_office.engine.publication_regeneration import (
    PublicationRegenerationAttemptAlreadyConsumedError,
    PublicationRegenerationAttemptClaim,
    PublicationRegenerationAttemptClaimError,
    approve_publication_regeneration,
    build_publication_regeneration_attempt_claim,
    build_publication_regeneration_plan,
    publication_regeneration_attempt_claim_path,
    publication_regeneration_consumption_key,
)
from ai_office.engine.publication_regeneration_execution import (
    PublicationRegenerationExecutionError,
)
from ai_office.engine.publication_regeneration_result import (
    PublicationRegenerationResultConflictError,
    PublicationRegenerationResultError,
    PublicationRegenerationResultLoadError,
    PublicationRegenerationResultPersistenceError,
    PublicationRegenerationResultRecord,
    build_publication_regeneration_result_record,
    load_publication_regeneration_result,
    persist_publication_regeneration_result,
    preflight_publication_regeneration_result_path,
    publication_regeneration_result_canonical_bytes,
    publication_regeneration_result_digest,
    publication_regeneration_result_record_canonical_bytes,
    publication_regeneration_result_record_digest,
    serialize_publication_regeneration_result_canonical,
    serialize_publication_regeneration_result_record_canonical,
)
from ai_office.engine.publication_regeneration_result_execution import (
    PublicationRegenerationResultExecutionError,
    execute_and_persist_approved_publication_regeneration,
)
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationFailureDiagnostics,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    RuntimeFactsSnapshot,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
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


class PublicationRegenerationResultRecordChild(PublicationRegenerationResultRecord):
    pass


class PublicationRegenerationAttemptClaimChild(PublicationRegenerationAttemptClaim):
    pass


class ModelInvocationSuccessChild(ModelInvocationSuccess):
    pass


class ModelInvocationFailureChild(ModelInvocationFailure):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase267-workflow",
            "name": "Phase 267 workflow",
            "description": "durable result evidence fixture",
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


def failure_response() -> OpenAIResponsesRawHttpResponse:
    return raw_response(
        429,
        {
            "error": {
                "message": "synthetic normalized failure",
                "type": "synthetic_type",
                "param": "safe_param",
                "code": "synthetic_code",
            }
        },
    )


class EvidenceFixture:
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
        self.result_directory = tmp_path / "results"
        self.result_directory.mkdir()
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
            approval_id="outer-approval-267",
        )
        self.inner_approval = approve_model_invocation_execution(
            self.request,
            self.tools,
            provider="openai",
            approved_by="inner-human",
            approval_id="inner-approval-267",
        )
        self.environment = {"OPENAI_API_KEY": "synthetic-key"}

    @property
    def result_path(self) -> Path:
        return self.result_directory / "result.json"

    def result_for(
        self,
        result: ModelInvocationSuccess | ModelInvocationFailure,
    ) -> PublicationRegenerationResultRecord:
        claim = build_publication_regeneration_attempt_claim(
            self.plan,
            self.outer_approval,
        )
        return build_publication_regeneration_result_record(claim, result)

    def execute_and_persist(
        self,
        transport: FakeTransport,
        *,
        result_path: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> PublicationRegenerationResultRecord:
        return execute_and_persist_approved_publication_regeneration(
            result_path=self.result_path if result_path is None else result_path,
            audit_path=self.audit_path,
            ledger_directory=self.ledger_directory,
            plan=self.plan,
            outer_approval=self.outer_approval,
            request=self.request,
            resolved_tools=self.tools,
            inner_approval=self.inner_approval,
            execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
            environment=self.environment if environment is None else environment,
            transport=transport,
        )


def success_result_fixture() -> ModelInvocationSuccess:
    return ModelInvocationSuccess(
        provider="openai",
        response_id="response-267",
        request_id="request-267",
        status="completed",
        text_parts=("part one", "最後 😀"),
        text="part one最後 😀",
    )


def failure_result_fixture() -> ModelInvocationFailure:
    return ModelInvocationFailure(
        provider="openai",
        category="invalid_response",
        message="safe message",
        request_id="request-267",
        status_code=502,
        provider_error_type="provider_type",
        provider_error_code="provider_code",
        response_diagnostics=ModelInvocationFailureDiagnostics(
            status_code=502,
            content_type="application/json",
            body_length=37,
            body_kind="json",
        ),
    )


def test_success_result_canonical_json_and_digest_are_exact() -> None:
    result = success_result_fixture()
    expected = (
        '{"kind":"success","provider":"openai","request_id":"request-267",'
        '"response_id":"response-267","status":"completed",'
        '"text":"part one最後 😀","text_parts":["part one","最後 😀"]}'
    )
    assert serialize_publication_regeneration_result_canonical(result) == expected
    assert publication_regeneration_result_canonical_bytes(result) == expected.encode(
        "utf-8"
    )
    assert (
        publication_regeneration_result_digest(result)
        == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    )


def test_failure_result_canonical_json_and_digest_preserve_diagnostics() -> None:
    result = failure_result_fixture()
    expected = (
        '{"category":"invalid_response","kind":"failure",'
        '"message":"safe message","provider":"openai",'
        '"provider_error_code":"provider_code",'
        '"provider_error_type":"provider_type","request_id":"request-267",'
        '"response_diagnostics":{"body_kind":"json","body_length":37,'
        '"content_type":"application/json","status_code":502},'
        '"status_code":502}'
    )
    assert serialize_publication_regeneration_result_canonical(result) == expected
    assert (
        publication_regeneration_result_digest(result)
        == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    )


def test_record_is_frozen_exact_and_binds_success_output_digest(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())

    assert record.outcome == "success"
    assert (
        record.business_output_sha256
        == hashlib.sha256(success_result_fixture().text.encode("utf-8")).hexdigest()
    )
    assert record.result_sha256 == publication_regeneration_result_digest(
        success_result_fixture()
    )
    assert record.attempt_claim_sha256 == record.attempt_claim.digest
    assert record.digest == publication_regeneration_result_record_digest(record)
    with pytest.raises(FrozenInstanceError):
        record.outcome = "failure"  # type: ignore[misc]
    with pytest.raises(PublicationRegenerationResultError):
        PublicationRegenerationResultRecordChild(**record.__dict__)


def test_result_record_canonical_json_and_digest_are_exact(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    expected = (
        '{"attempt_claim":{"approval_id":"outer-approval-267",'
        '"approved_by":"outer-human",'
        '"consumption_key":"096f77288c731bdeb3d6856c59cde50050112b16ad599ac588746fa3632cc74f",'
        '"execution_target_sha256":"f8d7bc1febd55eeb8cd53563b0348a87930836c688dcf930df4a50e3cef744e9",'
        '"invocation_request_sha256":"aae76e2d1c9f33ca5dfc59d3adcea7e8ce5ffd28f359a5d5696e6db6579ba34b",'
        '"provider":"openai",'
        '"regeneration_approval_sha256":"bee3b3015f7065a029a1a1ea8643cd42144087e02113c403de16ecda44d6a3e2",'
        '"regeneration_id":"regen-20260912-01",'
        '"regeneration_plan_sha256":"60eca3a91b575c6255d6c34f3e818c2169a875a2bd46983419618957989f01d1",'
        '"schema_version":"publication-regeneration-attempt.v1",'
        '"source_audit_sha256":"46a783b696550872a3eb8bc528875550fc9ff077419326988d8cc5d3e5dc2b59",'
        '"state":"claimed"},'
        '"attempt_claim_sha256":"668cb9fa9d12d6eed126be5951dbb2acaa0d7085968d927e51916a485f6cf82d",'
        '"business_output_sha256":"c89d03dd82e7a8996fbbbcb98b2627602d7b857db212f1a26ce4f03e59c2289f",'
        '"execution_target_sha256":"f8d7bc1febd55eeb8cd53563b0348a87930836c688dcf930df4a50e3cef744e9",'
        '"invocation_request_sha256":"aae76e2d1c9f33ca5dfc59d3adcea7e8ce5ffd28f359a5d5696e6db6579ba34b",'
        '"outcome":"success","provider":"openai",'
        '"regeneration_id":"regen-20260912-01",'
        '"regeneration_plan_sha256":"60eca3a91b575c6255d6c34f3e818c2169a875a2bd46983419618957989f01d1",'
        '"result":{"kind":"success","provider":"openai",'
        '"request_id":"request-267","response_id":"response-267",'
        '"status":"completed","text":"part one最後 😀",'
        '"text_parts":["part one","最後 😀"]},'
        '"result_sha256":"2a2b8129b4bbcbf53905603513a4a26154e0bccfd3949857150c4b0bdb8e89cf",'
        '"schema_version":"publication-regeneration-result.v1",'
        '"source_audit_sha256":"46a783b696550872a3eb8bc528875550fc9ff077419326988d8cc5d3e5dc2b59"}'
    )
    assert (
        serialize_publication_regeneration_result_record_canonical(record) == expected
    )
    assert publication_regeneration_result_record_canonical_bytes(
        record
    ) == expected.encode("utf-8")
    assert publication_regeneration_result_record_digest(record) == (
        "f234348ad249233ad627fd543bb0b208b19155e3e43fd91f2e109aecaca4615d"
    )


def test_failure_record_has_no_business_output_digest(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(failure_result_fixture())

    assert record.outcome == "failure"
    assert record.business_output_sha256 is None
    assert record.result == failure_result_fixture()


def test_result_provider_mismatch_and_tampered_bindings_reject(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())

    with pytest.raises(PublicationRegenerationResultError):
        fixture.result_for(replace(success_result_fixture(), provider="omniroute"))
    with pytest.raises(PublicationRegenerationResultError):
        replace(record, result_sha256="0" * 64)
    with pytest.raises(PublicationRegenerationResultError):
        replace(record, provider="omniroute")
    with pytest.raises(PublicationRegenerationResultError):
        replace(record, outcome="failure")
    with pytest.raises(PublicationRegenerationResultError):
        replace(record, business_output_sha256="0" * 64)


def test_result_and_claim_subclasses_are_rejected(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    claim = build_publication_regeneration_attempt_claim(
        fixture.plan,
        fixture.outer_approval,
    )
    result = success_result_fixture()
    with pytest.raises(PublicationRegenerationResultError):
        child_claim = object.__new__(PublicationRegenerationAttemptClaimChild)
        for field_name in claim.__dataclass_fields__:
            object.__setattr__(child_claim, field_name, getattr(claim, field_name))
        build_publication_regeneration_result_record(
            child_claim,
            result,
        )
    with pytest.raises(PublicationRegenerationAttemptClaimError):
        PublicationRegenerationAttemptClaimChild(**claim.__dict__)
    with pytest.raises(PublicationRegenerationResultError):
        build_publication_regeneration_result_record(
            claim,
            ModelInvocationSuccessChild(**result.__dict__),
        )
    failure = failure_result_fixture()
    with pytest.raises(PublicationRegenerationResultError):
        build_publication_regeneration_result_record(
            claim,
            ModelInvocationFailureChild(**failure.__dict__),
        )


def test_pure_record_builder_makes_no_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pure builder touched an external dependency")

    monkeypatch.setattr(result_module.os, "fsync", forbidden)
    monkeypatch.setattr(result_module.os, "open", forbidden)
    monkeypatch.setattr(result_module.os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    record = fixture.result_for(success_result_fixture())
    assert record.outcome == "success"


def test_first_persistence_writes_exact_bytes_and_fsyncs_file_and_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    fsync_calls: list[int] = []
    original_fsync = result_module.os.fsync

    def record_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(result_module.os, "fsync", record_fsync)
    persisted = persist_publication_regeneration_result(fixture.result_path, record)

    assert persisted == record
    canonical_record = publication_regeneration_result_record_canonical_bytes(record)
    assert fixture.result_path.read_bytes() == canonical_record
    assert len(fsync_calls) == 2


def test_idempotent_evidence_persistence_reloads_and_reestablishes_durability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    persist_publication_regeneration_result(fixture.result_path, record)
    fsync_calls: list[int] = []
    original_fsync = result_module.os.fsync

    def record_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(result_module.os, "fsync", record_fsync)
    assert (
        persist_publication_regeneration_result(fixture.result_path, record) == record
    )
    assert len(fsync_calls) == 2


def test_conflicting_corrupt_and_truncated_result_paths_are_never_overwritten(
    tmp_path: Path,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    fixture.result_path.write_bytes(b'{"truncated"')
    with pytest.raises(PublicationRegenerationResultConflictError) as error:
        persist_publication_regeneration_result(fixture.result_path, record)
    assert error.value.detail.classification == "conflict"
    assert fixture.result_path.read_bytes() == b'{"truncated"'

    fixture.result_path.unlink()
    different = fixture.result_for(
        replace(success_result_fixture(), text="different output")
    )
    persist_publication_regeneration_result(fixture.result_path, different)
    with pytest.raises(PublicationRegenerationResultConflictError):
        persist_publication_regeneration_result(fixture.result_path, record)
    assert load_publication_regeneration_result(fixture.result_path) == different


def test_file_fsync_ambiguity_leaves_result_evidence_and_never_resets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic result file fsync failure")

    monkeypatch.setattr(result_module.os, "fsync", fail_fsync)
    with pytest.raises(PublicationRegenerationResultPersistenceError) as error:
        persist_publication_regeneration_result(fixture.result_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert fixture.result_path.exists()
    assert fixture.result_path.read_bytes()

    monkeypatch.setattr(result_module.os, "fsync", os.fsync)
    assert load_publication_regeneration_result(fixture.result_path) == record


def test_write_ambiguity_leaves_result_marker_without_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    original_open = result_module.Path.open

    class WriteFailureHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> WriteFailureHandle:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def write(self, _contents: bytes) -> int:
            raise OSError("synthetic result write failure")

    def fail_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        handle = original_open(path, mode, *args, **kwargs)
        return WriteFailureHandle(handle) if mode == "xb" else handle

    monkeypatch.setattr(result_module.Path, "open", fail_open)
    with pytest.raises(PublicationRegenerationResultPersistenceError) as error:
        persist_publication_regeneration_result(fixture.result_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert fixture.result_path.exists()
    assert fixture.result_path.read_bytes() == b""


def test_flush_ambiguity_leaves_result_marker_without_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    original_open = result_module.Path.open

    class FlushFailureHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> FlushFailureHandle:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def write(self, contents: bytes) -> int:
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            raise OSError("synthetic result flush failure")

    def fail_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        handle = original_open(path, mode, *args, **kwargs)
        return FlushFailureHandle(handle) if mode == "xb" else handle

    monkeypatch.setattr(result_module.Path, "open", fail_open)
    with pytest.raises(PublicationRegenerationResultPersistenceError) as error:
        persist_publication_regeneration_result(fixture.result_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert fixture.result_path.exists()
    assert fixture.result_path.read_bytes()


def test_parent_directory_fsync_ambiguity_leaves_canonical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())

    def fail_directory_fsync(_directory: Path) -> None:
        raise OSError("synthetic parent fsync failure")

    monkeypatch.setattr(result_module, "_fsync_result_directory", fail_directory_fsync)
    with pytest.raises(PublicationRegenerationResultPersistenceError) as error:
        persist_publication_regeneration_result(fixture.result_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert load_publication_regeneration_result(fixture.result_path) == record


def test_strict_loader_round_trips_in_a_fresh_process(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    persist_publication_regeneration_result(fixture.result_path, record)
    script = """
import sys
from ai_office.engine.publication_regeneration_result import (
    load_publication_regeneration_result,
)
record = load_publication_regeneration_result(__import__("pathlib").Path(sys.argv[1]))
print(record.digest)
print(record.result_sha256)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (
        f"{Path.cwd() / 'src'}:{environment.get('PYTHONPATH', '')}"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(fixture.result_path)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    digest, result_digest = completed.stdout.splitlines()
    assert digest == record.digest
    assert result_digest == record.result_sha256


def test_strict_loader_rejects_tampered_and_noncanonical_records(
    tmp_path: Path,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    canonical = publication_regeneration_result_record_canonical_bytes(record)
    fixture.result_path.write_bytes(
        canonical.replace(b'"outcome":"success"', b'"outcome":"failure"')
    )
    with pytest.raises(PublicationRegenerationResultLoadError):
        load_publication_regeneration_result(fixture.result_path)

    fixture.result_path.write_bytes(canonical + b"\n")
    with pytest.raises(PublicationRegenerationResultLoadError):
        load_publication_regeneration_result(fixture.result_path)

    fixture.result_path.write_bytes(
        canonical.replace(
            b'"schema_version":"publication-regeneration-result.v1"',
            b'"unknown":"field","schema_version":"publication-regeneration-result.v1"',
        )
    )
    with pytest.raises(PublicationRegenerationResultLoadError):
        load_publication_regeneration_result(fixture.result_path)


def test_execute_and_persist_success_claims_once_then_persists_exact_result(
    tmp_path: Path,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    transport_calls = 0

    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal transport_calls
        transport_calls += 1
        marker = publication_regeneration_attempt_claim_path(
            fixture.ledger_directory,
            publication_regeneration_consumption_key(fixture.outer_approval),
        )
        assert marker.exists()
        assert request_value.headers[-1][0] == "Authorization"
        return success_response()

    before_state = fixture.history_targets.state_path.read_bytes()
    before_events = fixture.history_targets.events_path.read_bytes()
    before_audit = fixture.audit_path.read_bytes()
    record = fixture.execute_and_persist(transport)

    assert transport_calls == 1
    assert record.result == ModelInvocationSuccess(
        "openai",
        "synthetic-response",
        "synthetic-request",
        "completed",
        (" regenerated 日本語",),
        " regenerated 日本語",
    )
    assert load_publication_regeneration_result(fixture.result_path) == record
    assert fixture.history_targets.state_path.read_bytes() == before_state
    assert fixture.history_targets.events_path.read_bytes() == before_events
    assert fixture.audit_path.read_bytes() == before_audit


def test_execute_and_persist_failure_records_normalized_failure_and_claim_stays(
    tmp_path: Path,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    transport_calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal transport_calls
        transport_calls += 1
        return failure_response()

    record = fixture.execute_and_persist(transport)
    assert transport_calls == 1
    assert record.outcome == "failure"
    assert isinstance(record.result, ModelInvocationFailure)
    assert record.business_output_sha256 is None
    assert load_publication_regeneration_result(fixture.result_path) == record

    second_result_path = fixture.result_directory / "second.json"
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        fixture.execute_and_persist(transport, result_path=second_result_path)
    assert transport_calls == 1
    assert not second_result_path.exists()


def test_result_path_preflight_blocks_before_phase266_for_missing_existing_or_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    calls = 0

    def forbidden_phase266(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase266 must not run after result preflight rejection")

    monkeypatch.setattr(
        result_execution_module,
        "execute_approved_publication_regeneration",
        forbidden_phase266,
    )
    missing_parent = tmp_path / "missing-parent" / "result.json"
    existing_target = fixture.result_directory / "existing.json"
    existing_target.write_bytes(b"existing")
    directory_target = fixture.result_directory / "target-directory"
    directory_target.mkdir()

    for target in (missing_parent, existing_target, directory_target):
        with pytest.raises(PublicationRegenerationResultPersistenceError):
            fixture.execute_and_persist(
                lambda _: success_response(), result_path=target
            )
    assert calls == 0
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_result_path_preflight_rejects_nonexact_path_types(tmp_path: Path) -> None:
    fixture = EvidenceFixture(tmp_path)
    with pytest.raises(PublicationRegenerationResultPersistenceError) as error:
        preflight_publication_regeneration_result_path(str(fixture.result_path))  # type: ignore[arg-type]
    assert error.value.detail.classification == "path_type"


def test_phase266_control_failure_creates_no_result_and_no_claim(
    tmp_path: Path,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    with pytest.raises(PublicationRegenerationExecutionError):
        fixture.execute_and_persist(transport, environment={})
    assert calls == 0
    assert not fixture.result_path.exists()
    assert tuple(fixture.ledger_directory.iterdir()) == ()


def test_result_persistence_failure_never_reexecutes_phase266_or_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    transport_calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal transport_calls
        transport_calls += 1
        return success_response()

    persist_calls = 0

    def fail_persist(*_args: object, **_kwargs: object) -> None:
        nonlocal persist_calls
        persist_calls += 1
        raise PublicationRegenerationResultPersistenceError("ambiguous")

    monkeypatch.setattr(
        result_execution_module,
        "persist_publication_regeneration_result",
        fail_persist,
    )
    with pytest.raises(PublicationRegenerationResultPersistenceError) as error:
        fixture.execute_and_persist(transport)
    assert error.value.detail.classification == "ambiguous"
    assert persist_calls == 1
    assert transport_calls == 1
    marker = publication_regeneration_attempt_claim_path(
        fixture.ledger_directory,
        publication_regeneration_consumption_key(fixture.outer_approval),
    )
    assert marker.exists()
    assert not fixture.result_path.exists()

    second_result_path = fixture.result_directory / "second-after-failure.json"
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        fixture.execute_and_persist(transport, result_path=second_result_path)
    assert transport_calls == 1
    assert not second_result_path.exists()


def test_loaded_claim_must_equal_expected_claim_before_result_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    transport_calls = 0
    alternate_plan = build_publication_regeneration_plan(
        build_publication_readiness_audit_record(
            build_post_terminal_facts(
                load_persisted_terminal_snapshot(workflow(), fixture.history_targets)
            ),
            "ORIGINAL BUSINESS OUTPUT 日本語",
        ),
        "regen-20260912-alternate",
        fixture.request,
        fixture.tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    alternate_approval = approve_publication_regeneration(
        alternate_plan,
        approved_by="outer-human",
        approval_id="alternate-outer-267",
    )
    alternate_claim = build_publication_regeneration_attempt_claim(
        alternate_plan,
        alternate_approval,
    )

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal transport_calls
        transport_calls += 1
        return success_response()

    monkeypatch.setattr(
        result_execution_module,
        "load_publication_regeneration_attempt_claim",
        lambda *_args, **_kwargs: alternate_claim,
    )
    with pytest.raises(PublicationRegenerationResultExecutionError) as error:
        fixture.execute_and_persist(transport)
    assert error.value.detail.classification == "claim_mismatch"
    assert transport_calls == 1
    assert not fixture.result_path.exists()


def test_claim_load_failure_never_creates_result_or_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    transport_calls = 0

    def transport(
        _: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        nonlocal transport_calls
        transport_calls += 1
        return success_response()

    monkeypatch.setattr(
        result_execution_module,
        "load_publication_regeneration_attempt_claim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("corrupt")),
    )
    with pytest.raises(PublicationRegenerationResultExecutionError) as error:
        fixture.execute_and_persist(transport)
    assert error.value.detail.classification == "claim_load"
    assert transport_calls == 1
    assert not fixture.result_path.exists()


def test_no_raw_payload_credential_or_path_is_present_in_result_evidence(
    tmp_path: Path,
) -> None:
    fixture = EvidenceFixture(tmp_path)
    record = fixture.result_for(success_result_fixture())
    text = serialize_publication_regeneration_result_record_canonical(record)
    assert "synthetic-key" not in text
    assert "Authorization" not in text
    assert "SYSTEM SECRET INSTRUCTIONS" not in text
    assert "TASK SECRET INSTRUCTIONS" not in text
    assert str(fixture.result_path) not in text
    assert '"body"' not in text
    assert '"headers"' not in text
