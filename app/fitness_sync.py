from __future__ import annotations

import asyncio
from typing import Any

from app.fitness_db import FitnessDatabase


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
    return result
