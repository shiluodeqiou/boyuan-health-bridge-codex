from __future__ import annotations

from datetime import datetime, timezone

from app.fitness_db import SCHEMA_SQL, _as_date, _as_datetime, _record_time, _source_id


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



def test_parses_keepstrong_daystr_and_local_calendar_date() -> None:
    assert _as_date("20261121").isoformat() == "2026-11-21"
    assert _as_date("2026-11-21T23:30:00+08:00").isoformat() == "2026-11-21"
    assert _as_date(1795221108).isoformat() == "2026-11-21"


def test_unified_schema_contains_body_composition_and_analytics_views() -> None:
    assert "visceral_fat_level" in SCHEMA_SQL
    assert "segmental_metrics" in SCHEMA_SQL
    assert "training_capacity" in SCHEMA_SQL
    assert "provider_set_id" in SCHEMA_SQL
    assert "fitness_daily_overview" in SCHEMA_SQL
    assert "fitness_strength_progression" in SCHEMA_SQL
    assert "uq_fitness_health_metrics_day" in SCHEMA_SQL
    assert "ON CONFLICT (source, metric, recorded_date)" in SCHEMA_SQL
    assert "latest_health AS" in SCHEMA_SQL
