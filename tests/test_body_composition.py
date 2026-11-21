from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from app.body_composition import get_body_composition_history

DAY = date(2026, 11, 21)
TZ = timezone(timedelta(hours=8))
MEASURED_AT = int(datetime(2026, 11, 21, 8, 31, tzinfo=TZ).timestamp())


class FakeClient:
    def __init__(self, pages: list[dict]):
        self.pages = list(pages)
        self.calls: list[tuple[str, str, dict]] = []

    async def _request(self, method: str, path: str, *, params: dict):
        self.calls.append((method, path, params))
        return self.pages.pop(0)


def _row(
    sid: str,
    *,
    deleted: bool = False,
    body_fat_rate: float = 21.3,
) -> dict:
    value = {
        "time": MEASURED_AT,
        "weight": 74.85,
        "bmi": 24.4,
        "body_fat_rate": body_fat_rate,
        "fat_mass": 15.95,
        "muscle_mass": 31.8,
        "muscle_rate": 42.5,
        "skeletal_muscle_mass": 18.6,
        "bone_mass": 2.8,
        "protein_mass": 10.2,
        "protein_rate": 13.6,
        "body_moisture_mass": 42.1,
        "moisture_rate": 56.3,
        "basal_metabolism": 1640,
        "visceral_fat": 8,
        "body_age": 23,
        "body_score": 78,
        "bpm": 68,
        "left_upper_limb_fat_mass": 1.2,
        "trunk_fat_mass": 7.3,
        "fat_control": 1.0,
        "whr": 0.85,
        "vendor_extension": "preserve-me",
        "data": json.dumps({"segmental": {"left_arm": 2.1}}),
    }
    return {
        "sid": sid,
        "time": MEASURED_AT,
        "zone_offset": 28800,
        "deleted": deleted,
        "value": json.dumps(value),
    }


async def test_reads_raw_body_composition_fields_and_units() -> None:
    client = FakeClient([
        {"code": 0, "result": {"data_list": [_row("scale-row-1")], "has_more": False}}
    ])

    result = await get_body_composition_history(client, "2026-11-21", days=1, relative_uid=42)

    assert result["count"] == 1
    assert result["composition_available"] is True
    assert result["data"][0]["weight"] == 74.85
    assert result["data"][0]["body_fat_rate"] == 21.3
    assert result["data"][0]["muscle_mass"] == 31.8
    assert result["data"][0]["skeletal_muscle_mass"] == 18.6
    assert result["data"][0]["visceral_fat"] == 8
    assert result["data"][0]["left_upper_limb_fat_mass"] == 1.2
    assert result["data"][0]["trunk_fat_mass"] == 7.3
    assert result["data"][0]["fat_control"] == 1.0
    assert result["data"][0]["whr"] == 0.85
    assert result["data"][0]["raw_fields"]["vendor_extension"] == "preserve-me"
    assert result["data"][0]["additional_data"] == {
        "segmental": {"left_arm": 2.1}
    }
    assert result["units"]["body_fat_rate"] == "%"
    assert result["units"]["muscle_mass"] == "kg"
    method, path, params = client.calls[0]
    assert method == "GET"
    assert path == "/app/v1/data/get_fitness_data_by_time"
    assert params["key"] == "weight"
    assert params["relative_uid"] == 42
    assert params["limit"] == 200
    assert params["start_time"] < params["end_time"]


async def test_paginates_deduplicates_and_skips_deleted_scale_rows() -> None:
    client = FakeClient([
        {
            "code": 0,
            "result": {
                "data_list": [_row("scale-row-1"), _row("deleted", deleted=True)],
                "has_more": True,
                "next_key": "cursor-1",
            },
        },
        {
            "code": 0,
            "result": {
                "data_list": [_row("scale-row-1"), _row("scale-row-2", body_fat_rate=20.9)],
                "has_more": False,
            },
        },
    ])

    result = await get_body_composition_history(client, "2026-11-21", days=1, relative_uid=42)

    assert result["count"] == 2
    assert {row["body_fat_rate"] for row in result["data"]} == {21.3, 20.9}
    assert len(client.calls) == 2
    assert client.calls[1][2]["next_key"] == "cursor-1"


async def test_preserves_missing_composition_as_unavailable_not_zero() -> None:
    row = {
        "sid": "weight-only",
        "time": MEASURED_AT,
        "zone_offset": 28800,
        "value": json.dumps({"weight": 74.85, "bmi": 24.4}),
    }
    client = FakeClient([
        {"code": 0, "result": {"data_list": [row], "has_more": False}}
    ])

    result = await get_body_composition_history(client, "2026-11-21", days=1, relative_uid=42)

    assert result["composition_available"] is False
    assert "body_fat_rate" not in result["data"][0]
    assert "muscle_mass" not in result["data"][0]
