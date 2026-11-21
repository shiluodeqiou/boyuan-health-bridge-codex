from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

FITNESS_DATA_PATH = "/app/v1/data/get_fitness_data_by_time"
MAX_PAGES = 20
PAGE_SIZE = 100
XIAOMI_TIMEZONE = timezone(timedelta(hours=8))

# Values are kept in the units used by Xiaomi's raw weight record.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "weight": ("weight",),
    "bmi": ("bmi",),
    "body_fat_rate": ("body_fat_rate", "bodyFatRate"),
    "fat_mass": ("fat_mass", "fatMass"),
    "muscle_mass": ("muscle_mass", "muscleMass"),
    "muscle_rate": ("muscle_rate", "muscleRate"),
    "skeletal_muscle_mass": ("skeletal_muscle_mass", "skeletalMuscleMass"),
    "bone_mass": ("bone_mass", "boneMass"),
    "protein_mass": ("protein_mass", "proteinMass"),
    "protein_rate": ("protein_rate", "proteinRate"),
    "body_moisture_mass": ("body_moisture_mass", "bodyMoistureMass"),
    "moisture_rate": ("moisture_rate", "moistureRate"),
    "basal_metabolism": ("basal_metabolism", "basalMetabolism"),
    "visceral_fat": ("visceral_fat", "visceralFat"),
    "body_age": ("body_age", "bodyAge"),
    "metabolic_age": ("metabolic_age", "metabolicAge", "ma"),
    "body_score": ("body_score", "bodyScore"),
    "heart_rate": ("bpm", "heart_rate", "heartRate"),
    "fat_free_mass": ("fat_free_mass", "fatFreeMass"),
    "body_type": ("body_type", "bodyType", "bt"),
    "physical_rating": ("physical_rating", "physicalRating"),
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
    "protein_mass": "kg",
    "protein_rate": "%",
    "body_moisture_mass": "kg",
    "moisture_rate": "%",
    "basal_metabolism": "kcal/day",
    "visceral_fat": "scale_level",
    "body_age": "years",
    "metabolic_age": "years",
    "body_score": "score",
    "heart_rate": "bpm",
    "fat_free_mass": "kg",
    "body_type": "scale_code",
    "physical_rating": "scale_code",
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
    recognized_wire_keys: set[str] = {"time", "data"}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            recognized_wire_keys.add(alias)
            if alias in merged:
                number = _as_number(merged[alias])
                if number is not None:
                    record[field] = number
                break

    if nested_data:
        record["additional_data"] = nested_data
    unknown = sorted(
        str(key) for key in merged
        if key not in recognized_wire_keys and key not in {"createTime", "updateTime", "dataVersion"}
    )
    if unknown:
        record["unmapped_fields"] = unknown
    return record


async def get_body_composition_history(
    client: Any,
    query_date: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """Read raw Xiaomi weight records without the SDK's weight/BMI-only model."""
    start_day, end_day, start_time, end_time = _date_window(query_date, days)
    # Raw dense data may omit the first bucket at start_time; fetch one day early,
    # then enforce the requested inclusive range locally.
    request_start = max(1, start_time - 86400)
    request: dict[str, Any] = {
        "key": "weight",
        "start_time": request_start,
        "end_time": end_time,
        "limit": PAGE_SIZE,
        "reverse": True,
    }
    seen_cursors: set[str] = set()
    seen_rows: set[str] = set()
    records: list[dict[str, Any]] = []
    notes: list[str] = []

    for page in range(MAX_PAGES):
        payload = await client._request("GET", FITNESS_DATA_PATH, params=request)
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError("小米体重接口返回格式异常")
        rows = result.get("data_list", [])
        if not isinstance(rows, list):
            raise RuntimeError("小米体重接口缺少 data_list")

        for row in rows:
            if not isinstance(row, dict) or _is_true(row.get("deleted")):
                continue
            record = _normalize_record(row)
            if record is None or not start_time <= record["time"] <= end_time:
                continue
            identity = row.get("sid")
            if identity:
                dedupe_key = f"sid:{identity}"
            else:
                dedupe_key = json.dumps(record, sort_keys=True, ensure_ascii=False)
            if dedupe_key in seen_rows:
                continue
            seen_rows.add(dedupe_key)
            records.append(record)

        if not _is_true(result.get("has_more")):
            break
        cursor = result.get("next_key")
        if cursor is None or not str(cursor) or str(cursor) in seen_cursors:
            notes.append("体重记录分页游标缺失或重复，已停止读取，结果可能不完整。")
            break
        cursor_text = str(cursor)
        seen_cursors.add(cursor_text)
        request = {
            "key": "weight",
            "start_time": request_start,
            "end_time": end_time,
            "limit": PAGE_SIZE,
            "reverse": True,
            "next_key": cursor_text,
        }
        if page == MAX_PAGES - 1:
            notes.append("体重记录达到单次读取页数上限，较早记录可能未包含。")

    records.sort(key=lambda item: item["time"], reverse=True)
    composition_fields = [
        field for field in FIELD_ALIASES
        if field not in {"weight", "bmi"}
        and any(field in record for record in records)
    ]
    available_fields = [
        field for field in FIELD_ALIASES
        if any(field in record for record in records)
    ]
    return {
        "query_date": end_day.isoformat(),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "days": days,
        "count": len(records),
        "composition_available": bool(composition_fields),
        "available_fields": available_fields,
        "composition_fields": composition_fields,
        "units": {field: FIELD_UNITS[field] for field in available_fields},
        "data": records,
        "notes": notes,
        "notice": (
            "身体成分数据来自小米体脂秤及小米云端记录；体脂率、肌肉量等属于生物电阻抗估算值，"
            "建议结合连续趋势观察，不要将单次读数视为精确的医学测量。"
        ),
    }
