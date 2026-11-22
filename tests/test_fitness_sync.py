from __future__ import annotations

import asyncio

import pytest

import app.fitness_sync as fitness_sync
from app.fitness_sync import (
    _duration_seconds,
    fetch_all_lianlian_records,
    normalize_lianlian_record,
    sync_unified_fitness_data,
    sync_xiaomi_to_database,
)


def test_duration_parser_supports_mm_ss_and_hh_mm_ss() -> None:
    assert _duration_seconds("01:00") == 60
    assert _duration_seconds("01:02:03") == 3723
    assert _duration_seconds("bad") is None
    assert _duration_seconds("") is None


def test_normalizer_preserves_exact_source_json_and_maps_known_fields() -> None:
    raw = {
        "id": "normal:record-1",
        "name": "胸部训练",
        "dayStr": "20261120",
        "recordType": "strength",
        "status": "completed",
        "trainingType": "normal",
        "durationSeconds": 3312,
        "calorie": 295,
        "capacity": 5985,
        "avgHeartRate": 0,
        "actions": [
            {
                "name": "器械推胸",
                "actionId": "act-1",
                "weightUnit": "kg",
                "capacity": 2025,
                "restSeconds": 90,
                "weightTag": {},
                "combActions": [],
                "groups": [
                    {
                        "_id": "set-1",
                        "weight": "35",
                        "reps": "15",
                        "isDone": True,
                        "doneTs": 1795000000,
                        "realRestSeconds": 65,
                        "isNewRM1Record": True,
                        "rm1Weight": 52.5,
                        "combGroups": [],
                    },
                    {"_id": "set-2", "weight": "", "reps": "", "combGroups": []},
                ],
            },
            {
                "name": "俯卧撑",
                "actionId": "act-2",
                "groups": [{"_id": "set-3", "weight": "0", "reps": "10", "combGroups": []}],
            },
        ],
    }

    normalized = normalize_lianlian_record(
        raw,
        page=1,
        skill_version="1.29.0",
        start_date="20261023",
        end_date="20261121",
        page_size=50,
    )

    assert normalized["raw_data"] == raw
    assert normalized["date"] == "2026-11-20"
    assert normalized["started_at"] is None
    assert normalized["duration_seconds"] == 3312
    assert normalized["calories_kcal"] == 295
    assert normalized["training_capacity"] == 5985

    press = normalized["exercises"][0]
    assert press["raw_data"] == raw["actions"][0]
    assert press["action_id"] == "act-1"
    assert press["planned_rest_seconds"] == 90
    assert press["sets"][0]["raw_data"] == raw["actions"][0]["groups"][0]
    assert press["sets"][0]["weight_kg"] == 35
    assert press["sets"][0]["reps"] == 15
    assert press["sets"][0]["rest_seconds"] == 65
    assert press["sets"][0]["is_completed"] is True
    assert press["sets"][0]["rm1_weight_kg"] == 52.5
    assert press["sets"][1]["weight_kg"] is None
    assert press["sets"][1]["reps"] is None

    bodyweight = normalized["exercises"][1]["sets"][0]
    assert bodyweight["weight_value"] == 0
    assert bodyweight["weight_unit"] is None
    assert bodyweight["weight_kg"] is None


@pytest.mark.asyncio
async def test_fetch_all_lianlian_records_follows_pages_and_deduplicates(monkeypatch) -> None:
    pages = {
        1: {
            "provider": "keepstrong",
            "skill_version": "1.29.0",
            "response": {
                "list": [
                    {"id": "normal:1", "dayStr": "20261120", "actions": []},
                    {"id": "normal:2", "dayStr": "20261119", "actions": []},
                ],
                "page": 1,
                "pageSize": 2,
                "hasMore": True,
            },
        },
        2: {
            "provider": "keepstrong",
            "skill_version": "1.29.0",
            "response": {
                "list": [
                    {"id": "normal:2", "dayStr": "20261119", "actions": []},
                    {"id": "normal:3", "dayStr": "20261118", "actions": []},
                ],
                "page": 2,
                "pageSize": 2,
                "hasMore": False,
            },
        },
    }
    calls = []

    async def fake_page(**kwargs):
        calls.append(kwargs)
        return pages[kwargs["page"]]

    monkeypatch.setattr(fitness_sync, "get_training_records_page", fake_page)
    records, metadata = await fetch_all_lianlian_records(
        start_date="20261023",
        end_date="20261121",
        page_size=2,
    )

    assert [r["id"] for r in records] == ["normal:1", "normal:2", "normal:3"]
    assert len(calls) == 2
    assert metadata["pages_fetched"] == 2
    assert metadata["has_more"] is False
    assert metadata["record_count"] == 3


@pytest.mark.asyncio
async def test_fetch_all_lianlian_records_rejects_unknown_pagination_state(monkeypatch) -> None:
    async def fake_page(**kwargs):
        return {
            "skill_version": "1.29.0",
            "response": {"list": [], "page": 1, "pageSize": 50},
        }

    monkeypatch.setattr(fitness_sync, "get_training_records_page", fake_page)
    with pytest.raises(RuntimeError, match="hasMore"):
        await fetch_all_lianlian_records(
            start_date="20261023",
            end_date="20261121",
            page_size=50,
        )



@pytest.mark.asyncio
async def test_xiaomi_sync_persists_sport_detail_errors_as_partial() -> None:
    class FakeBridge:
        async def body_composition_history(self, **kwargs):
            return {"data": [], "history_complete": True, "pages_fetched": 1}

        async def sport_records(self, **kwargs):
            return {
                "records": [{"proto_type": 8, "workout_time": 123456}],
                "count": 1,
            }

        async def history(self, relative, metric, query_date, days):
            return {"data": []}

        async def sport_detail(self, workout_time):
            raise RuntimeError("sample unavailable")

    class FakeDatabase:
        def __init__(self):
            self.store_kwargs = None

        async def store_xiaomi(self, **kwargs):
            self.store_kwargs = kwargs
            return {"source": "xiaomi"}

    database = FakeDatabase()
    result = await sync_xiaomi_to_database(
        FakeBridge(),
        database,
        days=1,
        include_sport_details=True,
    )

    assert database.store_kwargs["sport_detail_errors"] == {
        "123456": "RuntimeError"
    }
    assert result["status"] == "partial"
    assert result["sport_detail_errors"] == {"123456": "RuntimeError"}


@pytest.mark.asyncio
async def test_unified_sync_runs_sources_concurrently(monkeypatch) -> None:
    events: list[str] = []

    async def fake_xiaomi(*args, **kwargs):
        events.append("xiaomi:start")
        await asyncio.sleep(0)
        events.append("xiaomi:end")
        return {"status": "success"}

    async def fake_lianlian(*args, **kwargs):
        events.append("lianlian:start")
        await asyncio.sleep(0)
        events.append("lianlian:end")
        return {"status": "success"}

    monkeypatch.setattr(fitness_sync, "sync_xiaomi_to_database", fake_xiaomi)
    monkeypatch.setattr(fitness_sync, "sync_lianlian_to_database", fake_lianlian)

    result = await sync_unified_fitness_data(object(), object(), days=30)

    assert events == [
        "xiaomi:start", "lianlian:start", "xiaomi:end", "lianlian:end"
    ]
    assert result["status"] == "success"
    assert set(result["sources"]) == {"xiaomi", "lianlian"}


@pytest.mark.asyncio
async def test_xiaomi_sync_marks_sport_pagination_warnings_as_partial() -> None:
    class FakeBridge:
        async def body_composition_history(self, **kwargs):
            return {"data": [], "history_complete": True, "pages_fetched": 1}

        async def sport_records(self, **kwargs):
            return {
                "records": [],
                "count": 0,
                "notes": ["运动记录超过单次读取页数上限"],
            }

        async def history(self, relative, metric, query_date, days):
            return {"data": []}

    class FakeDatabase:
        async def store_xiaomi(self, **kwargs):
            return {"source": "xiaomi", "status": "success"}

    result = await sync_xiaomi_to_database(
        FakeBridge(), FakeDatabase(), days=1, include_sport_details=False
    )

    assert result["status"] == "partial"
    assert result["sport_record_notes"] == ["运动记录超过单次读取页数上限"]
