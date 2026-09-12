"""Focused provider-free projection tests for Phase 270."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_office.engine import publication_regeneration_projection as projection_module
from ai_office.engine.publication_regeneration_projection import (
    PublicationRegenerationProjection,
    PublicationRegenerationProjectionError,
    project_publication_regeneration_output,
    publication_regeneration_projection_canonical_bytes,
    publication_regeneration_projection_digest,
    serialize_publication_regeneration_projection_canonical,
)
from ai_office.engine.publication_regeneration_readiness import (
    PublicationRegenerationReadinessAssessment,
    assess_publication_regeneration_result_readiness,
)
from ai_office.engine.publication_regeneration_readiness_record import (
    build_publication_regeneration_readiness_record,
    persist_publication_regeneration_readiness_record,
)
from ai_office.engine.publication_regeneration_result import (
    build_publication_regeneration_result_record,
    persist_publication_regeneration_result,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from tests.test_publication_regeneration_readiness import (
    ReadinessFixture,
    claim_contract_for,
    exact_contract,
    failure_result_fixture,
    readiness_fixture,
)


class PublicationRegenerationProjectionChild(PublicationRegenerationProjection):
    pass


def readiness_sidecar(
    fixture: ReadinessFixture,
    assessment: PublicationRegenerationReadinessAssessment,
    *,
    path: Path | None = None,
) -> tuple[Path, object]:
    record = build_publication_regeneration_readiness_record(assessment)
    readiness_path = (
        fixture.result_path.parent / "readiness-record.json"
        if path is None
        else path
    )
    persist_publication_regeneration_readiness_record(readiness_path, record)
    return readiness_path, record


def evidence_for_state(
    tmp_path: Path,
    state: str,
) -> tuple[ReadinessFixture, Path, object, str | None]:
    if state == "ready":
        fixture = readiness_fixture(tmp_path)
        assessment = assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
            claim_contract=exact_contract(fixture),
        )
        readiness_path, readiness_record = readiness_sidecar(fixture, assessment)
        return fixture, readiness_path, readiness_record, None
    if state == "insufficient_evidence":
        fixture = readiness_fixture(tmp_path)
        assessment = assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
        )
        readiness_path, readiness_record = readiness_sidecar(fixture, assessment)
        return fixture, readiness_path, readiness_record, fixture.record.result.text
    if state == "stale_or_inconsistent":
        fixture = readiness_fixture(tmp_path)
        changed_result = ModelInvocationSuccess(
            provider=fixture.record.result.provider,
            response_id="response-270-stale",
            request_id="request-270-stale",
            status="completed",
            text_parts=("REGENERATED CANDIDATE\n", "日本語 😀"),
            text="REGENERATED CANDIDATE\n日本語 😀",
        )
        changed_record = build_publication_regeneration_result_record(
            fixture.record.attempt_claim,
            changed_result,
        )
        fixture.result_path.unlink()
        persist_publication_regeneration_result(fixture.result_path, changed_record)
        mismatch = replace(
            claim_contract_for(
                fixture.audit.post_terminal_facts,
                output=changed_result.text,
            ),
            workflow_id="other-workflow",
        )
        assessment = assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
            claim_contract=mismatch,
        )
        readiness_path, readiness_record = readiness_sidecar(fixture, assessment)
        return fixture, readiness_path, readiness_record, changed_result.text
    if state == "result_failure":
        fixture = readiness_fixture(tmp_path, result=failure_result_fixture())
        assessment = assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
        )
        readiness_path, readiness_record = readiness_sidecar(fixture, assessment)
        return fixture, readiness_path, readiness_record, None
    raise AssertionError(state)


def test_ready_projection_returns_exact_success_text_and_cross_bound_digests(
    tmp_path: Path,
) -> None:
    exact_text = "\n  regenerated 日本語 😀\n\n\t"
    fixture = readiness_fixture(
        tmp_path,
        result=ModelInvocationSuccess(
            provider="openai",
            response_id="response-270-ready",
            request_id="request-270-ready",
            status="completed",
            text_parts=("\n  regenerated ", "日本語 😀\n\n\t"),
            text=exact_text,
        ),
    )
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=exact_contract(fixture),
    )
    readiness_path, readiness_record = readiness_sidecar(fixture, assessment)

    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    assert projection.schema_version == "publication-regeneration-projection.v1"
    assert projection.regeneration_id == fixture.record.regeneration_id
    assert projection.readiness_record_sha256 == readiness_record.digest
    assert projection.readiness == "ready"
    assert projection.reason_codes == ()
    assert projection.result_record_sha256 == fixture.record.digest
    assert projection.source_audit_sha256 == fixture.audit.digest
    assert projection.publishable is True
    assert projection.business_output_text == exact_text
    assert projection.business_output_text.encode("utf-8") == exact_text.encode(
        "utf-8"
    )
    expected_digest = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
    assert projection.business_output_sha256 == expected_digest
    assert projection.business_output_sha256 == fixture.record.business_output_sha256
    assert projection.business_output_sha256 == assessment.business_output_sha256


def test_projection_canonical_identity_is_compact_and_stable(tmp_path: Path) -> None:
    fixture, readiness_path, readiness_record, _ = evidence_for_state(
        tmp_path, "ready"
    )
    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    canonical = serialize_publication_regeneration_projection_canonical(projection)
    assert canonical == canonical.rstrip("\n")
    assert json.loads(canonical)["readiness_record_sha256"] == readiness_record.digest
    assert publication_regeneration_projection_canonical_bytes(projection) == (
        canonical.encode("utf-8")
    )
    assert publication_regeneration_projection_digest(projection) == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()

    script = """
import sys
from pathlib import Path
from ai_office.engine.publication_regeneration_projection import (
    load_publication_regeneration_readiness_record,
    load_publication_regeneration_result,
    project_publication_regeneration_output,
    publication_regeneration_projection_digest,
)
projection = project_publication_regeneration_output(
    readiness_record_path=Path(sys.argv[1]),
    result_path=Path(sys.argv[2]),
)
print(publication_regeneration_projection_digest(projection))
print(projection.digest)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (
        f"{Path.cwd() / 'src'}:{environment.get('PYTHONPATH', '')}"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(readiness_path), str(fixture.result_path)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [projection.digest, projection.digest]


@pytest.mark.parametrize(
    "state",
    ("insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_each_non_ready_state_is_non_publishable_without_output_or_digest(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, readiness_record, candidate_text = evidence_for_state(
        tmp_path / state, state
    )

    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    assert projection.readiness == state
    assert projection.reason_codes == readiness_record.reason_codes
    assert projection.publishable is False
    assert projection.business_output_text is None
    assert projection.business_output_sha256 is None
    assert candidate_text is None or candidate_text not in repr(projection)
    assert candidate_text is None or candidate_text not in (
        serialize_publication_regeneration_projection_canonical(projection)
    )


@pytest.mark.parametrize(
    "state",
    ("insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_non_ready_success_and_failure_text_never_becomes_projection_output(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, _, candidate_text = evidence_for_state(
        tmp_path / state, state
    )
    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    assert projection.publishable is False
    assert projection.business_output_text is None
    assert projection.business_output_sha256 is None
    if state == "result_failure":
        assert isinstance(fixture.record.result, ModelInvocationFailure)
        assert fixture.record.result.message not in repr(projection)
        assert fixture.record.result.message not in (
            serialize_publication_regeneration_projection_canonical(projection)
        )
    else:
        assert candidate_text not in repr(projection)


def test_ready_result_text_tampering_is_safely_rejected(tmp_path: Path) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, "ready")
    tampered_path = tmp_path / "tampered-result.json"
    tampered_path.write_bytes(
        fixture.result_path.read_bytes().replace(
            fixture.record.result.text.encode("utf-8"),
            b"tampered output",
            1,
        )
    )
    original_readiness = readiness_path.read_bytes()

    with pytest.raises(PublicationRegenerationProjectionError) as caught:
        project_publication_regeneration_output(
            readiness_record_path=readiness_path,
            result_path=tampered_path,
        )

    assert caught.value.detail.classification == "result_load"
    assert readiness_path.read_bytes() == original_readiness


def test_ready_result_record_digest_mismatch_is_safely_rejected(tmp_path: Path) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, "ready")
    other_result = build_publication_regeneration_result_record(
        fixture.record.attempt_claim,
        ModelInvocationSuccess(
            provider="openai",
            response_id="response-270-other",
            request_id="request-270-other",
            status="completed",
            text_parts=("other",),
            text="other",
        ),
    )
    other_path = tmp_path / "other-result.json"
    persist_publication_regeneration_result(other_path, other_result)

    with pytest.raises(PublicationRegenerationProjectionError) as caught:
        project_publication_regeneration_output(
            readiness_record_path=readiness_path,
            result_path=other_path,
        )

    assert caught.value.detail.classification == "result_record_binding"


def test_ready_regeneration_id_mismatch_is_safely_rejected(tmp_path: Path) -> None:
    fixture, readiness_path, readiness_record, _ = evidence_for_state(
        tmp_path, "ready"
    )
    forged_assessment = replace(
        readiness_record.assessment,
        regeneration_id="regen-20260912-02",
    )
    forged_path, _ = readiness_sidecar(
        fixture,
        forged_assessment,
        path=tmp_path / "forged-readiness-record.json",
    )

    with pytest.raises(PublicationRegenerationProjectionError) as caught:
        project_publication_regeneration_output(
            readiness_record_path=forged_path,
            result_path=fixture.result_path,
        )

    assert caught.value.detail.classification == "regeneration_id_binding"
    assert readiness_path.read_bytes()


def test_ready_source_audit_identity_mismatch_is_safely_rejected(
    tmp_path: Path,
) -> None:
    fixture, readiness_path, readiness_record, _ = evidence_for_state(
        tmp_path, "ready"
    )
    forged_assessment = replace(
        readiness_record.assessment,
        source_audit_sha256="a" * 64,
    )
    forged_path, _ = readiness_sidecar(
        fixture,
        forged_assessment,
        path=tmp_path / "forged-readiness-record.json",
    )

    with pytest.raises(PublicationRegenerationProjectionError) as caught:
        project_publication_regeneration_output(
            readiness_record_path=forged_path,
            result_path=fixture.result_path,
        )

    assert caught.value.detail.classification == "source_audit_binding"
    assert readiness_path.read_bytes()


def test_ready_with_failure_result_is_rejected_not_downgraded(tmp_path: Path) -> None:
    ready_fixture, _, ready_record, _ = evidence_for_state(
        tmp_path / "ready", "ready"
    )
    failure_fixture = readiness_fixture(
        tmp_path / "failure",
        result=failure_result_fixture(),
    )
    forged_assessment = replace(
        ready_record.assessment,
        result_record_sha256=failure_fixture.record.digest,
    )
    readiness_path, _ = readiness_sidecar(
        ready_fixture,
        forged_assessment,
        path=tmp_path / "ready-readiness-record.json",
    )

    with pytest.raises(PublicationRegenerationProjectionError) as caught:
        project_publication_regeneration_output(
            readiness_record_path=readiness_path,
            result_path=failure_fixture.result_path,
        )

    assert caught.value.detail.classification == "outcome_binding"
    assert "result_failure" not in caught.value.detail.classification


def test_direct_forged_projection_states_and_coercions_are_rejected(
    tmp_path: Path,
) -> None:
    fixture, readiness_path, readiness_record, _ = evidence_for_state(
        tmp_path, "ready"
    )
    ready = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )
    values = dict(
        schema_version=ready.schema_version,
        regeneration_id=ready.regeneration_id,
        readiness_record_sha256=ready.readiness_record_sha256,
        readiness=ready.readiness,
        reason_codes=ready.reason_codes,
        result_record_sha256=ready.result_record_sha256,
        source_audit_sha256=ready.source_audit_sha256,
        publishable=ready.publishable,
        business_output_sha256=ready.business_output_sha256,
        business_output_text=ready.business_output_text,
    )

    with pytest.raises(FrozenInstanceError):
        ready.publishable = False  # type: ignore[misc]
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(**values)
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(**{**values, "publishable": False})
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(
            **{
                **values,
                "readiness": "insufficient_evidence",
                "publishable": False,
                "business_output_sha256": None,
                "business_output_text": None,
            }
        )
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(**{**values, "business_output_text": None})
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(
            **{**values, "business_output_sha256": "0" * 64}
        )
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(**{**values, "reason_codes": []})
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(**{**values, "publishable": 1})
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjectionChild(**values)
    with pytest.raises(PublicationRegenerationProjectionError):
        serialize_publication_regeneration_projection_canonical(
            {"projection": ready}  # type: ignore[arg-type]
        )
    with pytest.raises(PublicationRegenerationProjectionError):
        PublicationRegenerationProjection(**{**values, "readiness_record_sha256": "x"})
    assert readiness_record.digest == ready.readiness_record_sha256


@pytest.mark.parametrize(
    "state",
    ("ready", "insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_projection_is_read_only_and_preserves_all_prior_artifacts(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, state)
    tracked = [
        fixture.audit_path,
        fixture.result_path,
        readiness_path,
        fixture.state_path,
        fixture.events_path,
        *sorted(fixture.ledger_directory.rglob("*")),
    ]
    before = {path: path.read_bytes() for path in tracked if path.is_file()}

    project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    after = {path: path.read_bytes() for path in tracked if path.is_file()}
    assert after == before


def test_projection_has_no_provider_network_environment_or_clock_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, "ready")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)

    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )
    assert projection.publishable is True


def test_phase_268_and_phase_269_canonical_identities_remain_pinned(
    tmp_path: Path,
) -> None:
    fixture = readiness_fixture(tmp_path)
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
    )
    readiness_path, readiness_record = readiness_sidecar(fixture, assessment)
    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    assert assessment.digest == (
        "b2cbe77da22a1d2dda2555b5262ab76b9bc5548ee8786c2e620c7073e0372798"
    )
    assert readiness_record.digest == (
        "9d6bd1ac15d5a6e34fd062d6d103610398f21f63d43c025a992619979436dff7"
    )
    assert projection.readiness == "insufficient_evidence"


def test_phase270_public_apis_are_available_from_engine_package() -> None:
    import ai_office.engine as engine

    for name in projection_module.__all__:
        assert getattr(engine, name) is getattr(projection_module, name)


def test_boundary_loads_readiness_before_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, "ready")
    calls: list[str] = []
    readiness_loader = projection_module.load_publication_regeneration_readiness_record
    result_loader = projection_module.load_publication_regeneration_result

    def load_readiness(path: Path):
        calls.append("readiness")
        return readiness_loader(path)

    def load_result(path: Path):
        calls.append("result")
        return result_loader(path)

    monkeypatch.setattr(
        projection_module,
        "load_publication_regeneration_readiness_record",
        load_readiness,
    )
    monkeypatch.setattr(
        projection_module,
        "load_publication_regeneration_result",
        load_result,
    )

    project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )
    assert calls == ["readiness", "result"]


@pytest.mark.parametrize(
    "state",
    ("ready", "insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_reason_codes_and_publishability_are_exactly_preserved(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, readiness_record, _ = evidence_for_state(tmp_path, state)
    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )
    assert projection.reason_codes == readiness_record.reason_codes
    assert projection.publishable is (state == "ready")
