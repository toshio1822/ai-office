"""Focused immutable Phase 269 readiness-evidence sidecar tests."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.engine import publication_regeneration_readiness_record as record_module
from ai_office.engine.publication_regeneration_readiness import (
    PublicationRegenerationReadinessError,
    assess_publication_regeneration_result_readiness,
    serialize_publication_regeneration_readiness_assessment_canonical,
)
from ai_office.engine.publication_regeneration_readiness_record import (
    PublicationRegenerationReadinessRecordConflictError,
    PublicationRegenerationReadinessRecordError,
    PublicationRegenerationReadinessRecordLoadError,
    PublicationRegenerationReadinessRecordPersistenceError,
    build_publication_regeneration_readiness_record,
    load_publication_regeneration_readiness_record,
    persist_publication_regeneration_readiness_record,
    publication_regeneration_readiness_record_canonical_bytes,
    publication_regeneration_readiness_record_digest,
    serialize_publication_regeneration_readiness_record_canonical,
)
from ai_office.engine.publication_regeneration_result import (
    build_publication_regeneration_result_record,
    persist_publication_regeneration_result,
)
from ai_office.invocation import ModelInvocationSuccess
from tests.test_publication_regeneration_readiness import (
    PublicationRegenerationReadinessAssessmentChild,
    ReadinessFixture,
    claim_contract_for,
    exact_contract,
    failure_result_fixture,
    readiness_fixture,
)


def stale_assessment_fixture(tmp_path: Path) -> tuple[ReadinessFixture, object]:
    fixture = readiness_fixture(tmp_path)
    changed_result = ModelInvocationSuccess(
        provider=fixture.record.result.provider,
        response_id="response-269-stale",
        request_id="request-269-stale",
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
    mismatch = claim_contract_for(
        fixture.audit.post_terminal_facts,
        output=changed_result.text,
    )
    mismatch = replace(mismatch, workflow_id="other-workflow")
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=mismatch,
    )
    return fixture, assessment


def assessment_for_state(tmp_path: Path, state: str):
    if state == "ready":
        fixture = readiness_fixture(tmp_path)
        return fixture, assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
            claim_contract=exact_contract(fixture),
        )
    if state == "insufficient_evidence":
        fixture = readiness_fixture(tmp_path)
        return fixture, assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
        )
    if state == "stale_or_inconsistent":
        return stale_assessment_fixture(tmp_path)
    if state == "result_failure":
        fixture = readiness_fixture(tmp_path, result=failure_result_fixture())
        return fixture, assess_publication_regeneration_result_readiness(
            result_path=fixture.result_path,
            source_audit_path=fixture.audit_path,
        )
    raise AssertionError(state)


@pytest.mark.parametrize(
    "state",
    (
        "ready",
        "insufficient_evidence",
        "stale_or_inconsistent",
        "result_failure",
    ),
)
def test_build_persist_load_preserves_every_phase268_state(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, assessment = assessment_for_state(tmp_path / state, state)
    record = build_publication_regeneration_readiness_record(assessment)
    path = tmp_path / state / "readiness-record.json"

    assert record.assessment is assessment
    assert record.readiness == state
    assert record.reason_codes == assessment.reason_codes
    assert record.regeneration_id == assessment.regeneration_id
    assert record.result_record_sha256 == assessment.result_record_sha256
    assert record.source_audit_sha256 == assessment.source_audit_sha256
    assert path.parent.exists()

    persist_publication_regeneration_readiness_record(path, record)
    loaded = load_publication_regeneration_readiness_record(path)

    assert loaded == record
    assert path.read_bytes() == (
        publication_regeneration_readiness_record_canonical_bytes(record)
    )
    if state == "stale_or_inconsistent":
        assert loaded.assessment.evaluated_claim_contract is not None
        assert (
            loaded.assessment.evaluated_claim_contract.workflow_id
            == "other-workflow"
        )
        assert "other-workflow" in path.read_text(encoding="utf-8")
    if state == "result_failure":
        assert loaded.assessment.business_output_sha256 is None
        assert "safe message" not in path.read_text(encoding="utf-8")


def test_record_canonical_fixture_is_exact_and_digest_pinned(tmp_path: Path) -> None:
    fixture, assessment = assessment_for_state(
        tmp_path / "fixture", "insufficient_evidence"
    )
    record = build_publication_regeneration_readiness_record(assessment)
    canonical = serialize_publication_regeneration_readiness_record_canonical(record)

    assert canonical == canonical.rstrip("\n")
    assert fixture.record.result.text not in canonical
    assert "result.json" not in canonical
    assert "source-audit.json" not in canonical
    expected = (
        '{"assessment":{"business_output_sha256":"f5a064be281eea4db190ed7268f4a1e005ca05227654bbfa260a6c5684da743e",'
        '"claim_contract_sha256":null,"evaluated_claim_contract":null,"outcome":"success",'
        '"readiness":"insufficient_evidence","reason_codes":["claim_contract_missing"],'
        '"regeneration_id":"regen-20260912-01",'
        '"result_record_sha256":"ded25e22fb8c1fac42db443c4e5a60682b10b9697ddb749bebacecd708624eda",'
        '"schema_version":"publication-regeneration-readiness.v1",'
        '"source_audit_sha256":"34d656c42f0b15d74b8d92babe0d361fa0a1c0a69a221b68e985ba87cfe68926",'
        '"source_post_terminal_facts":{"completed_step_ids":["research","publish"],'
        '"events_sha256":"1232505d7388720951336b434fe00df5474cefd0d659a99283c52a72d29ad6c8",'
        '"final_output_sha256":"f5a064be281eea4db190ed7268f4a1e005ca05227654bbfa260a6c5684da743e",'
        '"schema_version":"post-terminal-facts.v1",'
        '"state_sha256":"6e4acd32f41a8dc7c8d1e1a49b1ee6dde461786ac454cecf10f14f9af650c022",'
        '"terminal_employee_id":"editor","terminal_provider":"terminal-provider",'
        '"terminal_reason":"last_step_succeeded","terminal_status":"workflow_complete",'
        '"terminal_step_id":"publish","terminal_step_index":2,"workflow_id":"phase268-workflow"},'
        '"source_post_terminal_facts_sha256":"c3e68c8c60eee1b1e7a41a7df2dc338a3e0d7eca6838916a68396ff511aec6dd"},'
        '"assessment_sha256":"b2cbe77da22a1d2dda2555b5262ab76b9bc5548ee8786c2e620c7073e0372798",'
        '"readiness":"insufficient_evidence","reason_codes":["claim_contract_missing"],'
        '"regeneration_id":"regen-20260912-01",'
        '"result_record_sha256":"ded25e22fb8c1fac42db443c4e5a60682b10b9697ddb749bebacecd708624eda",'
        '"schema_version":"publication-regeneration-readiness-record.v1",'
        '"source_audit_sha256":"34d656c42f0b15d74b8d92babe0d361fa0a1c0a69a221b68e985ba87cfe68926"}'
    )
    assert canonical == expected
    assert publication_regeneration_readiness_record_digest(record) == (
        "9d6bd1ac15d5a6e34fd062d6d103610398f21f63d43c025a992619979436dff7"
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == record.digest


def test_record_digest_and_canonical_bytes_are_stable_in_fresh_process(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(
        tmp_path / "fresh-process", "stale_or_inconsistent"
    )
    record = build_publication_regeneration_readiness_record(assessment)
    path = tmp_path / "fresh-process" / "record.json"
    persist_publication_regeneration_readiness_record(path, record)
    script = """
import sys
from pathlib import Path
from ai_office.engine.publication_regeneration_readiness_record import (
    load_publication_regeneration_readiness_record,
    publication_regeneration_readiness_record_digest,
)
record = load_publication_regeneration_readiness_record(Path(sys.argv[1]))
print(publication_regeneration_readiness_record_digest(record))
print(record.digest)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (
        f"{Path.cwd() / 'src'}:{environment.get('PYTHONPATH', '')}"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [record.digest, record.digest]


def test_direct_forgery_and_coercion_are_rejected_without_weakening_phase268(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "forge", "ready")
    record = build_publication_regeneration_readiness_record(assessment)

    with pytest.raises(PublicationRegenerationReadinessRecordError) as digest_error:
        replace(record, assessment_sha256="0" * 64)
    assert digest_error.value.detail.classification == "assessment_binding"

    with pytest.raises(PublicationRegenerationReadinessRecordError) as identity_error:
        replace(record, regeneration_id="other-regeneration")
    assert identity_error.value.detail.classification == "assessment_binding"

    with pytest.raises(PublicationRegenerationReadinessRecordError):
        replace(record, readiness="result_failure")
    with pytest.raises(PublicationRegenerationReadinessRecordError):
        build_publication_regeneration_readiness_record({"assessment": assessment})  # type: ignore[arg-type]
    with pytest.raises(PublicationRegenerationReadinessError):
        PublicationRegenerationReadinessAssessmentChild(**assessment.__dict__)
    child = object.__new__(PublicationRegenerationReadinessAssessmentChild)
    for field in assessment.__dataclass_fields__:
        object.__setattr__(child, field, getattr(assessment, field))
    with pytest.raises(PublicationRegenerationReadinessRecordError):
        build_publication_regeneration_readiness_record(child)
    with pytest.raises(PublicationRegenerationReadinessRecordError):
        serialize_publication_regeneration_readiness_record_canonical(  # type: ignore[arg-type]
            {"assessment": assessment}
        )


def test_record_is_frozen_and_revalidates_exact_phase268_assessment(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "frozen", "ready")
    record = build_publication_regeneration_readiness_record(assessment)

    with pytest.raises(AttributeError):
        record.readiness = "result_failure"  # type: ignore[misc]
    with pytest.raises(PublicationRegenerationReadinessError):
        replace(assessment, source_post_terminal_facts_sha256="0" * 64)
    assert record.assessment is assessment


def test_identical_pure_repersist_is_idempotent_and_redoes_durability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "idempotent", "ready")
    record = build_publication_regeneration_readiness_record(assessment)
    path = tmp_path / "idempotent" / "record.json"
    persist_publication_regeneration_readiness_record(path, record)
    original_fsync = record_module.os.fsync
    fsync_calls: list[int] = []

    def record_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(record_module.os, "fsync", record_fsync)
    persist_publication_regeneration_readiness_record(path, record)

    assert len(fsync_calls) == 2
    assert path.read_bytes() == (
        publication_regeneration_readiness_record_canonical_bytes(record)
    )


def test_conflicting_corrupt_and_truncated_sidecars_are_never_repaired(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "conflict", "ready")
    record = build_publication_regeneration_readiness_record(assessment)
    path = tmp_path / "conflict" / "record.json"
    corrupt = b'{"truncated"'
    path.write_bytes(corrupt)

    with pytest.raises(PublicationRegenerationReadinessRecordConflictError):
        persist_publication_regeneration_readiness_record(path, record)
    assert path.read_bytes() == corrupt

    path.write_bytes(b"not-json")
    original = path.read_bytes()
    with pytest.raises(PublicationRegenerationReadinessRecordConflictError):
        persist_publication_regeneration_readiness_record(path, record)
    assert path.read_bytes() == original


def test_write_flush_file_fsync_and_directory_fsync_ambiguity_retains_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "ambiguity", "ready")
    record = build_publication_regeneration_readiness_record(assessment)

    original_open = record_module.Path.open

    class WriteFailureHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> WriteFailureHandle:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def write(self, _contents: bytes) -> int:
            raise OSError("synthetic readiness write failure")

    def fail_write_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        handle = original_open(path, mode, *args, **kwargs)
        return WriteFailureHandle(handle) if mode == "xb" else handle

    write_path = tmp_path / "ambiguity" / "write.json"
    monkeypatch.setattr(record_module.Path, "open", fail_write_open)
    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError) as error:
        persist_publication_regeneration_readiness_record(write_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert write_path.exists()
    assert write_path.read_bytes() == b""

    monkeypatch.undo()

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
            raise OSError("synthetic readiness flush failure")

    def fail_flush_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        handle = original_open(path, mode, *args, **kwargs)
        return FlushFailureHandle(handle) if mode == "xb" else handle

    flush_path = tmp_path / "ambiguity" / "flush.json"
    monkeypatch.setattr(record_module.Path, "open", fail_flush_open)
    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError) as error:
        persist_publication_regeneration_readiness_record(flush_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert flush_path.exists()
    assert flush_path.read_bytes()

    monkeypatch.undo()

    file_fsync_path = tmp_path / "ambiguity" / "file-fsync.json"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic readiness file fsync failure")

    monkeypatch.setattr(record_module.os, "fsync", fail_fsync)
    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError) as error:
        persist_publication_regeneration_readiness_record(file_fsync_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert file_fsync_path.exists()
    assert file_fsync_path.read_bytes()

    monkeypatch.undo()

    directory_fsync_path = tmp_path / "ambiguity" / "directory-fsync.json"

    def fail_directory_fsync(_directory: Path) -> None:
        raise OSError("synthetic readiness directory fsync failure")

    monkeypatch.setattr(
        record_module,
        "_fsync_record_directory",
        fail_directory_fsync,
    )
    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError) as error:
        persist_publication_regeneration_readiness_record(directory_fsync_path, record)
    assert error.value.detail.classification == "ambiguous"
    assert directory_fsync_path.exists()
    assert (
        load_publication_regeneration_readiness_record(directory_fsync_path)
        == record
    )


def test_strict_loader_rejects_duplicate_unknown_missing_type_utf8_and_noncanonical(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "loader", "ready")
    record = build_publication_regeneration_readiness_record(assessment)
    canonical = publication_regeneration_readiness_record_canonical_bytes(record)
    path = tmp_path / "loader" / "record.json"

    cases = {
        "duplicate": canonical.replace(
            b'"schema_version":"publication-regeneration-readiness-record.v1"',
            b'"schema_version":"publication-regeneration-readiness-record.v1",'
            b'"schema_version":"publication-regeneration-readiness-record.v1"',
            1,
        ),
        "unknown": canonical.replace(
            b'"assessment":{', b'"unknown":1,"assessment":{', 1
        ),
        "noncanonical": canonical + b"\n",
        "invalid_utf8": b"\xff",
    }
    missing_value = json.loads(canonical)
    del missing_value["readiness"]
    cases["missing"] = json.dumps(missing_value).encode("utf-8")
    type_value = json.loads(canonical)
    type_value["reason_codes"] = "not-an-array"
    cases["type"] = json.dumps(type_value).encode("utf-8")

    for name, contents in cases.items():
        path.write_bytes(contents)
        with pytest.raises(PublicationRegenerationReadinessRecordLoadError) as error:
            load_publication_regeneration_readiness_record(path)
        assert error.value.detail.classification in {
            "parse",
            "record",
            "keys",
            "noncanonical",
            "reason_codes",
        }
        assert path.read_bytes() == contents, name


def test_loader_rejects_assessment_digest_and_nested_identity_tampering(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "tamper", "ready")
    record = build_publication_regeneration_readiness_record(assessment)
    canonical = publication_regeneration_readiness_record_canonical_bytes(record)
    path = tmp_path / "tamper" / "record.json"

    tampered_digest = canonical.replace(
        record.assessment_sha256.encode("ascii"), b"0" * 64, 1
    )
    path.write_bytes(tampered_digest)
    with pytest.raises(PublicationRegenerationReadinessRecordLoadError):
        load_publication_regeneration_readiness_record(path)

    tampered_nested_identity = canonical.replace(
        record.assessment.regeneration_id.encode("utf-8"),
        b"other-regeneration",
        1,
    )
    path.write_bytes(tampered_nested_identity)
    with pytest.raises(PublicationRegenerationReadinessRecordLoadError):
        load_publication_regeneration_readiness_record(path)


def test_record_persistence_is_explicit_and_never_mutates_prior_lineages(
    tmp_path: Path,
) -> None:
    fixture, assessment = assessment_for_state(tmp_path / "lineage", "ready")
    before = {
        "result": fixture.result_path.read_bytes(),
        "audit": fixture.audit_path.read_bytes(),
        "state": fixture.state_path.read_bytes(),
        "events": fixture.events_path.read_bytes(),
        "ledger": tuple(
            (path.name, path.read_bytes())
            for path in sorted(fixture.ledger_directory.iterdir())
        ),
    }
    record = build_publication_regeneration_readiness_record(assessment)
    explicit_path = tmp_path / "lineage" / "caller-supplied" / "record.json"
    explicit_path.parent.mkdir()
    persist_publication_regeneration_readiness_record(explicit_path, record)

    assert explicit_path.exists()
    assert not (tmp_path / "lineage" / "readiness.json").exists()
    assert before == {
        "result": fixture.result_path.read_bytes(),
        "audit": fixture.audit_path.read_bytes(),
        "state": fixture.state_path.read_bytes(),
        "events": fixture.events_path.read_bytes(),
        "ledger": tuple(
            (path.name, path.read_bytes())
            for path in sorted(fixture.ledger_directory.iterdir())
        ),
    }


def test_builder_and_serializer_do_not_use_provider_network_environment_or_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "pure", "insufficient_evidence")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(record_module.os, "getenv", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    record = build_publication_regeneration_readiness_record(assessment)
    canonical = serialize_publication_regeneration_readiness_record_canonical(record)

    assert record.readiness == "insufficient_evidence"
    assert canonical.encode("utf-8") == (
        publication_regeneration_readiness_record_canonical_bytes(record)
    )


def test_path_validation_rejects_missing_parent_symlink_directory_and_coercion(
    tmp_path: Path,
) -> None:
    _, assessment = assessment_for_state(tmp_path / "paths", "ready")
    record = build_publication_regeneration_readiness_record(assessment)

    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError) as error:
        persist_publication_regeneration_readiness_record(
            tmp_path / "paths" / "missing" / "record.json", record
        )
    assert error.value.detail.classification == "parent"

    directory = tmp_path / "paths" / "directory"
    directory.mkdir()
    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError):
        persist_publication_regeneration_readiness_record(directory, record)

    symlink = tmp_path / "paths" / "symlink"
    symlink.symlink_to(directory)
    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError):
        persist_publication_regeneration_readiness_record(symlink, record)

    with pytest.raises(PublicationRegenerationReadinessRecordPersistenceError) as error:
        persist_publication_regeneration_readiness_record(str(tmp_path), record)  # type: ignore[arg-type]
    assert error.value.detail.classification == "path_type"


def test_loader_does_not_repair_or_delete_corrupt_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    corrupt = b"{\"truncated\""
    path.write_bytes(corrupt)

    with pytest.raises(PublicationRegenerationReadinessRecordLoadError):
        load_publication_regeneration_readiness_record(path)
    assert path.read_bytes() == corrupt


def test_public_phase268_assessment_digest_remains_unchanged(tmp_path: Path) -> None:
    _, assessment = assessment_for_state(
        tmp_path / "phase268-compat", "insufficient_evidence"
    )
    assert serialize_publication_regeneration_readiness_assessment_canonical(
        assessment
    ).startswith('{"business_output_sha256":')
    assert assessment.digest == (
        "b2cbe77da22a1d2dda2555b5262ab76b9bc5548ee8786c2e620c7073e0372798"
    )


@pytest.mark.parametrize(
    "export_name",
    (
        "PublicationRegenerationReadinessRecord",
        "build_publication_regeneration_readiness_record",
        "load_publication_regeneration_readiness_record",
        "persist_publication_regeneration_readiness_record",
    ),
)
def test_phase269_public_exports_are_available_from_engine_package(export_name: str):
    import ai_office.engine as engine

    assert getattr(engine, export_name)
