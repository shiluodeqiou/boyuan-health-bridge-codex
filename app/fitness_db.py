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
    body_age INTEGER,
    body_score INTEGER,
    bone_mass_kg DOUBLE PRECISION,
    protein_mass_kg DOUBLE PRECISION,
    body_water_mass_kg DOUBLE PRECISION,
    fat_free_mass_kg DOUBLE PRECISION,
    visceral_fat_level DOUBLE PRECISION,
    basal_metabolism_kcal DOUBLE PRECISION,
    waist_hip_ratio DOUBLE PRECISION,
    moisture_rate_percent DOUBLE PRECISION,
    protein_rate_percent DOUBLE PRECISION,
    segmental_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    session_date DATE,
    modality TEXT,
    record_type TEXT,
    training_type TEXT,
    status TEXT,
    calories_kcal DOUBLE PRECISION,
    training_capacity DOUBLE PRECISION,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
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
    action_id TEXT,
    weight_unit TEXT,
    capacity DOUBLE PRECISION,
    planned_rest_seconds INTEGER,
    weight_tag JSONB NOT NULL DEFAULT '{}'::jsonb,
    comb_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
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
    provider_set_id TEXT,
    weight_value DOUBLE PRECISION,
    weight_unit TEXT,
    planned_rest_seconds INTEGER,
    is_completed BOOLEAN,
    completed_at TIMESTAMPTZ,
    is_new_rm1_record BOOLEAN,
    rm1_weight_kg DOUBLE PRECISION,
    set_time_text TEXT,
    heart_rate_data JSONB,
    flag TEXT,
    comb_groups JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_data JSONB NOT NULL,
    PRIMARY KEY (source, external_id, exercise_index, set_index),
    FOREIGN KEY (source, external_id, exercise_index)
        REFERENCES fitness_strength_exercises (source, external_id, exercise_index)
        ON DELETE CASCADE
);

ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS body_age INTEGER;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS body_score INTEGER;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS bone_mass_kg DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS protein_mass_kg DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS body_water_mass_kg DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS fat_free_mass_kg DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS visceral_fat_level DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS basal_metabolism_kcal DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS waist_hip_ratio DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS moisture_rate_percent DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS protein_rate_percent DOUBLE PRECISION;
ALTER TABLE fitness_body_measurements ADD COLUMN IF NOT EXISTS segmental_metrics JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS session_date DATE;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS modality TEXT;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS record_type TEXT;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS training_type TEXT;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS calories_kcal DOUBLE PRECISION;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS training_capacity DOUBLE PRECISION;
ALTER TABLE fitness_workout_sessions ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE fitness_strength_exercises ADD COLUMN IF NOT EXISTS action_id TEXT;
ALTER TABLE fitness_strength_exercises ADD COLUMN IF NOT EXISTS weight_unit TEXT;
ALTER TABLE fitness_strength_exercises ADD COLUMN IF NOT EXISTS capacity DOUBLE PRECISION;
ALTER TABLE fitness_strength_exercises ADD COLUMN IF NOT EXISTS planned_rest_seconds INTEGER;
ALTER TABLE fitness_strength_exercises ADD COLUMN IF NOT EXISTS weight_tag JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE fitness_strength_exercises ADD COLUMN IF NOT EXISTS comb_actions JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS provider_set_id TEXT;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS weight_value DOUBLE PRECISION;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS weight_unit TEXT;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS planned_rest_seconds INTEGER;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS is_completed BOOLEAN;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS is_new_rm1_record BOOLEAN;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS rm1_weight_kg DOUBLE PRECISION;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS set_time_text TEXT;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS heart_rate_data JSONB;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS flag TEXT;
ALTER TABLE fitness_strength_sets ADD COLUMN IF NOT EXISTS comb_groups JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_workout_sessions_date ON fitness_workout_sessions (session_date DESC);
CREATE INDEX IF NOT EXISTS idx_strength_exercises_name ON fitness_strength_exercises (exercise_name);
CREATE INDEX IF NOT EXISTS idx_strength_sets_weight ON fitness_strength_sets (weight_kg, reps);

UPDATE fitness_body_measurements
SET body_age = NULLIF(raw_data->>'body_age','')::integer,
    body_score = NULLIF(raw_data->>'body_score','')::integer,
    bone_mass_kg = NULLIF(raw_data->>'bone_mass','')::double precision,
    protein_mass_kg = NULLIF(raw_data->>'protein_mass','')::double precision,
    body_water_mass_kg = NULLIF(raw_data->>'body_moisture_mass','')::double precision,
    fat_free_mass_kg = NULLIF(raw_data->>'fat_free_mass','')::double precision,
    visceral_fat_level = NULLIF(raw_data->>'visceral_fat','')::double precision,
    basal_metabolism_kcal = NULLIF(raw_data->>'basal_metabolism','')::double precision,
    waist_hip_ratio = NULLIF(raw_data->>'whr','')::double precision,
    moisture_rate_percent = NULLIF(raw_data->>'moisture_rate','')::double precision,
    protein_rate_percent = NULLIF(raw_data->>'protein_rate','')::double precision,
    segmental_metrics = COALESCE((
      SELECT jsonb_object_agg(key, value)
      FROM jsonb_each(raw_data)
      WHERE key LIKE 'left_%' OR key LIKE 'right_%'
         OR key LIKE 'trunk_%' OR key LIKE 'limbs_%'
         OR key LIKE 'upper_limb_%' OR key LIKE 'lower_limb_%'
    ), '{}'::jsonb)
WHERE source='xiaomi';

UPDATE fitness_workout_sessions
SET session_date = COALESCE(session_date, (started_at AT TIME ZONE 'Asia/Shanghai')::date),
    modality = COALESCE(modality,
      CASE WHEN category IN ('strength_training','free_training') THEN 'strength'
           WHEN category IS NOT NULL THEN 'cardio'
           ELSE 'other' END),
    record_type = COALESCE(record_type, raw_data->>'recordType'),
    training_type = COALESCE(training_type, raw_data->>'trainingType'),
    status = COALESCE(status, raw_data->>'status'),
    calories_kcal = COALESCE(calories_kcal, NULLIF(raw_data->>'calorie','')::double precision),
    training_capacity = COALESCE(training_capacity, NULLIF(raw_data->>'capacity','')::double precision)
WHERE session_date IS NULL OR modality IS NULL OR source='lianlian';

UPDATE fitness_strength_exercises
SET action_id = COALESCE(action_id, raw_data->>'actionId'),
    weight_unit = COALESCE(weight_unit, raw_data->>'weightUnit'),
    capacity = COALESCE(capacity, NULLIF(raw_data->>'capacity','')::double precision),
    planned_rest_seconds = COALESCE(planned_rest_seconds, NULLIF(raw_data->>'restSeconds','')::integer),
    weight_tag = COALESCE(raw_data->'weightTag', '{}'::jsonb),
    comb_actions = COALESCE(raw_data->'combActions', '[]'::jsonb)
WHERE source='lianlian';

UPDATE fitness_strength_sets AS st
SET provider_set_id = COALESCE(st.provider_set_id, st.raw_data->>'_id'),
    weight_value = COALESCE(st.weight_value, NULLIF(st.raw_data->>'weight','')::double precision),
    weight_unit = COALESCE(st.weight_unit, ex.weight_unit),
    planned_rest_seconds = COALESCE(st.planned_rest_seconds, ex.planned_rest_seconds),
    is_completed = CASE
      WHEN st.raw_data ? 'isDone' THEN (st.raw_data->>'isDone')::boolean
      ELSE st.is_completed END,
    completed_at = COALESCE(st.completed_at,
      CASE WHEN NULLIF(st.raw_data->>'doneTs','') ~ '^[0-9]+$'
           THEN to_timestamp((st.raw_data->>'doneTs')::double precision) END),
    is_new_rm1_record = CASE
      WHEN st.raw_data ? 'isNewRM1Record' THEN (st.raw_data->>'isNewRM1Record')::boolean
      ELSE st.is_new_rm1_record END,
    rm1_weight_kg = COALESCE(st.rm1_weight_kg,
      CASE WHEN ex.weight_unit='kg'
           THEN NULLIF(st.raw_data->>'rm1Weight','')::double precision END),
    set_time_text = COALESCE(st.set_time_text, st.raw_data->>'time'),
    heart_rate_data = COALESCE(st.heart_rate_data, st.raw_data->'hr'),
    flag = COALESCE(st.flag, st.raw_data->>'flag'),
    comb_groups = COALESCE(st.raw_data->'combGroups', '[]'::jsonb)
FROM fitness_strength_exercises AS ex
WHERE st.source='lianlian'
  AND ex.source=st.source
  AND ex.external_id=st.external_id
  AND ex.exercise_index=st.exercise_index;

CREATE OR REPLACE VIEW fitness_daily_overview AS
WITH all_days AS (
  SELECT measured_date AS day FROM fitness_body_measurements
  UNION
  SELECT recorded_date AS day FROM fitness_health_metrics
  UNION
  SELECT COALESCE(session_date, (started_at AT TIME ZONE 'Asia/Shanghai')::date) AS day
  FROM fitness_workout_sessions
  WHERE session_date IS NOT NULL OR started_at IS NOT NULL
),
latest_body AS (
  SELECT DISTINCT ON (measured_date)
    measured_date, weight_kg, bmi, body_fat_rate, fat_mass_kg,
    muscle_mass_kg, skeletal_muscle_mass_kg, body_age, body_score,
    bone_mass_kg, protein_mass_kg, body_water_mass_kg, fat_free_mass_kg,
    visceral_fat_level, basal_metabolism_kcal, waist_hip_ratio,
    moisture_rate_percent, protein_rate_percent, segmental_metrics
  FROM fitness_body_measurements
  ORDER BY measured_date, measured_at DESC
),
workout_rollup AS (
  SELECT COALESCE(session_date, (started_at AT TIME ZONE 'Asia/Shanghai')::date) AS day,
    count(*) FILTER (WHERE source='xiaomi') AS xiaomi_workout_count,
    count(*) FILTER (WHERE source='lianlian') AS strength_session_count,
    sum(duration_seconds) FILTER (WHERE source='xiaomi') AS xiaomi_duration_seconds,
    sum(duration_seconds) FILTER (WHERE source='lianlian') AS strength_duration_seconds,
    sum(COALESCE(active_calories_kcal,total_calories_kcal,calories_kcal))
      FILTER (WHERE source='xiaomi') AS xiaomi_calories_kcal,
    sum(calories_kcal) FILTER (WHERE source='lianlian') AS strength_calories_kcal
  FROM fitness_workout_sessions
  WHERE session_date IS NOT NULL OR started_at IS NOT NULL
  GROUP BY 1
),
strength_rollup AS (
  SELECT COALESCE(s.session_date, (s.started_at AT TIME ZONE 'Asia/Shanghai')::date) AS day,
    count(*) AS strength_set_count,
    count(*) FILTER (WHERE st.is_completed IS TRUE) AS completed_set_count,
    sum(st.reps) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS strength_reps,
    sum(st.weight_kg * st.reps)
      FILTER (WHERE st.weight_kg IS NOT NULL AND st.reps IS NOT NULL
              AND st.is_completed IS DISTINCT FROM FALSE) AS strength_volume_kg,
    max(st.weight_kg) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS max_lift_kg
  FROM fitness_workout_sessions s
  JOIN fitness_strength_exercises e
    ON e.source=s.source AND e.external_id=s.external_id
  JOIN fitness_strength_sets st
    ON st.source=e.source AND st.external_id=e.external_id
   AND st.exercise_index=e.exercise_index
  WHERE s.source='lianlian'
  GROUP BY 1
),
health_rollup AS (
  SELECT recorded_date AS day, jsonb_object_agg(metric, metric_rows) AS health_metrics
  FROM (
    SELECT recorded_date, metric, jsonb_agg(raw_data ORDER BY recorded_at) AS metric_rows
    FROM fitness_health_metrics
    GROUP BY recorded_date, metric
  ) grouped
  GROUP BY recorded_date
)
SELECT d.day,
  b.weight_kg, b.bmi, b.body_fat_rate, b.fat_mass_kg, b.muscle_mass_kg,
  b.skeletal_muscle_mass_kg, b.body_age, b.body_score, b.bone_mass_kg,
  b.protein_mass_kg, b.body_water_mass_kg, b.fat_free_mass_kg,
  b.visceral_fat_level, b.basal_metabolism_kcal, b.waist_hip_ratio,
  b.moisture_rate_percent, b.protein_rate_percent, b.segmental_metrics,
  COALESCE(w.xiaomi_workout_count,0) AS xiaomi_workout_count,
  COALESCE(w.strength_session_count,0) AS strength_session_count,
  COALESCE(w.xiaomi_duration_seconds,0) AS xiaomi_duration_seconds,
  COALESCE(w.strength_duration_seconds,0) AS strength_duration_seconds,
  COALESCE(w.xiaomi_calories_kcal,0) AS xiaomi_calories_kcal,
  COALESCE(w.strength_calories_kcal,0) AS strength_calories_kcal,
  COALESCE(sr.strength_set_count,0) AS strength_set_count,
  COALESCE(sr.completed_set_count,0) AS completed_set_count,
  COALESCE(sr.strength_reps,0) AS strength_reps,
  COALESCE(sr.strength_volume_kg,0) AS strength_volume_kg,
  sr.max_lift_kg,
  COALESCE(h.health_metrics,'{}'::jsonb) AS health_metrics
FROM all_days d
LEFT JOIN latest_body b ON b.measured_date=d.day
LEFT JOIN workout_rollup w ON w.day=d.day
LEFT JOIN strength_rollup sr ON sr.day=d.day
LEFT JOIN health_rollup h ON h.day=d.day;

CREATE OR REPLACE VIEW fitness_strength_progression AS
SELECT
  COALESCE(s.session_date, (s.started_at AT TIME ZONE 'Asia/Shanghai')::date) AS session_date,
  e.exercise_name,
  count(*) AS set_count,
  count(*) FILTER (WHERE st.is_completed IS TRUE) AS completed_set_count,
  sum(st.reps) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS total_reps,
  sum(st.weight_kg * st.reps)
    FILTER (WHERE st.weight_kg IS NOT NULL AND st.reps IS NOT NULL
            AND st.is_completed IS DISTINCT FROM FALSE) AS volume_kg,
  max(st.weight_kg) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS max_weight_kg,
  max(st.weight_kg * (1 + st.reps / 30.0))
    FILTER (WHERE st.weight_kg > 0 AND st.reps > 0
            AND st.is_completed IS DISTINCT FROM FALSE) AS best_estimated_1rm_kg
FROM fitness_workout_sessions s
JOIN fitness_strength_exercises e
  ON e.source=s.source AND e.external_id=s.external_id
JOIN fitness_strength_sets st
  ON st.source=e.source AND st.external_id=e.external_id
 AND st.exercise_index=e.exercise_index
WHERE s.source='lianlian'
GROUP BY 1, e.exercise_name;

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
    for key in ("measured_at", "recorded_at", "started_at", "start_time", "time", "timestamp", "date", "day"):
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



def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(CN_TZ).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) == 8 and text.isdigit():
            try:
                return datetime.strptime(text, "%Y%m%d").date()
            except ValueError:
                return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            parsed = _as_datetime(text)
            return parsed.astimezone(CN_TZ).date() if parsed else None
    parsed = _as_datetime(value)
    return parsed.astimezone(CN_TZ).date() if parsed else None


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
                            muscle_mass_kg, skeletal_muscle_mass_kg, body_age, body_score,
                            bone_mass_kg, protein_mass_kg, body_water_mass_kg, fat_free_mass_kg,
                            visceral_fat_level, basal_metabolism_kcal, waist_hip_ratio,
                            moisture_rate_percent, protein_rate_percent, segmental_metrics, raw_data)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source, source_record_id, measured_at)
                           DO UPDATE SET
                             measured_date=EXCLUDED.measured_date,
                             weight_kg=EXCLUDED.weight_kg,
                             bmi=EXCLUDED.bmi,
                             body_fat_rate=EXCLUDED.body_fat_rate,
                             fat_mass_kg=EXCLUDED.fat_mass_kg,
                             muscle_mass_kg=EXCLUDED.muscle_mass_kg,
                             skeletal_muscle_mass_kg=EXCLUDED.skeletal_muscle_mass_kg,
                             body_age=EXCLUDED.body_age,
                             body_score=EXCLUDED.body_score,
                             bone_mass_kg=EXCLUDED.bone_mass_kg,
                             protein_mass_kg=EXCLUDED.protein_mass_kg,
                             body_water_mass_kg=EXCLUDED.body_water_mass_kg,
                             fat_free_mass_kg=EXCLUDED.fat_free_mass_kg,
                             visceral_fat_level=EXCLUDED.visceral_fat_level,
                             basal_metabolism_kcal=EXCLUDED.basal_metabolism_kcal,
                             waist_hip_ratio=EXCLUDED.waist_hip_ratio,
                             moisture_rate_percent=EXCLUDED.moisture_rate_percent,
                             protein_rate_percent=EXCLUDED.protein_rate_percent,
                             segmental_metrics=EXCLUDED.segmental_metrics,
                             raw_data=EXCLUDED.raw_data,
                             updated_at=now()""",
                        (
                            source, record_id, measured_at, measured_at.astimezone(CN_TZ).date(),
                            _float_or_none(row.get("weight")),
                            _float_or_none(row.get("bmi")),
                            _float_or_none(row.get("body_fat_rate")),
                            _float_or_none(row.get("fat_mass")),
                            _float_or_none(row.get("muscle_mass")),
                            _float_or_none(row.get("skeletal_muscle_mass")),
                            _int_or_none(row.get("body_age")),
                            _int_or_none(row.get("body_score")),
                            _float_or_none(row.get("bone_mass")),
                            _float_or_none(row.get("protein_mass")),
                            _float_or_none(row.get("body_moisture_mass")),
                            _float_or_none(row.get("fat_free_mass")),
                            _float_or_none(row.get("visceral_fat")),
                            _float_or_none(row.get("basal_metabolism")),
                            _float_or_none(row.get("whr")),
                            _float_or_none(row.get("moisture_rate")),
                            _float_or_none(row.get("protein_rate")),
                            Jsonb({k: v for k, v in row.items() if k.startswith(
                                ("left_", "right_", "trunk_", "limbs_", "upper_limb_", "lower_limb_")
                            )}),
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
                                source, metric, recorded_at, recorded_at.astimezone(CN_TZ).date(),
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
                            average_heart_rate_bpm, maximum_heart_rate_bpm, session_date,
                            modality, raw_data)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source, external_id)
                           DO UPDATE SET category=EXCLUDED.category, sport_name=EXCLUDED.sport_name,
                             started_at=EXCLUDED.started_at, ended_at=EXCLUDED.ended_at,
                             duration_seconds=EXCLUDED.duration_seconds,
                             active_calories_kcal=EXCLUDED.active_calories_kcal,
                             total_calories_kcal=EXCLUDED.total_calories_kcal,
                             average_heart_rate_bpm=EXCLUDED.average_heart_rate_bpm,
                             maximum_heart_rate_bpm=EXCLUDED.maximum_heart_rate_bpm,
                             session_date=EXCLUDED.session_date, modality=EXCLUDED.modality,
                             raw_data=EXCLUDED.raw_data, updated_at=now()""",
                        (
                            source, external_id, row.get("category"), row.get("sport"),
                            started_at, ended_at, _int_or_none(row.get("duration_seconds")),
                            _float_or_none(row.get("active_calories_kcal")),
                            _float_or_none(row.get("total_calories_kcal")),
                            _float_or_none(row.get("average_heart_rate_bpm")),
                            _float_or_none(row.get("maximum_heart_rate_bpm")),
                            started_at.astimezone(CN_TZ).date() if started_at else None,
                            "strength" if row.get("category") in ("strength_training", "free_training") else "cardio",
                            Jsonb(row),
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
        """Idempotently mirror normalized KeepStrong records and exact source JSON."""
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
                    session_date = _as_date(record.get("session_date") or record.get("date") or record.get("dayStr"))
                    if session_date is None:
                        raise ValueError(f"练练训练记录 {external_id} 缺少有效日期")
                    started_at = _as_datetime(record.get("started_at"))
                    ended_at = _as_datetime(record.get("ended_at") or record.get("end_time"))
                    source_raw = record.get("raw_data", record)
                    metadata = record.get("source_metadata") or {}

                    await conn.execute(
                        """INSERT INTO fitness_workout_sessions
                           (source, external_id, category, sport_name, started_at, ended_at,
                            duration_seconds, active_calories_kcal, total_calories_kcal,
                            average_heart_rate_bpm, maximum_heart_rate_bpm, session_date,
                            modality, record_type, training_type, status, calories_kcal,
                            training_capacity, source_metadata, raw_data)
                           VALUES (%s,%s,'strength_training',%s,%s,%s,%s,NULL,NULL,%s,NULL,
                                   %s,'strength',%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (source, external_id)
                           DO UPDATE SET category=EXCLUDED.category,
                             sport_name=EXCLUDED.sport_name, started_at=EXCLUDED.started_at,
                             ended_at=EXCLUDED.ended_at, duration_seconds=EXCLUDED.duration_seconds,
                             average_heart_rate_bpm=EXCLUDED.average_heart_rate_bpm,
                             session_date=EXCLUDED.session_date, modality=EXCLUDED.modality,
                             record_type=EXCLUDED.record_type, training_type=EXCLUDED.training_type,
                             status=EXCLUDED.status, calories_kcal=EXCLUDED.calories_kcal,
                             training_capacity=EXCLUDED.training_capacity,
                             source_metadata=EXCLUDED.source_metadata,
                             raw_data=EXCLUDED.raw_data, updated_at=now()""",
                        (
                            source, external_id, str(record.get("name") or "力量训练"),
                            started_at, ended_at, _int_or_none(record.get("duration_seconds")),
                            _float_or_none(record.get("average_heart_rate_bpm")),
                            session_date, record.get("record_type"), record.get("training_type"),
                            record.get("status"), _float_or_none(record.get("calories_kcal")),
                            _float_or_none(record.get("training_capacity")), Jsonb(metadata),
                            Jsonb(source_raw),
                        ),
                    )
                    sessions_written += 1

                    exercises = record.get("exercises", [])
                    if not isinstance(exercises, list):
                        raise ValueError(f"练练训练记录 {external_id} 的 exercises 不是数组")
                    for exercise_index, exercise in enumerate(exercises):
                        if not isinstance(exercise, dict):
                            continue
                        exercise_name = str(exercise.get("name") or exercise.get("exercise_name") or "").strip()
                        if not exercise_name:
                            raise ValueError(f"练练训练记录 {external_id} 的第 {exercise_index + 1} 个动作缺少名称")
                        exercise_raw = exercise.get("raw_data", exercise)
                        weight_unit = exercise.get("weight_unit")
                        await conn.execute(
                            """INSERT INTO fitness_strength_exercises
                               (source, external_id, exercise_index, exercise_name, body_part,
                                action_id, weight_unit, capacity, planned_rest_seconds,
                                weight_tag, comb_actions, raw_data)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (source, external_id, exercise_index)
                               DO UPDATE SET exercise_name=EXCLUDED.exercise_name,
                                 body_part=EXCLUDED.body_part, action_id=EXCLUDED.action_id,
                                 weight_unit=EXCLUDED.weight_unit, capacity=EXCLUDED.capacity,
                                 planned_rest_seconds=EXCLUDED.planned_rest_seconds,
                                 weight_tag=EXCLUDED.weight_tag, comb_actions=EXCLUDED.comb_actions,
                                 raw_data=EXCLUDED.raw_data""",
                            (
                                source, external_id, exercise_index, exercise_name,
                                exercise.get("body_part"), exercise.get("action_id"), weight_unit,
                                _float_or_none(exercise.get("capacity")),
                                _int_or_none(exercise.get("planned_rest_seconds")),
                                Jsonb(exercise.get("weight_tag") or {}),
                                Jsonb(exercise.get("comb_actions") or []), Jsonb(exercise_raw),
                            ),
                        )
                        exercises_written += 1

                        sets = exercise.get("sets", [])
                        if not isinstance(sets, list):
                            raise ValueError(f"动作 {exercise_name} 的 sets 不是数组")
                        for set_index, set_row in enumerate(sets):
                            if not isinstance(set_row, dict):
                                continue
                            set_raw = set_row.get("raw_data", set_row)
                            set_unit = set_row.get("weight_unit") or weight_unit
                            weight_value = _float_or_none(set_row.get("weight_value"))
                            if weight_value is None:
                                weight_value = _float_or_none(set_row.get("weight_kg"))
                            weight_kg = (
                                _float_or_none(set_row.get("weight_kg"))
                                if str(set_unit or "").lower() == "kg" else None
                            )
                            completed = set_row.get("is_completed")
                            if not isinstance(completed, bool):
                                completed = None
                            await conn.execute(
                                """INSERT INTO fitness_strength_sets
                                   (source, external_id, exercise_index, set_index, weight_kg,
                                    reps, duration_seconds, rest_seconds, provider_set_id,
                                    weight_value, weight_unit, planned_rest_seconds, is_completed,
                                    completed_at, is_new_rm1_record, rm1_weight_kg, set_time_text,
                                    heart_rate_data, flag, comb_groups, raw_data)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                   ON CONFLICT (source, external_id, exercise_index, set_index)
                                   DO UPDATE SET weight_kg=EXCLUDED.weight_kg,
                                     reps=EXCLUDED.reps, duration_seconds=EXCLUDED.duration_seconds,
                                     rest_seconds=EXCLUDED.rest_seconds,
                                     provider_set_id=EXCLUDED.provider_set_id,
                                     weight_value=EXCLUDED.weight_value,
                                     weight_unit=EXCLUDED.weight_unit,
                                     planned_rest_seconds=EXCLUDED.planned_rest_seconds,
                                     is_completed=EXCLUDED.is_completed,
                                     completed_at=EXCLUDED.completed_at,
                                     is_new_rm1_record=EXCLUDED.is_new_rm1_record,
                                     rm1_weight_kg=EXCLUDED.rm1_weight_kg,
                                     set_time_text=EXCLUDED.set_time_text,
                                     heart_rate_data=EXCLUDED.heart_rate_data,
                                     flag=EXCLUDED.flag, comb_groups=EXCLUDED.comb_groups,
                                     raw_data=EXCLUDED.raw_data""",
                                (
                                    source, external_id, exercise_index, set_index, weight_kg,
                                    _int_or_none(set_row.get("reps")),
                                    _int_or_none(set_row.get("duration_seconds")),
                                    _int_or_none(set_row.get("rest_seconds")),
                                    set_row.get("provider_set_id"), weight_value, set_unit,
                                    _int_or_none(set_row.get("planned_rest_seconds")), completed,
                                    _as_datetime(set_row.get("completed_at")),
                                    set_row.get("is_new_rm1_record"),
                                    _float_or_none(set_row.get("rm1_weight_kg")),
                                    set_row.get("set_time_text"),
                                    Jsonb(set_row.get("heart_rate_data")) if set_row.get("heart_rate_data") is not None else None,
                                    set_row.get("flag"), Jsonb(set_row.get("comb_groups") or []),
                                    Jsonb(set_raw),
                                ),
                            )
                            sets_written += 1

                        await conn.execute(
                            """DELETE FROM fitness_strength_sets
                               WHERE source=%s AND external_id=%s AND exercise_index=%s
                                 AND set_index >= %s""",
                            (source, external_id, exercise_index, len(sets)),
                        )

                    await conn.execute(
                        """DELETE FROM fitness_strength_exercises
                           WHERE source=%s AND external_id=%s AND exercise_index >= %s""",
                        (source, external_id, len(exercises)),
                    )

                latest_dates = [
                    _as_date(r.get("session_date") or r.get("date") or r.get("dayStr"))
                    for r in records if isinstance(r, dict)
                ]
                latest_dates = [d for d in latest_dates if d is not None]
                latest_record_at = (
                    datetime.combine(max(latest_dates), time.min, tzinfo=CN_TZ)
                    if latest_dates else None
                )
                await conn.execute(
                    """UPDATE fitness_sync_runs SET status='success', finished_at=now(),
                       records_seen=%s, records_written=%s, details=%s WHERE id=%s""",
                    (
                        len(records), sessions_written + exercises_written + sets_written,
                        Jsonb({
                            "sessions": sessions_written,
                            "exercises": exercises_written,
                            "sets": sets_written,
                            "raw_source_json_preserved": True,
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
                    (source, now, latest_record_at, len(records)),
                )

        return {
            "database": "postgresql",
            "sync_run_id": run_id,
            "source": source,
            "strength_sessions_upserted": sessions_written,
            "exercises_upserted": exercises_written,
            "sets_upserted": sets_written,
        }


    async def dashboard(self, days: int = 90) -> dict[str, Any]:
        if not 1 <= days <= 3650:
            raise ValueError("days 必须在 1 到 3650 之间")
        await self.initialize()
        since_date = datetime.now(CN_TZ).date() - timedelta(days=days - 1)
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT day, weight_kg, bmi, body_fat_rate, fat_mass_kg,
                              muscle_mass_kg, skeletal_muscle_mass_kg, body_age, body_score,
                              bone_mass_kg, protein_mass_kg, body_water_mass_kg,
                              fat_free_mass_kg, visceral_fat_level, basal_metabolism_kcal,
                              waist_hip_ratio, moisture_rate_percent, protein_rate_percent,
                              xiaomi_workout_count, strength_session_count,
                              xiaomi_duration_seconds, strength_duration_seconds,
                              xiaomi_calories_kcal, strength_calories_kcal,
                              strength_set_count, completed_set_count, strength_reps,
                              strength_volume_kg, max_lift_kg, health_metrics
                       FROM fitness_daily_overview
                       WHERE day >= %s ORDER BY day DESC LIMIT 500""",
                    (since_date,),
                )
                daily_rows = await cur.fetchall()
                daily_columns = [column.name for column in cur.description]
                daily = [
                    {
                        key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
                        for key, value in zip(daily_columns, row)
                    }
                    for row in daily_rows
                ]
                await cur.execute(
                    """SELECT session_date, exercise_name, set_count, completed_set_count,
                              total_reps, volume_kg, max_weight_kg, best_estimated_1rm_kg
                       FROM fitness_strength_progression
                       WHERE session_date >= %s
                       ORDER BY session_date DESC, exercise_name LIMIT 1000""",
                    (since_date,),
                )
                progression_rows = await cur.fetchall()
                progression_columns = [column.name for column in cur.description]
                progression = [
                    {
                        key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
                        for key, value in zip(progression_columns, row)
                    }
                    for row in progression_rows
                ]
                await cur.execute(
                    """SELECT e.exercise_name,
                              count(*) AS set_count,
                              max(st.weight_kg) AS best_weight_kg,
                              max(st.weight_kg * (1 + st.reps / 30.0))
                                FILTER (WHERE st.weight_kg > 0 AND st.reps > 0) AS best_estimated_1rm_kg,
                              sum(st.weight_kg * st.reps)
                                FILTER (WHERE st.weight_kg IS NOT NULL AND st.reps IS NOT NULL) AS total_volume_kg
                       FROM fitness_strength_exercises e
                       JOIN fitness_strength_sets st
                         ON st.source=e.source AND st.external_id=e.external_id
                        AND st.exercise_index=e.exercise_index
                       WHERE e.source='lianlian'
                       GROUP BY e.exercise_name
                       ORDER BY best_estimated_1rm_kg DESC NULLS LAST, set_count DESC
                       LIMIT 20"""
                )
                top_rows = await cur.fetchall()
                top_columns = [column.name for column in cur.description]
                top_exercises = [dict(zip(top_columns, row)) for row in top_rows]
        return {
            "database": "postgresql",
            "window_days": days,
            "since_date": since_date.isoformat(),
            "daily": daily,
            "strength_progression": progression,
            "top_exercises": top_exercises,
            "notes": [
                "体脂秤每日保留逐次测量；daily 视图展示当日最后一次测量。",
                "力量训练容量只累计明确以 kg 记录且有次数的组；体重训练或未知单位不臆算外部负重。",
                "估算 1RM 使用 Epley 公式，仅用于趋势参考，不是实测最大重量。",
            ],
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
                       WHERE measured_date >= %s GROUP BY source ORDER BY source""",
                    (since.date(),),
                )
                body_counts = {row[0]: row[1] for row in await cur.fetchall()}
                await cur.execute(
                    """SELECT source, count(*) FROM fitness_workout_sessions
                       WHERE COALESCE(session_date, (started_at AT TIME ZONE 'Asia/Shanghai')::date) >= %s
                       GROUP BY source ORDER BY source""",
                    (since.date(),),
                )
                workout_counts = {row[0]: row[1] for row in await cur.fetchall()}
                await cur.execute(
                    """SELECT source, metric, count(*) FROM fitness_health_metrics
                       WHERE recorded_date >= %s GROUP BY source, metric ORDER BY source, metric""",
                    (since.date(),),
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

           THEN to_timestamp((st.raw_data->>'doneTs')::double precision) END),
    is_new_rm1_record = CASE
      WHEN st.raw_data ? 'isNewRM1Record' THEN (st.raw_data->>'isNewRM1Record')::boolean
      ELSE st.is_new_rm1_record END,
    rm1_weight_kg = COALESCE(st.rm1_weight_kg,
      CASE WHEN ex.weight_unit='kg'
           THEN NULLIF(st.raw_data->>'rm1Weight','')::double precision END),
    set_time_text = COALESCE(st.set_time_text, st.raw_data->>'time'),
    heart_rate_data = COALESCE(st.heart_rate_data, st.raw_data->'hr'),
    flag = COALESCE(st.flag, st.raw_data->>'flag'),
    comb_groups = COALESCE(st.raw_data->'combGroups', '[]'::jsonb)
FROM fitness_strength_exercises AS ex
WHERE st.source='lianlian'
  AND ex.source=st.source
  AND ex.external_id=st.external_id
  AND ex.exercise_index=st.exercise_index;

CREATE OR REPLACE VIEW fitness_daily_overview AS
WITH all_days AS (
  SELECT measured_date AS day FROM fitness_body_measurements
  UNION
  SELECT recorded_date AS day FROM fitness_health_metrics
  UNION
  SELECT COALESCE(session_date, (started_at AT TIME ZONE 'Asia/Shanghai')::date) AS day
  FROM fitness_workout_sessions
  WHERE session_date IS NOT NULL OR started_at IS NOT NULL
),
latest_body AS (
  SELECT DISTINCT ON (measured_date)
    measured_date, weight_kg, bmi, body_fat_rate, fat_mass_kg,
    muscle_mass_kg, skeletal_muscle_mass_kg, body_age, body_score,
    bone_mass_kg, protein_mass_kg, body_water_mass_kg, fat_free_mass_kg,
    visceral_fat_level, basal_metabolism_kcal, waist_hip_ratio,
    moisture_rate_percent, protein_rate_percent, segmental_metrics
  FROM fitness_body_measurements
  ORDER BY measured_date, measured_at DESC
),
workout_rollup AS (
  SELECT COALESCE(session_date, (started_at AT TIME ZONE 'Asia/Shanghai')::date) AS day,
    count(*) FILTER (WHERE source='xiaomi') AS xiaomi_workout_count,
    count(*) FILTER (WHERE source='lianlian') AS strength_session_count,
    sum(duration_seconds) FILTER (WHERE source='xiaomi') AS xiaomi_duration_seconds,
    sum(duration_seconds) FILTER (WHERE source='lianlian') AS strength_duration_seconds,
    sum(COALESCE(active_calories_kcal,total_calories_kcal,calories_kcal))
      FILTER (WHERE source='xiaomi') AS xiaomi_calories_kcal,
    sum(calories_kcal) FILTER (WHERE source='lianlian') AS strength_calories_kcal
  FROM fitness_workout_sessions
  WHERE session_date IS NOT NULL OR started_at IS NOT NULL
  GROUP BY 1
),
strength_rollup AS (
  SELECT COALESCE(s.session_date, (s.started_at AT TIME ZONE 'Asia/Shanghai')::date) AS day,
    count(*) AS strength_set_count,
    count(*) FILTER (WHERE st.is_completed IS TRUE) AS completed_set_count,
    sum(st.reps) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS strength_reps,
    sum(st.weight_kg * st.reps)
      FILTER (WHERE st.weight_kg IS NOT NULL AND st.reps IS NOT NULL
              AND st.is_completed IS DISTINCT FROM FALSE) AS strength_volume_kg,
    max(st.weight_kg) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS max_lift_kg
  FROM fitness_workout_sessions s
  JOIN fitness_strength_exercises e
    ON e.source=s.source AND e.external_id=s.external_id
  JOIN fitness_strength_sets st
    ON st.source=e.source AND st.external_id=e.external_id
   AND st.exercise_index=e.exercise_index
  WHERE s.source='lianlian'
  GROUP BY 1
),
health_rollup AS (
  SELECT recorded_date AS day, jsonb_object_agg(metric, metric_rows) AS health_metrics
  FROM (
    SELECT recorded_date, metric, jsonb_agg(raw_data ORDER BY recorded_at) AS metric_rows
    FROM fitness_health_metrics
    GROUP BY recorded_date, metric
  ) grouped
  GROUP BY recorded_date
)
SELECT d.day,
  b.weight_kg, b.bmi, b.body_fat_rate, b.fat_mass_kg, b.muscle_mass_kg,
  b.skeletal_muscle_mass_kg, b.body_age, b.body_score, b.bone_mass_kg,
  b.protein_mass_kg, b.body_water_mass_kg, b.fat_free_mass_kg,
  b.visceral_fat_level, b.basal_metabolism_kcal, b.waist_hip_ratio,
  b.moisture_rate_percent, b.protein_rate_percent, b.segmental_metrics,
  COALESCE(w.xiaomi_workout_count,0) AS xiaomi_workout_count,
  COALESCE(w.strength_session_count,0) AS strength_session_count,
  COALESCE(w.xiaomi_duration_seconds,0) AS xiaomi_duration_seconds,
  COALESCE(w.strength_duration_seconds,0) AS strength_duration_seconds,
  COALESCE(w.xiaomi_calories_kcal,0) AS xiaomi_calories_kcal,
  COALESCE(w.strength_calories_kcal,0) AS strength_calories_kcal,
  COALESCE(sr.strength_set_count,0) AS strength_set_count,
  COALESCE(sr.completed_set_count,0) AS completed_set_count,
  COALESCE(sr.strength_reps,0) AS strength_reps,
  COALESCE(sr.strength_volume_kg,0) AS strength_volume_kg,
  sr.max_lift_kg,
  COALESCE(h.health_metrics,'{}'::jsonb) AS health_metrics
FROM all_days d
LEFT JOIN latest_body b ON b.measured_date=d.day
LEFT JOIN workout_rollup w ON w.day=d.day
LEFT JOIN strength_rollup sr ON sr.day=d.day
LEFT JOIN health_rollup h ON h.day=d.day;

CREATE OR REPLACE VIEW fitness_strength_progression AS
SELECT
  COALESCE(s.session_date, (s.started_at AT TIME ZONE 'Asia/Shanghai')::date) AS session_date,
  e.exercise_name,
  count(*) AS set_count,
  count(*) FILTER (WHERE st.is_completed IS TRUE) AS completed_set_count,
  sum(st.reps) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS total_reps,
  sum(st.weight_kg * st.reps)
    FILTER (WHERE st.weight_kg IS NOT NULL AND st.reps IS NOT NULL
            AND st.is_completed IS DISTINCT FROM FALSE) AS volume_kg,
  max(st.weight_kg) FILTER (WHERE st.is_completed IS DISTINCT FROM FALSE) AS max_weight_kg,
  max(st.weight_kg * (1 + st.reps / 30.0))
    FILTER (WHERE st.weight_kg > 0 AND st.reps > 0
            AND st.is_completed IS DISTINCT FROM FALSE) AS best_estimated_1rm_kg
FROM fitness_workout_sessions s
JOIN fitness_strength_exercises e
  ON e.source=s.source AND e.external_id=s.external_id
JOIN fitness_strength_sets st
  ON st.source=e.source AND st.external_id=e.external_id
 AND st.exercise_index=e.exercise_index
WHERE s.source='lianlian'
GROUP BY 1, e.exercise_name;

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
    for key in ("measured_at", "recorded_at", "started_at", "start_time", "time", "timestamp", "date", "day"):
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

