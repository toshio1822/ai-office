"""Provider-free post-terminal facts and publication-readiness assessment.

This module is a read-only boundary after terminal state/event persistence.  It
loads the exact persisted bytes once, derives immutable facts from that fresh
snapshot, and assesses output identity without interpreting business prose.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    classify_loaded_persisted_execution_outcome,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    validate_strict_terminal_history,
)
from ai_office.engine.workflow_progression import decide_workflow_progression
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history_with_source_digests,
)

PostTerminalTerminalStatus = Literal["workflow_complete", "persisted_failure"]
PublicationReadiness = Literal[
    "ready",
    "stale_or_inconsistent",
    "insufficient_evidence",
]

_POST_TERMINAL_FACTS_SCHEMA_VERSION = "post-terminal-facts.v1"
_PUBLICATION_READINESS_SCHEMA_VERSION = "publication-readiness.v1"
_ERROR_MESSAGE = "post-terminal evidence is invalid"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_REASON_ORDER = (
    "execution_not_workflow_complete",
    "final_output_missing",
    "final_output_mismatch",
    "claim_contract_missing",
)
_REASON_CODES = frozenset(_REASON_ORDER)


@dataclass(frozen=True)
class PostTerminalEvidenceFailureDetail:
    """Safe classification for a rejected post-terminal evidence value."""

    classification: str


class PostTerminalEvidenceError(ValueError):
    """Base error for invalid post-terminal evidence inputs."""

    def __init__(self, classification: str = "evidence") -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PostTerminalEvidenceFailureDetail(classification)


class PersistedTerminalSnapshotError(PostTerminalEvidenceError):
    """Raised when fresh persisted terminal history is not an exact snapshot."""


class PostTerminalFactsError(PostTerminalEvidenceError):
    """Raised when post-terminal facts are not exactly typed."""


class PublicationReadinessError(PostTerminalEvidenceError):
    """Raised when a readiness assessment input is not exactly typed."""


@dataclass(frozen=True)
class PersistedTerminalSnapshot:
    """Fresh immutable terminal history plus exact source-byte identities."""

    history: LoadedWorkflowExecutionHistory
    terminal_status: PostTerminalTerminalStatus
    terminal_reason: str
    state_sha256: str
    events_sha256: str

    def __post_init__(self) -> None:
        _validate_persisted_terminal_snapshot(self)


@dataclass(frozen=True)
class PostTerminalFacts:
    """Safe facts that exist only after terminal persistence."""

    schema_version: Literal["post-terminal-facts.v1"]
    workflow_id: str
    terminal_status: PostTerminalTerminalStatus
    terminal_reason: str
    terminal_step_id: str
    terminal_step_index: int
    terminal_employee_id: str
    terminal_provider: str | None
    completed_step_ids: tuple[str, ...]
    state_sha256: str
    events_sha256: str
    final_output_sha256: str | None

    def __post_init__(self) -> None:
        _validate_post_terminal_facts(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical facts JSON."""
        return post_terminal_facts_digest(self)


@dataclass(frozen=True)
class PublicationReadinessAssessment:
    """Provider-free derived readiness judgment for one terminal snapshot."""

    schema_version: Literal["publication-readiness.v1"]
    execution_status: PostTerminalTerminalStatus
    readiness: PublicationReadiness
    reason_codes: tuple[str, ...]
    post_terminal_facts: PostTerminalFacts
    business_output_sha256: str | None
    claim_contract_sha256: str | None

    def __post_init__(self) -> None:
        _validate_publication_readiness_assessment(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical assessment JSON."""
        return publication_readiness_assessment_digest(self)


def load_persisted_terminal_snapshot(
    workflow: WorkflowDefinition,
    targets: WorkflowExecutionPersistenceTargets,
) -> PersistedTerminalSnapshot:
    """Load and validate one exact persisted terminal workflow history.

    The existing exact-byte history loader supplies both source digests.  The
    existing terminal classifier used by ``workflows result`` is applied to the
    already-loaded history without reopening or mutating either target;
    progression then distinguishes final completion from a nonterminal success.
    """
    if type(workflow) is not WorkflowDefinition:
        _raise_snapshot("workflow_definition")
    if type(targets) is not WorkflowExecutionPersistenceTargets:
        _raise_snapshot("persistence_targets")
    if (
        not isinstance(targets.state_path, Path)
        or not isinstance(targets.events_path, Path)
    ):
        _raise_snapshot("persistence_targets")
    try:
        history, state_sha256, events_sha256 = (
            load_workflow_execution_history_with_source_digests(targets)
        )
        validate_strict_terminal_history(workflow, history.state, history.events)
    except (WorkflowExecutionLoadError, TerminalHistoryContractError):
        _raise_snapshot("terminal_history")
    except Exception:
        _raise_snapshot("terminal_history")

    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise_snapshot("terminal_history")
    try:
        classified = classify_loaded_persisted_execution_outcome(workflow, history)
    except Exception:
        _raise_snapshot("terminal_classification")
    state = history.state
    if type(classified) is not PersistedExecutionOutcome:
        _raise_snapshot("terminal_classification")
    if (
        classified.workflow_id != state.workflow_id
        or classified.current_step_id != state.current_step_id
        or classified.current_step_index != state.current_step_index
        or classified.current_employee_id != state.current_employee_id
        or classified.failure_category != state.last_failure_category
    ):
        _raise_snapshot("terminal_classification")
    if state.status == "succeeded":
        if classified.outcome != "persisted_success":
            _raise_snapshot("terminal_classification")
        try:
            decision = decide_workflow_progression(workflow, history)
        except Exception:
            _raise_snapshot("terminal_classification")
        if decision.decision != "workflow_complete":
            _raise_snapshot("nonterminal_history")
        terminal_status: PostTerminalTerminalStatus = "workflow_complete"
        terminal_reason = decision.reason
    elif state.status == "failed":
        if (
            classified.outcome != "persisted_failure"
            or type(state.last_failure_category) is not str
        ):
            _raise_snapshot("terminal_classification")
        terminal_status = "persisted_failure"
        terminal_reason = state.last_failure_category
    else:
        _raise_snapshot("nonterminal_history")

    try:
        return PersistedTerminalSnapshot(
            history=history,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
            state_sha256=state_sha256,
            events_sha256=events_sha256,
        )
    except PostTerminalEvidenceError:
        raise
    except Exception:
        _raise_snapshot("terminal_history")


def build_post_terminal_facts(
    snapshot: PersistedTerminalSnapshot,
) -> PostTerminalFacts:
    """Build facts from a supplied snapshot without reading or writing files."""
    if type(snapshot) is not PersistedTerminalSnapshot:
        _raise_facts("snapshot_type")
    _validate_persisted_terminal_snapshot(snapshot)
    state = snapshot.history.state
    terminal_event = snapshot.history.events[-1]
    final_output_sha256 = (
        _raw_output_digest(terminal_event.output_text)
        if snapshot.terminal_status == "workflow_complete"
        else None
    )
    try:
        return PostTerminalFacts(
            schema_version=_POST_TERMINAL_FACTS_SCHEMA_VERSION,
            workflow_id=state.workflow_id,
            terminal_status=snapshot.terminal_status,
            terminal_reason=snapshot.terminal_reason,
            terminal_step_id=state.current_step_id,
            terminal_step_index=state.current_step_index,
            terminal_employee_id=state.current_employee_id,
            terminal_provider=terminal_event.provider,
            completed_step_ids=state.completed_step_ids,
            state_sha256=snapshot.state_sha256,
            events_sha256=snapshot.events_sha256,
            final_output_sha256=final_output_sha256,
        )
    except PostTerminalEvidenceError:
        raise
    except Exception:
        _raise_facts("facts_build")


def assess_terminal_publication_readiness(
    post_terminal_facts: PostTerminalFacts,
    business_output: str | None,
) -> PublicationReadinessAssessment:
    """Assess identity-only publication evidence without provider or prose work.

    Phase 261 intentionally cannot return ``ready``: a matching final output
    proves only byte identity, while the structured claim contract is absent.
    """
    if type(post_terminal_facts) is not PostTerminalFacts:
        _raise_readiness("facts_type")
    _validate_post_terminal_facts(post_terminal_facts)
    if business_output is not None and type(business_output) is not str:
        _raise_readiness("business_output_type")

    business_output_sha256 = (
        _raw_output_digest(business_output) if business_output is not None else None
    )
    reasons: list[str] = []
    if post_terminal_facts.terminal_status == "persisted_failure":
        reasons.append("execution_not_workflow_complete")
        if post_terminal_facts.final_output_sha256 is None:
            reasons.append("final_output_missing")
        return _build_assessment(
            post_terminal_facts,
            "insufficient_evidence",
            reasons,
            business_output_sha256,
        )

    declared_output_sha256 = post_terminal_facts.final_output_sha256
    if declared_output_sha256 is None:
        reasons.append("final_output_missing")
        return _build_assessment(
            post_terminal_facts,
            "stale_or_inconsistent",
            reasons,
            business_output_sha256,
        )
    if business_output is None:
        reasons.append("final_output_missing")
        return _build_assessment(
            post_terminal_facts,
            "stale_or_inconsistent",
            reasons,
            business_output_sha256,
        )
    if business_output_sha256 != declared_output_sha256:
        reasons.append("final_output_mismatch")
        return _build_assessment(
            post_terminal_facts,
            "stale_or_inconsistent",
            reasons,
            business_output_sha256,
        )

    return _build_assessment(
        post_terminal_facts,
        "insufficient_evidence",
        ["claim_contract_missing"],
        business_output_sha256,
    )


def serialize_post_terminal_facts_canonical(
    facts: PostTerminalFacts,
) -> str:
    """Serialize facts as compact deterministic UTF-8 JSON text."""
    if type(facts) is not PostTerminalFacts:
        _raise_facts("facts_type")
    _validate_post_terminal_facts(facts)
    value = {
        "completed_step_ids": list(facts.completed_step_ids),
        "events_sha256": facts.events_sha256,
        "final_output_sha256": facts.final_output_sha256,
        "schema_version": facts.schema_version,
        "terminal_employee_id": facts.terminal_employee_id,
        "terminal_provider": facts.terminal_provider,
        "terminal_reason": facts.terminal_reason,
        "terminal_status": facts.terminal_status,
        "terminal_step_id": facts.terminal_step_id,
        "terminal_step_index": facts.terminal_step_index,
        "workflow_id": facts.workflow_id,
        "state_sha256": facts.state_sha256,
    }
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _raise_facts("facts_serialization")


def post_terminal_facts_canonical_bytes(facts: PostTerminalFacts) -> bytes:
    """Return canonical facts JSON encoded as UTF-8 bytes."""
    return serialize_post_terminal_facts_canonical(facts).encode("utf-8")


def post_terminal_facts_digest(facts: PostTerminalFacts) -> str:
    """Return the SHA-256 digest of canonical facts JSON UTF-8 bytes."""
    return sha256(post_terminal_facts_canonical_bytes(facts)).hexdigest()


def serialize_publication_readiness_assessment_canonical(
    assessment: PublicationReadinessAssessment,
) -> str:
    """Serialize an assessment as compact deterministic UTF-8 JSON text."""
    if type(assessment) is not PublicationReadinessAssessment:
        _raise_readiness("assessment_type")
    _validate_publication_readiness_assessment(assessment)
    try:
        value = {
            "business_output_sha256": assessment.business_output_sha256,
            "claim_contract_sha256": assessment.claim_contract_sha256,
            "execution_status": assessment.execution_status,
            "post_terminal_facts": json.loads(
                serialize_post_terminal_facts_canonical(
                    assessment.post_terminal_facts
                )
            ),
            "readiness": assessment.readiness,
            "reason_codes": list(assessment.reason_codes),
            "schema_version": assessment.schema_version,
        }
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        _raise_readiness("assessment_serialization")


def publication_readiness_assessment_canonical_bytes(
    assessment: PublicationReadinessAssessment,
) -> bytes:
    """Return canonical assessment JSON encoded as UTF-8 bytes."""
    return serialize_publication_readiness_assessment_canonical(assessment).encode(
        "utf-8"
    )


def publication_readiness_assessment_digest(
    assessment: PublicationReadinessAssessment,
) -> str:
    """Return the SHA-256 digest of canonical assessment JSON UTF-8 bytes."""
    return sha256(
        publication_readiness_assessment_canonical_bytes(assessment)
    ).hexdigest()


def _build_assessment(
    facts: PostTerminalFacts,
    readiness: PublicationReadiness,
    reasons: list[str],
    business_output_sha256: str | None,
) -> PublicationReadinessAssessment:
    try:
        return PublicationReadinessAssessment(
            schema_version=_PUBLICATION_READINESS_SCHEMA_VERSION,
            execution_status=facts.terminal_status,
            readiness=readiness,
            reason_codes=tuple(reasons),
            post_terminal_facts=facts,
            business_output_sha256=business_output_sha256,
            claim_contract_sha256=None,
        )
    except PostTerminalEvidenceError:
        raise
    except Exception:
        _raise_readiness("assessment_build")


def _validate_persisted_terminal_snapshot(
    snapshot: PersistedTerminalSnapshot,
) -> None:
    if type(snapshot.history) is not LoadedWorkflowExecutionHistory:
        _raise_snapshot("history_type")
    history = snapshot.history
    if (
        type(history.state) is not WorkflowExecutionState
        or type(history.events) is not tuple
        or not history.events
        or any(type(event) is not RuntimeStepEvent for event in history.events)
    ):
        _raise_snapshot("history_type")
    _validate_digest(snapshot.state_sha256, "state_digest")
    _validate_digest(snapshot.events_sha256, "events_digest")
    if type(snapshot.terminal_status) is not str or snapshot.terminal_status not in {
        "workflow_complete",
        "persisted_failure",
    }:
        _raise_snapshot("terminal_status")
    if type(snapshot.terminal_reason) is not str or not snapshot.terminal_reason:
        _raise_snapshot("terminal_reason")

    state = history.state
    event = history.events[-1]
    _validate_state_shape(state)
    for history_event in history.events:
        _validate_event_shape(history_event)
    _validate_history_shape(history, state)
    if (
        event.workflow_id != state.workflow_id
        or event.step_id != state.current_step_id
        or event.step_index != state.current_step_index
        or event.employee_id != state.current_employee_id
        or event.previous_status != "running"
    ):
        _raise_snapshot("terminal_identity")
    if snapshot.terminal_status == "workflow_complete":
        if (
            state.status != "succeeded"
            or state.last_failure_category is not None
            or event.event_type != "step_succeeded"
            or event.next_status != "succeeded"
            or event.failure_category is not None
            or type(event.response_id) is not str
            or not event.response_id
            or type(event.output_text) is not str
            or not event.output_text
            or event.message is not None
            or snapshot.terminal_reason != "last_step_succeeded"
        ):
            _raise_snapshot("terminal_success")
    else:
        if (
            state.status != "failed"
            or type(state.last_failure_category) is not str
            or state.last_failure_category not in _FAILURE_CATEGORIES
            or event.event_type != "step_failed"
            or event.next_status != "failed"
            or event.failure_category != state.last_failure_category
            or event.response_id is not None
            or event.output_text is not None
            or event.message is None
            or snapshot.terminal_reason != state.last_failure_category
        ):
            _raise_snapshot("terminal_failure")


def _validate_state_shape(state: WorkflowExecutionState) -> None:
    if (
        type(state.workflow_id) is not str
        or not state.workflow_id
        or type(state.status) is not str
        or state.status not in {"succeeded", "failed"}
        or type(state.current_step_id) is not str
        or not state.current_step_id
        or type(state.current_step_index) is not int
        or state.current_step_index < 1
        or type(state.current_employee_id) is not str
        or not state.current_employee_id
        or type(state.completed_step_ids) is not tuple
        or any(
            type(step_id) is not str or not step_id
            for step_id in state.completed_step_ids
        )
        or len(state.completed_step_ids) != len(set(state.completed_step_ids))
        or (
            state.last_failure_category is not None
            and (
                type(state.last_failure_category) is not str
                or state.last_failure_category not in _FAILURE_CATEGORIES
            )
        )
    ):
        _raise_snapshot("state_shape")


def _validate_history_shape(
    history: LoadedWorkflowExecutionHistory,
    state: WorkflowExecutionState,
) -> None:
    """Check terminal history linkage without needing the workflow definition."""
    expected_event_count = len(state.completed_step_ids) + (
        0 if state.status == "succeeded" else 1
    )
    if len(history.events) != expected_event_count:
        _raise_snapshot("history_shape")
    expected_current_index = len(state.completed_step_ids) + (
        0 if state.status == "succeeded" else 1
    )
    if state.current_step_index != expected_current_index:
        _raise_snapshot("history_shape")
    for event_index, (event, step_id) in enumerate(
        zip(
            history.events[: len(state.completed_step_ids)],
            state.completed_step_ids,
            strict=True,
        ),
        1,
    ):
        if (
            event.event_type != "step_succeeded"
            or event.step_id != step_id
            or event.step_index != event_index
            or event.workflow_id != state.workflow_id
            or event.previous_status != "running"
            or event.next_status != "succeeded"
            or event.failure_category is not None
            or type(event.response_id) is not str
            or not event.response_id
            or type(event.output_text) is not str
            or not event.output_text
            or event.message is not None
        ):
            _raise_snapshot("history_shape")
    if state.status == "succeeded":
        if (
            not state.completed_step_ids
            or state.completed_step_ids[-1] != state.current_step_id
        ):
            _raise_snapshot("history_shape")
    elif history.events[-1].step_id in state.completed_step_ids:
        _raise_snapshot("history_shape")


def _validate_event_shape(event: RuntimeStepEvent) -> None:
    if (
        type(event.workflow_id) is not str
        or not event.workflow_id
        or type(event.step_id) is not str
        or not event.step_id
        or type(event.step_index) is not int
        or event.step_index < 1
        or type(event.employee_id) is not str
        or not event.employee_id
        or type(event.previous_status) is not str
        or event.previous_status != "running"
        or type(event.next_status) is not str
        or event.next_status not in {"succeeded", "failed"}
        or type(event.event_type) is not str
        or event.event_type not in {"step_succeeded", "step_failed"}
        or type(event.provider) is not str
        or not event.provider
    ):
        _raise_snapshot("event_shape")
    if event.response_id is not None and (
        type(event.response_id) is not str or not event.response_id
    ):
        _raise_snapshot("event_shape")
    if event.request_id is not None and type(event.request_id) is not str:
        _raise_snapshot("event_shape")
    if event.output_text is not None and type(event.output_text) is not str:
        _raise_snapshot("event_shape")
    if event.message is not None and type(event.message) is not str:
        _raise_snapshot("event_shape")
    if event.failure_category is not None and (
        type(event.failure_category) is not str
        or event.failure_category not in _FAILURE_CATEGORIES
    ):
        _raise_snapshot("event_shape")


def _validate_post_terminal_facts(facts: PostTerminalFacts) -> None:
    if (
        type(facts.schema_version) is not str
        or facts.schema_version != _POST_TERMINAL_FACTS_SCHEMA_VERSION
    ):
        _raise_facts("schema_version")
    if (
        type(facts.workflow_id) is not str
        or not facts.workflow_id
        or type(facts.terminal_status) is not str
        or facts.terminal_status not in {"workflow_complete", "persisted_failure"}
        or type(facts.terminal_reason) is not str
        or not facts.terminal_reason
        or type(facts.terminal_step_id) is not str
        or not facts.terminal_step_id
        or type(facts.terminal_step_index) is not int
        or facts.terminal_step_index < 1
        or type(facts.terminal_employee_id) is not str
        or not facts.terminal_employee_id
        or type(facts.completed_step_ids) is not tuple
        or any(
            type(step_id) is not str or not step_id
            for step_id in facts.completed_step_ids
        )
        or len(facts.completed_step_ids) != len(set(facts.completed_step_ids))
    ):
        _raise_facts("facts_shape")
    expected_step_index = len(facts.completed_step_ids) + (
        0 if facts.terminal_status == "workflow_complete" else 1
    )
    if facts.terminal_step_index != expected_step_index:
        _raise_facts("facts_shape")
    if facts.terminal_status == "workflow_complete":
        if facts.terminal_reason != "last_step_succeeded":
            _raise_facts("terminal_reason")
    elif facts.terminal_reason not in _FAILURE_CATEGORIES:
        _raise_facts("terminal_reason")
    if facts.terminal_provider is not None and (
        type(facts.terminal_provider) is not str or not facts.terminal_provider
    ):
        _raise_facts("facts_shape")
    _validate_digest(facts.state_sha256, "state_digest", error=_raise_facts)
    _validate_digest(facts.events_sha256, "events_digest", error=_raise_facts)
    _validate_optional_digest(
        facts.final_output_sha256, "output_digest", error=_raise_facts
    )


def _validate_publication_readiness_assessment(
    assessment: PublicationReadinessAssessment,
) -> None:
    if (
        type(assessment.schema_version) is not str
        or assessment.schema_version != _PUBLICATION_READINESS_SCHEMA_VERSION
    ):
        _raise_readiness("schema_version")
    if (
        type(assessment.execution_status) is not str
        or assessment.execution_status
        not in {"workflow_complete", "persisted_failure"}
    ):
        _raise_readiness("execution_status")
    if type(assessment.readiness) is not str or assessment.readiness not in {
        "ready",
        "stale_or_inconsistent",
        "insufficient_evidence",
    }:
        _raise_readiness("readiness")
    if type(assessment.reason_codes) is not tuple:
        _raise_readiness("reason_codes")
    if any(
        type(reason) is not str or reason not in _REASON_CODES
        for reason in assessment.reason_codes
    ) or len(set(assessment.reason_codes)) != len(assessment.reason_codes):
        _raise_readiness("reason_codes")
    canonical_reasons = tuple(
        sorted(assessment.reason_codes, key=_REASON_ORDER.index)
    )
    if canonical_reasons != assessment.reason_codes:
        _raise_readiness("reason_codes")
    if type(assessment.post_terminal_facts) is not PostTerminalFacts:
        _raise_readiness("facts_type")
    if assessment.execution_status != assessment.post_terminal_facts.terminal_status:
        _raise_readiness("execution_status")
    _validate_optional_digest(
        assessment.business_output_sha256,
        "business_output_digest",
        error=_raise_readiness,
    )
    if assessment.claim_contract_sha256 is not None:
        _raise_readiness("claim_contract_deferred")


def _raw_output_digest(output_text: str | None) -> str:
    if output_text is None or type(output_text) is not str:
        _raise_facts("output_type")
    return sha256(output_text.encode("utf-8")).hexdigest()


def _validate_digest(
    value: object,
    classification: str,
    *,
    error: Callable[[str], None] | None = None,
) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        (error or _raise_snapshot)(classification)


def _validate_optional_digest(value: object, classification: str, *, error) -> None:
    if value is not None:
        _validate_digest(value, classification, error=error)


def _raise_snapshot(classification: str) -> None:
    raise PersistedTerminalSnapshotError(classification) from None


def _raise_facts(classification: str) -> None:
    raise PostTerminalFactsError(classification) from None


def _raise_readiness(classification: str) -> None:
    raise PublicationReadinessError(classification) from None


__all__ = [
    "PersistedTerminalSnapshot",
    "PersistedTerminalSnapshotError",
    "PostTerminalEvidenceError",
    "PostTerminalEvidenceFailureDetail",
    "PostTerminalFacts",
    "PostTerminalFactsError",
    "PostTerminalTerminalStatus",
    "PublicationReadiness",
    "PublicationReadinessAssessment",
    "PublicationReadinessError",
    "assess_terminal_publication_readiness",
    "build_post_terminal_facts",
    "load_persisted_terminal_snapshot",
    "post_terminal_facts_canonical_bytes",
    "post_terminal_facts_digest",
    "publication_readiness_assessment_canonical_bytes",
    "publication_readiness_assessment_digest",
    "serialize_post_terminal_facts_canonical",
    "serialize_publication_readiness_assessment_canonical",
]
