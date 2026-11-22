from __future__ import annotations

import httpx
import pytest

import app.lianlian as lianlian

from app.lianlian import (
    _api_headers,
    _validate_training_query,
    get_skill_manifest,
    get_training_records_page,
    validate_normalized_workouts,
)


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
            [{"id": "session-1", "started_at": "2026-11-21T18:00:00+08:00", "exercises": [{"sets": []}]}]
        )



def test_training_records_query_enforces_official_page_limit() -> None:
    _validate_training_query("20261101", "20261121", 1, 50)
    with pytest.raises(ValueError, match="page_size"):
        _validate_training_query("20261101", "20261121", 1, 51)
    with pytest.raises(ValueError, match="yyyyMMdd"):
        _validate_training_query("2026-11-01", "20261121", 1, 50)
    with pytest.raises(ValueError, match="不能晚于"):
        _validate_training_query("20261122", "20261121", 1, 50)


def test_api_headers_include_keepstrong_contract() -> None:
    headers = _api_headers("secret-token")
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["X-KeepStrong-Skill-Version"] == "1.29.0"
    assert headers["X-KeepStrong-Agent-Name"] == "ChatGPT"


@pytest.mark.asyncio
async def test_training_records_page_returns_raw_payload_without_writing(monkeypatch) -> None:
    import httpx
    import app.lianlian as lianlian

    monkeypatch.setattr(lianlian, "_token", lambda: "test-token")
    calls = []

    async def fake_request(token, **kwargs):
        calls.append((token, kwargs))
        return httpx.Response(
            200,
            json={"records": [{"id": "record-1"}], "page": 1, "hasMore": False},
            request=httpx.Request("GET", "https://lianlian.gzyunke.cn/api/v1/ai/training/records"),
        )

    monkeypatch.setattr(lianlian, "_get_training_records_request", fake_request)
    result = await get_training_records_page(
        start_date="20261101",
        end_date="20261121",
        page=1,
        page_size=50,
    )

    assert result["response"]["records"][0]["id"] == "record-1"
    assert result["response"]["hasMore"] is False
    assert calls[0][0] == "test-token"
    assert calls[0][1]["page"] == 1
    assert calls[0][1]["page_size"] == 50


@pytest.mark.asyncio
async def test_training_records_http_session_reuses_one_client(monkeypatch) -> None:
    clients = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = 0
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            self.calls += 1
            return httpx.Response(200)

    monkeypatch.setattr(lianlian.httpx, "AsyncClient", FakeClient)

    async with lianlian.training_records_http_session():
        for page in (1, 2):
            await lianlian._get_training_records_request(
                "test-token",
                start_date="20261101",
                end_date="20261122",
                page=page,
                page_size=50,
                skill_version="1.29.0",
            )

    assert len(clients) == 1
    assert clients[0].calls == 2
