"""Focused provider-free export tests for Phase 272."""

from __future__ import annotations

import hashlib
import inspect
import socket
import time
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from ai_office.engine import publication_regeneration_export as export_module
from ai_office.engine.publication_regeneration_export import (
    PublicationRegenerationExportError,
    PublicationRegenerationExportReceipt,
    export_publication_regeneration_output,
)
from ai_office.engine.publication_regeneration_projection import (
    PublicationRegenerationProjection,
    project_publication_regeneration_output,
    publication_regeneration_projection_digest,
)
from ai_office.engine.publication_regeneration_readiness import (
    assess_publication_regeneration_result_readiness,
)
from ai_office.invocation import ModelInvocationSuccess
from tests.test_publication_regeneration_projection import (
    evidence_for_state,
    readiness_sidecar,
)
from tests.test_publication_regeneration_readiness import (
    exact_contract,
    readiness_fixture,
)


def ready_evidence(
    tmp_path: Path,
    *,
    text: str = "ready output\n",
) -> tuple[object, Path]:
    fixture = readiness_fixture(
        tmp_path,
        result=ModelInvocationSuccess(
            provider="openai",
            response_id="response-272-ready",
            request_id="request-272-ready",
            status="completed",
            text_parts=(text,),
            text=text,
        ),
    )
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=exact_contract(fixture),
    )
    readiness_path, _ = readiness_sidecar(fixture, assessment)
    return fixture, readiness_path


def snapshot_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def counted_projection(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[Path, Path]],
) -> None:
    real_projection = export_module.project_publication_regeneration_output

    def project(
        *,
        readiness_record_path: Path,
        result_path: Path,
    ) -> PublicationRegenerationProjection:
        calls.append((readiness_record_path, result_path))
        return real_projection(
            readiness_record_path=readiness_record_path,
            result_path=result_path,
        )

    monkeypatch.setattr(
        export_module,
        "project_publication_regeneration_output",
        project,
    )


def test_ready_projection_exports_exact_utf8_bytes_and_bound_receipt(
    tmp_path: Path,
) -> None:
    text = "\n  ready 日本語 😀\n\ttrailing whitespace  "
    fixture, readiness_path = ready_evidence(tmp_path, text=text)
    output_path = tmp_path / "export.txt"
    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    receipt = export_publication_regeneration_output(
        output_path=output_path,
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    output_bytes = text.encode("utf-8")
    assert output_path.read_bytes() == output_bytes
    assert receipt.schema_version == "publication-regeneration-export-receipt.v1"
    assert receipt.regeneration_id == projection.regeneration_id
    assert receipt.projection_sha256 == publication_regeneration_projection_digest(
        projection
    )
    assert receipt.readiness_record_sha256 == projection.readiness_record_sha256
    assert receipt.result_record_sha256 == projection.result_record_sha256
    assert receipt.source_audit_sha256 == projection.source_audit_sha256
    assert receipt.business_output_sha256 == hashlib.sha256(output_bytes).hexdigest()
    assert receipt.business_output_sha256 == projection.business_output_sha256
    assert receipt.output_byte_length == len(output_bytes)
    assert receipt.output_byte_length != len(text)
    assert b"\xef\xbb\xbf" not in output_bytes


def test_ready_empty_string_exports_a_durable_zero_byte_file(tmp_path: Path) -> None:
    fixture, readiness_path = ready_evidence(tmp_path, text="")
    output_path = tmp_path / "empty.txt"

    receipt = export_publication_regeneration_output(
        output_path=output_path,
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    assert output_path.is_file()
    assert output_path.read_bytes() == b""
    assert receipt.business_output_sha256 == hashlib.sha256(b"").hexdigest()
    assert receipt.output_byte_length == 0


def test_receipt_is_frozen_exact_and_contains_only_the_narrow_contract() -> None:
    values = {
        "schema_version": "publication-regeneration-export-receipt.v1",
        "regeneration_id": "regen-272",
        "projection_sha256": "a" * 64,
        "readiness_record_sha256": "b" * 64,
        "result_record_sha256": "c" * 64,
        "source_audit_sha256": "d" * 64,
        "business_output_sha256": "e" * 64,
        "output_byte_length": 3,
    }
    receipt = PublicationRegenerationExportReceipt(**values)

    with pytest.raises(FrozenInstanceError):
        receipt.output_byte_length = 4  # type: ignore[misc]
    assert [field.name for field in fields(receipt)] == list(values)

    with pytest.raises(PublicationRegenerationExportError):
        PublicationRegenerationExportReceipt(**{**values, "output_byte_length": True})
    with pytest.raises(PublicationRegenerationExportError):
        PublicationRegenerationExportReceipt(
            **{**values, "projection_sha256": "A" * 64}
        )


@pytest.mark.parametrize(
    "state",
    ("insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_non_ready_projection_never_exports_or_leaks_business_text(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, _, hidden_success_text = evidence_for_state(
        tmp_path, state
    )
    output_path = tmp_path / "not-exported.txt"
    failure_message = (
        fixture.record.result.message
        if state == "result_failure"
        else None
    )

    with pytest.raises(PublicationRegenerationExportError) as caught:
        export_publication_regeneration_output(
            output_path=output_path,
            readiness_record_path=readiness_path,
            result_path=fixture.result_path,
        )

    assert not output_path.exists()
    assert str(caught.value) == "publication regeneration export is invalid"
    assert caught.value.detail.classification == "not_publishable"
    for secret in (hidden_success_text, failure_message):
        if secret is not None:
            assert secret not in str(caught.value)


def test_projection_failure_is_mapped_safely_without_creating_output(
    tmp_path: Path,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    fixture.result_path.write_bytes(fixture.result_path.read_bytes() + b"tampered")
    output_path = tmp_path / "invalid.txt"

    with pytest.raises(PublicationRegenerationExportError) as caught:
        export_publication_regeneration_output(
            output_path=output_path,
            readiness_record_path=readiness_path,
            result_path=fixture.result_path,
        )

    assert not output_path.exists()
    assert str(caught.value) == "publication regeneration export is invalid"
    assert caught.value.detail.classification == "projection"


@pytest.mark.parametrize(
    "state",
    ("ready", "insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_valid_export_path_calls_phase270_exactly_once_with_exact_paths(
    tmp_path: Path,
    state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, state)
    output_path = tmp_path / "export.txt"
    calls: list[tuple[Path, Path]] = []
    counted_projection(monkeypatch, calls)

    if state == "ready":
        export_publication_regeneration_output(
            output_path=output_path,
            readiness_record_path=readiness_path,
            result_path=fixture.result_path,
        )
    else:
        with pytest.raises(PublicationRegenerationExportError):
            export_publication_regeneration_output(
                output_path=output_path,
                readiness_record_path=readiness_path,
                result_path=fixture.result_path,
            )

    assert calls == [(readiness_path, fixture.result_path)]
    assert calls[0][0] is readiness_path
    assert calls[0][1] is fixture.result_path


@pytest.mark.parametrize(
    "target_kind",
    (
        "missing_parent",
        "directory",
        "symlink",
        "existing_identical",
        "existing_different",
    ),
)
def test_output_preflight_blocks_before_phase270_and_preserves_target(
    tmp_path: Path,
    target_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    if target_kind == "missing_parent":
        output_path = tmp_path / "missing" / "export.txt"
        before = None
    elif target_kind == "directory":
        output_path = tmp_path / "directory"
        output_path.mkdir()
        before = None
    elif target_kind == "symlink":
        output_path = tmp_path / "symlink"
        output_path.symlink_to(tmp_path / "source.txt")
        before = output_path.readlink()
    else:
        output_path = tmp_path / "existing.txt"
        output_path.write_bytes(
            b"ready output\n" if target_kind == "existing_identical" else b"keep me"
        )
        before = output_path.read_bytes()

    calls: list[tuple[Path, Path]] = []
    counted_projection(monkeypatch, calls)

    with pytest.raises(PublicationRegenerationExportError) as caught:
        export_publication_regeneration_output(
            output_path=output_path,
            readiness_record_path=readiness_path,
            result_path=fixture.result_path,
        )

    assert calls == []
    assert str(caught.value) == "publication regeneration export is invalid"
    if target_kind == "missing_parent":
        assert not output_path.parent.exists()
    elif target_kind == "directory":
        assert output_path.is_dir()
    elif target_kind == "symlink":
        assert output_path.readlink() == before
    else:
        assert output_path.read_bytes() == before


def test_race_created_target_is_rejected_without_overwrite_or_second_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    output_path = tmp_path / "race.txt"
    calls: list[tuple[Path, Path]] = []
    counted_projection(monkeypatch, calls)
    original_open = Path.open

    def racing_open(self: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == output_path and mode == "xb":
            self.write_bytes(b"created by another actor")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)

    with pytest.raises(PublicationRegenerationExportError) as caught:
        export_publication_regeneration_output(
            output_path=output_path,
            readiness_record_path=readiness_path,
            result_path=fixture.result_path,
        )

    assert output_path.read_bytes() == b"created by another actor"
    assert calls == [(readiness_path, fixture.result_path)]
    assert caught.value.detail.classification == "target_exists"


def test_successful_export_flushes_file_and_fsyncs_file_then_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    output_path = tmp_path / "durable.txt"
    events: list[str] = []
    original_open = Path.open
    original_fsync = export_module.os.fsync

    class HandleSpy:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> HandleSpy:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            events.append("close")
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def write(self, data: bytes) -> int:
            events.append("write")
            return self.handle.write(data)  # type: ignore[attr-defined]

        def flush(self) -> None:
            events.append("flush")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

    def spying_open(self: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode", "r")
        handle = original_open(self, *args, **kwargs)
        if self == output_path and mode == "xb":
            return HandleSpy(handle)
        return handle

    fsync_count = 0

    def spying_fsync(fd: int) -> None:
        nonlocal fsync_count
        fsync_count += 1
        events.append("file-fsync" if fsync_count == 1 else "parent-fsync")
        original_fsync(fd)

    monkeypatch.setattr(Path, "open", spying_open)
    monkeypatch.setattr(export_module.os, "fsync", spying_fsync)

    export_publication_regeneration_output(
        output_path=output_path,
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    assert events == ["write", "flush", "file-fsync", "close", "parent-fsync"]
    assert output_path.read_bytes() == b"ready output\n"


@pytest.mark.parametrize(
    "failure_point",
    ("write", "short_write", "flush", "file_fsync", "parent_fsync"),
)
def test_post_create_persistence_failures_are_ambiguous_and_retain_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    output_path = tmp_path / f"ambiguous-{failure_point}.txt"
    calls: list[tuple[Path, Path]] = []
    counted_projection(monkeypatch, calls)
    original_open = Path.open
    original_fsync = export_module.os.fsync
    fsync_count = 0

    class FaultyHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def write(self, data: bytes) -> int:
            if failure_point == "write":
                raise OSError("write failure")
            if failure_point == "short_write":
                return 0
            return self.handle.write(data)  # type: ignore[attr-defined]

        def __enter__(self) -> FaultyHandle:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def flush(self) -> None:
            if failure_point == "flush":
                raise OSError("flush failure")
            self.handle.flush()  # type: ignore[attr-defined]

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

        def close(self) -> None:
            self.handle.close()  # type: ignore[attr-defined]

    def faulty_open(self: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode", "r")
        handle = original_open(self, *args, **kwargs)
        if self == output_path and mode == "xb":
            return FaultyHandle(handle)
        return handle

    def faulty_fsync(fd: int) -> None:
        nonlocal fsync_count
        fsync_count += 1
        if (
            failure_point == "file_fsync" and fsync_count == 1
        ) or (
            failure_point == "parent_fsync" and fsync_count == 2
        ):
            raise OSError("fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(Path, "open", faulty_open)
    monkeypatch.setattr(export_module.os, "fsync", faulty_fsync)

    with pytest.raises(PublicationRegenerationExportError) as caught:
        export_publication_regeneration_output(
            output_path=output_path,
            readiness_record_path=readiness_path,
            result_path=fixture.result_path,
        )

    assert output_path.exists()
    assert caught.value.detail.classification == "ambiguous"
    assert str(caught.value) == "publication regeneration export is invalid"
    assert calls == [(readiness_path, fixture.result_path)]


def test_export_preserves_every_preexisting_lineage_artifact_byte_for_byte(
    tmp_path: Path,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    output_path = tmp_path / "new-export.txt"
    before = snapshot_files(tmp_path)

    export_publication_regeneration_output(
        output_path=output_path,
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )

    after = snapshot_files(tmp_path)
    assert {
        path: contents
        for path, contents in after.items()
        if path != Path(output_path.name)
    } == before
    assert Path(output_path.name) in after


def test_export_has_no_direct_predecessor_loaders_or_readiness_assessor() -> None:
    source = inspect.getsource(export_module)
    assert "load_publication_regeneration_result" not in source
    assert "load_publication_regeneration_readiness_record" not in source
    assert "assess_publication_regeneration_result_readiness" not in source


def test_export_has_no_provider_network_environment_or_clock_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(export_module.os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)

    output_path = tmp_path / "no-external-access.txt"
    export_publication_regeneration_output(
        output_path=output_path,
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )
    assert output_path.read_bytes() == b"ready output\n"
