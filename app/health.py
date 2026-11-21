from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from mi_fitness import AuthToken, MiHealthClient, XiaomiAuth
from mi_fitness.client import data as _data
from pydantic import BaseModel, ConfigDict, ValidationError

from app.sports import get_sport_detail as read_sport_detail
from app.sports import get_sport_records as read_sport_records

Metric = Literal[
    "heart_rate",
    "sleep",
    "steps",
    "calories",
    "spo2",
    "intensity",
    "valid_stand",
    "weight",
    "blood_pressure",
]

_RELATIVE_LIST_KEYS = (
    "relative_list",
    "relativeList",
    "relatives",
    "family_user_list",
    "familyUserList",
    "member_list",
    "memberList",
    "list",
    "data",
)
_RELATIVE_UID_KEYS = (
    "relative_uid",
    "relativeUid",
    "relative_user_id",
    "relativeUserId",
    "user_id",
    "userId",
    "uid",
)
_XIAOMI_TIMEZONE = timezone(timedelta(hours=8))


def _configure_own_account_endpoints() -> None:
    """Use Xiaomi's own-account data endpoints instead of family-sharing paths."""

    _data.RELATIVES_AGGREGATED_DATA_PATH = "/app/v1/data/get_aggregated_fitness_data_by_time"
    _data.RELATIVES_LATEST_DATA_PATH = "/app/v1/data/get_latest_fitness_data"
    _data.RELATIVES_FITNESS_DATA_PATH = "/app/v1/data/get_fitness_data_by_time"


_configure_own_account_endpoints()


class RelativeMember(BaseModel):
    """Bridge-owned relative model tolerant of Xiaomi response changes."""

    model_config = ConfigDict(extra="ignore")

    relative_uid: int
    relative_note: str = ""
    relative_icon: str = ""
    latest_data_time: int = 0
    latest_abnormal_record_time: int | None = 0
    source_tag: int = 0


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _pick(item: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None:
            return value
    return default


def _coerce_int(value: Any, *, default: int | None = 0) -> int | None:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default


def _coerce_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _looks_like_relative(item: dict[str, Any]) -> bool:
    return any(item.get(key) is not None for key in _RELATIVE_UID_KEYS)


def _extract_relative_items(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Find relative records in known and lightly nested Xiaomi response shapes."""

    value = _decode_json_value(value)
    if depth > 4:
        return []
    if isinstance(value, list):
        decoded = [_decode_json_value(item) for item in value]
        direct = [item for item in decoded if isinstance(item, dict) and _looks_like_relative(item)]
        if direct:
            return direct
        for item in decoded:
            nested = _extract_relative_items(item, depth=depth + 1)
            if nested:
                return nested
        return []
    if not isinstance(value, dict):
        return []
    if _looks_like_relative(value):
        return [value]
    for key in _RELATIVE_LIST_KEYS:
        if key in value:
            nested = _extract_relative_items(value[key], depth=depth + 1)
            if nested:
                return nested
    for nested_value in value.values():
        nested = _extract_relative_items(nested_value, depth=depth + 1)
        if nested:
            return nested
    return []


def _normalize_relative(item: dict[str, Any]) -> RelativeMember | None:
    merged = dict(item)
    for key in ("user_info", "userInfo", "profile", "relative_info", "relativeInfo"):
        nested = _decode_json_value(item.get(key))
        if isinstance(nested, dict):
            merged = {**nested, **merged}

    relative_uid = _coerce_int(_pick(merged, _RELATIVE_UID_KEYS), default=None)
    if relative_uid is None:
        return None
    normalized = {
        "relative_uid": relative_uid,
        "relative_note": _coerce_text(
            _pick(
                merged,
                (
                    "relative_note",
                    "relativeNote",
                    "relation_note",
                    "note",
                    "nickname",
                    "nick_name",
                    "name",
                ),
                "",
            )
        ),
        "relative_icon": _coerce_text(
            _pick(
                merged,
                ("relative_icon", "relativeIcon", "icon", "avatar", "avatar_url", "avatarUrl"),
                "",
            )
        ),
        "latest_data_time": _coerce_int(
            _pick(
                merged,
                ("latest_data_time", "latestDataTime", "data_time", "latest_update_time", "updateTime"),
                0,
            )
        ),
        "latest_abnormal_record_time": _coerce_int(
            _pick(
                merged,
                ("latest_abnormal_record_time", "latestAbnormalRecordTime"),
                0,
            )
        ),
        "source_tag": _coerce_int(_pick(merged, ("source_tag", "sourceTag"), 0)),
    }
    try:
        return RelativeMember.model_validate(normalized)
    except ValidationError:
        return None


def _parse_relatives(value: Any) -> list[RelativeMember]:
    members: list[RelativeMember] = []
    seen: set[int] = set()
    for item in _extract_relative_items(value):
        member = _normalize_relative(item)
        if member and member.relative_uid not in seen:
            seen.add(member.relative_uid)
            members.append(member)
    return members


def _safe_shape(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Describe response structure without exposing IDs, names, or health data."""

    value = _decode_json_value(value)
    if depth > 3:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value)
        return {
            "type": "dict",
            "keys": keys,
            "children": {
                str(key): _safe_shape(child, depth=depth + 1)
                for key, child in value.items()
                if isinstance(_decode_json_value(child), (dict, list))
            },
        }
    if isinstance(value, list):
        summary: dict[str, Any] = {"type": "list", "length": len(value)}
        if value:
            first = _decode_json_value(value[0])
            if isinstance(first, dict):
                summary["item_keys"] = sorted(str(key) for key in first)
        return summary
    return {"type": type(value).__name__}


class HealthBridge:
    def __init__(self, token_json: str = ""):
        self._token_json = token_json
        self._client: MiHealthClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token_json)

    def set_token_json(self, token_json: str) -> None:
        AuthToken.model_validate_json(token_json)
        self._token_json = token_json
        self._client = None

    def safe_status(self) -> dict[str, Any]:
        user_id = ""
        if self._token_json:
            try:
                token = AuthToken.model_validate_json(self._token_json)
                user_id = token.user_id
            except ValueError:
                pass
        return {
            "ok": True,
            "configured": self.configured,
            "xiaomi_user_id_suffix": user_id[-4:] if user_id else None,
            "mode": "read_only_own_account",
        }

    def _get_client(self) -> MiHealthClient:
        if self._client:
            return self._client
        if not self._token_json:
            raise RuntimeError("尚未配置小米登录凭证，请先打开 /setup 完成登录")
        token = AuthToken.model_validate_json(self._token_json)
        auth = XiaomiAuth()
        auth.token = token
        self._client = MiHealthClient(auth)
        return self._client

    async def _get_relatives(self) -> list[Any]:
        token = AuthToken.model_validate_json(self._token_json)
        own_uid = _coerce_int(token.user_id, default=None)
        if own_uid is None:
            raise RuntimeError("小米登录凭证中没有可用的账号 UID")
        return [RelativeMember(relative_uid=own_uid, relative_note="本人")]

    async def list_relatives(self) -> list[dict[str, Any]]:
        members = await self._get_relatives()
        return [member.model_dump(mode="json") for member in members]

    async def resolve_relative(self, relative: str | None) -> Any:
        members = await self._get_relatives()
        if relative:
            lowered = relative.strip().lower()
            for member in members:
                if str(member.relative_uid) == lowered or member.relative_note.strip().lower() == lowered:
                    return member
            partial = [member for member in members if lowered in member.relative_note.strip().lower()]
            if len(partial) == 1:
                return partial[0]
            raise ValueError(f"没有找到亲友：{relative}")
        if len(members) == 1:
            return members[0]
        if not members:
            raise ValueError("亲友列表为空，请先在小米运动健康里建立亲友共享")
        names = [member.relative_note or str(member.relative_uid) for member in members]
        raise ValueError(f"存在多位亲友，请指定其中一位：{', '.join(names)}")

    async def snapshot(
        self,
        relative: str | None,
        query_date: str | None = None,
    ) -> dict[str, Any]:
        """Build a complete daily snapshot from the working per-metric history APIs.

        The Xiaomi own-account latest-data endpoint is not consistently available.
        Historical metric endpoints are used instead so one unsupported endpoint
        cannot break the entire summary.
        """
        client = self._get_client()
        member = await self.resolve_relative(relative)
        selected_date = (
            date.fromisoformat(query_date)
            if query_date
            else datetime.now(_XIAOMI_TIMEZONE).date()
        )

        method_names = {
            "heart_rate": "get_heart_rate",
            "sleep": "get_sleep",
            "steps": "get_steps",
            "calories": "get_calories_history",
            "spo2": "get_spo2_history",
            "intensity": "get_intensity_history",
            "valid_stand": "get_valid_stand_history",
            "weight": "get_weight_history",
            "blood_pressure": "get_blood_pressure_history",
        }

        async def read_metric(metric: str) -> tuple[str, list[dict[str, Any]], str | None]:
            try:
                method = getattr(client, method_names[metric])
                rows = await method(member.relative_uid, selected_date, days=1)
                serialized = [
                    row.model_dump(mode="json") if hasattr(row, "model_dump") else row
                    for row in rows
                ]
                return metric, serialized, None
            except Exception as exc:
                # Keep error details minimal: provider responses may contain private data.
                return metric, [], type(exc).__name__

        results = await asyncio.gather(*(read_metric(metric) for metric in method_names))
        metrics: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        for metric, rows, error_type in results:
            metrics[metric] = rows
            if error_type:
                errors[metric] = error_type

        if errors and len(errors) == len(method_names):
            failed = ", ".join(f"{name} ({kind})" for name, kind in errors.items())
            raise RuntimeError(f"所有健康分项接口均读取失败：{failed}")

        latest: dict[str, Any] = {"updated_time": 0}
        for metric, rows in metrics.items():
            if not rows:
                latest[metric] = None
                continue
            latest_row = max(
                rows,
                key=lambda row: (
                    row.get("time", 0) if isinstance(row, dict) else 0
                ),
            )
            latest[metric] = latest_row
            if isinstance(latest_row, dict):
                latest["updated_time"] = max(
                    latest["updated_time"],
                    latest_row.get("time", 0) or 0,
                )

        daily_summary = {
            "date": selected_date.isoformat(),
            "relative_uid": member.relative_uid,
            **{
                metric: (rows[0] if rows else None)
                for metric, rows in metrics.items()
            },
        }

        return {
            "relative": member.model_dump(mode="json"),
            "query_date": selected_date.isoformat(),
            "shared_data_types": None,
            "available_data_types": [
                metric for metric, rows in metrics.items() if rows
            ],
            "latest": latest,
            "daily_summary": daily_summary,
            "metrics": metrics,
            "errors": errors,
            "notice": "数据来自当前登录的小米运动健康本人账号；部分指标可能因未记录或未开放而为空，不用于医疗诊断。",
        }

    async def history(
        self,
        relative: str | None,
        metric: Metric,
        query_date: str | None,
        days: int,
    ) -> dict[str, Any]:
        if not 1 <= days <= 30:
            raise ValueError("days 必须在 1 到 30 之间")
        parsed_date = date.fromisoformat(query_date) if query_date else None
        client = self._get_client()
        member = await self.resolve_relative(relative)
        method_names = {
            "heart_rate": "get_heart_rate",
            "sleep": "get_sleep",
            "steps": "get_steps",
            "calories": "get_calories_history",
            "spo2": "get_spo2_history",
            "intensity": "get_intensity_history",
            "valid_stand": "get_valid_stand_history",
            "weight": "get_weight_history",
            "blood_pressure": "get_blood_pressure_history",
        }
        method = getattr(client, method_names[metric])
        rows = await method(member.relative_uid, parsed_date, days=days)
        return {
            "relative": member.model_dump(mode="json"),
            "metric": metric,
            "query_date": parsed_date.isoformat() if parsed_date else None,
            "days": days,
            "data": [row.model_dump(mode="json") for row in rows],
            "notice": "数据来自当前登录的小米运动健康本人账号，不用于医疗诊断。",
        }

    async def sport_records(
        self,
        query_date: str | None = None,
        days: int = 30,
    ) -> dict[str, Any]:
        """Read the current user's individual Xiaomi sport sessions."""
        if not 1 <= days <= 30:
            raise ValueError("days 必须在 1 到 30 之间")
        return await read_sport_records(self._get_client(), query_date, days)

    async def sport_detail(
        self,
        workout_time: int,
        include_track: bool = False,
    ) -> dict[str, Any]:
        """Read one workout's detailed samples; GPS is opt-in."""
        return await read_sport_detail(
            self._get_client(),
            workout_time,
            include_track=include_track,
        )

    def export_token_json(self) -> str:
        if not self._token_json:
            raise RuntimeError("token 尚未生成")
        parsed = json.loads(self._token_json)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
