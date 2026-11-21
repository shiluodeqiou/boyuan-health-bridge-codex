from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

FITNESS_DATA_PATH = "/app/v1/data/get_fitness_data_by_time"
MAX_PAGES = 200
XIAOMI_TIMEZONE = timezone(timedelta(hours=8))

# Preserve Xiaomi's wire names as normalized snake_case fields.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "weight": ("weight",),
    "bmi": ("bmi",),
    "body_fat_rate": ("body_fat_rate", "bodyFatRate"),
    "fat_mass": ("fat_mass", "fatMass"),
    "muscle_mass": ("muscle_mass", "muscleMass"),
    "muscle_rate": ("muscle_rate", "muscleRate"),
    "skeletal_muscle_mass": ("skeletal_muscle_mass", "skeletalMuscleMass"),
    "bone_mass": ("bone_mass", "boneMass"),
    "bone_rate": ("bone_rate", "boneRate"),
    "protein_mass": ("protein_mass", "proteinMass"),
    "protein_rate": ("protein_rate", "proteinRate"),
    "body_moisture_mass": ("body_moisture_mass", "bodyMoistureMass"),
    "moisture_rate": ("moisture_rate", "moistureRate"),
    "basal_metabolism": ("basal_metabolism", "basalMetabolism"),
    "visceral_fat": ("visceral_fat", "visceralFat"),
    "body_age": ("body_age", "bodyAge"),
    "body_score": ("body_score", "bodyScore"),
    "heart_rate": ("bpm", "heart_rate", "heartRate"),
    "fat_free_mass": ("fat_free_body", "fat_free_mass", "fatFreeBody", "fatFreeMass"),
    "fat_control": ("fat_control", "fatControl"),
    "muscle_control": ("muscle_control", "muscleControl"),
    "weight_control": ("weight_control", "weightControl"),
    "standard_weight": ("standard_weight", "standardWeight"),
    "standard_weight_v2": ("standard_weight_v2", "standardWeightV2"),
    "whr": ("whr", "waist_hip_ratio", "waistHipRatio"),
    "somatotype": ("somatotype",),
    "body_shape": ("body_shape", "bodyShape"),
    "score_standard_type": ("score_standard_type", "scoreStandardType"),
    "left_upper_limb_fat_mass": ("left_upper_limb_fat_mass", "leftUpperLimbFatMass"),
    "left_upper_limb_fat_rank": ("left_upper_limb_fat_rank", "leftUpperLimbFatRank"),
    "left_upper_limb_muscle_mass": ("left_upper_limb_muscle_mass", "leftUpperLimbMuscleMass"),
    "left_upper_limb_muscle_rank": ("left_upper_limb_muscle_rank", "leftUpperLimbMuscleRank"),
    "right_upper_limb_fat_mass": ("right_upper_limb_fat_mass", "rightUpperLimbFatMass"),
    "right_upper_limb_fat_rank": ("right_upper_limb_fat_rank", "rightUpperLimbFatRank"),
    "right_upper_limb_muscle_mass": ("right_upper_limb_muscle_mass", "rightUpperLimbMuscleMass"),
    "right_upper_limb_muscle_rank": ("right_upper_limb_muscle_rank", "rightUpperLimbMuscleRank"),
    "left_lower_limb_fat_mass": ("left_lower_limb_fat_mass", "leftLowerLimbFatMass"),
    "left_lower_limb_fat_rank": ("left_lower_limb_fat_rank", "leftLowerLimbFatRank"),
    "left_lower_limb_muscle_mass": ("left_lower_limb_muscle_mass", "leftLowerLimbMuscleMass"),
    "left_lower_limb_muscle_rank": ("left_lower_limb_muscle_rank", "leftLowerLimbMuscleRank"),
    "right_lower_limb_fat_mass": ("right_lower_limb_fat_mass", "rightLowerLimbFatMass"),
    "right_lower_limb_fat_rank": ("right_lower_limb_fat_rank", "rightLowerLimbFatRank"),
    "right_lower_limb_muscle_mass": ("right_lower_limb_muscle_mass", "rightLowerLimbMuscleMass"),
    "right_lower_limb_muscle_rank": ("right_lower_limb_muscle_rank", "rightLowerLimbMuscleRank"),
    "trunk_fat_mass": ("trunk_fat_mass", "trunkFatMass"),
    "trunk_fat_rank": ("trunk_fat_rank", "trunkFatRank"),
    "trunk_muscle_mass": ("trunk_muscle_mass", "trunkMuscleMass"),
    "trunk_muscle_rank": ("trunk_muscle_rank", "trunkMuscleRank"),
    "limbs_fat_balance": ("limbs_fat_balance", "limbsFatBalance"),
    "limbs_muscle_balance": ("limbs_muscle_balance", "limbsMuscleBalance"),
    "limbs_skeletal_muscle_index": ("limbs_skeletal_muscle_index", "limbsSkeletalMuscleIndex"),
    "lower_limb_fat_balance": ("lower_limb_fat_balance", "lowerLimbFatBalance"),
    "lower_limb_muscle_balance": ("lower_limb_muscle_balance", "lowerLimbMuscleBalance"),
    "upper_limb_fat_balance": ("upper_limb_fat_balance", "upperLimbFatBalance"),
    "upper_limb_muscle_balance": ("upper_limb_muscle_balance", "upperLimbMuscleBalance"),
    "recommended_calories_intake": ("recommended_calories_intake", "recommendedCaloriesIntake"),
    "physical_rating": ("physical_rating", "physicalRating"),
    "body_type": ("body_type", "bodyType", "bt"),
    "metabolic_age": ("metabolic_age", "metabolicAge", "ma"),
}

FIELD_UNITS = {
    "weight": "kg",
    "bmi": "kg/m²",
    "body_fat_rate": "%",
    "fat_mass": "kg",
    "muscle_mass": "kg",
    "muscle_rate": "%",
    "skeletal_muscle_mass": "kg",
    "bone_mass": "kg",
    "bone_rate": "%",
    "protein_mass": "kg",
    "protein_rate": "%",
    "body_moisture_mass": "kg",
    "moisture_rate": "%",
    "basal_metabolism": "kcal/day",
    "visceral_fat": "scale_level",
    "body_age": "years",
    "body_score": "score",
    "heart_rate": "bpm",
    "fat_free_mass": "kg",
    "fat_control": "kg",
    "muscle_control": "kg",
    "weight_control": "kg",
    "standard_weight": "kg",
    "standard_weight_v2": "kg",
    "whr": "ratio",
    "somatotype": "scale_code",
    "body_shape": "scale_code",
    "score_standard_type": "scale_code",
    "left_upper_limb_fat_mass": "kg",
    "left_upper_limb_fat_rank": "scale_rank",
    "left_upper_limb_muscle_mass": "kg",
    "left_upper_limb_muscle_rank": "scale_rank",
    "right_upper_limb_fat_mass": "kg",
    "right_upper_limb_fat_rank": "scale_rank",
    "right_upper_limb_muscle_mass": "kg",
    "right_upper_limb_muscle_rank": "scale_rank",
    "left_lower_limb_fat_mass": "kg",
    "left_lower_limb_fat_rank": "scale_rank",
    "left_lower_limb_muscle_mass": "kg",
    "left_lower_limb_muscle_rank": "scale_rank",
    "right_lower_limb_fat_mass": "kg",
    "right_lower_limb_fat_rank": "scale_rank",
    "right_lower_limb_muscle_mass": "kg",
    "right_lower_limb_muscle_rank": "scale_rank",
    "trunk_fat_mass": "kg",
    "trunk_fat_rank": "scale_rank",
    "trunk_muscle_mass": "kg",
    "trunk_muscle_rank": "scale_rank",
    "limbs_fat_balance": "scale_code",
    "limbs_muscle_balance": "scale_code",
    "limbs_skeletal_muscle_index": "scale_index",
    "lower_limb_fat_balance": "scale_code",
    "lower_limb_muscle_balance": "scale_code",
    "upper_limb_fat_balance": "scale_code",
    "upper_limb_muscle_balance": "scale_code",
    "recommended_calories_intake": "kcal/day",
    "physical_rating": "scale_code",
    "body_type": "scale_code",
    "metabolic_age": "years",
}

_METADATA_KEYS = {
    "time", "data", "createTime", "updateTime", "dataVersion",
    "deviceId", "device_id", "accountId", "account_id", "userId", "user_id",
    "id", "sid", "model", "sn", "fromSource",
}


def _decode_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError, OverflowError):
            return None
        return int(number) if number.is_integer() else number
    return None


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "y"}


def _epoch_seconds(value: Any) -> int | None:
    result = _as_int(value)
    if result is not None and result > 10**12:
        result //= 1000
    return result if result is not None and result > 0 else None


def _date_window(query_date: str | None, days: int) -> tuple[date, date, int, int]:
    if not 1 <= days <= 3650:
        raise ValueError("days 必须在 1 到 3650 之间")
    end_day = date.fromisoformat(query_date) if query_date else datetime.now(XIAOMI_TIMEZONE).date()
    start_day = end_day - timedelta(days=days - 1)
    start_dt = datetime.combine(start_day, time.min, tzinfo=XIAOMI_TIMEZONE)
    end_dt = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=XIAOMI_TIMEZONE)
    return start_day, end_day, int(start_dt.timestamp()), int(end_dt.timestamp()) - 1


def _normalize_record(row: dict[str, Any]) -> dict[str, Any] | None:
    value = _decode_object(row.get("value"))
    if not value:
        return None
    nested_data = _decode_object(value.get("data"))
    merged = {**nested_data, **value}
    timestamp = _epoch_seconds(row.get("time")) or _epoch_seconds(merged.get("time"))
    if timestamp is None:
        return None
    offset = _as_int(row.get("zone_offset"))
    if offset is None or abs(offset) > 14 * 3600:
        offset = 8 * 3600
    try:
        measured_at = datetime.fromtimestamp(
            timestamp, timezone(timedelta(seconds=offset))
        ).isoformat()
    except (OverflowError, OSError, ValueError):
        measured_at = datetime.fromtimestamp(timestamp, XIAOMI_TIMEZONE).isoformat()

    record: dict[str, Any] = {
        "time": timestamp,
        "measured_at": measured_at,
        "timezone_offset_seconds": offset,
    }
    recognized_wire_keys: set[str] = set(_METADATA_KEYS)
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            recognized_wire_keys.add(alias)
            if alias in merged:
                number = _as_number(merged[alias])
                if number is not None:
                    record[field] = number
                elif isinstance(merged[alias], str) and field in {
                    "somatotype", "body_shape", "score_standard_type", "body_type", "physical_rating"
                }:
                    record[field] = merged[alias]
                break

    raw_fields = {
        str(key): item
        for key, item in merged.items()
        if key not in recognized_wire_keys and key not in _METADATA_KEYS
    }
    if nested_data:
        record["additional_data"] = nested_data
    if raw_fields:
        record["raw_fields"] = raw_fields
        record["unmapped_fields"] = sorted(raw_fields)
    if row.get("sid"):
        record["record_id"] = str(row["sid"])
    return record


def _merge_records(
    records: list[dict[str, Any]],
    row: dict[str, Any],
    source: str,
    index: dict[str, int],
) -> None:
    record = _normalize_record(row)
    if record is None:
        return
    sid = str(row.get("sid") or "")
    weight = record.get("weight")
    bmi = record.get("bmi")
    if sid:
        identity = f"sid:{sid}:time:{record['time']}"
    elif weight is not None:
        identity = f"measurement:{record['time']}:{weight}:{bmi}"
    else:
        identity = json.dumps(record, sort_keys=True, ensure_ascii=False)

    record["sources"] = [source]
    if identity not in index:
        index[identity] = len(records)
        records.append(record)
        return

    existing = records[index[identity]]
    for key, value in record.items():
        if key == "sources":
            existing["sources"] = sorted(set(existing.get("sources", []) + value))
        elif key == "raw_fields":
            existing.setdefault("raw_fields", {}).update(value)
        elif key == "additional_data":
            existing.setdefault("additional_data", {}).update(value)
        elif key == "unmapped_fields":
            existing["unmapped_fields"] = sorted(set(existing.get(key, []) + value))
        elif key not in existing or existing[key] in (None, "", 0, 0.0):
            existing[key] = value


async def _fetch_weight_rows(
    client: Any,
    start_time: int,
    end_time: int,
    notes: list[str],
) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Read the owner's own-account weight stream using its native cursor contract.

    The own-account endpoint is not the relatives endpoint: it takes camelCase
    millisecond bounds and a snake_case next_key cursor, and does not take
    relative_uid or limit.
    """
    end_time_ms = end_time * 1000
    request: dict[str, Any] = {
        "key": "weight",
        # Xiaomi's cursor can freeze when startTime is 0/1 or too old.
        # A recent one-day anchor plus next_key walks backwards through history.
        "startTime": max(1, end_time_ms - 86_400_000),
        "endTime": end_time_ms,
        "reverse": True,
    }
    rows_out: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    fetched_count = 0
    pages_fetched = 0
    has_more = False

    for page in range(MAX_PAGES):
        payload = await client._request("GET", FITNESS_DATA_PATH, params=request)
        if not isinstance(payload, dict):
            raise RuntimeError("小米体重接口返回格式异常")
        code = payload.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(f"小米体重接口返回错误 code={code}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("小米体重接口缺少 result")
        rows = result.get("data_list", [])
        if not isinstance(rows, list):
            raise RuntimeError("小米体重接口缺少 data_list")

        pages_fetched += 1
        page_timestamps: list[int] = []
        for row in rows:
            if not isinstance(row, dict) or _is_true(row.get("deleted")):
                continue
            timestamp = _epoch_seconds(row.get("time"))
            if timestamp is None:
                value = _decode_object(row.get("value"))
                timestamp = _epoch_seconds(value.get("time"))
            if timestamp is None:
                continue
            page_timestamps.append(timestamp)
            if start_time <= timestamp <= end_time:
                rows_out.append(row)
                fetched_count += 1

        # The own-account API returns newest-first pages. Once the entire page
        # is older than the requested window, all later cursor pages are older.
        if page_timestamps and max(page_timestamps) < start_time:
            has_more = False
            break

        if not _is_true(result.get("has_more")):
            has_more = False
            break

        cursor = result.get("next_key")
        if cursor is None or not str(cursor) or str(cursor) in seen_cursors:
            notes.append("体重记录分页游标缺失或重复，已停止读取，结果可能不完整。")
            has_more = True
            break

        cursor_text = str(cursor)
        seen_cursors.add(cursor_text)
        request = {
            "key": "weight",
            "startTime": max(1, end_time_ms - 86_400_000),
            "endTime": end_time_ms,
            "reverse": True,
            "next_key": cursor_text,
        }
        if page == MAX_PAGES - 1:
            has_more = True
            notes.append("体重记录达到单次读取页数上限，仍有历史页未读取；请缩短查询范围。")

    return rows_out, fetched_count, pages_fetched, has_more


async def get_body_composition_history(
    client: Any,
    query_date: str | None = None,
    days: int = 30,
    *,
) -> dict[str, Any]:
    """Read the owner's full raw weight/body-composition history."""
    start_day, end_day, start_time, end_time = _date_window(query_date, days)

    notes: list[str] = []
    rows, fetched_count, pages_fetched, has_more = await _fetch_weight_rows(
        client, start_time, end_time, notes
    )
    records: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for row in rows:
        _merge_records(records, row, "get_fitness_data_by_time", index)

    records.sort(key=lambda item: item["time"], reverse=True)
    available_fields = [
        field for field in FIELD_ALIASES
        if any(field in record for record in records)
    ]
    composition_fields = [
        field for field in available_fields if field not in {"weight", "bmi"}
    ]
    field_coverage = {
        field: sum(field in record for record in records)
        for field in available_fields
    }
    return {
        "query_date": end_day.isoformat(),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "days": days,
        "count": len(records),
        "fetched_count": fetched_count,
        "pages_fetched": pages_fetched,
        "has_more": has_more,
        "history_complete": not has_more,
        "records_with_composition": sum(
            any(field in record for field in composition_fields) for record in records
        ),
        "composition_available": bool(composition_fields),
        "available_fields": available_fields,
        "composition_fields": composition_fields,
        "field_coverage": field_coverage,
        "units": {field: FIELD_UNITS[field] for field in available_fields},
        "data": records,
        "notes": notes,
        "notice": (
            "身体成分数据来自小米体脂秤及小米云端记录；体脂率、肌肉量等属于生物电阻抗估算值，"
            "建议结合连续趋势观察，不要将单次读数视为精确的医学测量。"
        ),
    }
