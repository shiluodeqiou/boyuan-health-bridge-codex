from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from mi_fitness.crypto import build_encrypted_params, decrypt_response

SPORT_RECORDS_PATH = "/app/v1/data/get_sport_records_by_time"
FDS_URL_PATH = "/healthapp/service/gen_download_url"
FDS_SIGN_PATH = "/service/gen_download_url"
FDS_IV = b"1234567887654321"
MAX_PAGES = 20
MAX_FDS_BYTES = 16 * 1024 * 1024
MAX_SAMPLE_OUTPUT = 1200
MAX_TRACK_OUTPUT = 1000
CN_TZ = timezone(timedelta(hours=8))

SPORT_NAMES = {
    1: "户外跑步",
    6: "户外骑行",
    300: "爬楼机",
    308: "力量训练",
}

# Legacy FDS record layouts verified for outdoor running (proto 22, v8)
# and outdoor cycling (proto 23, v5); GPS v2 is shared by both.
_WHOLE = (None,)
_ALTITUDE = ("isKilometer", "isAltitudeRising", "altitudeChange")
_ALTITUDE_BITS = ((7, 1), (6, 1), (0, 6))
RUN_FIELDS = [
    (("newCalories", "newSteps"), 1, ((4, 4), (0, 4)), 1, None),
    (("heartRate",), 1, _WHOLE, 1, None),
    (_ALTITUDE, 1, _ALTITUDE_BITS, 1, 8),
    (("newDistance",), 1, _WHOLE, 1, 8),
    (("stride",), 1, _WHOLE, 2, None),
    (("newImpactForce", "newContactTime", "newFlightTime"), 4, ((26, 6), (13, 13), (0, 13)), 4, None),
    (("contactRatio",), 1, _WHOLE, 5, None),
    (("cadence",), 1, _WHOLE, 5, None),
    (("pace",), 2, _WHOLE, 5, None),
    (("equalIntensityPace",), 2, _WHOLE, 6, None),
    (("runningPower",), 2, _WHOLE, 6, None),
    (("verticalStrideRatio",), 2, _WHOLE, 8, None),
    (("verticalAmplitude",), 2, _WHOLE, 8, None),
]
RIDE_FIELDS = [
    (("newCalories", "workoutcount"), 1, ((4, 4), (0, 4)), 1, None),
    (("heartRate",), 1, _WHOLE, 1, None),
    (_ALTITUDE, 1, _ALTITUDE_BITS, 1, 5),
    (("newDistance",), 1, _WHOLE, 1, 5),
    (("speed",), 2, _WHOLE, 3, None),
    (("cadence",), 1, _WHOLE, 3, None),
]
RUN_SEGMENT = [
    ("initialAltitude", "<f"), ("recordCount", "<I"), ("resumeTimestamp", "<I"),
    ("segmentInfo", "<B"), ("totalDistance", "<I"), ("totalDuration", "<I"),
    ("totalSteps", "<I"), ("segmentPace", "<H"),
]
RIDE_SEGMENT = [
    ("initialAltitude", "<f"), ("recordCount", "<I"), ("resumeTimestamp", "<I"),
    ("segmentInfo", "<B"), ("totalDistance", "<I"), ("totalTime", "<I"),
    ("totalSpeed", "<H"),
]
GPS_FIELDS = [
    ("time", 4, "<I", 1), ("longitude", 4, "<f", 1), ("latitude", 4, "<f", 1),
    ("locationHorizontalAccuracy", 4, "<f", 2),
    ("locationSpeedAndGpsSource", 2, "<H", 2), ("gpsSourceValid", 0, None, 2),
]
VALIDITY_LENGTHS = {
    (22, 0): {1: 2, 2: 3, 3: 3, 4: 3, 5: 5, 6: 6, 7: 6, 8: 7},
    (23, 0): {1: 2, 2: 2, 3: 3, 4: 3, 5: 3, 6: 3, 7: 4},
    (22, 2): {1: 1, 2: 1, 3: 1, 4: 1, 5: 2},
    (23, 2): {1: 1, 2: 1, 3: 1, 4: 1, 5: 2},
}


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _positive(value: Any) -> int | None:
    number = _int(value)
    return number if number is not None and number > 0 else None


def _decode_value(row: dict[str, Any]) -> dict[str, Any] | None:
    value = row.get("value")
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _unwrap_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("小米运动记录接口返回了无法识别的数据")
    result = payload.get("result")
    return result if isinstance(result, dict) else payload


def _date_window(query_date: str | None, days: int) -> tuple[date, int, int]:
    if not 1 <= days <= 30:
        raise ValueError("days 必须在 1 到 30 之间")
    end_day = date.fromisoformat(query_date) if query_date else datetime.now(CN_TZ).date()
    end_dt = datetime.combine(end_day, datetime.max.time().replace(microsecond=0), tzinfo=CN_TZ)
    start_dt = end_dt - timedelta(days=days) + timedelta(seconds=1)
    return end_day, int(start_dt.timestamp()), int(end_dt.timestamp())


def _fetch_sport_rows_sync_shape(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("sport_records") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


async def _fetch_sport_rows(
    client: Any, start_epoch_s: int, end_epoch_s: int
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[str]]:
    """Read all sport summaries in the inclusive window. The source endpoint excludes its start."""
    request: dict[str, Any] = {
        "start_time": start_epoch_s - 1,
        "end_time": end_epoch_s,
        "limit": 50,
        "reverse": True,
    }
    seen: set[str] = set()
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    notes: list[str] = []
    for page in range(MAX_PAGES):
        payload = await client._request("POST", SPORT_RECORDS_PATH, params=request)
        body = _unwrap_result(payload)
        for row in _fetch_sport_rows_sync_shape(body):
            outer_time = _int(row.get("time"))
            if outer_time is None or not start_epoch_s <= outer_time <= end_epoch_s:
                continue
            if row.get("deleted"):
                continue
            value = _decode_value(row)
            if value is not None:
                rows.append((row, value))
            else:
                notes.append("有一条运动记录的摘要内容无法解析，已跳过。")
        if not body.get("has_more"):
            break
        cursor = body.get("next_key")
        if not cursor or str(cursor) in seen:
            notes.append("运动记录分页游标异常，已停止继续读取。")
            break
        cursor = str(cursor)
        seen.add(cursor)
        request = {
            "start_time": start_epoch_s - 1,
            "end_time": end_epoch_s,
            "limit": 50,
            "reverse": True,
            "next_key": cursor,
        }
        if page == MAX_PAGES - 1:
            notes.append("运动记录超过单次读取页数上限，较早记录可能未包含；请缩短日期范围。")
    rows.sort(key=lambda pair: _int(pair[0].get("time")) or 0, reverse=True)
    return rows, notes


def _local_iso(epoch: int | None, offset_s: int) -> str | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone(timedelta(seconds=offset_s))).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_workout(row: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    outer_time = _int(row.get("time"))
    offset = _int(row.get("zone_offset"))
    if offset is None:
        zone15 = _int(value.get("timezone"))
        offset = zone15 * 900 if zone15 is not None else 8 * 3600
    start = _positive(value.get("start_time")) or outer_time
    end = _positive(value.get("end_time"))
    sport_code = _int(value.get("sport_type"))
    category = row.get("category") if isinstance(row.get("category"), str) else None
    source_key = row.get("key") if isinstance(row.get("key"), str) else None
    summary = {
        str(k): v for k, v in value.items()
        if isinstance(k, str) and isinstance(v, (str, int, float, bool, type(None)))
    }
    return {
        "workout_time": outer_time,
        "sport": SPORT_NAMES.get(sport_code) or category or source_key or "其他运动",
        "sport_type": sport_code,
        "sport_key": source_key,
        "category": category,
        "start_time": _local_iso(start, offset),
        "end_time": _local_iso(end, offset),
        "duration_seconds": _positive(value.get("duration")),
        "duration_minutes": round((_positive(value.get("duration")) or 0) / 60, 1) or None,
        "distance_m": _positive(value.get("distance")),
        "active_calories_kcal": _positive(value.get("calories")),
        "total_calories_kcal": _positive(value.get("total_cal")),
        "average_heart_rate_bpm": _positive(value.get("avg_hrm")),
        "maximum_heart_rate_bpm": _positive(value.get("max_hrm")),
        "minimum_heart_rate_bpm": _positive(value.get("min_hrm")),
        "steps": _positive(value.get("steps")),
        "average_pace_seconds_per_km": _positive(value.get("avg_pace")),
        "average_speed_kmh": value.get("avg_speed") if isinstance(value.get("avg_speed"), (int, float)) else None,
        "proto_type": _int(value.get("proto_type")),
        "timezone_offset_seconds": offset,
        "summary": summary,
    }


async def get_sport_records(
    client: Any, query_date: str | None = None, days: int = 30
) -> dict[str, Any]:
    end_day, start_ts, end_ts = _date_window(query_date, days)
    rows, notes = await _fetch_sport_rows(client, start_ts, end_ts)
    records = [_normalize_workout(row, value) for row, value in rows]
    return {
        "query_date": end_day.isoformat(),
        "days": days,
        "count": len(records),
        "records": records,
        "notes": notes,
        "notice": "运动记录来自当前登录的小米运动健康本人账号；时长、距离和热量以设备同步结果为准。",
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _fds_ref(sid: str, value: dict[str, Any], file_type: int) -> tuple[int, str]:
    timestamp = _int(value.get("time"))
    tz15 = _int(value.get("timezone"))
    proto = _int(value.get("proto_type"))
    if not sid or timestamp is None or not 0 < timestamp < 2**32:
        raise ValueError("运动记录缺少可用的 FDS 时间标识")
    if tz15 is None or not -128 <= tz15 <= 127 or proto not in (22, 23):
        raise ValueError("该运动格式暂不支持读取逐秒明细")
    key_bytes = struct.pack("<Ib", timestamp, tz15) + bytes([((1 << 7) + (proto << 2) + file_type) & 0xFF])
    suffix = f"{_b64url(key_bytes)}_{_b64url(hashlib.sha1(sid.encode('utf-8'), usedforsecurity=False).digest())}"
    return timestamp, suffix


async def _request_fds_entries(client: Any, sid: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Request presigned FDS links with the existing Xiaomi session, without exposing them."""
    from mi_fitness.crypto import build_encrypted_params, decrypt_response

    token = client.auth.token
    for attempt in range(2):
        token = client.auth.token
        nonce_params = build_encrypted_params(
            "GET", FDS_SIGN_PATH, token.ssecurity, {"did": sid, "items": items}
        )
        cookies = {"cUserId": token.c_user_id, "serviceToken": token.service_token}
        response = await client._http.get(
            client.base_url.rstrip("/") + FDS_URL_PATH,
            params=nonce_params,
            cookies=cookies,
        )
        if response.status_code == 401 and attempt == 0:
            await client._refresh_auth(token.service_token)
            continue
        if response.status_code != 200:
            raise RuntimeError(f"FDS 下载链接请求失败（HTTP {response.status_code}）")
        decoded = decrypt_response(token.ssecurity, nonce_params["_nonce"], response.text)
        if not isinstance(decoded, dict) or decoded.get("code") != 0:
            raise RuntimeError("FDS 下载链接接口返回异常")
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("FDS 下载链接接口缺少文件映射")
        return result
    raise RuntimeError("小米登录凭证刷新后仍无法读取运动详情")


def _decode_b64url(text: str) -> bytes:
    core = text.rstrip("=")
    if not core or len(core) % 4 == 1:
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(core + "=" * (-len(core) % 4))


def _decrypt_fds(body: bytes, key_b64: str) -> bytes:
    text = body.decode("ascii").strip()
    if text.startswith('"'):
        text = json.loads(text)
    if not isinstance(text, str):
        raise ValueError("invalid FDS body")
    ciphertext = _decode_b64url(text)
    key = _decode_b64url(key_b64)
    if len(ciphertext) == 0 or len(ciphertext) % 16 or len(key) not in (16, 24, 32):
        raise ValueError("invalid FDS ciphertext")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(FDS_IV)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


async def _download_fds(url: str) -> bytes:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        raise ValueError("FDS 下载链接格式不安全")
    if not (host.endswith(".fds.api.xiaomi.com") or host == "fds.api.xiaomi.com"):
        raise ValueError("FDS 下载链接主机不受信任")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as http:
        response = await http.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"FDS 文件下载失败（HTTP {response.status_code}）")
    if len(response.content) > MAX_FDS_BYTES:
        raise RuntimeError("FDS 文件超过安全大小限制")
    return response.content


def _header(plain: bytes, file_type: int) -> tuple[dict[str, Any], bytes]:
    if len(plain) < 8:
        raise ValueError("FDS 文件头不完整")
    timestamp, tz15, version, proto, mode = struct.unpack_from("<IbBBB", plain)
    validity_len = VALIDITY_LENGTHS.get((proto, file_type), {}).get(version)
    validity = plain[8:8 + validity_len] if mode == 0 and validity_len else None
    if validity is not None and len(validity) != validity_len:
        raise ValueError("FDS 文件有效性标记不完整")
    return {
        "timestamp": timestamp,
        "timezone": tz15,
        "version": version,
        "proto_type": proto,
        "storage_mode": mode,
    }, validity or b""


def _unpack_value(data: bytes, offset: int, fmt: str) -> tuple[int | float, int]:
    size = struct.calcsize(fmt)
    return struct.unpack_from(fmt, data, offset)[0], offset + size


def _decode_record_file(plain: bytes, expected_time: int, expected_tz: int, proto: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    header, validity = _header(plain, 0)
    if (header["timestamp"], header["timezone"], header["proto_type"]) != (expected_time, expected_tz, proto):
        raise ValueError("FDS 文件头与运动记录不匹配")
    if header["storage_mode"] != 0 or not validity:
        raise ValueError("FDS 文件压缩模式或版本暂不支持")
    version = header["version"]
    if proto == 22 and version == 8:
        fields, segment_fields = RUN_FIELDS, RUN_SEGMENT
    elif proto == 23 and version == 5:
        fields, segment_fields = RIDE_FIELDS, RIDE_SEGMENT
    else:
        raise ValueError(f"FDS 运动记录版本 {version} 暂不支持")
    present = []
    if len(fields) > 2 * len(validity):
        raise ValueError("FDS 有效性标记长度异常")
    for index, field in enumerate(fields):
        names, size, bits, since, until = field
        if version < since or (until is not None and version > until):
            continue
        nibble = validity[index // 2] >> 4 if index % 2 == 0 else validity[index // 2] & 0x0F
        if nibble & 8:
            valid = tuple(bool(nibble & bit) for bit in (4, 2, 1)[:len(names)])
            present.append((names, size, bits, valid))
    point_size = sum(field[1] for field in present)
    segment_size = sum(struct.calcsize(fmt) for _, fmt in segment_fields)
    offset = 8 + len(validity)
    segments = []
    point_total = 0
    while len(plain) - offset >= segment_size:
        segment: dict[str, Any] = {}
        for name, fmt in segment_fields:
            value, offset = _unpack_value(plain, offset, fmt)
            if name == "segmentInfo":
                segment["pause_type"] = int(value) >> 7
                segment["segment_type"] = int(value) & 0x7F
            else:
                segment[name] = value
        count = int(segment["recordCount"])
        point_total += count
        if point_total > 1_000_000 or (count and not point_size) or count * point_size > len(plain) - offset:
            raise ValueError("FDS 运动采样点数量或长度异常")
        points = []
        for _ in range(count):
            point: dict[str, Any] = {}
            for names, size, bits, valid in present:
                raw = int.from_bytes(plain[offset:offset + size], "little")
                offset += size
                for name, bit_spec, is_valid in zip(names, bits, valid, strict=True):
                    if is_valid:
                        point[name] = raw if bit_spec is None else (raw >> bit_spec[0]) & ((1 << bit_spec[1]) - 1)
            points.append(point)
        segment["data_points"] = points
        segments.append(segment)
    samples = []
    distance_units = 0
    steps = 0
    for segment in segments:
        resume = int(segment["resumeTimestamp"])
        for index, point in enumerate(segment["data_points"]):
            distance_m = None
            if "newDistance" in point:
                distance_units += int(point["newDistance"])
                distance_m = distance_units / (10 if proto == 22 else 1)
            steps_value = None
            if proto == 22 and "newSteps" in point:
                steps += int(point["newSteps"])
                steps_value = steps
            samples.append({
                "time": resume + index,
                "heart_rate_bpm": point.get("heartRate") or None,
                "distance_m": distance_m,
                "steps": steps_value,
                "cadence_spm": point.get("cadence") if proto == 22 else None,
                "pace_seconds_per_km": (point.get("pace") or None) if proto == 22 else None,
                "speed_mps": (point["speed"] / 36) if proto == 23 and "speed" in point else None,
            })
    return samples, header


def _decode_gps_file(plain: bytes, expected_time: int, expected_tz: int, proto: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    header, validity = _header(plain, 2)
    if (header["timestamp"], header["timezone"], header["proto_type"]) != (expected_time, expected_tz, proto):
        raise ValueError("GPS 文件头与运动记录不匹配")
    if header["storage_mode"] != 0 or header["version"] != 2 or not validity:
        raise ValueError("GPS 文件版本或压缩模式暂不支持")
    selected = [field for field in GPS_FIELDS if field[3] <= header["version"]]
    width = 8 * len(validity)
    if len(selected) > width:
        raise ValueError("GPS 有效性标记长度异常")
    flags = int.from_bytes(validity, "big")
    valid = {field[0]: bool((flags >> (width - 1 - i)) & 1) for i, field in enumerate(selected)}
    point_size = sum(field[1] for field in selected)
    offset = 8 + len(validity)
    count = (len(plain) - offset) // point_size
    if count > 1_000_000:
        raise ValueError("GPS 采样点数量异常")
    points = []
    for _ in range(count):
        point = {}
        for name, size, fmt, _since in selected:
            if fmt is None:
                continue
            value, offset = _unpack_value(plain, offset, fmt)
            if valid[name]:
                point[name] = value
        if all(k in point for k in ("time", "longitude", "latitude")):
            lat, lon = float(point["latitude"]), float(point["longitude"])
            if math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180 and (lat, lon) != (0.0, 0.0):
                points.append({"time": int(point["time"]), "latitude": lat, "longitude": lon})
    return points, header


def _sample_output(samples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    if len(samples) <= MAX_SAMPLE_OUTPUT:
        return samples, 1
    step = math.ceil(len(samples) / MAX_SAMPLE_OUTPUT)
    return samples[::step], step


def _track_output(points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    if len(points) <= MAX_TRACK_OUTPUT:
        return points, 1
    step = math.ceil(len(points) / MAX_TRACK_OUTPUT)
    return points[::step], step


async def get_sport_detail(
    client: Any, workout_time: int, *, include_track: bool = False
) -> dict[str, Any]:
    if workout_time <= 0:
        raise ValueError("workout_time 必须是有效的 Unix 秒时间戳")
    rows, notes = await _fetch_sport_rows(client, workout_time, workout_time)
    selected = next(
        ((row, value) for row, value in rows if _int(row.get("time")) == workout_time),
        None,
    )
    if selected is None:
        raise ValueError("没有找到该时间对应的运动记录；请先查询运动记录列表并复制 workout_time")
    row, value = selected
    summary = _normalize_workout(row, value)
    proto = _int(value.get("proto_type"))
    if proto not in (22, 23):
        return {
            "workout": summary,
            "detail_supported": False,
            "samples": [],
            "track": None,
            "notes": notes + ["该运动类型目前没有已验证的逐秒 FDS 明细解析格式；运动摘要中的详细指标仍可读取。"],
        }
    sid = row.get("sid")
    if not isinstance(sid, str) or not sid:
        return {
            "workout": summary,
            "detail_supported": False,
            "samples": [],
            "track": None,
            "notes": notes + ["该运动记录缺少来源标识，无法定位明细文件。"],
        }
    report_time = _int(value.get("time"))
    tz15 = _int(value.get("timezone"))
    if report_time is None or tz15 is None:
        return {
            "workout": summary,
            "detail_supported": False,
            "samples": [],
            "track": None,
            "notes": notes + ["该运动记录缺少明细文件所需的时间或时区信息。"],
        }

    file_types = [0, 2] if include_track else [0]
    refs = []
    request_items = []
    server_keys = []
    for file_type in file_types:
        timestamp, suffix = _fds_ref(sid, value, file_type)
        refs.append((timestamp, tz15, proto, file_type))
        request_items.append({"timestamp": timestamp, "suffix": suffix})
        server_keys.append(f"{suffix}_{timestamp}")

    try:
        entries = await _request_fds_entries(client, sid, request_items)
    except Exception as exc:
        return {
            "workout": summary,
            "detail_supported": True,
            "samples": [],
            "track": None,
            "notes": notes + [f"读取运动明细下载地址失败（{type(exc).__name__}）。"],
        }

    samples: list[dict[str, Any]] = []
    track: list[dict[str, Any]] | None = None
    for index, (timestamp, tz15, file_proto, file_type) in enumerate(refs):
        entry = entries.get(server_keys[index])
        kind = "GPS 轨迹" if file_type == 2 else "逐秒运动采样"
        if not isinstance(entry, dict) or not isinstance(entry.get("obj_key"), str) or not entry.get("obj_key"):
            notes.append(f"小米云端没有提供本次运动的{kind}文件。")
            continue
        if str(entry.get("method", "GET")).upper() != "GET" or not isinstance(entry.get("url"), str):
            notes.append(f"本次运动的{kind}文件下载信息无效。")
            continue
        try:
            body = await _download_fds(entry["url"])
            plain = _decrypt_fds(body, entry["obj_key"])
            if file_type == 0:
                samples, _ = _decode_record_file(plain, timestamp, tz15, file_proto)
            else:
                track, _ = _decode_gps_file(plain, timestamp, tz15, file_proto)
        except Exception as exc:
            notes.append(f"读取{kind}失败（{type(exc).__name__}）。")

    shown_samples, sample_step = _sample_output(samples)
    shown_track, track_step = _track_output(track) if track is not None else (None, 1)
    return {
        "workout": summary,
        "detail_supported": True,
        "sample_count": len(samples),
        "sample_interval_seconds": sample_step,
        "samples": shown_samples,
        "track_count": len(track) if track is not None else None,
        "track_sampling_step": track_step if track is not None else None,
        "track": shown_track,
        "notes": notes,
        "notice": "逐秒采样和 GPS 轨迹仅在小米云端提供对应文件且文件版本受支持时返回；默认不读取 GPS 轨迹。",
    }
