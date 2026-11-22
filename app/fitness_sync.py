from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from app.fitness_db import CN_TZ, FitnessDatabase
from app.lianlian import get_training_records_page
from app.health import HealthBridge


HEALTH_METRICS = (
    "heart_rate",
    "sleep",
    "steps",
    "calories",
    "spo2",
    "intensity",
    "valid_stand",
    "blood_pressure",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _duration_seconds(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        return max(0, numbers[0] * 60 + numbers[1])
    if len(numbers) == 3:
        return max(0, numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
    return None


def normalize_lianlian_record(
    raw: dict[str, Any],
    *,
    page: int,
    skill_version: str,
    start_date: str,
    end_date: str,
    page_size: int,
) -> dict[str, Any]:
    """Map KeepStrong v1.29 records while retaining each exact source JSON object."""
    record_id = raw.get("id")
    day_str = raw.get("dayStr")
    if record_id in (None, ""):
        raise ValueError("练练训练记录缺少 id")
    if not isinstance(day_str, str) or len(day_str) != 8 or not day_str.isdigit():
        raise ValueError(f"练练训练记录 {record_id} 的 dayStr 无效")

    from datetime import datetime as _datetime
    try:
        session_day = _datetime.strptime(day_str, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"练练训练记录 {record_id} 的 dayStr 不是有效日期") from exc

    exercises: list[dict[str, Any]] = []
    actions = raw.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError(f"练练训练记录 {record_id} 的 actions 不是数组")

    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = str(action.get("name") or "").strip()
        if not action_name:
            raise ValueError(f"练练训练记录 {record_id} 存在缺少名称的动作")
        weight_unit = action.get("weightUnit")
        unit_is_kg = str(weight_unit or "").strip().lower() == "kg"
        raw_groups = action.get("groups", [])
        if not isinstance(raw_groups, list):
            raise ValueError(f"练练动作 {action_name} 的 groups 不是数组")

        sets: list[dict[str, Any]] = []
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            weight_value = _number(group.get("weight"))
            rm1_value = _number(group.get("rm1Weight"))
            is_done = group.get("isDone")
            is_new_rm = group.get("isNewRM1Record")
            sets.append({
                "provider_set_id": group.get("_id"),
                "weight_value": weight_value,
                "weight_unit": weight_unit,
                "weight_kg": weight_value if unit_is_kg else None,
                "reps": _integer(group.get("reps")),
                "duration_seconds": _duration_seconds(
                    group.get("time") or group.get("durationSeconds")
                ),
                "set_time_text": group.get("time"),
                "rest_seconds": _integer(group.get("realRestSeconds")),
                "planned_rest_seconds": _integer(action.get("restSeconds")),
                "is_completed": is_done if isinstance(is_done, bool) else None,
                "completed_at": group.get("doneTs"),
                "is_new_rm1_record": is_new_rm if isinstance(is_new_rm, bool) else None,
                "rm1_weight_kg": rm1_value if unit_is_kg else None,
                "heart_rate_data": group.get("hr"),
                "flag": group.get("flag"),
                "comb_groups": group.get("combGroups") if isinstance(group.get("combGroups"), list) else [],
                "raw_data": group,
            })

        exercises.append({
            "name": action_name,
            "action_id": action.get("actionId"),
            "weight_unit": weight_unit,
            "capacity": _number(action.get("capacity")),
            "planned_rest_seconds": _integer(action.get("restSeconds")),
            "weight_tag": action.get("weightTag") if isinstance(action.get("weightTag"), dict) else {},
            "comb_actions": action.get("combActions") if isinstance(action.get("combActions"), list) else [],
            "sets": sets,
            "raw_data": action,
        })

    avg_hr = _number(raw.get("avgHeartRate"))
    return {
        "id": str(record_id),
        "date": session_day.isoformat(),
        "session_date": session_day.isoformat(),
        # KeepStrong list records expose dayStr, not an exact session start timestamp.
        "started_at": None,
        "ended_at": None,
        "name": raw.get("name") or "力量训练",
        "record_type": raw.get("recordType"),
        "training_type": raw.get("trainingType"),
        "status": raw.get("status"),
        "duration_seconds": _integer(raw.get("durationSeconds")),
        "calories_kcal": _number(raw.get("calorie")),
        "training_capacity": _number(raw.get("capacity")),
        "average_heart_rate_bpm": avg_hr if avg_hr and avg_hr > 0 else None,
        "exercises": exercises,
        "raw_data": raw,
        "source_metadata": {
            "provider": "keepstrong",
            "skill_version": skill_version,
            "query": {"startDate": start_date, "endDate": end_date},
            "page": page,
            "page_size": page_size,
        },
    }


async def fetch_all_lianlian_records(
    *,
    start_date: str,
    end_date: str,
    page_size: int = 50,
    max_pages: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read every page first; return only after the provider confirms hasMore=false."""
    records_by_id: dict[str, dict[str, Any]] = {}
    page = 1
    pages_fetched = 0
    skill_version = "unknown"

    while page <= max_pages:
        result = await get_training_records_page(
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
        response = result.get("response")
        if not isinstance(response, dict):
            raise RuntimeError(f"练练第 {page} 页没有返回 response 对象")
        raw_list = response.get("list")
        if not isinstance(raw_list, list):
            raise RuntimeError(f"练练第 {page} 页的 response.list 不是数组")
        has_more = response.get("hasMore")
        if not isinstance(has_more, bool):
            raise RuntimeError(f"练练第 {page} 页缺少布尔类型 hasMore，停止导入")
        if has_more and not raw_list:
            raise RuntimeError(f"练练第 {page} 页为空但 hasMore=true，停止导入")

        skill_version = str(result.get("skill_version") or skill_version)
        for raw in raw_list:
            if not isinstance(raw, dict):
                raise RuntimeError(f"练练第 {page} 页包含非对象训练记录")
            normalized = normalize_lianlian_record(
                raw,
                page=page,
                skill_version=skill_version,
                start_date=start_date,
                end_date=end_date,
                page_size=page_size,
            )
            records_by_id[normalized["id"]] = normalized

        pages_fetched += 1
        if not has_more:
            break
        page += 1
    else:
        raise RuntimeError(f"练练分页超过安全上限 {max_pages} 页，未写入数据库")

    metadata = {
        "provider": "keepstrong",
        "skill_version": skill_version,
        "start_date": start_date,
        "end_date": end_date,
        "page_size": page_size,
        "pages_fetched": pages_fetched,
        "has_more": False,
        "record_count": len(records_by_id),
    }
    return list(records_by_id.values()), metadata


async def sync_lianlian_to_database(
    database: FitnessDatabase,
    *,
    days: int = 30,
    page_size: int = 50,
) -> dict[str, Any]:
    if not 1 <= days <= 3650:
        raise ValueError("练练健身同步 days 必须在 1 到 3650 之间")
    today = datetime.now(CN_TZ).date()
    start = today - timedelta(days=days - 1)
    start_date = start.strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")

    records, metadata = await fetch_all_lianlian_records(
        start_date=start_date,
        end_date=end_date,
        page_size=page_size,
    )
    result = await database.store_lianlian(records)
    result.update(metadata)
    result["status"] = "success"
    return result


async def sync_xiaomi_to_database(
    bridge: Any,
    database: FitnessDatabase,
    *,
    days: int = 30,
    include_sport_details: bool = False,
) -> dict[str, Any]:
    """Collect Xiaomi own-account data and idempotently upsert it into PostgreSQL."""
    if not 1 <= days <= 30:
        raise ValueError("小米运动和日常健康历史同步的 days 必须在 1 到 30 之间")

    body_composition, sport_records = await asyncio.gather(
        bridge.body_composition_history(days=days),
        bridge.sport_records(days=days),
    )

    async def read_metric(metric: str) -> tuple[str, list[dict[str, Any]] | None]:
        try:
            result = await bridge.history(None, metric, None, days)
            rows = result.get("data", [])
            return metric, rows if isinstance(rows, list) else []
        except Exception:
            return metric, None

    metric_results = await asyncio.gather(
        *(read_metric(metric) for metric in HEALTH_METRICS)
    )
    health_metrics = dict(metric_results)

    details: dict[str, dict[str, Any]] = {}
    detail_errors: dict[str, str] = {}
    if include_sport_details:
        eligible = [
            row for row in sport_records.get("records", [])
            if isinstance(row, dict)
            and row.get("proto_type") in (8, 22, 23)
            and row.get("workout_time")
        ][:60]
        semaphore = asyncio.Semaphore(3)

        async def read_detail(row: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
            external_id = str(row["workout_time"])
            async with semaphore:
                try:
                    result = await bridge.sport_detail(int(row["workout_time"]))
                    return external_id, result, None
                except Exception as exc:
                    return external_id, None, type(exc).__name__

        detail_results = await asyncio.gather(*(read_detail(row) for row in eligible))
        for external_id, result, error in detail_results:
            if result is not None:
                details[external_id] = result
            if error:
                detail_errors[external_id] = error

    result = await database.store_xiaomi(
        days=days,
        body_composition=body_composition,
        sport_records=sport_records,
        health_metrics=health_metrics,
        sport_details=details,
        sport_detail_errors=detail_errors,
    )
    result.update({
        "body_history_complete": body_composition.get("history_complete"),
        "body_history_pages": body_composition.get("pages_fetched"),
        "sport_record_count": sport_records.get("count", 0),
        "health_metrics_read": {
            key: len(value) if isinstance(value, list) else None
            for key, value in health_metrics.items()
        },
        "health_metric_errors": [
            key for key, value in health_metrics.items() if value is None
        ],
        "sport_detail_errors": detail_errors,
        "sport_detail_count": len(details),
    })
    result["status"] = (
        "success"
        if body_composition.get("history_complete") is not False
        and not result["health_metric_errors"]
        and not detail_errors
        else "partial"
    )
    return result


async def sync_unified_fitness_data(
    bridge: HealthBridge,
    database: FitnessDatabase,
    *,
    days: int = 30,
    include_sport_details: bool = False,
) -> dict[str, Any]:
    """Synchronize Xiaomi and KeepStrong into the same idempotent fitness database."""
    if not 1 <= days <= 30:
        raise ValueError("统一同步 days 必须在 1 到 30 之间")

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    try:
        results["xiaomi"] = await sync_xiaomi_to_database(
            bridge, database, days=days, include_sport_details=include_sport_details
        )
        if results["xiaomi"].get("status") != "success":
            errors["xiaomi"] = "小米数据部分指标或分页未完整读取"
    except Exception as exc:
        errors["xiaomi"] = f"{type(exc).__name__}: {str(exc)[:500]}"

    try:
        results["lianlian"] = await sync_lianlian_to_database(database, days=days)
    except Exception as exc:
        errors["lianlian"] = f"{type(exc).__name__}: {str(exc)[:500]}"

    return {
        "database": "postgresql",
        "status": "success" if not errors else "partial",
        "window_days": days,
        "sources": results,
        "errors": errors,
        "idempotent": True,
    }
