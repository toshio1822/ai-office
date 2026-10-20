"""Canned offline review examples exercise the existing mechanical contract.

These fixtures are not model responses and do not evaluate model judgment.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from ai_office.engine.workflow_bounded_job import _review_verdict


def _review_input(assignment: str, artifact: str) -> str:
    content = artifact.encode("utf-8")
    return json.dumps(
        {
            "assignment": assignment,
            "source_artifact": {
                "artifact_id": "offline-example",
                "content": artifact,
                "content_length": len(content),
                "content_sha256": hashlib.sha256(content).hexdigest(),
            },
        },
        ensure_ascii=False,
    )


def _review_result(
    *,
    verdict: str,
    assignment: str,
    line: int,
    artifact_quote: str,
    requirement: str,
    assessment: str,
) -> str:
    cited_line = assignment.splitlines()[line - 1]
    if verdict == "PASS":
        return (
            "Verdict: PASS\n"
            "Unresolved material issues: none\n\n"
            "### Check 1\n"
            f"Requirement: {requirement}\n"
            f"Artifact location: `{artifact_quote}`\n"
            f"Evidence source/reference: assignment:L{line} `{cited_line}`\n"
            f"Assessment: {assessment}"
        )
    return (
        "Verdict: CHANGES_REQUIRED\n\n"
        "### Finding 1\n"
        f"Requirement: {requirement}\n"
        f"Artifact location: `{artifact_quote}`\n"
        f"Evidence source/reference: assignment:L{line} `{cited_line}`\n"
        "Impact: The claim or omission is material to the supplied requirement.\n"
        f"Requested correction: {assessment}"
    )


CASES = (
    pytest.param(
        "The author has 20 years of software-engineering experience.",
        "私はソフトウェアエンジニアとして20年働いてきた。",
        1,
        "PASS",
        "The stated author experience.",
        "The brief explicitly supplies this experience.",
        id="supported-personal-experience",
    ),
    pytest.param(
        "Explain the method of classifying unknowns into four categories.",
        "不明点を四つの分類に分けて考える方法を説明する。",
        1,
        "PASS",
        "Explain the requested method.",
        "The artifact explains the method without adding author biography.",
        id="general-method-not-misattributed",
    ),
    pytest.param(
        "Explain the method of classifying unknowns into four categories.",
        "私は、不明点をざっくりと「技術」「仕様」「人」「リスク」に分けるようにしている。",
        1,
        "CHANGES_REQUIRED",
        "Explain the method of classifying unknowns.",
        "The source supports a method explanation, not an author habit; "
        "generalize or confirm the claim.",
        id="issue-764-unsupported-habit",
    ),
    pytest.param(
        "The author may use a clearly labeled hypothetical example.",
        "仮想例：あるチームが不明点を四つに分類して考える。",
        1,
        "PASS",
        "Use a labeled hypothetical, if useful.",
        "The example is explicitly hypothetical rather than attributed to the author.",
        id="hypothetical-stays-hypothetical",
    ),
    pytest.param(
        "The author may use a clearly labeled hypothetical example.",
        "私は以前、顧客チームにこの方法を導入して成功させた。",
        1,
        "CHANGES_REQUIRED",
        "Use a labeled hypothetical, if useful.",
        "The artifact changes the hypothetical into an unsupported personal "
        "outcome; remove or confirm it.",
        id="hypothetical-not-personal-history",
    ),
    pytest.param(
        "The source reports that downtime declined after rollout, but gives "
        "no percentage.",
        "新しい手順で障害時間を32%削減した。",
        1,
        "CHANGES_REQUIRED",
        "Report only the result and precision supported by the source.",
        "The percentage is not supplied; remove it or cite a source that states it.",
        id="unsupported-number-in-press-release",
    ),
    pytest.param(
        "The guide must tell readers the first action to take.",
        "## Start\n\nまず内容を確認してください。",
        1,
        "CHANGES_REQUIRED",
        "Include a concrete first action.",
        "Replace the vague instruction with the specific action required by the brief.",
        id="missing-reader-action",
    ),
)


@pytest.mark.parametrize(
    (
        "assignment",
        "artifact",
        "line",
        "verdict",
        "requirement",
        "assessment",
    ),
    CASES,
)
def test_offline_examples_are_representable_by_the_existing_review_contract(
    assignment: str,
    artifact: str,
    line: int,
    verdict: str,
    requirement: str,
    assessment: str,
) -> None:
    review = _review_result(
        verdict=verdict,
        assignment=assignment,
        line=line,
        artifact_quote=artifact.splitlines()[-1],
        requirement=requirement,
        assessment=assessment,
    )

    assert (
        _review_verdict(
            review,
            review_input=_review_input(assignment, artifact),
        )
        == verdict
    )
