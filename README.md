# 柏渊健康桥

把当前登录的小米运动健康账号本人的健康数据和逐次运动记录，以只读 MCP 工具连接到 ChatGPT。

项目不会把小米凭证或健康数据写进 Git。远程 MCP 使用 OAuth 2.1、PKCE 和短时访问令牌保护，所有健康工具均为只读。

## 手机上完成部署

1. 点击下方按钮，在 Render 创建服务。
2. Render 会要求填写 `OWNER_SECRET`。请设置一个至少 12 位、只有你知道的连接密码。
3. 部署完成后，打开 `https://你的服务地址/setup`，输入连接密码，按页面提示登录小米账号。
4. 登录成功后复制页面中的 token JSON。
5. 回到 Render 服务的 **Environment**，新增密钥 `MI_TOKEN_JSON`，粘贴 JSON 并保存。Render 会自动重新部署。
6. 打开 `https://你的服务地址/health`，看到 `configured: true` 即配置完成。

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/yuanleaf-lab/boyuan-health-bridge)

## 连接 ChatGPT

1. 在 ChatGPT 的 **设置 → 安全与登录** 中打开开发者模式。
2. 进入 **插件**，点击右上角加号。
3. MCP 地址填写 `https://你的服务地址/mcp`。
4. 授权页面出现后，输入部署时设置的 `OWNER_SECRET`。

连接后可以直接问：

- “看看我今天的健康摘要”
- “确认当前登录的小米账号是本人”
- “看看最近 7 天的睡眠和心率”
- “读取我最近 30 天的小米运动记录”
- “读取 workout_time 为……的运动详情”

## 提供的只读工具

- `list_family_members`：确认当前登录的小米账号本人。
- `get_health_snapshot`：读取本人最新健康快照和每日摘要。
- `get_health_history`：按日期读取心率、睡眠、步数、血氧、卡路里等历史数据；查询 `weight` 时保留原始体脂秤身体成分字段。
- `get_body_composition_history`：按本人账号原始接口读取 1–3650 天体脂秤记录；按 `(设备 sid, 测量时间)` 去重，避免同一设备多次称重被合并；使用 `pages_fetched`、`has_more` 和 `history_complete` 明确报告分页完整性。
- `get_sport_records`：读取本人逐次运动记录，含运动类型、起止时间、时长、距离、热量和心率等摘要。
- `get_sport_detail`：读取单次运动逐秒采样；目前覆盖自由训练/力量训练（proto 8，FDS v1–v5）、已验证的户外跑步/骑行格式，可返回心率及设备提供的其他采样指标；GPS 轨迹默认关闭。
- `get_bridge_status`：检查桥接服务是否已配置，不返回任何密钥。

## 统一个人健身数据库

项目可将多个来源的数据写入 PostgreSQL，保留原始 JSON 以便以后扩展字段，并按来源 ID 幂等更新。

### 数据模型

- `fitness_body_measurements`：体重、BMI、体脂率、脂肪量、肌肉量、骨骼肌量及完整体脂秤原始字段。
- `fitness_health_metrics`：心率、睡眠、步数、热量、血氧、活动强度、站立和血压等日常健康记录。
- `fitness_workout_sessions`：小米有氧/运动摘要和练练健身力量训练会话。
- `fitness_workout_samples`：小米运动逐秒采样。
- `fitness_strength_exercises` / `fitness_strength_sets`：练练健身动作、组数、重量、次数和组间休息。
- `fitness_sync_runs` / `fitness_sync_state`：同步历史、数据覆盖和最近成功时间。

### 部署配置

当前 Render 免费 Web Service 的本地文件系统不适合作为长期数据库，因此统一数据库使用外部 PostgreSQL（例如 Neon）。在 Render → Environment 配置：

- `DATABASE_URL`：PostgreSQL 连接串。只放在 Render 环境变量，不要提交到 Git。
- `LIANLIAN_API_TOKEN`：练练健身 AI skill 的 Bearer Token。只放在 Render 环境变量，不要发到聊天或提交到 Git。

保存后 Render 会自动重新部署。首次调用同步工具时会自动创建所需表，不需要手动执行 SQL。

### MCP 数据库工具

- `sync_personal_fitness_data(days=30, include_sport_details=false)`：将小米本人最近 1–30 天的身体成分、日常健康指标和运动记录写入数据库；开启 `include_sport_details` 时额外读取支持格式的逐秒运动采样。
- `get_fitness_database_summary(days=30)`：查看各来源记录数、最近同步结果和数据覆盖。
- `get_lianlian_skill_manifest()`：安全读取练练健身当前返回的技能/API说明，不返回 Token。
- `ingest_lianlian_workouts(records)`：接收已标准化的练练健身训练记录，写入训练会话、动作和组数据。

练练健身自动读取适配器必须先依据 `get_lianlian_skill_manifest` 返回的当前文档确认端点和字段，不猜测未公开的 API。标准导入结构为：训练记录包含 `id`、`started_at`、`exercises`；每个动作包含 `name`、`sets`；每组可包含 `weight_kg`、`reps`、`duration_seconds`、`rest_seconds`。完整原始记录会保留在 `raw_data`。

## 本地开发

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
export OWNER_SECRET='change-this-password'
export OAUTH_SIGNING_SECRET='change-this-signing-secret-at-least-32-chars'
uvicorn app.server:app --reload
```

测试使用合成数据，不会访问真实小米账号：

```bash
pytest
```

## 数据与安全

- 只读取当前登录的小米账号本人数据。
- 不提供邀请、删除亲友或修改健康数据的工具。
- `MI_TOKEN_JSON` 只应放在部署平台的加密环境变量中。
- `/setup` 生成的 token 页面带有 `no-store`，且需要 `OWNER_SECRET`。
- 这是个人健康数据桥接工具，不用于医疗诊断或紧急情况。

## 上游与许可

健康数据基础访问由 [Misty02600/mi-fitness-python](https://github.com/Misty02600/mi-fitness-python) 提供；运动记录和 FDS 运动明细读取在本项目中扩展。本项目采用 GPL-3.0 许可。
