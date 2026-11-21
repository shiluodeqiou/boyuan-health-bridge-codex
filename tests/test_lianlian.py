from __future__ import annotations

import pytest

from app.lianlian import get_skill_manifest, validate_normalized_workouts


def test_requires_lianlian_token_without_exposing_secret(monkeypatch) -> None:
    monkeypatch.delenv("LIANLIAN_API_TOKEN", raising=False)

    import asyncio
    result = asyncio.run(get_skill_manifest())

    assert result["configured"] is False
    assert "Token" in result["message"]


def test_validates_normalized_lianlian_workout_contract() -> None:
    records = [
        {
            "id": "session-1",
            "started_at": "2026-11-21T18:00:00+08:00",
            "name": "力量训练",
            "exercises": [
                {
                    "name": "坐姿器械推胸",
                    "body_part": "胸",
                    "sets": [
                        {"weight_kg": 30, "reps": 12},
                        {"weight_kg": 35, "reps": 10},
                    ],
                }
            ],
        }
    ]

    assert validate_normalized_workouts(records) == records


def test_rejects_lianlian_workout_without_id() -> None:
    with pytest.raises(ValueError, match="缺少 id"):
        validate_normalized_workouts([{"started_at": "2026-11-21", "exercises": []}])


def test_rejects_lianlian_exercise_without_name() -> None:
    with pytest.raises(ValueError, match="缺少名称"):
        validate_normalized_workouts(
            [{"id": "session-1", "exercises": [{"sets": []}]}]
        )
