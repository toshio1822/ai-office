from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_office.definitions.style_profile import (
    MAX_STYLE_PROFILE_CONTENT_BYTES,
    MAX_STYLE_PROFILE_FILE_BYTES,
    StyleProfileLoadError,
    add_style_profile_to_assignment,
    load_style_profile,
)


def test_profile_definitions_are_additive_and_assignment_lines_stay_stable(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    profile = load_style_profile("toshio-web", repository / "style_profiles")
    assert profile.definition.version == "0.2"

    additional_directory = tmp_path / "style_profiles"
    additional_directory.mkdir()
    (additional_directory / "concise-tech.yaml").write_text(
        'id: concise-tech\nversion: "1.0"\ncontent: |-\n  Use concise wording.\n',
        encoding="utf-8",
    )
    additional = load_style_profile("concise-tech", additional_directory)
    assignment = "Topic: Keep this brief first.\nRequirement: Preserve its line number."
    effective = add_style_profile_to_assignment(assignment, additional)

    assert effective.startswith(assignment + "\n\n---\n")
    assert "assignment above remains authoritative" in effective
    assert "Profile ID: `concise-tech`" in effective
    assert "Version: `1.0`" in effective
    assert (
        f"Content SHA-256: `{hashlib.sha256(b'Use concise wording.').hexdigest()}`"
        in effective
    )
    assert "Requirement: Preserve its line number." in effective.splitlines()[1]


@pytest.mark.parametrize("profile_id", ["../outside", "a/b", ".", "unknown"])
def test_profile_selection_rejects_paths_and_unknown_ids(
    tmp_path: Path, profile_id: str
) -> None:
    directory = tmp_path / "style_profiles"
    directory.mkdir()

    with pytest.raises(StyleProfileLoadError):
        load_style_profile(profile_id, directory)


def test_profile_loader_rejects_ambiguous_invalid_and_oversized_files(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "style_profiles"
    directory.mkdir()
    invalid_definitions = {
        "duplicate": ('id: duplicate\nid: duplicate\nversion: "1.0"\ncontent: text\n'),
        "mismatched": 'id: other\nversion: "1.0"\ncontent: text\n',
        "bad-version": "id: bad-version\nversion: latest\ncontent: text\n",
        "invalid-unicode": 'id: invalid-unicode\nversion: "1.0"\ncontent: "\\ud800"\n',
        "extra-field": (
            'id: extra-field\nversion: "1.0"\ncontent: text\nurl: https://example.test\n'
        ),
    }
    for profile_id, contents in invalid_definitions.items():
        (directory / f"{profile_id}.yaml").write_text(contents, encoding="utf-8")
        with pytest.raises(StyleProfileLoadError):
            load_style_profile(profile_id, directory)

    (directory / "oversized-content.yaml").write_text(
        'id: oversized-content\nversion: "1.0"\ncontent: |\n  '
        + "x" * (MAX_STYLE_PROFILE_CONTENT_BYTES + 1),
        encoding="utf-8",
    )
    with pytest.raises(StyleProfileLoadError):
        load_style_profile("oversized-content", directory)

    (directory / "oversized-file.yaml").write_bytes(
        b" " * (MAX_STYLE_PROFILE_FILE_BYTES + 1)
    )
    with pytest.raises(StyleProfileLoadError):
        load_style_profile("oversized-file", directory)


def test_profile_loader_rejects_symlinked_files_and_directories(tmp_path: Path) -> None:
    directory = tmp_path / "style_profiles"
    directory.mkdir()
    external = tmp_path / "external.yaml"
    external.write_text('id: linked\nversion: "1.0"\ncontent: text\n', encoding="utf-8")
    (directory / "linked.yaml").symlink_to(external)

    with pytest.raises(StyleProfileLoadError):
        load_style_profile("linked", directory)

    linked_directory = tmp_path / "linked-profiles"
    linked_directory.symlink_to(directory, target_is_directory=True)
    with pytest.raises(StyleProfileLoadError):
        load_style_profile("linked", linked_directory)


def test_profile_digest_matches_content_with_trailing_newline(tmp_path: Path) -> None:
    directory = tmp_path / "style_profiles"
    directory.mkdir()
    (directory / "trailing.yaml").write_text(
        'id: trailing\nversion: "1.0"\ncontent: |\n  Keep the final newline.\n',
        encoding="utf-8",
    )
    profile = load_style_profile("trailing", directory)
    effective = add_style_profile_to_assignment("Brief", profile)

    assert profile.definition.content == "Keep the final newline.\n"
    assert (
        profile.content_sha256
        == hashlib.sha256(b"Keep the final newline.\n").hexdigest()
    )
    assert effective.endswith(
        "Keep the final newline.\n"
        f"AI-OFFICE-STYLE-PROFILE-END trailing {profile.content_sha256}"
    )


def test_profile_file_can_be_changed_without_mutating_loaded_snapshot(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "style_profiles"
    directory.mkdir()
    path = directory / "stable.yaml"
    original = 'id: stable\nversion: "1.0"\ncontent: original\n'
    path.write_text(original, encoding="utf-8")
    profile = load_style_profile("stable", directory)
    first_assignment = add_style_profile_to_assignment("Brief", profile)

    path.write_text('id: stable\nversion: "2.0"\ncontent: changed\n', encoding="utf-8")
    path.unlink()
    second_assignment = add_style_profile_to_assignment("Brief", profile)

    assert first_assignment == second_assignment
    assert profile.definition.version == "1.0"
    assert profile.content_sha256 == hashlib.sha256(b"original").hexdigest()
