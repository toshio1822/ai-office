"""Provider-free post-terminal facts and publication-readiness assessment.

This module is a read-only boundary after terminal state/event persistence.  It
loads the exact persisted bytes once, derives immutable facts from that fresh
snapshot, and assesses output identity through an explicit structured claim
contract without interpreting business prose.
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
_PUBLICATION_CLAIM_CONTRACT_SCHEMA_VERSION = "publication-claims.v1"
_PUBLICATION_READINESS_AUDIT_SCHEMA_VERSION = "publication-readiness-audit.v1"
_PUBLICATION_CLAIM_CONTRACT_SCOPE = "post_terminal_runtime_consistency"
_ERROR_MESSAGE = "post-terminal evidence is invalid"
_AUDIT_ERROR_MESSAGE = "publication-readiness audit is invalid"
_AUDIT_PERSISTENCE_ERROR_MESSAGE = "publication-readiness audit persistence failed"
_AUDIT_LOAD_ERROR_MESSAGE = "publication-readiness audit could not be loaded"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_REASON_ORDER = (
    "execution_not_workflow_complete",
    "final_output_missing",
    "final_output_mismatch",
    "claim_contract_missing",
    "claim_contract_mismatch",
)
_REASON_CODES = frozenset(_REASON_ORDER)
_AUDIT_KEYS = frozenset(
    {
        "assessment",
        "assessment_sha256",
        "evaluated_claim_contract",
        "evaluated_claim_contract_sha256",
        "post_terminal_facts",
        "post_terminal_facts_sha256",
        "schema_version",
    }
)
_FACTS_KEYS = frozenset(
    {
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
)
_CLAIM_CONTRACT_KEYS = frozenset(
    {
        "asserted_terminal_status",
        "business_output_sha256",
        "post_terminal_facts_sha256",
        "schema_version",
        "scope",
        "workflow_id",
    }
)
_ASSESSMENT_KEYS = frozenset(
    {
        "business_output_sha256",
        "claim_contract_sha256",
        "execution_status",
        "post_terminal_facts",
        "readiness",
        "reason_codes",
        "schema_version",
    }
)
_ASSESSMENT_KEYS_WITH_CONTRACT = _ASSESSMENT_KEYS | {"claim_contract"}


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


class PublicationClaimContractError(PostTerminalEvidenceError):
    """Raised when a structured publication claim contract is invalid."""


class PublicationReadinessError(PostTerminalEvidenceError):
    """Raised when a readiness assessment input is not exactly typed."""


@dataclass(frozen=True)
class PublicationReadinessAuditFailureDetail:
    """Safe classification for a rejected readiness audit operation."""

    classification: str


class PublicationReadinessAuditError(ValueError):
    """Raised when a publication-readiness audit record is invalid."""

    def __init__(self, classification: str = "audit") -> None:
        super().__init__(_AUDIT_ERROR_MESSAGE)
        self.detail = PublicationReadinessAuditFailureDetail(classification)


class PublicationReadinessAuditPersistenceError(PublicationReadinessAuditError):
    """Raised when an explicit audit sidecar cannot be created safely."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _AUDIT_PERSISTENCE_ERROR_MESSAGE)
        self.detail = PublicationReadinessAuditFailureDetail(classification)


class PublicationReadinessAuditConflictError(PublicationReadinessAuditPersistenceError):
    """Raised when an existing sidecar has different bytes."""


class PublicationReadinessAuditLoadError(PublicationReadinessAuditError):
    """Raised when an audit sidecar is not an exact canonical record."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _AUDIT_LOAD_ERROR_MESSAGE)
        self.detail = PublicationReadinessAuditFailureDetail(classification)


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
class PublicationClaimContract:
    """Explicit, narrow assertion binding output to terminal runtime facts."""

    schema_version: Literal["publication-claims.v1"]
    scope: Literal["post_terminal_runtime_consistency"]
    workflow_id: str
    business_output_sha256: str
    post_terminal_facts_sha256: str
    asserted_terminal_status: Literal["workflow_complete"]

    def __post_init__(self) -> None:
        _validate_publication_claim_contract_model(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical contract JSON."""
        return publication_claim_contract_digest(self)


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
    claim_contract: PublicationClaimContract | None = None

    def __post_init__(self) -> None:
        _validate_publication_readiness_assessment(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical assessment JSON."""
        return publication_readiness_assessment_digest(self)


@dataclass(frozen=True)
class PublicationReadinessAuditRecord:
    """One immutable, separately persisted readiness evaluation."""

    schema_version: Literal["publication-readiness-audit.v1"]
    post_terminal_facts: PostTerminalFacts
    evaluated_claim_contract: PublicationClaimContract | None
    assessment: PublicationReadinessAssessment

    def __post_init__(self) -> None:
        _validate_publication_readiness_audit_record(self)

    @property
    def digest(self) -> str:
        """Return the SHA-256 identity of canonical audit JSON."""
        return publication_readiness_audit_digest(self)

    @property
    def evaluated_claim_contract_sha256(self) -> str | None:
        """Return the safe identity of the explicitly evaluated contract."""
        if self.evaluated_claim_contract is None:
            return None
        return publication_claim_contract_digest(self.evaluated_claim_contract)

    @property
    def post_terminal_facts_sha256(self) -> str:
        """Return the exact nested facts canonical digest."""
        return post_terminal_facts_digest(self.post_terminal_facts)

    @property
    def assessment_sha256(self) -> str:
        """Return the exact nested assessment canonical digest."""
        return publication_readiness_assessment_digest(self.assessment)


@dataclass(frozen=True)
class PublicationReadinessAuditPersistenceResult:
    """Result of an explicit create-only audit-sidecar persistence attempt."""

    bytes_written: int
    idempotent: bool


def build_publication_readiness_audit_record(
    post_terminal_facts: PostTerminalFacts,
    business_output: str | None,
    *,
    claim_contract: PublicationClaimContract | None = None,
) -> PublicationReadinessAuditRecord:
    """Evaluate readiness once and bind the supplied inputs to an audit record.

    This is deliberately the only builder for the audit record.  The supplied
    contract is retained even when Phase 262's precedence rules omit it from
    the returned assessment, which preserves safe mismatch identity without
    storing the candidate output itself.
    """
    if type(post_terminal_facts) is not PostTerminalFacts:
        _raise_audit("facts_type")
    if business_output is not None and type(business_output) is not str:
        _raise_audit("business_output_type")
    if claim_contract is not None and type(claim_contract) is not (
        PublicationClaimContract
    ):
        _raise_audit("claim_contract_type")
    try:
        assessment = assess_terminal_publication_readiness(
            post_terminal_facts,
            business_output,
            claim_contract=claim_contract,
        )
        return PublicationReadinessAuditRecord(
            schema_version=_PUBLICATION_READINESS_AUDIT_SCHEMA_VERSION,
            post_terminal_facts=post_terminal_facts,
            evaluated_claim_contract=claim_contract,
            assessment=assessment,
        )
    except PublicationReadinessAuditError:
        raise
    except PostTerminalEvidenceError:
        raise
    except Exception:
        _raise_audit("audit_build")


def serialize_publication_readiness_audit_canonical(
    record: PublicationReadinessAuditRecord,
) -> str:
    """Serialize one readiness audit as compact deterministic JSON text."""
    if type(record) is not PublicationReadinessAuditRecord:
        _raise_audit("record_type")
    _validate_publication_readiness_audit_record(record)
    try:
        value = {
            "assessment": json.loads(
                serialize_publication_readiness_assessment_canonical(
                    record.assessment
                )
            ),
            "assessment_sha256": record.assessment_sha256,
            "evaluated_claim_contract": (
                json.loads(
                    serialize_publication_claim_contract_canonical(
                        record.evaluated_claim_contract
                    )
                )
                if record.evaluated_claim_contract is not None
                else None
            ),
            "evaluated_claim_contract_sha256": (
                record.evaluated_claim_contract_sha256
            ),
            "post_terminal_facts": json.loads(
                serialize_post_terminal_facts_canonical(record.post_terminal_facts)
            ),
            "post_terminal_facts_sha256": record.post_terminal_facts_sha256,
            "schema_version": record.schema_version,
        }
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        _raise_audit("audit_serialization")


def publication_readiness_audit_canonical_bytes(
    record: PublicationReadinessAuditRecord,
) -> bytes:
    """Return canonical readiness-audit JSON encoded as UTF-8 bytes."""
    return serialize_publication_readiness_audit_canonical(record).encode("utf-8")


def publication_readiness_audit_digest(record: PublicationReadinessAuditRecord) -> str:
    """Return the SHA-256 identity of canonical readiness-audit bytes."""
    return sha256(publication_readiness_audit_canonical_bytes(record)).hexdigest()


def persist_publication_readiness_audit(
    path: Path,
    record: PublicationReadinessAuditRecord,
) -> PublicationReadinessAuditPersistenceResult:
    """Create one explicit immutable audit sidecar without overwriting it."""
    _validate_audit_path(path, _raise_audit_persistence)
    if type(record) is not PublicationReadinessAuditRecord:
        _raise_audit_persistence("record_type")
    contents = publication_readiness_audit_canonical_bytes(record)
    created = False
    try:
        with path.open("xb") as handle:
            created = True
            written = handle.write(contents)
            if written != len(contents):
                raise OSError
            handle.flush()
    except FileExistsError:
        try:
            existing = path.read_bytes()
        except OSError:
            _raise_audit_persistence("target")
        if existing == contents:
            return PublicationReadinessAuditPersistenceResult(
                bytes_written=len(contents),
                idempotent=True,
            )
        raise PublicationReadinessAuditConflictError("conflict") from None
    except OSError:
        if created:
            try:
                path.unlink()
            except OSError:
                _raise_audit_persistence("rollback")
        _raise_audit_persistence("write")
    return PublicationReadinessAuditPersistenceResult(
        bytes_written=len(contents),
        idempotent=False,
    )


def load_publication_readiness_audit(
    path: Path,
) -> PublicationReadinessAuditRecord:
    """Read, strictly validate, and canonically revalidate one audit sidecar."""
    _validate_audit_path(path, _raise_audit_load)
    try:
        contents = path.read_bytes()
    except OSError:
        _raise_audit_load("target")
    try:
        text = contents.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_audit_keys,
        )
        record = _parse_publication_readiness_audit(value)
        if publication_readiness_audit_canonical_bytes(record) != contents:
            _raise_audit_load("noncanonical")
        return record
    except PublicationReadinessAuditLoadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateAuditKeyError):
        _raise_audit_load("parse")
    except PublicationReadinessAuditError:
        _raise_audit_load("record")
    except Exception:
        _raise_audit_load("record")


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
    *,
    claim_contract: PublicationClaimContract | None = None,
) -> PublicationReadinessAssessment:
    """Assess narrow runtime-consistency evidence without provider or prose work."""
    if type(post_terminal_facts) is not PostTerminalFacts:
        _raise_readiness("facts_type")
    _validate_post_terminal_facts(post_terminal_facts)
    if business_output is not None and type(business_output) is not str:
        _raise_readiness("business_output_type")
    if claim_contract is not None:
        if type(claim_contract) is not PublicationClaimContract:
            _raise_readiness("claim_contract_type")
        _validate_publication_claim_contract_model(claim_contract)

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

    if claim_contract is None:
        return _build_assessment(
            post_terminal_facts,
            "insufficient_evidence",
            ["claim_contract_missing"],
            business_output_sha256,
        )

    try:
        validate_publication_claim_contract(
            claim_contract,
            post_terminal_facts,
            business_output_sha256,
        )
    except PublicationClaimContractError:
        return _build_assessment(
            post_terminal_facts,
            "stale_or_inconsistent",
            ["claim_contract_mismatch"],
            business_output_sha256,
        )

    return _build_assessment(
        post_terminal_facts,
        "ready",
        [],
        business_output_sha256,
        claim_contract,
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


def serialize_publication_claim_contract_canonical(
    contract: PublicationClaimContract,
) -> str:
    """Serialize one structured claim contract as compact canonical JSON."""
    if type(contract) is not PublicationClaimContract:
        _raise_claim_contract("contract_type")
    _validate_publication_claim_contract_model(contract)
    value = {
        "asserted_terminal_status": contract.asserted_terminal_status,
        "business_output_sha256": contract.business_output_sha256,
        "post_terminal_facts_sha256": contract.post_terminal_facts_sha256,
        "schema_version": contract.schema_version,
        "scope": contract.scope,
        "workflow_id": contract.workflow_id,
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
        _raise_claim_contract("contract_serialization")


def publication_claim_contract_canonical_bytes(
    contract: PublicationClaimContract,
) -> bytes:
    """Return canonical publication-claim JSON encoded as UTF-8 bytes."""
    return serialize_publication_claim_contract_canonical(contract).encode("utf-8")


def publication_claim_contract_digest(contract: PublicationClaimContract) -> str:
    """Return the SHA-256 digest of canonical claim-contract JSON bytes."""
    return sha256(publication_claim_contract_canonical_bytes(contract)).hexdigest()


def validate_publication_claim_contract(
    contract: PublicationClaimContract,
    post_terminal_facts: PostTerminalFacts,
    business_output_sha256: str,
) -> None:
    """Validate an explicit contract against exact terminal evidence."""
    if type(contract) is not PublicationClaimContract:
        _raise_claim_contract("contract_type")
    if type(post_terminal_facts) is not PostTerminalFacts:
        _raise_claim_contract("facts_type")
    _validate_publication_claim_contract_model(contract)
    _validate_post_terminal_facts(post_terminal_facts)
    if type(business_output_sha256) is not str:
        _raise_claim_contract("business_output_type")
    _validate_digest(
        business_output_sha256,
        "business_output_digest",
        error=_raise_claim_contract,
    )
    if post_terminal_facts.terminal_status != "workflow_complete":
        _raise_claim_contract("terminal_status_mismatch")
    if contract.workflow_id != post_terminal_facts.workflow_id:
        _raise_claim_contract("workflow_mismatch")
    if contract.business_output_sha256 != business_output_sha256:
        _raise_claim_contract("business_output_mismatch")
    if (
        post_terminal_facts.final_output_sha256 is None
        or contract.business_output_sha256
        != post_terminal_facts.final_output_sha256
    ):
        _raise_claim_contract("business_output_mismatch")
    if contract.post_terminal_facts_sha256 != post_terminal_facts_digest(
        post_terminal_facts
    ):
        _raise_claim_contract("post_terminal_facts_mismatch")
    if contract.asserted_terminal_status != post_terminal_facts.terminal_status:
        _raise_claim_contract("terminal_status_mismatch")


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
        if assessment.claim_contract is not None:
            value["claim_contract"] = json.loads(
                serialize_publication_claim_contract_canonical(
                    assessment.claim_contract
                )
            )
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
    claim_contract: PublicationClaimContract | None = None,
) -> PublicationReadinessAssessment:
    try:
        return PublicationReadinessAssessment(
            schema_version=_PUBLICATION_READINESS_SCHEMA_VERSION,
            execution_status=facts.terminal_status,
            readiness=readiness,
            reason_codes=tuple(reasons),
            post_terminal_facts=facts,
            business_output_sha256=business_output_sha256,
            claim_contract_sha256=(
                publication_claim_contract_digest(claim_contract)
                if claim_contract is not None
                else None
            ),
            claim_contract=claim_contract,
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


def _validate_publication_claim_contract_model(
    contract: PublicationClaimContract,
) -> None:
    if type(contract) is not PublicationClaimContract:
        _raise_claim_contract("contract_type")
    if type(contract.schema_version) is not str or (
        contract.schema_version != _PUBLICATION_CLAIM_CONTRACT_SCHEMA_VERSION
    ):
        _raise_claim_contract("schema_version")
    if type(contract.scope) is not str or (
        contract.scope != _PUBLICATION_CLAIM_CONTRACT_SCOPE
    ):
        _raise_claim_contract("scope")
    if type(contract.workflow_id) is not str or not contract.workflow_id:
        _raise_claim_contract("workflow_id")
    _validate_digest(
        contract.business_output_sha256,
        "business_output_digest",
        error=_raise_claim_contract,
    )
    _validate_digest(
        contract.post_terminal_facts_sha256,
        "post_terminal_facts_digest",
        error=_raise_claim_contract,
    )
    if type(contract.asserted_terminal_status) is not str or (
        contract.asserted_terminal_status != "workflow_complete"
    ):
        _raise_claim_contract("terminal_status")


def _validate_publication_readiness_assessment(
    assessment: PublicationReadinessAssessment,
) -> None:
    if type(assessment) is not PublicationReadinessAssessment:
        _raise_readiness("assessment_type")
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
    if assessment.claim_contract is not None:
        if type(assessment.claim_contract) is not PublicationClaimContract:
            _raise_readiness("claim_contract_type")
        _validate_publication_claim_contract_model(assessment.claim_contract)
    if assessment.claim_contract_sha256 is not None:
        _validate_digest(
            assessment.claim_contract_sha256,
            "claim_contract_digest",
            error=_raise_readiness,
        )
    if assessment.readiness == "ready":
        if assessment.claim_contract is None:
            _raise_readiness("ready_without_claim_contract")
        if assessment.claim_contract_sha256 != publication_claim_contract_digest(
            assessment.claim_contract
        ):
            _raise_readiness("claim_contract_digest_mismatch")
        try:
            validate_publication_claim_contract(
                assessment.claim_contract,
                assessment.post_terminal_facts,
                assessment.business_output_sha256 or "",
            )
        except PublicationClaimContractError:
            _raise_readiness("ready_claim_contract_mismatch")
        if assessment.reason_codes:
            _raise_readiness("ready_reason_codes")
    elif (
        assessment.claim_contract is not None
        or assessment.claim_contract_sha256 is not None
    ):
        if (
            assessment.claim_contract is None
            or assessment.claim_contract_sha256 is None
        ):
            _raise_readiness("claim_contract_binding")
        if assessment.claim_contract_sha256 != publication_claim_contract_digest(
            assessment.claim_contract
        ):
            _raise_readiness("claim_contract_digest_mismatch")


def _validate_publication_readiness_audit_record(
    record: PublicationReadinessAuditRecord,
) -> None:
    if type(record) is not PublicationReadinessAuditRecord:
        _raise_audit("record_type")
    if type(record.schema_version) is not str or (
        record.schema_version != _PUBLICATION_READINESS_AUDIT_SCHEMA_VERSION
    ):
        _raise_audit("schema_version")
    if type(record.post_terminal_facts) is not PostTerminalFacts:
        _raise_audit("facts_type")
    if type(record.assessment) is not PublicationReadinessAssessment:
        _raise_audit("assessment_type")
    if record.evaluated_claim_contract is not None and type(
        record.evaluated_claim_contract
    ) is not PublicationClaimContract:
        _raise_audit("claim_contract_type")
    try:
        _validate_post_terminal_facts(record.post_terminal_facts)
        if record.evaluated_claim_contract is not None:
            _validate_publication_claim_contract_model(
                record.evaluated_claim_contract
            )
        _validate_publication_readiness_assessment(record.assessment)
        if record.assessment.post_terminal_facts != record.post_terminal_facts:
            _raise_audit("assessment_facts_mismatch")
        if (
            record.assessment.claim_contract is not None
            and record.evaluated_claim_contract != record.assessment.claim_contract
        ):
            _raise_audit("assessment_contract_mismatch")
        if record.assessment.readiness == "ready":
            if record.evaluated_claim_contract is None:
                _raise_audit("ready_without_claim_contract")
            if record.assessment.claim_contract != record.evaluated_claim_contract:
                _raise_audit("ready_contract_mismatch")
        elif record.assessment.claim_contract is not None:
            _raise_audit("assessment_contract_unexpected")
        elif record.assessment.reason_codes == ("claim_contract_missing",):
            if record.evaluated_claim_contract is not None:
                _raise_audit("claim_contract_binding")
        elif record.assessment.reason_codes == ("claim_contract_mismatch",):
            if record.evaluated_claim_contract is None:
                _raise_audit("claim_contract_missing")
        _validate_audit_assessment_evaluation(record)
    except PublicationReadinessAuditError:
        raise
    except PostTerminalEvidenceError:
        _raise_audit("nested_contract")
    except Exception:
        _raise_audit("nested_contract")


def _validate_audit_assessment_evaluation(
    record: PublicationReadinessAuditRecord,
) -> None:
    """Reject a structurally valid audit that contradicts Phase 262 output."""
    facts = record.post_terminal_facts
    assessment = record.assessment
    output_digest = assessment.business_output_sha256
    if facts.terminal_status == "persisted_failure":
        expected_reasons = ("execution_not_workflow_complete",) + (
            ("final_output_missing",)
            if facts.final_output_sha256 is None
            else ()
        )
        if (
            assessment.readiness != "insufficient_evidence"
            or assessment.reason_codes != expected_reasons
        ):
            _raise_audit("failure_assessment")
        return

    declared_output_digest = facts.final_output_sha256
    if declared_output_digest is None:
        if (
            assessment.readiness != "stale_or_inconsistent"
            or assessment.reason_codes != ("final_output_missing",)
        ):
            _raise_audit("missing_output_assessment")
        return
    if assessment.reason_codes == ("final_output_missing",):
        if (
            assessment.readiness != "stale_or_inconsistent"
            or output_digest is not None
        ):
            _raise_audit("missing_output_assessment")
        return
    if assessment.reason_codes == ("final_output_mismatch",):
        if (
            assessment.readiness != "stale_or_inconsistent"
            or output_digest is None
            or output_digest == declared_output_digest
        ):
            _raise_audit("output_mismatch_assessment")
        return
    if assessment.reason_codes == ("claim_contract_missing",):
        if (
            assessment.readiness != "insufficient_evidence"
            or output_digest != declared_output_digest
        ):
            _raise_audit("missing_claim_assessment")
        return
    if assessment.reason_codes == ("claim_contract_mismatch",):
        if (
            assessment.readiness != "stale_or_inconsistent"
            or output_digest != declared_output_digest
            or record.evaluated_claim_contract is None
        ):
            _raise_audit("claim_mismatch_assessment")
        try:
            validate_publication_claim_contract(
                record.evaluated_claim_contract,
                facts,
                output_digest or "",
            )
        except PublicationClaimContractError:
            return
        _raise_audit("claim_mismatch_assessment")
    if assessment.reason_codes == ():
        if (
            assessment.readiness != "ready"
            or output_digest != declared_output_digest
            or record.evaluated_claim_contract is None
        ):
            _raise_audit("ready_assessment")
        return
    _raise_audit("assessment_evaluation")


def _parse_publication_readiness_audit(
    value: object,
) -> PublicationReadinessAuditRecord:
    data = _audit_object(value, _AUDIT_KEYS, "audit_parse")
    schema_version = _audit_string(data["schema_version"], "audit_parse")
    facts = _parse_audit_facts(data["post_terminal_facts"])
    assessment = _parse_audit_assessment(data["assessment"])
    contract_value = data["evaluated_claim_contract"]
    contract = (
        None
        if contract_value is None
        else _parse_audit_claim_contract(contract_value)
    )
    facts_digest = _audit_digest(data["post_terminal_facts_sha256"], "audit_parse")
    assessment_digest = _audit_digest(data["assessment_sha256"], "audit_parse")
    contract_digest_value = data["evaluated_claim_contract_sha256"]
    contract_digest = (
        None
        if contract_digest_value is None
        else _audit_digest(contract_digest_value, "audit_parse")
    )
    try:
        record = PublicationReadinessAuditRecord(
            schema_version=schema_version,
            post_terminal_facts=facts,
            evaluated_claim_contract=contract,
            assessment=assessment,
        )
    except (PostTerminalEvidenceError, PublicationReadinessAuditError):
        _raise_audit_load("record")
    if facts_digest != post_terminal_facts_digest(record.post_terminal_facts):
        _raise_audit_load("facts_digest")
    if assessment_digest != publication_readiness_assessment_digest(record.assessment):
        _raise_audit_load("assessment_digest")
    if contract_digest != record.evaluated_claim_contract_sha256:
        _raise_audit_load("claim_contract_digest")
    return record


def _parse_audit_facts(value: object) -> PostTerminalFacts:
    data = _audit_object(value, _FACTS_KEYS, "facts_parse")
    completed = _audit_string_array(data["completed_step_ids"], "facts_parse")
    try:
        return PostTerminalFacts(
            schema_version=_audit_string(data["schema_version"], "facts_parse"),
            workflow_id=_audit_string(data["workflow_id"], "facts_parse"),
            terminal_status=_audit_status(data["terminal_status"], "facts_parse"),
            terminal_reason=_audit_string(data["terminal_reason"], "facts_parse"),
            terminal_step_id=_audit_string(data["terminal_step_id"], "facts_parse"),
            terminal_step_index=_audit_positive_int(
                data["terminal_step_index"], "facts_parse"
            ),
            terminal_employee_id=_audit_string(
                data["terminal_employee_id"], "facts_parse"
            ),
            terminal_provider=_audit_optional_string(
                data["terminal_provider"], "facts_parse"
            ),
            completed_step_ids=completed,
            state_sha256=_audit_digest(data["state_sha256"], "facts_parse"),
            events_sha256=_audit_digest(data["events_sha256"], "facts_parse"),
            final_output_sha256=_audit_optional_digest(
                data["final_output_sha256"], "facts_parse"
            ),
        )
    except PostTerminalEvidenceError:
        _raise_audit_load("facts_parse")


def _parse_audit_claim_contract(value: object) -> PublicationClaimContract:
    data = _audit_object(value, _CLAIM_CONTRACT_KEYS, "claim_contract_parse")
    try:
        return PublicationClaimContract(
            schema_version=_audit_string(
                data["schema_version"], "claim_contract_parse"
            ),
            scope=_audit_string(data["scope"], "claim_contract_parse"),
            workflow_id=_audit_string(data["workflow_id"], "claim_contract_parse"),
            business_output_sha256=_audit_digest(
                data["business_output_sha256"], "claim_contract_parse"
            ),
            post_terminal_facts_sha256=_audit_digest(
                data["post_terminal_facts_sha256"], "claim_contract_parse"
            ),
            asserted_terminal_status=_audit_string(
                data["asserted_terminal_status"], "claim_contract_parse"
            ),
        )
    except PostTerminalEvidenceError:
        _raise_audit_load("claim_contract_parse")


def _parse_audit_assessment(value: object) -> PublicationReadinessAssessment:
    if type(value) is not dict or frozenset(value) not in {
        _ASSESSMENT_KEYS,
        _ASSESSMENT_KEYS_WITH_CONTRACT,
    }:
        _raise_audit_load("assessment_parse")
    data = value
    contract_value = data.get("claim_contract")
    contract = (
        None
        if contract_value is None
        else _parse_audit_claim_contract(contract_value)
    )
    reasons = _audit_string_array(data["reason_codes"], "assessment_parse")
    try:
        return PublicationReadinessAssessment(
            schema_version=_audit_string(
                data["schema_version"], "assessment_parse"
            ),
            execution_status=_audit_status(
                data["execution_status"], "assessment_parse"
            ),
            readiness=_audit_readiness(data["readiness"], "assessment_parse"),
            reason_codes=reasons,
            post_terminal_facts=_parse_audit_facts(data["post_terminal_facts"]),
            business_output_sha256=_audit_optional_digest(
                data["business_output_sha256"], "assessment_parse"
            ),
            claim_contract_sha256=_audit_optional_digest(
                data["claim_contract_sha256"], "assessment_parse"
            ),
            claim_contract=contract,
        )
    except PostTerminalEvidenceError:
        _raise_audit_load("assessment_parse")


def _audit_object(
    value: object,
    keys: frozenset[str],
    classification: str,
) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        _raise_audit_load(classification)
    return value


def _audit_string(value: object, classification: str) -> str:
    if type(value) is not str or not value:
        _raise_audit_load(classification)
    return value


def _audit_optional_string(value: object, classification: str) -> str | None:
    if value is None:
        return None
    return _audit_string(value, classification)


def _audit_string_array(value: object, classification: str) -> tuple[str, ...]:
    if type(value) is not list or any(
        type(item) is not str or not item for item in value
    ):
        _raise_audit_load(classification)
    return tuple(value)


def _audit_positive_int(value: object, classification: str) -> int:
    if type(value) is not int or value < 1:
        _raise_audit_load(classification)
    return value


def _audit_digest(value: object, classification: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _raise_audit_load(classification)
    return value


def _audit_optional_digest(value: object, classification: str) -> str | None:
    if value is None:
        return None
    return _audit_digest(value, classification)


def _audit_status(value: object, classification: str) -> PostTerminalTerminalStatus:
    if type(value) is not str or value not in {
        "workflow_complete",
        "persisted_failure",
    }:
        _raise_audit_load(classification)
    return value


def _audit_readiness(value: object, classification: str) -> PublicationReadiness:
    if type(value) is not str or value not in {
        "ready",
        "stale_or_inconsistent",
        "insufficient_evidence",
    }:
        _raise_audit_load(classification)
    return value


class _DuplicateAuditKeyError(ValueError):
    pass


def _reject_duplicate_audit_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateAuditKeyError
        result[key] = value
    return result


def _validate_audit_path(path: Path, error: Callable[[str], None]) -> None:
    if type(path) is not type(Path()):
        error("path_type")
    try:
        if path.is_dir():
            error("target")
    except OSError:
        error("target")


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


def _raise_claim_contract(classification: str) -> None:
    raise PublicationClaimContractError(classification) from None


def _raise_readiness(classification: str) -> None:
    raise PublicationReadinessError(classification) from None


def _raise_audit(classification: str) -> None:
    raise PublicationReadinessAuditError(classification) from None


def _raise_audit_persistence(classification: str) -> None:
    raise PublicationReadinessAuditPersistenceError(classification) from None


def _raise_audit_load(classification: str) -> None:
    raise PublicationReadinessAuditLoadError(classification) from None


__all__ = [
    "PersistedTerminalSnapshot",
    "PersistedTerminalSnapshotError",
    "PostTerminalEvidenceError",
    "PostTerminalEvidenceFailureDetail",
    "PostTerminalFacts",
    "PostTerminalFactsError",
    "PostTerminalTerminalStatus",
    "PublicationClaimContract",
    "PublicationClaimContractError",
    "PublicationReadiness",
    "PublicationReadinessAssessment",
    "PublicationReadinessError",
    "PublicationReadinessAuditConflictError",
    "PublicationReadinessAuditError",
    "PublicationReadinessAuditFailureDetail",
    "PublicationReadinessAuditLoadError",
    "PublicationReadinessAuditPersistenceError",
    "PublicationReadinessAuditPersistenceResult",
    "PublicationReadinessAuditRecord",
    "assess_terminal_publication_readiness",
    "build_publication_readiness_audit_record",
    "build_post_terminal_facts",
    "load_publication_readiness_audit",
    "load_persisted_terminal_snapshot",
    "post_terminal_facts_canonical_bytes",
    "post_terminal_facts_digest",
    "publication_claim_contract_canonical_bytes",
    "publication_claim_contract_digest",
    "publication_readiness_audit_canonical_bytes",
    "publication_readiness_audit_digest",
    "publication_readiness_assessment_canonical_bytes",
    "publication_readiness_assessment_digest",
    "persist_publication_readiness_audit",
    "serialize_post_terminal_facts_canonical",
    "serialize_publication_claim_contract_canonical",
    "serialize_publication_readiness_audit_canonical",
    "serialize_publication_readiness_assessment_canonical",
    "validate_publication_claim_contract",
]
