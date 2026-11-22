from __future__ import annotations

import inspect
from datetime import date, datetime, timezone

import pytest

from app.fitness_db import (
    SCHEMA_MIGRATIONS,
    SCHEMA_SQL,
    FitnessDatabase,
    _as_date,
    _as_datetime,
    _record_time,
    _source_id,
    _workout_sample_rows,
    _workout_zone_durations,
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



@pytest.mark.asyncio
async def test_strength_progression_rejects_blank_exercise_name_before_database_access() -> None:
    with pytest.raises(ValueError, match="exercise_name"):
        await FitnessDatabase().strength_progression("   ")


@pytest.mark.asyncio
async def test_cardio_analysis_rejects_invalid_period_before_database_access() -> None:
    with pytest.raises(ValueError, match="days"):
        await FitnessDatabase().cardio_analysis(days=0)



def test_fat_loss_summary_session_cte_projects_filter_columns() -> None:
    source = inspect.getsource(FitnessDatabase.fat_loss_summary)
    sessions_cte = source.split("WITH sessions AS (", 1)[1].split(
        "weekly_sessions AS", 1
    )[0]
    assert "source, modality, status, record_type" in sessions_cte



def test_workout_sample_rows_prepare_batch_and_preserve_sample_data() -> None:
    sample = {"time": 1_795_314_930, "heart_rate_bpm": 145, "calories_raw": 2}
    rows = _workout_sample_rows(
        "xiaomi",
        "1795314930",
        {"samples": [sample, None, {"time": 1_795_314_934, "heart_rate_bpm": 0}]},
    )

    assert len(rows) == 2
    assert rows[0][0:3] == ("xiaomi", "1795314930", 0)
    assert rows[0][3] == datetime.fromtimestamp(1_795_314_930, timezone.utc)
    assert rows[0][4] == 145.0
    assert rows[0][5].obj == sample
    assert rows[1][2] == 2
    assert rows[1][4] == 0.0


def test_workout_sample_rows_ignore_missing_or_invalid_samples() -> None:
    assert _workout_sample_rows("xiaomi", "workout-1", {}) == []
    assert _workout_sample_rows("xiaomi", "workout-1", {"samples": None}) == []
    assert _workout_sample_rows("xiaomi", "workout-1", {"samples": [None, "bad"]}) == []



def test_workout_zone_durations_read_device_summary_without_estimating() -> None:
    detail = {
        "workout": {
            "summary": {
                "hrm_warm_up_duration": 387,
                "hrm_fat_burning_duration": 886,
                "hrm_aerobic_duration": 1487,
                "hrm_anaerobic_duration": 1099,
                "hrm_extreme_duration": 0,
            }
        },
        "samples": [{"time": 1_795_314_930, "heart_rate_bpm": 145}],
    }
    assert _workout_zone_durations(detail) == {
        "hrm_warmup_duration_seconds": 387,
        "hrm_fat_burning_duration_seconds": 886,
        "hrm_aerobic_duration_seconds": 1487,
        "hrm_anaerobic_duration_seconds": 1099,
        "hrm_extreme_duration_seconds": 0,
    }


def test_workout_zone_durations_accept_top_level_summary_and_preserve_missing() -> None:
    assert _workout_zone_durations({
        "summary": {"hrm_aerobic_duration": "120"}
    }) == {
        "hrm_warmup_duration_seconds": None,
        "hrm_fat_burning_duration_seconds": None,
        "hrm_aerobic_duration_seconds": 120,
        "hrm_anaerobic_duration_seconds": None,
        "hrm_extreme_duration_seconds": None,
    }
    assert _workout_zone_durations({"samples": []}) is None


def test_workout_session_schema_persists_device_heart_rate_zones() -> None:
    for column in (
        "hrm_warmup_duration_seconds",
        "hrm_fat_burning_duration_seconds",
        "hrm_aerobic_duration_seconds",
        "hrm_anaerobic_duration_seconds",
        "hrm_extreme_duration_seconds",
    ):
        assert column in SCHEMA_SQL


def test_schema_migrations_are_versioned_and_serialized() -> None:
    versions = [version for version, _ in SCHEMA_MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions and versions[0] == 1
    source = inspect.getsource(FitnessDatabase.initialize)
    assert "fitness_schema_migrations" in source
    assert "pg_advisory_xact_lock" in source


def test_database_pool_is_bounded_and_reused() -> None:
    source = inspect.getsource(FitnessDatabase._connect)
    assert "AsyncConnectionPool" in source
    assert "max_size=4" in source
    assert "self._pool.connection()" in source


def test_upserts_skip_unchanged_rows_and_keepstrong_writes_are_batched() -> None:
    xiaomi = inspect.getsource(FitnessDatabase.store_xiaomi)
    keepstrong = inspect.getsource(FitnessDatabase.store_lianlian)
    assert xiaomi.count("IS DISTINCT FROM") >= 4
    assert keepstrong.count("cursor.executemany") >= 5
    assert keepstrong.count("IS DISTINCT FROM") >= 3
