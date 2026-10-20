"""T5 — level assessment scoring."""

from __future__ import annotations

import pytest

from agent.assessment import assess, load, max_total

DATA = load()


def test_load_shape():
    assert len(DATA["questions"]) == 4
    assert DATA["expert_skip"]["value"] == "skip_expert"
    for q in DATA["questions"]:
        assert {o["score"] for o in q["options"]} == {0, 1, 2}


def test_max_total():
    assert max_total(DATA) == 10  # q1 weight2*2 + 3*(1*2)


def test_mehran_profile_is_beginner():
    # experienced programmer, never deployed, knows the concepts
    res = assess({"q1": "never", "q2": "used", "q3": "know", "q4": "know"})
    assert res["level"] == "beginner"


def test_all_top_scores_is_expert():
    res = assess({"q1": "solo", "q2": "used", "q3": "know", "q4": "know"})
    assert res["level"] == "expert"


def test_solo_deploy_but_weak_concepts_is_beginner():
    res = assess({"q1": "solo", "q2": "unknown", "q3": "guess", "q4": "unknown"})
    assert res["level"] == "beginner"


def test_guided_deploy_with_knowledge_is_beginner():
    res = assess({"q1": "guided", "q2": "used", "q3": "know", "q4": "know"})
    assert res["total"] >= 6
    assert res["level"] == "beginner"  # require_top_experience gate


def test_expert_skip():
    res = assess({}, skip_expert=True)
    assert res["level"] == "expert" and res["reason"] == "expert_skip"


def test_missing_answer_raises():
    with pytest.raises(ValueError):
        assess({"q1": "solo"})


def test_invalid_value_raises():
    with pytest.raises(ValueError):
        assess({"q1": "solo", "q2": "nope", "q3": "know", "q4": "know"})
