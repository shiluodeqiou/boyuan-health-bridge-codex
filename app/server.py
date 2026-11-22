from __future__ import annotations

import asyncio
import contextlib
import hmac
import html
from dataclasses import dataclass
from typing import Literal

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from mi_fitness import XiaomiAuth
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

from app.config import Settings
from app.fitness_db import FitnessDatabase
from app.fitness_sync import sync_unified_fitness_data as run_unified_fitness_sync
from app.fitness_sync import sync_xiaomi_to_database
from app.health import HealthBridge, Metric
from app.lianlian import connection_status as lianlian_connection_status
from app.lianlian import get_skill_manifest as read_lianlian_skill_manifest
from app.lianlian import get_training_records_page as read_lianlian_training_records_page
from app.lianlian import validate_normalized_workouts
from app.oauth import BridgeOAuthProvider, SignedTokens

settings = Settings.from_env()
signer = SignedTokens(settings.signing_secret)
health_bridge = HealthBridge(settings.mi_token_json)
fitness_database = FitnessDatabase()
oauth_provider = BridgeOAuthProvider(settings.public_base_url, settings.mcp_url, signer)


@dataclass
class SetupState:
    status: str = "idle"
    qr_image_url: str = ""
    login_url: str = ""
    error: str = ""
    token_json: str = ""
    task: asyncio.Task[None] | None = None


setup_state = SetupState()

mcp = MCPServer(
    "柏渊健康桥",
    instructions=("读取当前登录小米账号本人的健康数据，并将小米运动健康与练练健身记录汇总到个人 PostgreSQL 数据库。"
                  "数据源接口只读；统一同步工具只写入用户自己的数据库，不修改上游健康或训练记录。"
                  "分析工具从统一数据库读取跨来源时间轴、身体成分和力量训练趋势。"
                  "健康数据可能有同步延迟，不能用于医疗诊断或紧急情况。"),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(settings.public_base_url),
        resource_server_url=AnyHttpUrl(settings.mcp_url),
        required_scopes=["health.read"],
    ),
    auth_server_provider=oauth_provider,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITE_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)


@mcp.tool(
    title="列出本人账号",
    description="列出当前登录小米账号本人，可用于确认正在读取的账号。",
    annotations=READ_ONLY,
)
async def list_family_members() -> list[dict]:
    return await health_bridge.list_relatives()


@mcp.tool(
    title="读取本人健康摘要",
    description="读取当前登录小米账号本人的最新健康快照和每日摘要；若原始体重接口可用，也会包含体脂率、肌肉量等身体成分。",
    annotations=READ_ONLY,
)
async def get_health_snapshot(
    relative: str | None = None,
    query_date: str | None = None,
) -> dict:
    return await health_bridge.snapshot(relative, query_date)


@mcp.tool(
    title="读取本人健康历史",
    description="按日期读取当前登录小米账号本人的心率、睡眠、步数、血氧、卡路里等历史数据；查询 weight 时会保留原始体脂秤身体成分字段。",
    annotations=READ_ONLY,
)
async def get_health_history(
    metric: Metric,
    relative: str | None = None,
    query_date: str | None = None,
    days: int = 1,
) -> dict:
    return await health_bridge.history(relative, metric, query_date, days)


@mcp.tool(
    title="读取体脂秤身体成分",
    description="读取当前登录小米账号本人最近指定天数的原始体重与体脂秤身体成分记录，包括体脂率、脂肪量、肌肉量、骨骼肌量、骨量、蛋白质、水分、内脏脂肪、基础代谢、四肢及躯干分段脂肪/肌肉量和设备实际提供的其他原始字段。days 为 1 到 3650；按设备 sid 与测量时间去重，并返回分页完整性标记；不会将缺失字段补成 0。",
    annotations=READ_ONLY,
)
async def get_body_composition_history(
    query_date: str | None = None,
    days: int = 30,
) -> dict:
    return await health_bridge.body_composition_history(query_date, days)


@mcp.tool(
    title="读取小米运动记录",
    description="读取当前登录小米账号本人指定日期范围内的逐次运动记录，包括运动类型、起止时间、时长、距离、活动热量、平均/最高心率、配速等摘要指标。days 为 1 到 30。",
    annotations=READ_ONLY,
)
async def get_sport_records(
    query_date: str | None = None,
    days: int = 30,
) -> dict:
    return await health_bridge.sport_records(query_date, days)


@mcp.tool(
    title="读取单次运动详情",
    description="按运动记录列表中的 workout_time（Unix 秒）读取单次运动逐秒采样详情。支持自由训练/力量训练（proto 8，含爬楼机记录）以及已验证的户外跑步/骑行格式；可返回心率及设备提供的其他采样指标。GPS 轨迹默认关闭，只有明确需要时才设置 include_track=true。",
    annotations=READ_ONLY,
)
async def get_sport_detail(
    workout_time: int,
    include_track: bool = False,
) -> dict:
    return await health_bridge.sport_detail(workout_time, include_track)


@mcp.tool(
    title="同步小米数据到个人健身数据库",
    description="将本人最近 1–30 天的小米体脂秤身体成分、逐次运动记录，以及心率、睡眠、步数、热量、血氧等日常指标写入 PostgreSQL 个人健身数据库。按来源 ID 和时间幂等去重；默认同步运动摘要，可选同步逐秒运动采样。需要先配置 DATABASE_URL。",
    annotations=WRITE_IDEMPOTENT,
)
async def sync_personal_fitness_data(
    days: int = 30,
    include_sport_details: bool = False,
) -> dict:
    return await sync_xiaomi_to_database(
        health_bridge,
        fitness_database,
        days=days,
        include_sport_details=include_sport_details,
    )


@mcp.tool(
    title="同步统一个人健身数据库",
    description="一次同步当前登录小米账号本人的体脂秤身体成分、日常健康指标和运动摘要，并通过练练健身官方分页 API 同步力量训练记录、动作和每组数据。默认回看最近 30 天，幂等更新 Neon，不修改两个上游 App。只有两边读取完整且无错误才返回 success。",
    annotations=WRITE_IDEMPOTENT,
)
async def sync_unified_fitness_database(
    days: int = 30,
    include_sport_details: bool = False,
) -> dict:
    return await run_unified_fitness_sync(
        health_bridge,
        fitness_database,
        days=days,
        include_sport_details=include_sport_details,
    )


@mcp.tool(
    title="查看统一健身分析仪表盘",
    description="从 Neon 读取统一健身分析。默认返回轻量汇总，包含体重、体脂率、脂肪量、肌肉量的首末值和 7 日均值，以及按周训练次数、完成组数、有氧时长和热量。需要逐日趋势、动作进步和原始健康指标时设置 include_details=true。days 为 1 到 3650。",
    annotations=READ_ONLY,
)
async def get_fitness_dashboard(days: int = 90, include_details: bool = False) -> dict:
    return await fitness_database.dashboard(days, include_details=include_details)


@mcp.tool(
    title="查看最近减脂摘要",
    description="直接从 Neon 返回最近指定天数的减脂摘要。包含体重、体脂率、脂肪量、肌肉量的首末值、变化量和 7 日均值，以及按周训练次数、完成组数、有氧时长和热量。不会读取小米或练练健身上游接口，也不会传输原始身体成分 JSON。days 为 1 到 3650，默认 30 天。",
    annotations=READ_ONLY,
)
async def get_fat_loss_summary(days: int = 30) -> dict:
    return await fitness_database.fat_loss_summary(days)


@mcp.tool(
    title="查看个人健身数据库概况",
    description="查看统一数据库中按来源统计的体脂秤记录、运动训练记录、日常健康指标和最近同步任务。",
    annotations=READ_ONLY,
)
async def get_fitness_database_summary(days: int = 30) -> dict:
    return await fitness_database.summary(days)


@mcp.tool(
    title="查看分段身体成分趋势",
    description="从 Neon 读取最近指定天数的体脂秤分段脂肪量或肌肉量趋势。metric 可选 fat_mass/muscle_mass；parts 可选 trunk、left_upper_limb、right_upper_limb、left_lower_limb、right_lower_limb，省略时返回全部。按每日最后一次测量聚合，不补零；smoothing_window 为最近有效测量次数。下肢整体数据不等于独立大腿数据。",
    annotations=READ_ONLY,
)
async def get_segmental_body_trends(
    days: int = 30,
    metric: Literal["fat_mass", "muscle_mass"] = "fat_mass",
    parts: list[Literal[
        "trunk",
        "left_upper_limb",
        "right_upper_limb",
        "left_lower_limb",
        "right_lower_limb",
    ]] | None = None,
    smoothing_window: int = 7,
) -> dict:
    return await fitness_database.segmental_body_trends(
        days=days,
        metric=metric,
        parts=parts,
        smoothing_window=smoothing_window,
    )


@mcp.tool(
    title="查看健身数据质量报告",
    description="检查最近 1–3650 天的身体成分覆盖率、力量训练组完成状态、健康指标数量、逐秒运动采样和同步状态；不返回原始健康 JSON。",
    annotations=READ_ONLY,
)
async def get_data_quality_report(days: int = 30) -> dict:
    return await fitness_database.data_quality_report(days)


@mcp.tool(
    title="查看数据同步状态",
    description="查看小米和练练健身各数据资源的最后成功同步、最近一次尝试、最新源记录时间及最近同步日志。",
    annotations=READ_ONLY,
)
async def get_sync_status() -> dict:
    return await fitness_database.sync_status()


@mcp.tool(
    title="检查练练健身连接",
    description="检查 Render 是否配置练练健身 API Token；不会返回密钥。",
    annotations=READ_ONLY,
)
async def get_lianlian_connection_status() -> dict:
    return lianlian_connection_status()


@mcp.tool(
    title="读取练练健身最新技能说明",
    description="使用 Render 环境变量中的 LIANLIAN_API_TOKEN 请求练练健身官方 AI skill 接口，读取最新的只读训练记录 API 说明；不会暴露 Token。",
    annotations=READ_ONLY,
)
async def get_lianlian_skill_manifest() -> dict:
    return await read_lianlian_skill_manifest()


@mcp.tool(
    title="读取练练健身训练记录原始分页",
    description="只读调用练练健身官方 GET /api/v1/ai/training/records，按 yyyyMMdd 日期范围读取单页原始 JSON。每页最多 50 条。用于先核实官方实际返回字段，再开发标准化同步；不会写入或修改练练数据，也不会写入数据库。",
    annotations=READ_ONLY,
)
async def get_lianlian_training_records_page(
    start_date: str,
    end_date: str,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    return await read_lianlian_training_records_page(
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )


@mcp.tool(
    title="导入练练健身力量训练记录",
    description="将练练健身适配器标准化后的训练记录写入统一数据库。每条记录需包含 id、started_at 或 date，以及 exercises 数组；动作包含 name 和 sets，组包含 weight_kg、reps、duration_seconds、rest_seconds。原始字段完整保留。正式自动同步适配器将在确认练练最新技能说明后接入。",
    annotations=WRITE_IDEMPOTENT,
)
async def ingest_lianlian_workouts(records: list[dict]) -> dict:
    normalized = validate_normalized_workouts(records)
    return await fitness_database.store_lianlian(normalized)


@mcp.tool(
    title="检查健康桥状态",
    description="检查小米凭证是否已经配置；不会返回任何密钥或完整账号信息。",
    annotations=READ_ONLY,
)
async def get_bridge_status() -> dict:
    status = health_bridge.safe_status()
    status["fitness_database_configured"] = fitness_database.configured
    status["lianlian_configured"] = lianlian_connection_status()["configured"]
    return status


def _no_store(response: HTMLResponse | JSONResponse | RedirectResponse):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _setup_authorized(request: Request) -> bool:
    cookie = request.cookies.get("boyuan_setup", "")
    return signer.verify(cookie, "setup_session") is not None


async def home(_: Request) -> HTMLResponse:
    body = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>
    <title>柏渊健康桥</title><style>body{font:16px system-ui;max-width:720px;margin:60px auto;padding:0 20px;line-height:1.7}a{color:#165dff}</style>
    <h1>柏渊健康桥</h1><p>小米运动健康本人数据与运动记录的只读 MCP 服务。</p>
    <p><a href='/setup'>配置小米登录</a> · <a href='/health'>服务状态</a></p>"""
    return HTMLResponse(body)


async def health(_: Request) -> JSONResponse:
    return JSONResponse(health_bridge.safe_status())


async def oauth_metadata(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "issuer": settings.public_base_url,
            "authorization_endpoint": f"{settings.public_base_url}/authorize",
            "token_endpoint": f"{settings.public_base_url}/token",
            "scopes_supported": ["health.read"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }
    )


async def approve_get(request: Request) -> HTMLResponse:
    ticket = request.query_params.get("ticket", "")
    if not signer.verify(ticket, "approval"):
        return _no_store(HTMLResponse("授权请求已失效，请返回 ChatGPT 重试。", status_code=400))
    safe_ticket = html.escape(ticket, quote=True)
    body = f"""<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>
    <title>授权柏渊健康桥</title><style>body{{font:16px system-ui;max-width:520px;margin:50px auto;padding:0 20px;line-height:1.7}}input,button{{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;font-size:16px}}button{{background:#111;color:white;border:0;border-radius:8px}}</style>
    <h1>授权健康数据读取</h1><p>允许 ChatGPT 只读访问当前登录的小米账号本人健康数据和运动记录。</p>
    <form method=post action='/approve'><input type=hidden name=ticket value='{safe_ticket}'>
    <label>连接密码<input type=password name=password autocomplete=current-password required></label>
    <button type=submit>确认授权</button></form><p><small>不会授予修改、邀请或删除权限。</small></p>"""
    return _no_store(HTMLResponse(body))


async def approve_post(request: Request):
    form = await request.form()
    password = str(form.get("password", ""))
    ticket = str(form.get("ticket", ""))
    if not hmac.compare_digest(password.encode(), settings.owner_secret.encode()):
        return _no_store(HTMLResponse("连接密码不正确。", status_code=403))
    redirect_url = oauth_provider.approve(ticket)
    if not redirect_url:
        return _no_store(HTMLResponse("授权请求已失效，请返回 ChatGPT 重试。", status_code=400))
    return _no_store(RedirectResponse(redirect_url, status_code=302))


async def setup_get(request: Request):
    if _setup_authorized(request):
        return RedirectResponse("/setup/home", status_code=303)
    body = """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>
    <title>配置柏渊健康桥</title><style>body{font:16px system-ui;max-width:520px;margin:50px auto;padding:0 20px;line-height:1.7}input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:12px;font-size:16px}button{background:#111;color:white;border:0;border-radius:8px}</style>
    <h1>配置小米登录</h1><p>先输入部署时设置的连接密码。</p><form method=post action='/setup/login'>
    <label>连接密码<input type=password name=password autocomplete=current-password required></label><button type=submit>进入配置</button></form>"""
    return _no_store(HTMLResponse(body))


async def setup_login(request: Request):
    form = await request.form()
    password = str(form.get("password", ""))
    if not hmac.compare_digest(password.encode(), settings.owner_secret.encode()):
        return _no_store(HTMLResponse("连接密码不正确。", status_code=403))
    cookie = signer.issue("setup_session", {"sub": "owner"}, ttl_seconds=1800)
    response = RedirectResponse("/setup/home", status_code=303)
    response.set_cookie(
        "boyuan_setup",
        cookie,
        max_age=1800,
        httponly=True,
        secure=settings.public_base_url.startswith("https://"),
        samesite="strict",
    )
    return _no_store(response)


async def setup_home(request: Request):
    if not _setup_authorized(request):
        return RedirectResponse("/setup", status_code=303)
    configured = "已配置" if health_bridge.configured else "尚未配置"
    body = f"""<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>
    <title>小米登录</title><style>body{{font:16px system-ui;max-width:600px;margin:50px auto;padding:0 20px;line-height:1.7}}button{{width:100%;padding:13px;font-size:16px;background:#111;color:#fff;border:0;border-radius:8px}}</style>
    <h1>小米登录</h1><p>当前状态：{configured}</p><p>点击后会生成小米登录二维码，也可以在同一部手机上打开登录链接。</p>
    <form method=post action='/setup/start'><button type=submit>生成小米登录码</button></form>"""
    return _no_store(HTMLResponse(body))


async def _run_qr_login() -> None:
    auth = XiaomiAuth()

    async def on_qr(qr_image_url: str, login_url: str) -> None:
        setup_state.qr_image_url = qr_image_url
        setup_state.login_url = login_url
        setup_state.status = "waiting"

    try:
        token = await auth.login_qr(qr_callback=on_qr, max_wait=300)
        token_json = token.model_dump_json(indent=2)
        health_bridge.set_token_json(token_json)
        setup_state.token_json = token_json
        setup_state.status = "done"
    except Exception as exc:
        setup_state.error = str(exc)
        setup_state.status = "error"
    finally:
        await auth.close()


async def setup_start(request: Request):
    if not _setup_authorized(request):
        return RedirectResponse("/setup", status_code=303)
    if setup_state.task and not setup_state.task.done():
        setup_state.task.cancel()
    setup_state.status = "starting"
    setup_state.qr_image_url = ""
    setup_state.login_url = ""
    setup_state.error = ""
    setup_state.token_json = ""
    setup_state.task = asyncio.create_task(_run_qr_login())
    return RedirectResponse("/setup/status", status_code=303)


async def setup_status(request: Request):
    if not _setup_authorized(request):
        return RedirectResponse("/setup", status_code=303)
    if setup_state.status in {"starting", "waiting"}:
        qr = (
            f"<img src='{html.escape(setup_state.qr_image_url, quote=True)}' alt='小米登录二维码' style='max-width:100%'>"
            if setup_state.qr_image_url
            else "<p>正在获取登录码……</p>"
        )
        link = (
            f"<p><a href='{html.escape(setup_state.login_url, quote=True)}' target='_blank' rel='noreferrer'>在浏览器打开小米登录</a></p>"
            if setup_state.login_url
            else ""
        )
        content = f"<meta http-equiv=refresh content=3><h1>请登录小米账号</h1>{qr}{link}<p>页面会自动检查登录结果。</p>"
    elif setup_state.status == "done":
        token = html.escape(health_bridge.export_token_json())
        content = f"""<h1>登录成功</h1><p>先复制下面的 JSON，然后到 Render 的 Environment 新增 <code>MI_TOKEN_JSON</code> 并粘贴保存。</p>
        <textarea readonly style='width:100%;height:300px'>{token}</textarea><p>保存环境变量后，这一页中的临时凭证会随服务重启清除。</p>"""
    elif setup_state.status == "error":
        content = f"<h1>登录没有完成</h1><p>{html.escape(setup_state.error)}</p><p><a href='/setup/home'>重新生成</a></p>"
    else:
        content = "<h1>尚未开始</h1><p><a href='/setup/home'>返回配置</a></p>"
    body = f"""<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>
    <title>小米登录状态</title><style>body{{font:16px system-ui;max-width:620px;margin:40px auto;padding:0 20px;line-height:1.7}}textarea{{font:12px ui-monospace,monospace}}a{{color:#165dff}}</style>{content}"""
    return _no_store(HTMLResponse(body))


mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@contextlib.asynccontextmanager
async def lifespan(_: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/", home, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", oauth_metadata, methods=["GET"]),
        Route("/approve", approve_get, methods=["GET"]),
        Route("/approve", approve_post, methods=["POST"]),
        Route("/setup", setup_get, methods=["GET"]),
        Route("/setup/login", setup_login, methods=["POST"]),
        Route("/setup/home", setup_home, methods=["GET"]),
        Route("/setup/start", setup_start, methods=["POST"]),
        Route("/setup/status", setup_status, methods=["GET"]),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
)
