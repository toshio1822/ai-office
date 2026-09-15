"""Focused provider-free tests for Phase 283 lineage reconciliation."""

from __future__ import annotations

import dataclasses
import inspect
import os
import random
import socket
import time
import uuid
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_office.engine.external_publication as claim_module
import ai_office.engine.external_publication_execution as execution_module
import ai_office.engine.external_publication_execution_evidence as evidence_module
from ai_office.engine import (
    ExternalPublicationApproval,
    ExternalPublicationAttemptClaim,
    ExternalPublicationExecutionReconciliation,
    ExternalPublicationExecutionReconciliationError,
    ExternalPublicationExecutionReconciliationFailureDetail,
    ExternalPublicationExecutionResult,
    ExternalPublicationPlan,
    approve_external_publication,
    build_external_publication_attempt_claim,
    external_publication_attempt_claim_canonical_bytes,
    external_publication_attempt_claim_digest,
    external_publication_execution_result_canonical_bytes,
    external_publication_execution_result_digest,
    load_external_publication_attempt_claim,
    load_external_publication_execution_result,
    reconcile_external_publication_execution,
)
from ai_office.engine import (
    external_publication_execution_reconciliation as reconciliation_module,
)

_RECONCILIATION_SCHEMA = "external-publication-execution-reconciliation.v1"
_LINEAGE_FIELDS = (
    "publication_attempt_claim_sha256",
    "regeneration_id",
    "publication_plan_sha256",
    "publication_approval_sha256",
    "business_output_sha256",
    "output_byte_length",
    "provider",
    "publication_target_sha256",
)
_UNSET = object()


def _plan(
    *,
    regeneration_id: str = "regen-283-reconcile",
    output_byte_length: int = 17,
    provider: str = "future-provider",
) -> ExternalPublicationPlan:
    return ExternalPublicationPlan(
        schema_version="external-publication-plan.v1",
        regeneration_id=regeneration_id,
        reconciliation_evidence_sha256="c" * 64,
        receipt_sha256="b" * 64,
        business_output_sha256="a" * 64,
        output_byte_length=output_byte_length,
        provider=provider,
        publication_target_sha256="e" * 64,
    )


def _approval(
    plan: ExternalPublicationPlan | None = None,
) -> ExternalPublicationApproval:
    return approve_external_publication(
        plan or _plan(),
        approved_by="human-reviewer-283",
        approval_id="approval-283-1",
    )


def _claim(
    plan: ExternalPublicationPlan | None = None,
) -> ExternalPublicationAttemptClaim:
    actual_plan = plan or _plan()
    return build_external_publication_attempt_claim(
        actual_plan,
        _approval(actual_plan),
    )


def _evidence(
    claim: ExternalPublicationAttemptClaim,
    *,
    publication_id: str = "publication-283-1",
) -> ExternalPublicationExecutionResult:
    return ExternalPublicationExecutionResult(
        schema_version="external-publication-execution-result.v1",
        regeneration_id=claim.regeneration_id,
        publication_attempt_claim_sha256=(
            external_publication_attempt_claim_digest(claim)
        ),
        publication_plan_sha256=claim.publication_plan_sha256,
        publication_approval_sha256=claim.publication_approval_sha256,
        business_output_sha256=claim.business_output_sha256,
        output_byte_length=claim.output_byte_length,
        provider=claim.provider,
        publication_target_sha256=claim.publication_target_sha256,
        publication_id=publication_id,
        status="published",
    )


def _forged_instance(cls: type[object], source: object) -> object:
    value = object.__new__(cls)
    for field in dataclasses.fields(source):  # type: ignore[arg-type]
        object.__setattr__(value, field.name, getattr(source, field.name))
    return value


def _assert_error(error: ValueError, classification: str) -> None:
    assert type(error) is ExternalPublicationExecutionReconciliationError
    assert str(error) == "external publication execution reconciliation failed"
    assert type(error.detail) is ExternalPublicationExecutionReconciliationFailureDetail
    assert error.detail.classification == classification


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    claim: object,
    evidence: object,
    *,
    claim_digest: object = _UNSET,
    evidence_digest: object = _UNSET,
    calls: list[tuple[str, object]] | None = None,
) -> None:
    seen = calls if calls is not None else []
    if claim_digest is _UNSET:
        claim_digest = (
            external_publication_attempt_claim_digest(claim)
            if type(claim) is ExternalPublicationAttemptClaim
            else "1" * 64
        )
    if evidence_digest is _UNSET:
        evidence_digest = (
            external_publication_execution_result_digest(evidence)
            if type(evidence) is ExternalPublicationExecutionResult
            else "2" * 64
        )

    def load_claim(path: object) -> object:
        seen.append(("claim_load", path))
        return claim

    def digest_claim(value: object) -> object:
        seen.append(("claim_digest", value))
        return claim_digest

    def load_evidence(path: object) -> object:
        seen.append(("evidence_load", path))
        return evidence

    def digest_evidence(value: object) -> object:
        seen.append(("evidence_digest", value))
        return evidence_digest

    monkeypatch.setattr(
        reconciliation_module,
        "load_external_publication_attempt_claim",
        load_claim,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "external_publication_attempt_claim_digest",
        digest_claim,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "load_external_publication_execution_result",
        load_evidence,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "external_publication_execution_result_digest",
        digest_evidence,
    )


def _tree_snapshot(root: Path) -> tuple[tuple[str, int, int, bytes], ...]:
    entries: list[tuple[str, int, int, bytes]] = []
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        if path.is_file():
            entries.append(
                (
                    str(path.relative_to(root)),
                    stat.st_mode,
                    stat.st_mtime_ns,
                    path.read_bytes(),
                )
            )
        else:
            entries.append(
                (str(path.relative_to(root)), stat.st_mode, stat.st_mtime_ns, b"")
            )
    return tuple(entries)


def test_exact_match_returns_exact_frozen_result_and_field_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    claim_path = tmp_path / "caller-claim.json"
    evidence_path = tmp_path / "caller-evidence.json"
    calls: list[tuple[str, object]] = []
    _install_fakes(monkeypatch, claim, evidence, calls=calls)

    result = reconcile_external_publication_execution(
        claim_path=claim_path,
        execution_evidence_path=evidence_path,
    )

    assert type(result) is ExternalPublicationExecutionReconciliation
    assert tuple(field.name for field in fields(result)) == (
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    )
    assert result == ExternalPublicationExecutionReconciliation(
        schema_version=_RECONCILIATION_SCHEMA,
        claim_sha256=external_publication_attempt_claim_digest(claim),
        execution_evidence_sha256=external_publication_execution_result_digest(
            evidence
        ),
        status="matched",
        mismatched_fields=(),
    )
    assert result.__dataclass_params__.frozen is True
    with pytest.raises(FrozenInstanceError):
        result.status = "lineage_mismatch"  # type: ignore[misc]
    assert [name for name, _ in calls] == [
        "claim_load",
        "claim_digest",
        "evidence_load",
        "evidence_digest",
    ]
    assert calls[0][1] is claim_path
    assert calls[1][1] is claim
    assert calls[2][1] is evidence_path
    assert calls[3][1] is evidence


def test_strict_loader_and_digest_call_contract_is_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    claim_path = tmp_path / "claim.json"
    evidence_path = tmp_path / "evidence.json"
    calls: list[tuple[str, object]] = []
    _install_fakes(monkeypatch, claim, evidence, calls=calls)

    reconcile_external_publication_execution(
        claim_path=claim_path,
        execution_evidence_path=evidence_path,
    )

    assert [name for name, _ in calls] == [
        "claim_load",
        "claim_digest",
        "evidence_load",
        "evidence_digest",
    ]
    assert sum(name == "claim_load" for name, _ in calls) == 1
    assert sum(name == "claim_digest" for name, _ in calls) == 1
    assert sum(name == "evidence_load" for name, _ in calls) == 1
    assert sum(name == "evidence_digest" for name, _ in calls) == 1


def test_actual_strict_loaders_receive_exact_paths_and_objects_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    claim_path = tmp_path / "claim.json"
    evidence_path = tmp_path / "evidence.json"
    claim_path.write_bytes(external_publication_attempt_claim_canonical_bytes(claim))
    evidence_path.write_bytes(
        external_publication_execution_result_canonical_bytes(evidence)
    )
    seen: list[tuple[str, object]] = []
    actual_claim_loader = load_external_publication_attempt_claim
    actual_claim_digest = external_publication_attempt_claim_digest
    actual_evidence_loader = load_external_publication_execution_result
    actual_evidence_digest = external_publication_execution_result_digest

    def load_claim(path: Path) -> ExternalPublicationAttemptClaim:
        value = actual_claim_loader(path)
        seen.append(("claim_load", path, value))
        return value

    def digest_claim(value: ExternalPublicationAttemptClaim) -> str:
        seen.append(("claim_digest", value))
        return actual_claim_digest(value)

    def load_evidence(path: Path) -> ExternalPublicationExecutionResult:
        value = actual_evidence_loader(path)
        seen.append(("evidence_load", path, value))
        return value

    def digest_evidence(value: ExternalPublicationExecutionResult) -> str:
        seen.append(("evidence_digest", value))
        return actual_evidence_digest(value)

    monkeypatch.setattr(
        reconciliation_module, "load_external_publication_attempt_claim", load_claim
    )
    monkeypatch.setattr(
        reconciliation_module, "external_publication_attempt_claim_digest", digest_claim
    )
    monkeypatch.setattr(
        reconciliation_module,
        "load_external_publication_execution_result",
        load_evidence,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "external_publication_execution_result_digest",
        digest_evidence,
    )

    result = reconcile_external_publication_execution(
        claim_path=claim_path,
        execution_evidence_path=evidence_path,
    )

    assert result.status == "matched"
    assert [name for name, *_ in seen] == [
        "claim_load",
        "claim_digest",
        "evidence_load",
        "evidence_digest",
    ]
    assert seen[0][1] is claim_path
    assert seen[1][1] is seen[0][2]
    assert seen[2][1] is evidence_path
    assert seen[3][1] is seen[2][2]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("publication_attempt_claim_sha256", "f" * 64),
        ("regeneration_id", "regen-283-other"),
        ("publication_plan_sha256", "f" * 64),
        ("publication_approval_sha256", "f" * 64),
        ("business_output_sha256", "f" * 64),
        ("output_byte_length", 18),
        ("provider", "other-provider"),
        ("publication_target_sha256", "f" * 64),
    ],
)
def test_each_lineage_binding_independently_returns_valid_mismatch(
    field: str,
    replacement: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = dataclasses.replace(_evidence(claim), **{field: replacement})
    _install_fakes(monkeypatch, claim, evidence)

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )

    assert result.status == "lineage_mismatch"
    assert result.mismatched_fields == (field,)


def test_claim_digest_mismatch_reports_only_claim_digest_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    _install_fakes(
        monkeypatch,
        claim,
        evidence,
        claim_digest="0" * 64,
    )

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )

    assert result.status == "lineage_mismatch"
    assert result.mismatched_fields == ("publication_attempt_claim_sha256",)


def test_multiple_mismatches_use_canonical_order_and_safe_fields_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = dataclasses.replace(
        _evidence(claim),
        publication_attempt_claim_sha256="f" * 64,
        regeneration_id="regen-283-other",
        publication_plan_sha256="f" * 64,
        publication_approval_sha256="f" * 64,
        business_output_sha256="f" * 64,
        output_byte_length=18,
        provider="other-provider",
        publication_target_sha256="f" * 64,
    )
    _install_fakes(monkeypatch, claim, evidence)

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )

    assert result.status == "lineage_mismatch"
    assert result.mismatched_fields == _LINEAGE_FIELDS
    assert len(result.mismatched_fields) == len(set(result.mismatched_fields))
    rendered = repr(result)
    for sensitive in (
        "approval-283-1",
        "human-reviewer-283",
        "regen-283-other",
        "other-provider",
        "publication-283-1",
        "destination",
        "credential",
    ):
        assert sensitive not in rendered


def test_valid_lineage_mismatch_is_observation_not_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = dataclasses.replace(_evidence(claim), provider="other-provider")
    _install_fakes(monkeypatch, claim, evidence)

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )

    assert result.status == "lineage_mismatch"


@pytest.mark.parametrize("bad_digest", ["", "A" * 64, "a" * 63, "a" * 65, None, 1])
def test_malformed_claim_digest_output_fails_closed(
    bad_digest: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    calls: list[tuple[str, object]] = []
    _install_fakes(
        monkeypatch,
        claim,
        evidence,
        claim_digest=bad_digest,
        calls=calls,
    )

    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        reconcile_external_publication_execution(
            claim_path=Path("claim.json"),
            execution_evidence_path=Path("evidence.json"),
        )

    _assert_error(raised.value, "claim_digest")
    assert [name for name, _ in calls] == ["claim_load", "claim_digest"]


@pytest.mark.parametrize("bad_digest", ["", "A" * 64, "b" * 63, "b" * 65, None, 1])
def test_malformed_execution_evidence_digest_output_fails_closed(
    bad_digest: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    calls: list[tuple[str, object]] = []
    _install_fakes(
        monkeypatch,
        claim,
        evidence,
        evidence_digest=bad_digest,
        calls=calls,
    )

    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        reconcile_external_publication_execution(
            claim_path=Path("claim.json"),
            execution_evidence_path=Path("evidence.json"),
        )

    _assert_error(raised.value, "execution_evidence_digest")
    assert [name for name, _ in calls] == [
        "claim_load",
        "claim_digest",
        "evidence_load",
        "evidence_digest",
    ]


@pytest.mark.parametrize("side", ["claim", "execution_evidence"])
def test_loader_failure_is_fixed_and_detail_safe(
    side: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    calls: list[str] = []
    _install_fakes(monkeypatch, claim, evidence)
    sensitive = "approval-secret provider-secret /private/path exception-secret"

    def failing_claim_loader(_path: object) -> object:
        calls.append("claim_load")
        raise RuntimeError(sensitive)

    def failing_evidence_loader(_path: object) -> object:
        calls.append("evidence_load")
        raise RuntimeError(sensitive)

    if side == "claim":
        monkeypatch.setattr(
            reconciliation_module,
            "load_external_publication_attempt_claim",
            failing_claim_loader,
        )
    else:
        monkeypatch.setattr(
            reconciliation_module,
            "load_external_publication_execution_result",
            failing_evidence_loader,
        )

    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        reconcile_external_publication_execution(
            claim_path=Path("claim.json"),
            execution_evidence_path=Path("evidence.json"),
        )

    _assert_error(raised.value, side)
    assert sensitive not in str(raised.value)
    assert calls == ["claim_load"] if side == "claim" else calls == ["evidence_load"]


@pytest.mark.parametrize("side", ["claim", "execution_evidence"])
def test_digest_failure_is_fixed_and_never_retried(
    side: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    calls: list[str] = []
    call_objects: list[tuple[str, object]] = []
    _install_fakes(monkeypatch, claim, evidence, calls=call_objects)

    def failing_claim_digest(_value: object) -> str:
        calls.append("claim_digest")
        raise RuntimeError("secret claim digest detail")

    def failing_evidence_digest(_value: object) -> str:
        calls.append("evidence_digest")
        raise RuntimeError("secret evidence digest detail")

    if side == "claim":
        monkeypatch.setattr(
            reconciliation_module,
            "external_publication_attempt_claim_digest",
            failing_claim_digest,
        )
    else:
        monkeypatch.setattr(
            reconciliation_module,
            "external_publication_execution_result_digest",
            failing_evidence_digest,
        )

    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        reconcile_external_publication_execution(
            claim_path=Path("claim.json"),
            execution_evidence_path=Path("evidence.json"),
        )

    _assert_error(raised.value, f"{side}_digest")
    assert (
        calls == ["claim_digest"] if side == "claim" else calls == ["evidence_digest"]
    )
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize("side", ["claim", "execution_evidence"])
def test_subclass_lookalike_and_mapping_loader_results_are_rejected(
    side: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    if side == "claim":

        class ClaimChild(ExternalPublicationAttemptClaim):
            pass

        candidates: list[object] = [
            _forged_instance(ClaimChild, claim),
            SimpleNamespace(**claim.__dict__),
            dict(claim.__dict__),
        ]
        loader_name = "load_external_publication_attempt_claim"
        digest_name = "external_publication_attempt_claim_digest"
        classification = "claim"
    else:

        class EvidenceChild(ExternalPublicationExecutionResult):
            pass

        candidates = [
            _forged_instance(EvidenceChild, evidence),
            SimpleNamespace(**evidence.__dict__),
            dict(evidence.__dict__),
        ]
        loader_name = "load_external_publication_execution_result"
        digest_name = "external_publication_execution_result_digest"
        classification = "execution_evidence"

    for candidate in candidates:
        _install_fakes(monkeypatch, claim, evidence)
        monkeypatch.setattr(
            reconciliation_module,
            loader_name,
            lambda _path, candidate=candidate: candidate,
        )
        digest_calls: list[object] = []
        monkeypatch.setattr(
            reconciliation_module,
            digest_name,
            lambda value: digest_calls.append(value) or "1" * 64,
        )

        with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
            reconcile_external_publication_execution(
                claim_path=Path("claim.json"),
                execution_evidence_path=Path("evidence.json"),
            )

        _assert_error(raised.value, classification)
        assert digest_calls == [] if side == "claim" else digest_calls == []


def test_paths_require_exact_concrete_path_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    calls: list[tuple[str, object]] = []
    _install_fakes(monkeypatch, claim, evidence, calls=calls)

    class PathChild(type(Path())):
        pass

    candidates = [
        ("claim.json", Path("evidence.json")),
        (Path("claim.json"), "evidence.json"),
        (PathChild("claim.json"), Path("evidence.json")),
        (Path("claim.json"), PathChild("evidence.json")),
    ]
    for claim_path, evidence_path in candidates:
        with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
            reconcile_external_publication_execution(
                claim_path=claim_path,  # type: ignore[arg-type]
                execution_evidence_path=evidence_path,  # type: ignore[arg-type]
            )
        _assert_error(raised.value, "path_type")
    assert calls == []


def _valid_result_kwargs() -> dict[str, object]:
    return {
        "schema_version": _RECONCILIATION_SCHEMA,
        "claim_sha256": "1" * 64,
        "execution_evidence_sha256": "2" * 64,
        "status": "matched",
        "mismatched_fields": (),
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "lineage_mismatch", "mismatched_fields": ()},
        {"status": "matched", "mismatched_fields": ("provider",)},
        {"schema_version": "wrong"},
        {"claim_sha256": "A" * 64},
        {"execution_evidence_sha256": "3" * 63},
        {"mismatched_fields": ["provider"]},
    ],
)
def test_result_model_rejects_invalid_status_digest_and_container(
    overrides: dict[str, object],
) -> None:
    values = _valid_result_kwargs()
    values.update(overrides)

    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        ExternalPublicationExecutionReconciliation(**values)  # type: ignore[arg-type]
    _assert_error(raised.value, "result")


@pytest.mark.parametrize(
    "mismatched_fields",
    [
        ("provider", "provider"),
        ("unknown",),
        ("provider", "publication_attempt_claim_sha256"),
    ],
)
def test_result_model_rejects_duplicate_unknown_and_out_of_order_fields(
    mismatched_fields: tuple[str, ...],
) -> None:
    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        ExternalPublicationExecutionReconciliation(
            schema_version=_RECONCILIATION_SCHEMA,
            claim_sha256="1" * 64,
            execution_evidence_sha256="2" * 64,
            status="lineage_mismatch",
            mismatched_fields=mismatched_fields,  # type: ignore[arg-type]
        )
    _assert_error(raised.value, "result")


def test_subclass_result_is_rejected_by_result_validation() -> None:
    class ResultChild(ExternalPublicationExecutionReconciliation):
        pass

    with pytest.raises(ExternalPublicationExecutionReconciliationError) as raised:
        ResultChild(**_valid_result_kwargs())  # type: ignore[arg-type]
    _assert_error(raised.value, "result")


def test_module_does_not_directly_read_or_mutate_sidecars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    _install_fakes(monkeypatch, claim, evidence)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("reconciliation boundary touched the filesystem")

    for owner, name in (
        (Path, "open"),
        (Path, "read_bytes"),
        (Path, "read_text"),
        (Path, "write_bytes"),
        (Path, "write_text"),
        (Path, "unlink"),
        (Path, "mkdir"),
        (Path, "rename"),
        (os, "open"),
        (os, "remove"),
        (os, "unlink"),
        (os, "rename"),
        (os, "replace"),
    ):
        monkeypatch.setattr(owner, name, forbidden, raising=False)

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )
    assert result.status == "matched"
    source = inspect.getsource(reconciliation_module)
    for forbidden_name in (
        "execute_approved_external_publication(",
        "claim_external_publication_attempt(",
        "persist_external_publication_execution_result(",
        "read_bytes(",
        "write_bytes(",
        "write_text(",
    ):
        assert forbidden_name not in source


def test_success_mismatch_and_failure_have_zero_filesystem_mutation(
    tmp_path: Path,
) -> None:
    claim = _claim()
    matched_evidence = _evidence(claim)
    mismatch_evidence = dataclasses.replace(
        matched_evidence,
        provider="other-provider",
    )
    cases = {
        "matched": matched_evidence,
        "mismatch": mismatch_evidence,
    }
    for name, evidence in cases.items():
        case_dir = tmp_path / name
        case_dir.mkdir()
        claim_path = case_dir / "claim.json"
        evidence_path = case_dir / "evidence.json"
        claim_path.write_bytes(
            external_publication_attempt_claim_canonical_bytes(claim)
        )
        evidence_path.write_bytes(
            external_publication_execution_result_canonical_bytes(evidence)
        )
        before = _tree_snapshot(case_dir)

        result = reconcile_external_publication_execution(
            claim_path=claim_path,
            execution_evidence_path=evidence_path,
        )

        assert result.status == ("matched" if name == "matched" else "lineage_mismatch")
        assert _tree_snapshot(case_dir) == before

    failure_dir = tmp_path / "failure"
    failure_dir.mkdir()
    claim_path = failure_dir / "claim.json"
    evidence_path = failure_dir / "evidence.json"
    claim_path.write_bytes(b"not canonical claim")
    evidence_path.write_bytes(
        external_publication_execution_result_canonical_bytes(matched_evidence)
    )
    before = _tree_snapshot(failure_dir)
    with pytest.raises(ExternalPublicationExecutionReconciliationError):
        reconcile_external_publication_execution(
            claim_path=claim_path,
            execution_evidence_path=evidence_path,
        )
    assert _tree_snapshot(failure_dir) == before


def test_forbidden_predecessor_boundaries_are_not_accessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    _install_fakes(monkeypatch, claim, evidence)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden predecessor/runtime boundary called")

    for module, name in (
        (claim_module, "claim_external_publication_attempt"),
        (execution_module, "execute_approved_external_publication"),
        (evidence_module, "persist_external_publication_execution_result"),
    ):
        monkeypatch.setattr(module, name, forbidden)

    source = inspect.getsource(reconciliation_module)
    for forbidden_name in (
        "claim_external_publication_attempt",
        "execute_approved_external_publication",
        "persist_external_publication_execution_result",
        "build_external_publication_plan",
        "validate_external_publication_plan",
        "load_publication_regeneration_export_reconciliation",
        "reconcile_publication_regeneration_export",
    ):
        assert forbidden_name not in source

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )
    assert result.status == "matched"


def test_runtime_environment_clock_random_uuid_socket_and_provider_access_are_unused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    _install_fakes(monkeypatch, claim, evidence)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden runtime/environment boundary called")

    for owner, name in (
        (os, "getenv"),
        (time, "time"),
        (random, "random"),
        (uuid, "uuid4"),
        (socket, "socket"),
    ):
        monkeypatch.setattr(owner, name, forbidden)

    source = inspect.getsource(reconciliation_module)
    for forbidden_name in (
        "import os",
        "import time",
        "import random",
        "import uuid",
        "import socket",
    ):
        assert forbidden_name not in source

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )
    assert result.status == "matched"


def test_no_output_file_is_read_and_no_provider_call_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim)
    _install_fakes(monkeypatch, claim, evidence)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("output/provider access called")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(
        execution_module,
        "execute_approved_external_publication",
        forbidden,
    )

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )
    assert result.status == "matched"


def test_result_excludes_publication_id_and_approval_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    evidence = _evidence(claim, publication_id="publication-secret-283")
    _install_fakes(monkeypatch, claim, evidence)

    result = reconcile_external_publication_execution(
        claim_path=Path("claim.json"),
        execution_evidence_path=Path("evidence.json"),
    )

    rendered = repr(result)
    for sensitive in (
        "publication-secret-283",
        "approval-283-1",
        "human-reviewer-283",
        "destination",
        "raw output",
        "credential",
    ):
        assert sensitive not in rendered


def test_public_result_fields_are_only_the_specified_five() -> None:
    assert tuple(
        field.name for field in fields(ExternalPublicationExecutionReconciliation)
    ) == (
        "schema_version",
        "claim_sha256",
        "execution_evidence_sha256",
        "status",
        "mismatched_fields",
    )
    assert not hasattr(ExternalPublicationExecutionReconciliation, "publication_id")
    assert not hasattr(ExternalPublicationExecutionReconciliation, "approval_id")
    assert not hasattr(ExternalPublicationExecutionReconciliation, "provider")


def test_phase_283_adds_no_cli_command() -> None:
    from ai_office import cli

    assert not hasattr(cli, "reconcile_external_publication_execution")
