import json
from datetime import date

import pytest
from pydantic import BaseModel

from app.health import HealthBridge


class Row(BaseModel):
    value: int


class FakeClient:
    async def get_steps(self, relative_uid, query_date, *, days):
        assert relative_uid == 42
        assert days == 3
        return [Row(value=8000)]


class SnapshotClient:
    async def _request(self, method, path, *, params):
        assert method == "GET"
        assert path == "/app/v1/data/get_fitness_data_by_time"
        assert params["key"] == "weight"
        assert "relative_uid" not in params
        return {"code": 0, "result": {"data_list": [], "has_more": False}}

    async def get_heart_rate(self, relative_uid, query_date, *, days):
        return []

    async def get_sleep(self, relative_uid, query_date, *, days):
        return []

    async def get_steps(self, relative_uid, query_date, *, days):
        assert relative_uid == 42
        assert query_date == date(2026, 8, 13)
        assert days == 1
        return [Row(value=8000)]

    async def get_calories_history(self, relative_uid, query_date, *, days):
        return []

    async def get_spo2_history(self, relative_uid, query_date, *, days):
        return []

    async def get_intensity_history(self, relative_uid, query_date, *, days):
        return []

    async def get_valid_stand_history(self, relative_uid, query_date, *, days):
        return []

    async def get_weight_history(self, relative_uid, query_date, *, days):
        return []

    async def get_blood_pressure_history(self, relative_uid, query_date, *, days):
        return []



class RawWeightClient:
    async def _request(self, method, path, *, params):
        assert method == "GET"
        assert path == "/app/v1/data/get_fitness_data_by_time"
        assert params["key"] == "weight"
        assert params["reverse"] is True
        assert params["startTime"] < params["endTime"]
        assert params["endTime"] - params["startTime"] == 86_400_000
        assert "relative_uid" not in params
        return {
            "code": 0,
            "result": {
                "data_list": [
                    {
                        "sid": "private-scale-row",
                        "time": 1786579200,
                        "zone_offset": 28800,
                        "value": json.dumps({
                            "weight": 74.8,
                            "bmi": 24.4,
                            "body_fat_rate": 20.8,
                            "muscle_mass": 31.6,
                        }),
                    }
                ],
                "has_more": False,
            },
        }


def _bridge_token(uid: str = "42") -> str:
    return f'{{"user_id":"{uid}"}}'


async def test_resolve_own_account_and_read_history() -> None:
    bridge = HealthBridge(_bridge_token())
    bridge._client = FakeClient()

    result = await bridge.history(None, "steps", "2026-08-12", 3)

    assert result["relative"]["relative_note"] == "本人"
    assert result["relative"]["relative_uid"] == 42
    assert result["data"] == [{"value": 8000}]


async def test_status_never_exposes_token() -> None:
    bridge = HealthBridge()
    status = bridge.safe_status()
    assert status["configured"] is False
    assert "token" not in status


async def test_list_relatives_returns_authenticated_own_account_without_cloud_lookup() -> None:
    bridge = HealthBridge(_bridge_token())

    members = await bridge.list_relatives()

    assert members == [
        {
            "relative_uid": 42,
            "relative_note": "本人",
            "relative_icon": "",
            "latest_data_time": 0,
            "latest_abnormal_record_time": 0,
            "source_tag": 0,
        }
    ]


async def test_missing_own_account_uid_returns_safe_error() -> None:
    bridge = HealthBridge(_bridge_token(""))

    with pytest.raises(RuntimeError, match="UID"):
        await bridge.list_relatives()


async def test_snapshot_uses_xiaomi_timezone_and_own_account_uid() -> None:
    bridge = HealthBridge(_bridge_token())
    bridge._client = SnapshotClient()

    result = await bridge.snapshot("本人", "2026-08-13")

    assert result["daily_summary"]["date"] == "2026-08-13"
    assert result["daily_summary"]["steps"] == {"value": 8000}
    assert result["available_data_types"] == ["steps"]
    assert result["metrics"]["steps"] == [{"value": 8000}]

async def test_weight_history_keeps_raw_body_composition_fields() -> None:
    bridge = HealthBridge(_bridge_token())
    bridge._client = RawWeightClient()

    result = await bridge.history(None, "weight", "2026-08-13", 1)

    assert result["query_date"] == "2026-08-13"
    assert result["composition_available"] is True
    assert result["data"][0]["weight"] == 74.8
    assert result["data"][0]["body_fat_rate"] == 20.8
    assert result["data"][0]["muscle_mass"] == 31.6
