"""Focused provider-free readiness projection tests for Phase 268."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_office.cli import app
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.post_terminal_facts import (
    PublicationClaimContract,
    build_post_terminal_facts,
    build_publication_readiness_audit_record,
    load_persisted_terminal_snapshot,
    load_publication_readiness_audit,
    persist_publication_readiness_audit,
    post_terminal_facts_digest,
    publication_readiness_audit_digest,
)
from ai_office.engine.publication_regeneration import (
    approve_publication_regeneration,
    build_publication_regeneration_plan,
    claim_publication_regeneration_attempt,
)
from ai_office.engine.publication_regeneration_readiness import (
    PublicationRegenerationReadinessAssessment,
    PublicationRegenerationReadinessError,
    assess_publication_regeneration_result_readiness,
    publication_regeneration_readiness_assessment_canonical_bytes,
    publication_regeneration_readiness_assessment_digest,
    serialize_publication_regeneration_readiness_assessment_canonical,
)
from ai_office.engine.publication_regeneration_result import (
    PublicationRegenerationResultLoadError,
    build_publication_regeneration_result_record,
    load_publication_regeneration_result,
    persist_publication_regeneration_result,
)
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationFailureDiagnostics,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    RuntimeFactsSnapshot,
    UpstreamStepOutput,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition


class PublicationRegenerationReadinessAssessmentChild(
    PublicationRegenerationReadinessAssessment
):
    pass


@dataclass(frozen=True)
class ReadinessFixture:
    audit_path: Path
    result_path: Path
    ledger_directory: Path
    state_path: Path
    events_path: Path
    audit: object
    record: object


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase268-workflow",
            "name": "Phase 268 workflow",
            "description": "readiness projection fixture",
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


def tool() -> ToolDefinition:
    return ToolDefinition(
        name="search",
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


def success_result_fixture() -> ModelInvocationSuccess:
    return ModelInvocationSuccess(
        provider="openai",
        response_id="response-268",
        request_id="request-268",
        status="completed",
        text_parts=("ORIGINAL BUSINESS OUTPUT ", "日本語"),
        text="ORIGINAL BUSINESS OUTPUT 日本語",
    )


def failure_result_fixture() -> ModelInvocationFailure:
    return ModelInvocationFailure(
        provider="openai",
        category="invalid_response",
        message="safe message",
        request_id="request-268",
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


def claim_contract_for(
    facts: object,
    *,
    output: str,
    workflow_id: str | None = None,
) -> PublicationClaimContract:
    return PublicationClaimContract(
        schema_version="publication-claims.v1",
        scope="post_terminal_runtime_consistency",
        workflow_id=workflow_id or facts.workflow_id,
        business_output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        post_terminal_facts_sha256=post_terminal_facts_digest(facts),
        asserted_terminal_status="workflow_complete",
    )


def readiness_fixture(
    tmp_path: Path,
    *,
    result: ModelInvocationSuccess | ModelInvocationFailure | None = None,
    source_output: str | None = None,
) -> ReadinessFixture:
    history_targets = write_history(tmp_path / "history")
    facts = build_post_terminal_facts(
        load_persisted_terminal_snapshot(workflow(), history_targets)
    )
    if result is None:
        result = success_result_fixture()
    if source_output is None and type(result) is ModelInvocationSuccess:
        source_output = result.text
    if source_output is None:
        source_output = "ORIGINAL BUSINESS OUTPUT 日本語"
    audit = build_publication_readiness_audit_record(facts, source_output)
    audit_path = tmp_path / "source-audit.json"
    persist_publication_readiness_audit(audit_path, audit)

    ledger_directory = tmp_path / "ledger"
    ledger_directory.mkdir()
    plan = build_publication_regeneration_plan(
        audit,
        "regen-20260912-01",
        request(),
        (tool(),),
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    approval = approve_publication_regeneration(
        plan,
        approved_by="outer-human",
        approval_id="outer-approval-268",
    )
    claim = claim_publication_regeneration_attempt(
        ledger_directory,
        plan,
        approval,
    )
    record = build_publication_regeneration_result_record(claim, result)
    result_path = tmp_path / "result.json"
    persist_publication_regeneration_result(result_path, record)
    return ReadinessFixture(
        audit_path=audit_path,
        result_path=result_path,
        ledger_directory=ledger_directory,
        state_path=history_targets.state_path,
        events_path=history_targets.events_path,
        audit=audit,
        record=record,
    )


def exact_contract(fixture: ReadinessFixture) -> PublicationClaimContract:
    return claim_contract_for(
        fixture.audit.post_terminal_facts,
        output=fixture.record.result.text,
    )


def test_success_without_claim_is_insufficient_evidence_and_uses_exact_text(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )

    assert assessment.outcome == "success"
    assert assessment.readiness == "insufficient_evidence"
    assert assessment.reason_codes == ("claim_contract_missing",)
    assert assessment.claim_contract_sha256 is None
    assert assessment.publication_assessment is not None
    assert (
        assessment.publication_assessment.business_output_sha256
        == fixture.record.business_output_sha256
    )
    assert fixture.record.result.text not in (
        serialize_publication_regeneration_readiness_assessment_canonical(assessment)
    )


def test_success_with_exact_caller_claim_is_ready_and_cross_bound(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    contract = exact_contract(fixture)

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=contract,
    )

    assert assessment.readiness == "ready"
    assert assessment.reason_codes == ()
    assert assessment.regeneration_id == fixture.record.regeneration_id
    assert assessment.result_record_sha256 == fixture.record.digest
    assert assessment.source_audit_sha256 == fixture.audit.digest
    assert assessment.source_post_terminal_facts_sha256 == post_terminal_facts_digest(
        fixture.audit.post_terminal_facts
    )
    assert assessment.claim_contract_sha256 == contract.digest
    assert assessment.publication_assessment is not None
    assert assessment.publication_assessment.claim_contract is contract
    assert assessment.publication_assessment.post_terminal_facts == (
        fixture.audit.post_terminal_facts
    )
    assert assessment.publication_assessment.business_output_sha256 == (
        fixture.record.business_output_sha256
    )


def test_success_with_mismatching_caller_claim_is_stale_without_auto_repair(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    mismatch = replace(exact_contract(fixture), workflow_id="other-workflow")

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=mismatch,
    )

    assert assessment.readiness == "stale_or_inconsistent"
    assert assessment.reason_codes == ("claim_contract_mismatch",)
    assert assessment.claim_contract_sha256 == mismatch.digest
    assert assessment.publication_assessment is not None
    assert assessment.publication_assessment.claim_contract is None
    assert mismatch.digest in (
        serialize_publication_regeneration_readiness_assessment_canonical(assessment)
    )


def test_success_uses_regenerated_text_against_exact_source_facts(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(
        tmp_path,
        source_output="ORIGINAL BUSINESS OUTPUT 日本語",
    )
    changed_result = ModelInvocationSuccess(
        provider=fixture.record.result.provider,
        response_id="response-268-changed",
        request_id="request-268-changed",
        status="completed",
        text_parts=("REGENERATED CANDIDATE",),
        text="REGENERATED CANDIDATE",
    )
    changed_record = build_publication_regeneration_result_record(
        fixture.record.attempt_claim,
        changed_result,
    )
    fixture.result_path.unlink()
    persist_publication_regeneration_result(fixture.result_path, changed_record)

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )

    assert assessment.readiness == "stale_or_inconsistent"
    assert assessment.reason_codes == ("final_output_mismatch",)
    assert assessment.publication_assessment is not None
    assert assessment.publication_assessment.business_output_sha256 == (
        changed_record.business_output_sha256
    )


def test_failure_result_is_result_failure_and_never_uses_failure_message(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path, result=failure_result_fixture())

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )

    assert assessment.outcome == "failure"
    assert assessment.readiness == "result_failure"
    assert assessment.reason_codes == ("regeneration_result_failure",)
    assert assessment.publication_assessment is None
    assert assessment.claim_contract_sha256 is None
    assert "safe message" not in (
        serialize_publication_regeneration_readiness_assessment_canonical(assessment)
    )


def test_failure_result_rejects_claim_as_inapplicable(tmp_path: Path) -> None:
    fixture = readiness_fixture(tmp_path, result=failure_result_fixture())
    contract = claim_contract_for(
        fixture.audit.post_terminal_facts,
        output="ORIGINAL BUSINESS OUTPUT 日本語",
    )

    with pytest.raises(PublicationRegenerationReadinessError) as caught:
        assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
            claim_contract=contract,
        )

    assert caught.value.detail.classification == "claim_contract_inapplicable"
    assert str(caught.value) == "publication regeneration readiness is invalid"
    assert "safe message" not in str(caught.value)


def test_source_audit_digest_mismatch_is_rejected_before_assessment(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    other_audit = build_publication_readiness_audit_record(
        fixture.audit.post_terminal_facts,
        "another source output",
    )
    other_path = tmp_path / "other-audit.json"
    persist_publication_readiness_audit(other_path, other_audit)

    with pytest.raises(PublicationRegenerationReadinessError) as caught:
        assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=other_path,
        )

    assert caught.value.detail.classification == "source_audit_mismatch"
    assert "another source output" not in str(caught.value)


def test_tampered_result_source_identity_is_strictly_rejected(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    original = fixture.result_path.read_bytes()
    tampered = original.replace(
        fixture.record.source_audit_sha256.encode("ascii"),
        ("a" * 64).encode("ascii"),
        1,
    )
    fixture.result_path.write_bytes(tampered)

    with pytest.raises(PublicationRegenerationReadinessError) as caught:
        assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
        )

    assert caught.value.detail.classification == "result_load"
    with pytest.raises(PublicationRegenerationResultLoadError):
        load_publication_regeneration_result(fixture.result_path)


def test_wrapper_rejects_direct_forged_ready_and_subclasses(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    contract = exact_contract(fixture)
    values = {
        "schema_version": "publication-regeneration-readiness.v1",
        "regeneration_id": fixture.record.regeneration_id,
        "result_record_sha256": fixture.record.digest,
        "source_audit_sha256": fixture.audit.digest,
        "source_post_terminal_facts_sha256": fixture.audit.post_terminal_facts.digest,
        "outcome": "success",
        "publication_assessment": None,
        "readiness": "ready",
        "claim_contract_sha256": contract.digest,
        "reason_codes": (),
    }

    with pytest.raises(PublicationRegenerationReadinessError):
        PublicationRegenerationReadinessAssessment(**values)
    with pytest.raises(PublicationRegenerationReadinessError):
        PublicationRegenerationReadinessAssessmentChild(**values)
    with pytest.raises(PublicationRegenerationReadinessError):
        serialize_publication_regeneration_readiness_assessment_canonical(
            values  # type: ignore[arg-type]
        )


def test_wrapper_is_frozen_and_mirrors_nested_assessment_exactly(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=exact_contract(fixture),
    )

    with pytest.raises(FrozenInstanceError):
        assessment.readiness = "stale_or_inconsistent"  # type: ignore[misc]
    assert assessment.readiness == assessment.publication_assessment.readiness
    assert assessment.reason_codes == assessment.publication_assessment.reason_codes
    assert assessment.claim_contract_sha256 == (
        assessment.publication_assessment.claim_contract_sha256
    )


def test_wrapper_rejects_tampered_source_facts_identity(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=exact_contract(fixture),
    )

    with pytest.raises(PublicationRegenerationReadinessError) as caught:
        replace(assessment, source_post_terminal_facts_sha256="a" * 64)

    assert caught.value.detail.classification == "source_facts_binding"


def test_canonical_wrapper_fixture_is_compact_and_digest_bound(tmp_path: Path) -> None:
    fixture = readiness_fixture(tmp_path)
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )
    canonical = serialize_publication_regeneration_readiness_assessment_canonical(
        assessment
    )

    assert canonical == canonical.rstrip("\n")
    assert "result.json" not in canonical
    assert "source-audit.json" not in canonical
    assert fixture.record.result.text not in canonical
    assert publication_regeneration_readiness_assessment_canonical_bytes(
        assessment
    ) == canonical.encode("utf-8")
    expected = (
        '{"claim_contract_sha256":null,"outcome":"success",'
        '"publication_assessment":{"business_output_sha256":"f5a064be281eea4db190ed7268f4a1e005ca05227654bbfa260a6c5684da743e",'
        '"claim_contract_sha256":null,"execution_status":"workflow_complete",'
        '"post_terminal_facts":{"completed_step_ids":["research","publish"],'
        '"events_sha256":"1232505d7388720951336b434fe00df5474cefd0d659a99283c52a72d29ad6c8",'
        '"final_output_sha256":"f5a064be281eea4db190ed7268f4a1e005ca05227654bbfa260a6c5684da743e",'
        '"schema_version":"post-terminal-facts.v1",'
        '"state_sha256":"6e4acd32f41a8dc7c8d1e1a49b1ee6dde461786ac454cecf10f14f9af650c022",'
        '"terminal_employee_id":"editor","terminal_provider":"terminal-provider",'
        '"terminal_reason":"last_step_succeeded","terminal_status":"workflow_complete",'
        '"terminal_step_id":"publish","terminal_step_index":2,"workflow_id":"phase268-workflow"},'
        '"readiness":"insufficient_evidence","reason_codes":["claim_contract_missing"],'
        '"schema_version":"publication-readiness.v1"},'
        '"readiness":"insufficient_evidence","reason_codes":["claim_contract_missing"],'
        '"regeneration_id":"regen-20260912-01",'
        '"result_record_sha256":"ded25e22fb8c1fac42db443c4e5a60682b10b9697ddb749bebacecd708624eda",'
        '"schema_version":"publication-regeneration-readiness.v1",'
        '"source_audit_sha256":"34d656c42f0b15d74b8d92babe0d361fa0a1c0a69a221b68e985ba87cfe68926",'
        '"source_post_terminal_facts_sha256":"c3e68c8c60eee1b1e7a41a7df2dc338a3e0d7eca6838916a68396ff511aec6dd"}'
    )
    assert canonical == expected
    assert publication_regeneration_readiness_assessment_digest(assessment) == (
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    assert assessment.digest == (
        "ad65b40bb48a2885ac3b0cb62d360c4a801f05df3431151a783dde55af8a5595"
    )


def test_canonical_wrapper_digest_is_stable_in_fresh_process(tmp_path: Path) -> None:
    fixture = readiness_fixture(tmp_path)
    expected = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    ).digest
    script = """
import sys
from pathlib import Path
from ai_office.engine.publication_regeneration_readiness import (
    assess_publication_regeneration_result_readiness,
)
print(assess_publication_regeneration_result_readiness(
    result_path=Path(sys.argv[1]), source_audit_path=Path(sys.argv[2])
).digest)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (
        f"{Path.cwd() / 'src'}:{environment.get('PYTHONPATH', '')}"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(fixture.result_path),
            str(fixture.audit_path),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected


def test_readiness_boundary_does_not_mutate_any_evidence_or_ledger(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    before = {
        "result": fixture.result_path.read_bytes(),
        "audit": fixture.audit_path.read_bytes(),
        "state": fixture.state_path.read_bytes(),
        "events": fixture.events_path.read_bytes(),
        "ledger": tuple(
            (path.name, path.read_bytes())
            for path in fixture.ledger_directory.iterdir()
        ),
    }

    assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=exact_contract(fixture),
    )

    assert before == {
        "result": fixture.result_path.read_bytes(),
        "audit": fixture.audit_path.read_bytes(),
        "state": fixture.state_path.read_bytes(),
        "events": fixture.events_path.read_bytes(),
        "ledger": tuple(
            (path.name, path.read_bytes())
            for path in fixture.ledger_directory.iterdir()
        ),
    }


def test_readiness_boundary_has_no_write_network_environment_or_clock_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = readiness_fixture(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(time, "time", forbidden)

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )
    assert assessment.readiness == "insufficient_evidence"


def test_loaded_source_audit_and_result_digests_are_authoritative(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    loaded_audit = load_publication_readiness_audit(fixture.audit_path)
    loaded_result = load_publication_regeneration_result(fixture.result_path)
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )

    assert assessment.result_record_sha256 == loaded_result.digest
    assert assessment.source_audit_sha256 == publication_readiness_audit_digest(
        loaded_audit
    )
    assert assessment.source_post_terminal_facts_sha256 == post_terminal_facts_digest(
        loaded_audit.post_terminal_facts
    )
    assert loaded_result.source_audit_sha256 == loaded_audit.digest
    assert loaded_result.attempt_claim.source_audit_sha256 == loaded_audit.digest


def test_invalid_claim_type_is_rejected_without_coercion(tmp_path: Path) -> None:
    fixture = readiness_fixture(tmp_path)

    with pytest.raises(PublicationRegenerationReadinessError) as caught:
        assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
            claim_contract={
                "workflow_id": fixture.audit.post_terminal_facts.workflow_id
            },  # type: ignore[arg-type]
        )

    assert caught.value.detail.classification == "claim_contract_type"


def test_current_workflows_result_bytes_are_unchanged_by_readiness_projection(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    (workflows_directory / "workflow.yaml").write_text(
        """id: phase268-workflow
name: Phase 268 workflow
description: readiness projection fixture
steps:
  - id: research
    name: Research
    employee: researcher
    instructions: Research.
  - id: publish
    name: Publish
    employee: editor
    instructions: Prepare.
""",
        encoding="utf-8",
    )
    employee_template = """id: {employee_id}
name: {employee_name}
role: Work deterministically.
instructions: Work deterministically.
model: fixture-model
allowed_tools: []
"""
    for employee_id in ("researcher", "editor"):
        (employees_directory / f"{employee_id}.yaml").write_text(
            employee_template.format(
                employee_id=employee_id,
                employee_name=employee_id.title(),
            ),
            encoding="utf-8",
        )

    runner = CliRunner()
    args = [
        "workflows",
        "result",
        "phase268-workflow",
        "--state-path",
        str(fixture.state_path),
        "--events-path",
        str(fixture.events_path),
        "--directory",
        str(workflows_directory),
        "--employees-directory",
        str(employees_directory),
    ]
    before = runner.invoke(app, args)
    assert before.exit_code == 0, before.output
    assert "ORIGINAL BUSINESS OUTPUT 日本語" in before.stdout

    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )
    assert assessment.readiness == "insufficient_evidence"

    after = runner.invoke(app, args)
    assert after.exit_code == 0, after.output
    assert after.stdout == before.stdout
