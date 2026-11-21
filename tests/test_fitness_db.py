from __future__ import annotations

from datetime import datetime, timezone

from app.fitness_db import _as_datetime, _record_time, _source_id


def test_parses_unix_seconds_and_timezone_aware_iso() -> None:
    assert _as_datetime(1_795_221_108) == datetime.fromtimestamp(
        1_795_221_108, timezone.utc
    )
    parsed = _as_datetime("2026-11-21T08:31:48+08:00")
    assert parsed is not None
    assert parsed.isoformat() == "2026-11-21T08:31:48+08:00"


def test_parses_normalized_lianlian_started_at() -> None:
    parsed = _record_time({"started_at": "2026-11-21T18:30:00+08:00"})
    assert parsed is not None
    assert parsed.isoformat() == "2026-11-21T18:30:00+08:00"


def test_source_id_prefers_provider_identifier_and_hashes_fallback() -> None:
    assert _source_id({"workout_id": 123}, ("id", "workout_id")) == "123"
    first = _source_id({"date": "2026-11-21", "name": "胸"}, ("id",))
    second = _source_id({"date": "2026-11-21", "name": "胸"}, ("id",))
    assert first == second
    assert first.startswith("sha256:")
