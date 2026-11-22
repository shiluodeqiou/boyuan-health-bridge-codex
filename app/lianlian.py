from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

SKILL_URL = "https://lianlian.gzyunke.cn/api/v1/ai/skill"
API_BASE_URL = "https://lianlian.gzyunke.cn"
SKILL_VERSION = "1.29.0"
AGENT_NAME = "ChatGPT"

_TRAINING_RECORDS_HTTP_CLIENT: ContextVar[httpx.AsyncClient | None] = ContextVar(
    "training_records_http_client", default=None
)


@asynccontextmanager
async def training_records_http_session():
    """Reuse one HTTP connection pool across a complete paginated sync."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
    ) as client:
        token = _TRAINING_RECORDS_HTTP_CLIENT.set(client)
        try:
            yield
        finally:
            _TRAINING_RECORDS_HTTP_CLIENT.reset(token)


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



def _api_headers(token: str, skill_version: str = SKILL_VERSION) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-KeepStrong-Skill-Version": skill_version,
        "X-KeepStrong-Agent-Name": AGENT_NAME,
        "User-Agent": "BoyuanHealthBridge/1.0",
    }


def _validate_training_query(start_date: str, end_date: str, page: int, page_size: int) -> None:
    for name, value in (("start_date", start_date), ("end_date", end_date)):
        if not re.fullmatch(r"\d{8}", value or ""):
            raise ValueError(f"{name} 必须使用 yyyyMMdd 格式")
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise ValueError(f"{name} 不是有效日期") from exc
    if start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")
    if page < 1:
        raise ValueError("page 必须大于等于 1")
    if not 1 <= page_size <= 50:
        raise ValueError("page_size 必须在 1 到 50 之间")


def _error_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {"detail": value}
    except ValueError:
        return {"detail": response.text[:2000]}


async def _get_training_records_request(
    token: str,
    *,
    start_date: str,
    end_date: str,
    page: int,
    page_size: int,
    skill_version: str,
) -> httpx.Response:
    headers = _api_headers(token, skill_version)
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "page": page,
        "pageSize": page_size,
    }
    client = _TRAINING_RECORDS_HTTP_CLIENT.get()
    if client is not None:
        return await client.get(
            f"{API_BASE_URL}/api/v1/ai/training/records",
            headers=headers,
            params=params,
        )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
    ) as owned_client:
        return await owned_client.get(
            f"{API_BASE_URL}/api/v1/ai/training/records",
            headers=headers,
            params=params,
        )


async def get_training_records_page(
    *,
    start_date: str,
    end_date: str,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Read one raw KeepStrong training-record page; no data is written."""
    _validate_training_query(start_date, end_date, page, page_size)
    token = _token()
    if not token:
        raise RuntimeError("练练健身尚未配置 LIANLIAN_API_TOKEN")

    version = SKILL_VERSION
    response = await _get_training_records_request(
        token,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
        skill_version=version,
    )

    if response.status_code == 426:
        error = _error_payload(response)
        if error.get("skillUpdateRequired") is True:
            manifest = await get_skill_manifest()
            document = manifest.get("skill_document")
            if isinstance(document, dict) and document.get("version"):
                version = str(document["version"])
                response = await _get_training_records_request(
                    token,
                    start_date=start_date,
                    end_date=end_date,
                    page=page,
                    page_size=page_size,
                    skill_version=version,
                )
        if response.status_code == 426:
            raise RuntimeError("练练健身要求更新技能版本；请重新读取最新技能说明后再试")

    if response.status_code == 401:
        error = _error_payload(response)
        if error.get("apiKeyExpired") is True:
            raise RuntimeError(
                "练练健身 API Key 已过期。请打开 练练健身 > 我的 > 连接 AI，"
                "重新生成 API Key，点击 Copy AI connection details，并更新 Render 的 "
                "LIANLIAN_API_TOKEN。"
            )
        raise RuntimeError("练练健身 API 鉴权失败（HTTP 401），请检查 Render 环境变量")

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        wait_seconds: float | None = None
        try:
            wait_seconds = float(retry_after)
        except (TypeError, ValueError):
            try:
                wait_seconds = max(
                    0.0,
                    (parsedate_to_datetime(retry_after) - datetime.now(parsedate_to_datetime(retry_after).tzinfo)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                wait_seconds = None
        if wait_seconds is not None and 0 < wait_seconds <= 60:
            await asyncio.sleep(wait_seconds)
            response = await _get_training_records_request(
                token,
                start_date=start_date,
                end_date=end_date,
                page=page,
                page_size=page_size,
                skill_version=version,
            )
        else:
            raise RuntimeError(
                f"练练健身 API 请求频率受限（HTTP 429）；Retry-After={retry_after or '未提供'}，"
                "本次未自动重试。"
            )

    if response.status_code >= 400:
        error = _error_payload(response)
        raise RuntimeError(
            f"练练健身训练记录接口失败（HTTP {response.status_code}）："
            f"{str(error)[:1500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("练练健身训练记录接口返回的不是有效 JSON") from exc
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("练练健身训练记录接口返回的 JSON 类型无效")
    return {
        "provider": "keepstrong",
        "skill_version": version,
        "page": page,
        "page_size": page_size,
        "query": {"startDate": start_date, "endDate": end_date},
        "response": payload,
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
        if not any(
            record.get(key) not in (None, "")
            for key in ("started_at", "start_time", "time", "date", "workout_date", "created_at")
        ):
            raise ValueError(f"第 {index + 1} 条训练记录缺少训练时间")
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
