from __future__ import annotations

import json

import pytest
from app.sports import _fds_ref, get_sport_detail, get_sport_records


START = 1_795_190_400  # 2026-11-21 00:00:00 Asia/Shanghai


def _row(*, proto_type: int = 30) -> dict:
    value = {
        "time": START,
        "start_time": START,
        "end_time": START + 3600,
        "timezone": 32,
        "sport_type": 300,
        "proto_type": proto_type,
        "duration": 3500,
        "calories": 260,
        "total_cal": 310,
        "avg_hrm": 145,
        "max_hrm": 168,
        "min_hrm": 82,
    }
    return {
        "sid": "private-source-id",
        "key": "climbing_machine",
        "category": "climbing",
        "time": START,
        "zone_offset": 28800,
        "value": json.dumps(value),
    }


class FakeClient:
    def __init__(self, row: dict):
        self.row = row
        self.calls: list[tuple[str, str, dict]] = []

    async def _request(self, method: str, path: str, *, params: dict):
        self.calls.append((method, path, params))
        return {
            "code": 0,
            "result": {
                "sport_records": [self.row],
                "has_more": False,
                "next_key": "",
            },
        }


async def test_reads_and_normalizes_individual_sport_records() -> None:
    client = FakeClient(_row())

    result = await get_sport_records(client, "2026-11-21", 1)

    assert result["count"] == 1
    workout = result["records"][0]
    assert workout["sport"] == "爬楼机"
    assert workout["duration_seconds"] == 3500
    assert workout["active_calories_kcal"] == 260
    assert workout["average_heart_rate_bpm"] == 145
    assert "sid" not in workout
    assert "private-source-id" not in json.dumps(result)
    assert client.calls[0][0] == "POST"
    assert client.calls[0][1] == "/app/v1/data/get_sport_records_by_time"
    assert client.calls[0][2]["reverse"] is True


async def test_non_fds_sport_detail_returns_summary_without_fabricating_samples() -> None:
    client = FakeClient(_row(proto_type=30))

    result = await get_sport_detail(client, START)

    assert result["detail_supported"] is False
    assert result["workout"]["sport"] == "爬楼机"
    assert result["samples"] == []
    assert result["track"] is None
    assert any("没有已验证" in note for note in result["notes"])


def test_fds_file_reference_matches_verified_identifier_vector() -> None:
    timestamp, suffix = _fds_ref(
        "1234567890",
        {"time": 1_700_000_000, "timezone": 32, "proto_type": 22},
        0,
    )

    assert timestamp == 1_700_000_000
    assert suffix == "APFTZSDY_AbMHrLpPVPVar8M7sGu79sqAPpo"


async def test_rejects_invalid_history_window() -> None:
    with pytest.raises(ValueError, match="days"):
        await get_sport_records(FakeClient(_row()), "2026-11-21", 31)
