from __future__ import annotations

import os
from typing import Any

import httpx

SKILL_URL = "https://lianlian.gzyunke.cn/api/v1/ai/skill"


def _token() -> str:
    return os.getenv("LIANLIAN_API_TOKEN", "").strip()


def connection_status() -> dict[str, Any]:
    return {
        "configured": bool(_token()),
        "provider": "lianlian",
        "mode": "read_only_import",
    }


async def get_skill_manifest() -> dict[str, Any]:
    """Fetch the current Lianlian AI skill contract without exposing its bearer token."""
    token = _token()
    if not token:
        return {
            "configured": False,
            "provider": "lianlian",
            "message": (
                "练练健身尚未配置 API Token。请在 Render 环境变量中设置 "
                "LIANLIAN_API_TOKEN；不要把 Token 写入 Git 或聊天消息。"
            ),
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "BoyuanHealthBridge/1.0",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0), follow_redirects=False) as client:
        response = await client.get(SKILL_URL, headers=headers)

    if response.status_code == 401:
        raise RuntimeError("练练健身 API Token 无效或已过期，请在 Render 更新 LIANLIAN_API_TOKEN")
    if response.status_code == 403:
        raise RuntimeError("练练健身拒绝访问当前 API Token（HTTP 403）")
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    try:
        document: Any = response.json()
    except ValueError:
        document = response.text

    if isinstance(document, str) and len(document) > 120_000:
        document = document[:120_000] + "\n[文档超过 120 KB，已截断]"
    return {
        "configured": True,
        "provider": "lianlian",
        "status_code": response.status_code,
        "content_type": content_type,
        "skill_document": document,
        "notice": (
            "这是练练健身当前返回的技能说明。应先按该说明确认只读训练记录接口和字段，"
            "不要猜测未在文档中出现的端点。"
        ),
    }


def validate_normalized_workouts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the bridge's stable import shape before database insertion."""
    if not isinstance(records, list):
        raise ValueError("records 必须是数组")
    if len(records) > 5000:
        raise ValueError("单次最多导入 5000 条训练记录")

    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"第 {index + 1} 条训练记录不是对象")
        external_id = next(
            (record.get(key) for key in ("id", "record_id", "workout_id", "session_id") if record.get(key) not in (None, "")),
            None,
        )
        if external_id is None:
            raise ValueError(f"第 {index + 1} 条训练记录缺少 id")
        exercises = record.get("exercises", [])
        if not isinstance(exercises, list):
            raise ValueError(f"第 {index + 1} 条训练记录的 exercises 必须是数组")
        for exercise_index, exercise in enumerate(exercises):
            if not isinstance(exercise, dict) or not str(
                exercise.get("name") or exercise.get("exercise_name") or ""
            ).strip():
                raise ValueError(
                    f"第 {index + 1} 条训练记录的第 {exercise_index + 1} 个动作缺少名称"
                )
            sets = exercise.get("sets", [])
            if not isinstance(sets, list):
                raise ValueError("每个动作的 sets 必须是数组")
            for set_index, set_row in enumerate(sets):
                if not isinstance(set_row, dict):
                    raise ValueError(
                        f"第 {index + 1} 条训练记录的动作组 {set_index + 1} 格式无效"
                    )
        normalized.append(record)
    return normalized
