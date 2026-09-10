"""Focused provider-free tests for Phase 261 post-terminal evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.post_terminal_facts import (
    PersistedTerminalSnapshot,
    PersistedTerminalSnapshotError,
    PostTerminalFactsError,
    PublicationReadinessError,
    assess_terminal_publication_readiness,
    build_post_terminal_facts,
    load_persisted_terminal_snapshot,
    post_terminal_facts_digest,
    publication_readiness_assessment_digest,
    serialize_post_terminal_facts_canonical,
    serialize_publication_readiness_assessment_canonical,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class StringChild(str):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase261-workflow",
            "name": "Phase 261 workflow",
            "description": "post-terminal facts fixture",
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


def success_history(output: str = "FINAL 日本語 😀") -> tuple[
    WorkflowExecutionState, tuple[RuntimeStepEvent, ...]
]:
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
            response_id="response-terminal-secret-like",
            request_id="request-terminal-secret-like",
            output_text=output,
            message=None,
        ),
    )
    return state, events


def failed_history() -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    definition = workflow()
    state = WorkflowExecutionState(
        workflow_id=definition.id,
        status="failed",
        current_step_id="research",
        current_step_index=1,
        current_employee_id="researcher",
        completed_step_ids=(),
        last_failure_category="api_error",
    )
    event = RuntimeStepEvent(
        event_type="step_failed",
        workflow_id=definition.id,
        step_id="research",
        step_index=1,
        employee_id="researcher",
        previous_status="running",
        next_status="failed",
        provider="failure-provider",
        failure_category="api_error",
        response_id=None,
        request_id="request-secret-like",
        output_text=None,
        message="safe failure",
    )
    return state, (event,)


def write_history(
    tmp_path: Path,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        state_path=tmp_path / "state.json",
        events_path=tmp_path / "events.jsonl",
    )
    targets.state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return targets


def load_facts(
    tmp_path: Path,
    *,
    output: str = "FINAL 日本語 😀",
):
    state, events = success_history(output)
    targets = write_history(tmp_path, state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    return targets, snapshot, build_post_terminal_facts(snapshot)


def test_fresh_snapshot_uses_exact_source_bytes_and_terminal_event_identity(
    tmp_path: Path,
) -> None:
    targets, snapshot, facts = load_facts(tmp_path)

    assert snapshot.terminal_status == "workflow_complete"
    assert snapshot.terminal_reason == "last_step_succeeded"
    assert snapshot.state_sha256 == hashlib.sha256(
        targets.state_path.read_bytes()
    ).hexdigest()
    assert snapshot.events_sha256 == hashlib.sha256(
        targets.events_path.read_bytes()
    ).hexdigest()
    assert facts.workflow_id == "phase261-workflow"
    assert facts.terminal_step_id == "publish"
    assert facts.terminal_step_index == 2
    assert facts.terminal_employee_id == "editor"
    assert facts.terminal_provider == "terminal-provider"
    assert facts.completed_step_ids == ("research", "publish")
    assert facts.final_output_sha256 == hashlib.sha256(
        "FINAL 日本語 😀".encode()
    ).hexdigest()


def test_facts_omit_raw_output_request_response_path_and_diagnostics(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    canonical = serialize_post_terminal_facts_canonical(facts)

    assert "FINAL 日本語 😀" not in canonical
    assert "response-terminal-secret-like" not in canonical
    assert "request-terminal-secret-like" not in canonical
    assert "state.json" not in canonical
    assert "diagnostic" not in canonical
    assert set(json.loads(canonical)) == {
        "completed_step_ids",
        "events_sha256",
        "final_output_sha256",
        "schema_version",
        "state_sha256",
        "terminal_employee_id",
        "terminal_provider",
        "terminal_reason",
        "terminal_status",
        "terminal_step_id",
        "terminal_step_index",
        "workflow_id",
    }


def test_exact_output_match_is_insufficient_evidence_not_ready(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    assessment = assess_terminal_publication_readiness(facts, "FINAL 日本語 😀")

    assert assessment.execution_status == "workflow_complete"
    assert assessment.readiness == "insufficient_evidence"
    assert assessment.reason_codes == ("claim_contract_missing",)
    assert assessment.claim_contract_sha256 is None
    assert assessment.business_output_sha256 == facts.final_output_sha256
    assert "ready" not in assessment.reason_codes


def test_missing_or_mismatched_output_is_stale_or_inconsistent(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    missing = assess_terminal_publication_readiness(facts, None)
    mismatch = assess_terminal_publication_readiness(facts, "different output")

    assert missing.readiness == "stale_or_inconsistent"
    assert missing.reason_codes == ("final_output_missing",)
    assert mismatch.readiness == "stale_or_inconsistent"
    assert mismatch.reason_codes == ("final_output_mismatch",)


def test_terminal_failure_is_never_publication_ready(tmp_path: Path) -> None:
    state, events = failed_history()
    targets = write_history(tmp_path, state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    facts = build_post_terminal_facts(snapshot)

    assessment = assess_terminal_publication_readiness(facts, "candidate")

    assert snapshot.terminal_status == "persisted_failure"
    assert facts.final_output_sha256 is None
    assert assessment.execution_status == "persisted_failure"
    assert assessment.readiness == "insufficient_evidence"
    assert assessment.reason_codes == (
        "execution_not_workflow_complete",
        "final_output_missing",
    )


@pytest.mark.parametrize("status", ["ready", "running"])
def test_nonterminal_history_is_rejected(tmp_path: Path, status: str) -> None:
    definition = workflow()
    state = WorkflowExecutionState(
        definition.id,
        status,  # type: ignore[arg-type]
        "research",
        1,
        "researcher",
        (),
        None,
    )
    targets = write_history(tmp_path, state, ())

    with pytest.raises(PersistedTerminalSnapshotError):
        load_persisted_terminal_snapshot(definition, targets)


def test_nonfinal_success_is_not_workflow_complete(tmp_path: Path) -> None:
    definition = workflow()
    state = WorkflowExecutionState(
        definition.id,
        "succeeded",
        "research",
        1,
        "researcher",
        ("research",),
        None,
    )
    event = RuntimeStepEvent(
        "step_succeeded",
        definition.id,
        "research",
        1,
        "researcher",
        "running",
        "succeeded",
        "provider",
        None,
        "response",
        "request",
        "intermediate",
        None,
    )
    targets = write_history(tmp_path, state, (event,))

    with pytest.raises(PersistedTerminalSnapshotError):
        load_persisted_terminal_snapshot(definition, targets)


def test_corrupt_history_is_rejected_without_mutation(tmp_path: Path) -> None:
    definition = workflow()
    state, events = success_history()
    targets = write_history(tmp_path, state, events)
    targets.state_path.write_bytes(b"not-json")
    before = targets.state_path.read_bytes(), targets.events_path.read_bytes()

    with pytest.raises(PersistedTerminalSnapshotError):
        load_persisted_terminal_snapshot(definition, targets)

    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_facts_and_assessment_canonicalization_is_deterministic(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    first_facts = serialize_post_terminal_facts_canonical(facts)
    second_facts = serialize_post_terminal_facts_canonical(replace(facts))
    assessment = assess_terminal_publication_readiness(facts, "FINAL 日本語 😀")
    first_assessment = serialize_publication_readiness_assessment_canonical(assessment)
    second_assessment = serialize_publication_readiness_assessment_canonical(
        replace(assessment)
    )

    assert first_facts == second_facts
    assert post_terminal_facts_digest(facts) == hashlib.sha256(
        first_facts.encode("utf-8")
    ).hexdigest()
    assert first_assessment == second_assessment
    assert publication_readiness_assessment_digest(assessment) == hashlib.sha256(
        first_assessment.encode("utf-8")
    ).hexdigest()


def test_facts_digest_binds_terminal_identity_sources_and_raw_output() -> None:
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        "step",
        1,
        "employee",
        ("step",),
        None,
    )
    event = RuntimeStepEvent(
        "step_succeeded",
        "w",
        "step",
        1,
        "employee",
        "running",
        "succeeded",
        "provider",
        None,
        "response",
        "request",
        "output",
        None,
    )
    from ai_office.storage import LoadedWorkflowExecutionHistory

    snapshot = PersistedTerminalSnapshot(
        LoadedWorkflowExecutionHistory(state, (event,)),
        "workflow_complete",
        "last_step_succeeded",
        "a" * 64,
        "b" * 64,
    )
    facts = build_post_terminal_facts(snapshot)
    variants = (
        replace(facts, state_sha256="c" * 64),
        replace(facts, events_sha256="c" * 64),
        replace(facts, final_output_sha256="c" * 64),
        replace(facts, terminal_provider="other-provider"),
        replace(facts, terminal_step_id="other-step"),
        replace(facts, completed_step_ids=("other-step",)),
    )

    assert len({facts.digest, *(variant.digest for variant in variants)}) == 7
    canonical_result_digest = hashlib.sha256(
        json.dumps(
            {
                "employee_id": "employee",
                "output_text": "output",
                "step_id": "step",
                "step_index": 1,
                "workflow_id": "w",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert facts.final_output_sha256 != canonical_result_digest


def test_snapshot_and_assessment_are_byte_for_byte_read_only(
    tmp_path: Path,
) -> None:
    targets, snapshot, facts = load_facts(tmp_path)
    before = targets.state_path.read_bytes(), targets.events_path.read_bytes()

    assessment = assess_terminal_publication_readiness(facts, "FINAL 日本語 😀")
    serialize_post_terminal_facts_canonical(facts)
    serialize_publication_readiness_assessment_canonical(assessment)

    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before
    assert snapshot.history.events[-1].output_text == "FINAL 日本語 😀"
    assert not (tmp_path / "publication-readiness.json").exists()


def test_strict_models_are_frozen_and_reject_subclass_values(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    with pytest.raises(FrozenInstanceError):
        facts.workflow_id = "changed"  # type: ignore[misc]
    with pytest.raises(PostTerminalFactsError):
        replace(facts, workflow_id=StringChild(facts.workflow_id))
    with pytest.raises(PublicationReadinessError):
        assess_terminal_publication_readiness(facts, StringChild("FINAL 日本語 😀"))
