from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.fitness_db import (
    SCHEMA_SQL,
    FitnessDatabase,
    _as_date,
    _as_datetime,
    _record_time,
    _source_id,
)


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
    assert "latest_health AS" in SCHEMA_SQL


def test_metric_summary_uses_first_last_and_recent_seven_values() -> None:
    rows = [
        {"weight_kg": 80.0},
        {"weight_kg": 79.5},
        {"weight_kg": 79.0},
        {"weight_kg": 78.5},
        {"weight_kg": 78.0},
        {"weight_kg": 77.5},
        {"weight_kg": 77.0},
        {"weight_kg": 76.5},
    ]
    summary = FitnessDatabase._metric_summary(rows, "weight_kg")
    assert summary["first"] == 80.0
    assert summary["last"] == 76.5
    assert summary["change"] == -3.5
    assert summary["seven_day_average"] == pytest.approx(78.0)



def test_strength_analytics_only_counts_explicitly_completed_sets() -> None:
    assert "st.is_completed IS DISTINCT FROM FALSE" not in SCHEMA_SQL
    assert "sum(st.reps) FILTER (WHERE st.is_completed IS TRUE)" in SCHEMA_SQL
    assert "AS unknown_set_count" in SCHEMA_SQL
    assert "AS incomplete_set_count" in SCHEMA_SQL



def test_segmental_trends_keep_missing_values_missing_and_smooth_observations() -> None:
    rows = [
        {
            "measured_date": date(2026, 11, 1),
            "segmental_metrics": {"trunk_fat_mass": "8.0"},
        },
        {
            "measured_date": date(2026, 11, 3),
            "segmental_metrics": {"trunk_fat_mass": 7.6},
        },
        {
            "measured_date": date(2026, 11, 5),
            "segmental_metrics": {},
        },
        {
            "measured_date": date(2026, 11, 7),
            "segmental_metrics": {"trunk_fat_mass": 7.2},
        },
    ]
    trends = FitnessDatabase._build_segmental_trends(
        rows,
        metric="fat_mass",
        parts=["trunk"],
        smoothing_window=2,
    )
    trunk = trends["trunk"]
    assert trunk["measurement_count"] == 3
    assert trunk["first"]["value_kg"] == 8.0
    assert trunk["last"]["value_kg"] == 7.2
    assert trunk["change_kg"] == pytest.approx(-0.8)
    assert trunk["series"][1]["moving_average_kg"] == pytest.approx(7.8)
    assert trunk["series"][2]["moving_average_kg"] == pytest.approx(7.4)
    assert all(point["date"] != "2026-11-05" for point in trunk["series"])


def test_segmental_trends_support_lower_limb_not_thigh_alias() -> None:
    rows = [
        {
            "measured_date": date(2026, 11, 1),
            "segmental_metrics": {"left_lower_limb_fat_mass": 4.1},
        }
    ]
    trends = FitnessDatabase._build_segmental_trends(
        rows,
        metric="fat_mass",
        parts=["left_lower_limb"],
        smoothing_window=1,
    )
    assert trends["left_lower_limb"]["last"]["value_kg"] == 4.1



@pytest.mark.asyncio
async def test_segmental_trends_reject_invalid_metric_before_database_access() -> None:
    with pytest.raises(ValueError, match="metric"):
        await FitnessDatabase().segmental_body_trends(metric="thigh")


@pytest.mark.asyncio
async def test_segmental_trends_reject_invalid_part_before_database_access() -> None:
    with pytest.raises(ValueError, match="parts"):
        await FitnessDatabase().segmental_body_trends(parts=["thigh"])


@pytest.mark.asyncio
async def test_segmental_trends_reject_invalid_smoothing_window_before_database_access() -> None:
    with pytest.raises(ValueError, match="smoothing_window"):
        await FitnessDatabase().segmental_body_trends(smoothing_window=0)



def test_strength_views_exclude_planned_and_non_strength_sessions() -> None:
    assert "s.status='completed' AND s.record_type='strength'" in SCHEMA_SQL
    assert (
        "FILTER (WHERE source='lianlian' AND status='completed' "
        "AND record_type='strength')" in SCHEMA_SQL
    )
