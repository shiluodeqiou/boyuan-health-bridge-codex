from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

CN_TZ = timezone(timedelta(hours=8))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fitness_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    resource TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    records_seen INTEGER NOT NULL DEFAULT 0,
    records_written INTEGER NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_type TEXT
);

CREATE TABLE IF NOT EXISTS fitness_sync_state (
    source TEXT NOT NULL,
    resource TEXT NOT NULL,
    last_success_at TIMESTAMPTZ,
    latest_record_at TIMESTAMPTZ,
    records_seen BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (source, resource)
);

CREATE TABLE IF NOT EXISTS fitness_body_measurements (
    source TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL,
    measured_date DATE NOT NULL,
    weight_kg DOUBLE PRECISION,
    bmi DOUBLE PRECISION,
    body_fat_rate DOUBLE PRECISION,
    fat_mass_kg DOUBLE PRECISION,
    muscle_mass_kg DOUBLE PRECISION,
    skeletal_muscle_mass_kg DOUBLE PRECISION,
    raw_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, source_record_id, measured_at)
);
CREATE INDEX IF NOT EXISTS idx_body_measurements_date
    ON fitness_body_measurements (measured_date DESC);

CREATE TABLE IF NOT EXISTS fitness_health_metrics (
    source TEXT NOT NULL,
    metric TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    recorded_date DATE NOT NULL,
    record_hash TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, metric, recorded_at, record_hash)
);
CREATE INDEX IF NOT EXISTS idx_health_metrics_date
    ON fitness_health_metrics (metric, recorded_date DESC);

CREATE TABLE IF NOT EXISTS fitness_workout_sessions (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    category TEXT,
    sport_name TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    active_calories_kcal DOUBLE PRECISION,
    total_calories_kcal DOUBLE PRECISION,
    average_heart_rate_bpm DOUBLE PRECISION,
    maximum_heart_rate_bpm DOUBLE PRECISION,
    raw_data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_workout_sessions_start
    ON fitness_workout_sessions (started_at DESC);

CREATE TABLE IF NOT EXISTS fitness_workout_samples (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    sample_index INTEGER NOT NULL,
    recorded_at TIMESTAMPTZ,
    heart_rate_bpm DOUBLE PRECISION,
    raw_data JSONB NOT NULL,
    PRIMARY KEY (source, external_id, sample_index),
    FOREIGN KEY (source, external_id)
        REFERENCES fitness_workout_sessions (source, external_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fitness_strength_exercises (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    exercise_index INTEGER NOT NULL,
    exercise_name TEXT NOT NULL,
    body_part TEXT,
    raw_data JSONB NOT NULL,
    PRIMARY KEY (source, external_id, exercise_index),
    FOREIGN KEY (source, external_id)
        REFERENCES fitness_workout_sessions (source, external_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fitness_strength_sets (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    exercise_index INTEGER NOT NULL,
    set_index INTEGER NOT NULL,
    weight_kg DOUBLE PRECISION,
    reps INTEGER,
    duration_seconds INTEGER,
    rest_seconds INTEGER,
    raw_data JSONB NOT NULL,
    PRIMARY KEY (source, external_id, exercise_index, set_index),
    FOREIGN KEY (source, external_id, exercise_index)
        REFERENCES fitness_strength_exercises (source, external_id, exercise_index)
        ON DELETE CASCADE
);
"""


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError(
            "统一健身数据库尚未配置：请在 Render 设置 DATABASE_URL（PostgreSQL 连接串）"
        )
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(text[:10])
                parsed = datetime.combine(parsed_date, time.min, tzinfo=CN_TZ)
            except ValueError:
                return None
        return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed
    return None


def _record_time(row: dict[str, Any]) -> datetime | None:
    for key in ("measured_at", "recorded_at", "start_time", "time", "timestamp", "date", "day"):
        value = _as_datetime(row.get(key))
        if value is not None:
            return value
    return None


def _source_id(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return "sha256:" + _json_hash(row)


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


class FitnessDatabase:
    """Persistent PostgreSQL store for normalized fitness data and original payloads."""

    @property
    def configured(self) -> bool:
        return bool(os.getenv("DATABASE_URL", "").strip())

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(_database_url())

    async def initialize(self) -> None:
        async with await self._connect() as conn:
            for statement in SCHEMA_SQL.split(";"):
                if statement.strip():
                    await conn.execute(statement)

    async def store_xiaomi(
        self,
        *,
        days: int,
        body_composition: dict[str, Any],
        sport_records: dict[str, Any],
        health_metrics: dict[str, list[dict[str, Any]]],
        sport_details: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        await self.initialize()
        source = "xiaomi"
        resource = "personal_fitness"
        body_rows = body_composition.get("data", [])
        workout_rows = sport_records.get("records", [])
        details = sport_details or {}
        run_id: int | None = None
        body_written = workout_written = metric_written = sample_written = 0
        now = datetime.now(timezone.utc)

        async with await self._connect() as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """INSERT INTO fitness_sync_runs (source, resource, status, details)
                       VALUES (%s, %s, 'running', %s) RETURNING id""",
                    (source, resource, Jsonb({"days": days})),
                )
                run_id = (await cursor.fetchone())[0]

                for row in body_rows:
                    if not isinstance(row, dict):
                        continue
                    measured_at = _record_time(row)
                    if measured_at is None:
                        continue
                    record_id = _source_id(row, ("record_id", "id", "sid"))
                    await conn.execute(
                        """INSERT INTO fitness_body_measurements
                           (source, source_record_id, measured_at, measured_date,
                            weight_kg, bmi, body_fat_rate, fat_mass_kg,
                            muscle_mass_kg, skeletal_muscle_mass_kg, raw_data)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source, source_record_id, measured_at)
                           DO UPDATE SET
                             measured_date=EXCLUDED.measured_date,
                             weight_kg=EXCLUDED.weight_kg,
                             bmi=EXCLUDED.bmi,
                             body_fat_rate=EXCLUDED.body_fat_rate,
                             fat_mass_kg=EXCLUDED.fat_mass_kg,
                             muscle_mass_kg=EXCLUDED.muscle_mass_kg,
                             skeletal_muscle_mass_kg=EXCLUDED.skeletal_muscle_mass_kg,
                             raw_data=EXCLUDED.raw_data,
                             updated_at=now()""",
                        (
                            source, record_id, measured_at, measured_at.date(),
                            _float_or_none(row.get("weight")),
                            _float_or_none(row.get("bmi")),
                            _float_or_none(row.get("body_fat_rate")),
                            _float_or_none(row.get("fat_mass")),
                            _float_or_none(row.get("muscle_mass")),
                            _float_or_none(row.get("skeletal_muscle_mass")),
                            Jsonb(row),
                        ),
                    )
                    body_written += 1

                for metric, rows in health_metrics.items():
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        recorded_at = _record_time(row)
                        if recorded_at is None:
                            continue
                        await conn.execute(
                            """INSERT INTO fitness_health_metrics
                               (source, metric, recorded_at, recorded_date, record_hash, raw_data)
                               VALUES (%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (source, metric, recorded_at, record_hash)
                               DO UPDATE SET raw_data=EXCLUDED.raw_data, updated_at=now()""",
                            (
                                source, metric, recorded_at, recorded_at.date(),
                                _json_hash(row), Jsonb(row),
                            ),
                        )
                        metric_written += 1

                for row in workout_rows:
                    if not isinstance(row, dict):
                        continue
                    external_id = _source_id(row, ("workout_time", "id", "record_id"))
                    started_at = _record_time(row)
                    ended_at = _as_datetime(row.get("end_time"))
                    await conn.execute(
                        """INSERT INTO fitness_workout_sessions
                           (source, external_id, category, sport_name, started_at, ended_at,
                            duration_seconds, active_calories_kcal, total_calories_kcal,
                            average_heart_rate_bpm, maximum_heart_rate_bpm, raw_data)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source, external_id)
                           DO UPDATE SET category=EXCLUDED.category, sport_name=EXCLUDED.sport_name,
                             started_at=EXCLUDED.started_at, ended_at=EXCLUDED.ended_at,
                             duration_seconds=EXCLUDED.duration_seconds,
                             active_calories_kcal=EXCLUDED.active_calories_kcal,
                             total_calories_kcal=EXCLUDED.total_calories_kcal,
                             average_heart_rate_bpm=EXCLUDED.average_heart_rate_bpm,
                             maximum_heart_rate_bpm=EXCLUDED.maximum_heart_rate_bpm,
                             raw_data=EXCLUDED.raw_data, updated_at=now()""",
                        (
                            source, external_id, row.get("category"), row.get("sport"),
                            started_at, ended_at, _int_or_none(row.get("duration_seconds")),
                            _float_or_none(row.get("active_calories_kcal")),
                            _float_or_none(row.get("total_calories_kcal")),
                            _float_or_none(row.get("average_heart_rate_bpm")),
                            _float_or_none(row.get("maximum_heart_rate_bpm")), Jsonb(row),
                        ),
                    )
                    workout_written += 1

                    detail = details.get(external_id) or details.get(str(row.get("workout_time")))
                    if not isinstance(detail, dict):
                        continue
                    samples = detail.get("samples", [])
                    for index, sample in enumerate(samples):
                        if not isinstance(sample, dict):
                            continue
                        await conn.execute(
                            """INSERT INTO fitness_workout_samples
                               (source, external_id, sample_index, recorded_at, heart_rate_bpm, raw_data)
                               VALUES (%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (source, external_id, sample_index)
                               DO UPDATE SET recorded_at=EXCLUDED.recorded_at,
                                 heart_rate_bpm=EXCLUDED.heart_rate_bpm,
                                 raw_data=EXCLUDED.raw_data""",
                            (
                                source, external_id, index, _record_time(sample),
                                _float_or_none(sample.get("heart_rate_bpm")), Jsonb(sample),
                            ),
                        )
                        sample_written += 1

                latest_times = [
                    _record_time(row) for row in body_rows + workout_rows if isinstance(row, dict)
                ]
                latest_times = [value for value in latest_times if value is not None]
                latest_record_at = max(latest_times) if latest_times else None
                await conn.execute(
                    """INSERT INTO fitness_sync_state
                       (source, resource, last_success_at, latest_record_at, records_seen)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (source, resource)
                       DO UPDATE SET last_success_at=EXCLUDED.last_success_at,
                         latest_record_at=EXCLUDED.latest_record_at,
                         records_seen=EXCLUDED.records_seen""",
                    (source, resource, now, latest_record_at, len(body_rows) + len(workout_rows)),
                )
                await conn.execute(
                    """UPDATE fitness_sync_runs SET status='success', finished_at=now(),
                       records_seen=%s, records_written=%s, details=%s WHERE id=%s""",
                    (
                        len(body_rows) + len(workout_rows) + metric_written,
                        body_written + workout_written + metric_written + sample_written,
                        Jsonb({
                            "days": days,
                            "body_measurements": body_written,
                            "workout_sessions": workout_written,
                            "health_metric_rows": metric_written,
                            "workout_samples": sample_written,
                            "health_metric_errors": [
                                key for key, rows in health_metrics.items()
                                if not isinstance(rows, list)
                            ],
                        }),
                        run_id,
                    ),
                )

        return {
            "database": "postgresql",
            "sync_run_id": run_id,
            "source": source,
            "days": days,
            "body_measurements_upserted": body_written,
            "workout_sessions_upserted": workout_written,
            "health_metric_rows_upserted": metric_written,
            "workout_samples_upserted": sample_written,
            "sport_details_included": bool(details),
        }

    async def store_lianlian(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Store Lianlian records after an adapter maps them to the normalized contract."""
        await self.initialize()
        source = "lianlian"
        sessions_written = exercises_written = sets_written = 0
        now = datetime.now(timezone.utc)

        async with await self._connect() as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """INSERT INTO fitness_sync_runs (source, resource, status, details)
                       VALUES (%s, 'strength_training', 'running', %s) RETURNING id""",
                    (source, Jsonb({"records": len(records)})),
                )
                run_id = (await cursor.fetchone())[0]

                for record in records:
                    if not isinstance(record, dict):
                        continue
                    external_id = _source_id(record, ("id", "record_id", "workout_id", "session_id"))
                    started_at = _record_time(record)
                    ended_at = _as_datetime(record.get("end_time") or record.get("ended_at"))
                    await conn.execute(
                        """INSERT INTO fitness_workout_sessions
                           (source, external_id, category, sport_name, started_at, ended_at,
                            duration_seconds, active_calories_kcal, total_calories_kcal,
                            average_heart_rate_bpm, maximum_heart_rate_bpm, raw_data)
                           VALUES (%s,%s,'strength_training',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source, external_id)
                           DO UPDATE SET sport_name=EXCLUDED.sport_name,
                             started_at=EXCLUDED.started_at, ended_at=EXCLUDED.ended_at,
                             duration_seconds=EXCLUDED.duration_seconds,
                             active_calories_kcal=EXCLUDED.active_calories_kcal,
                             total_calories_kcal=EXCLUDED.total_calories_kcal,
                             average_heart_rate_bpm=EXCLUDED.average_heart_rate_bpm,
                             maximum_heart_rate_bpm=EXCLUDED.maximum_heart_rate_bpm,
                             raw_data=EXCLUDED.raw_data, updated_at=now()""",
                        (
                            source, external_id, str(record.get("name") or "力量训练"),
                            started_at, ended_at,
                            _int_or_none(record.get("duration_seconds")),
                            _float_or_none(record.get("active_calories_kcal")),
                            _float_or_none(record.get("total_calories_kcal")),
                            _float_or_none(record.get("average_heart_rate_bpm")),
                            _float_or_none(record.get("maximum_heart_rate_bpm")),
                            Jsonb(record),
                        ),
                    )
                    sessions_written += 1

                    exercises = record.get("exercises", [])
                    if not isinstance(exercises, list):
                        continue
                    for exercise_index, exercise in enumerate(exercises):
                        if not isinstance(exercise, dict):
                            continue
                        exercise_name = str(exercise.get("name") or exercise.get("exercise_name") or "").strip()
                        if not exercise_name:
                            continue
                        await conn.execute(
                            """INSERT INTO fitness_strength_exercises
                               (source, external_id, exercise_index, exercise_name, body_part, raw_data)
                               VALUES (%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (source, external_id, exercise_index)
                               DO UPDATE SET exercise_name=EXCLUDED.exercise_name,
                                 body_part=EXCLUDED.body_part, raw_data=EXCLUDED.raw_data""",
                            (
                                source, external_id, exercise_index, exercise_name,
                                exercise.get("body_part"), Jsonb(exercise),
                            ),
                        )
                        exercises_written += 1
                        sets = exercise.get("sets", [])
                        if not isinstance(sets, list):
                            continue
                        for set_index, set_row in enumerate(sets):
                            if not isinstance(set_row, dict):
                                continue
                            await conn.execute(
                                """INSERT INTO fitness_strength_sets
                                   (source, external_id, exercise_index, set_index, weight_kg,
                                    reps, duration_seconds, rest_seconds, raw_data)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                   ON CONFLICT (source, external_id, exercise_index, set_index)
                                   DO UPDATE SET weight_kg=EXCLUDED.weight_kg, reps=EXCLUDED.reps,
                                     duration_seconds=EXCLUDED.duration_seconds,
                                     rest_seconds=EXCLUDED.rest_seconds, raw_data=EXCLUDED.raw_data""",
                                (
                                    source, external_id, exercise_index, set_index,
                                    _float_or_none(set_row.get("weight_kg")),
                                    _int_or_none(set_row.get("reps")),
                                    _int_or_none(set_row.get("duration_seconds")),
                                    _int_or_none(set_row.get("rest_seconds")),
                                    Jsonb(set_row),
                                ),
                            )
                            sets_written += 1

                await conn.execute(
                    """UPDATE fitness_sync_runs SET status='success', finished_at=now(),
                       records_seen=%s, records_written=%s, details=%s WHERE id=%s""",
                    (
                        len(records), sessions_written + exercises_written + sets_written,
                        Jsonb({
                            "sessions": sessions_written,
                            "exercises": exercises_written,
                            "sets": sets_written,
                        }),
                        run_id,
                    ),
                )
                await conn.execute(
                    """INSERT INTO fitness_sync_state
                       (source, resource, last_success_at, latest_record_at, records_seen)
                       VALUES (%s,'strength_training',%s,%s,%s)
                       ON CONFLICT (source, resource)
                       DO UPDATE SET last_success_at=EXCLUDED.last_success_at,
                         latest_record_at=EXCLUDED.latest_record_at,
                         records_seen=EXCLUDED.records_seen""",
                    (source, now, max((_record_time(r) for r in records if isinstance(r, dict) and _record_time(r)), default=None), len(records)),
                )

        return {
            "database": "postgresql",
            "sync_run_id": run_id,
            "source": source,
            "strength_sessions_upserted": sessions_written,
            "exercises_upserted": exercises_written,
            "sets_upserted": sets_written,
        }

    async def summary(self, days: int = 30) -> dict[str, Any]:
        if not 1 <= days <= 3650:
            raise ValueError("days 必须在 1 到 3650 之间")
        await self.initialize()
        since = datetime.now(CN_TZ) - timedelta(days=days)
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT source, count(*) FROM fitness_body_measurements
                       WHERE measured_at >= %s GROUP BY source ORDER BY source""",
                    (since,),
                )
                body_counts = {row[0]: row[1] for row in await cur.fetchall()}
                await cur.execute(
                    """SELECT source, count(*) FROM fitness_workout_sessions
                       WHERE started_at >= %s GROUP BY source ORDER BY source""",
                    (since,),
                )
                workout_counts = {row[0]: row[1] for row in await cur.fetchall()}
                await cur.execute(
                    """SELECT source, metric, count(*) FROM fitness_health_metrics
                       WHERE recorded_at >= %s GROUP BY source, metric ORDER BY source, metric""",
                    (since,),
                )
                metric_counts = [
                    {"source": row[0], "metric": row[1], "records": row[2]}
                    for row in await cur.fetchall()
                ]
                await cur.execute(
                    """SELECT source, resource, status, started_at, finished_at,
                              records_seen, records_written, details
                       FROM fitness_sync_runs ORDER BY id DESC LIMIT 10"""
                )
                runs = [
                    {
                        "source": r[0], "resource": r[1], "status": r[2],
                        "started_at": r[3].isoformat() if r[3] else None,
                        "finished_at": r[4].isoformat() if r[4] else None,
                        "records_seen": r[5], "records_written": r[6],
                        "details": r[7],
                    }
                    for r in await cur.fetchall()
                ]
        return {
            "database": "postgresql",
            "window_days": days,
            "body_measurements_by_source": body_counts,
            "workout_sessions_by_source": workout_counts,
            "health_metric_counts": metric_counts,
            "recent_sync_runs": runs,
        }
