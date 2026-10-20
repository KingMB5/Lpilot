"""Pure level assessment: load /data/assessment.json, score answers -> level.

Rules (deterministic):
- explicit expert skip -> expert
- weighted total >= expert_min_total AND top deployment experience -> expert
- anything else -> beginner (teach while working)
"""

from __future__ import annotations

import functools
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "assessment.json"


@functools.lru_cache(maxsize=1)
def load(path: str | None = None) -> dict:
    p = pathlib.Path(path) if path else DATA_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def max_total(data: dict) -> int:
    return sum(int(q["weight"]) * max(o["score"] for o in q["options"]) for q in data["questions"])


def assess(answers: dict[str, str], skip_expert: bool = False, data: dict | None = None) -> dict:
    d = data if data is not None else load()
    if skip_expert:
        return {
            "level": "expert",
            "total": max_total(d),
            "max_total": max_total(d),
            "reason": "expert_skip",
        }

    total = 0
    top_experience = True
    for q in d["questions"]:
        value = answers.get(q["id"])
        option = next((o for o in q["options"] if o["value"] == value), None)
        if option is None:
            raise ValueError(f"missing or invalid answer for {q['id']}")
        total += int(q["weight"]) * int(option["score"])
        if q["id"] == "q1" and int(option["score"]) < 2:
            top_experience = False

    scoring = d.get("scoring", {})
    is_expert = total >= int(scoring.get("expert_min_total", 6)) and (
        top_experience or not scoring.get("require_top_experience", False)
    )
    return {
        "level": "expert" if is_expert else "beginner",
        "total": total,
        "max_total": max_total(d),
        "reason": "scored",
    }
