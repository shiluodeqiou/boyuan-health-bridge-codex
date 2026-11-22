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

项目将当前登录的小米运动健康本人数据与练练健身训练记录写入同一个 PostgreSQL 数据库（Neon）。上游数据源只读；同步只写入用户自己的数据库，不修改小米或练练 App 中的记录。

### 统一数据模型

- \`fitness_body_measurements\`：逐次体脂秤测量，含体重、BMI、体脂率、脂肪量、肌肉量、骨骼肌、骨量、蛋白质、水分、去脂体重、内脏脂肪、基础代谢、腰臀比、身体评分/年龄及四肢/躯干分段数据。完整原始 JSON 永久保留。
- \`fitness_health_metrics\`：心率、睡眠、步数、热量、血氧、活动强度、站立和血压等日常指标。
- \`fitness_workout_sessions\`：统一运动会话；用 \`source\` 区分 \`xiaomi\` 与 \`lianlian\`，并用 \`session_date\`、\`modality\`、时长、热量、心率、训练类型和来源元数据关联。
- \`fitness_workout_samples\`：小米运动逐次采样。
- \`fitness_strength_exercises\`：动作名称、动作 ID、重量单位、动作容量、计划休息和原始动作 JSON。
- \`fitness_strength_sets\`：每组重量数值/单位、kg 标准重量、次数、时长、计划/实际休息、完成状态/时间、PR 和估算 1RM、心率扩展字段及原始组 JSON。
- \`fitness_sync_runs\` / \`fitness_sync_state\`：每次同步的状态、覆盖范围、计数和最近成功时间。

### 自动迁移与分析视图

服务启动时通过幂等 DDL 将旧表升级，不删除历史记录；迁移会从已有原始 JSON 回填新增的身体成分、训练类型、动作和组字段。

- \`fitness_daily_overview\`：按中国标准时间日期合并体脂秤、日常健康指标、小米有氧运动和练练力量训练。
- \`fitness_strength_progression\`：按日期和动作统计组数、次数、训练容量、最大重量及 Epley 估算 1RM。

力量训练容量仅累计明确标注为 kg 且有次数的组；体重训练或未知单位不会被臆算为外部负重。Epley 估算 1RM 仅用于趋势参考，不代表实测最大重量。体脂秤原始测量逐条保存，日视图仅展示当日最后一次测量。

### 部署配置

在 Render → Environment 配置：

- \`DATABASE_URL\`：Neon PostgreSQL 连接串，应用运行建议使用带 \`-pooler\` 的连接地址。
- \`LIANLIAN_API_TOKEN\`：练练健身 AI skill Bearer Token。

密钥只放在 Render 环境变量，不提交到 Git。当前 Render 免费 Web Service 的本地文件系统不适合持久化数据库文件，因此统一数据保存在 Neon。

### MCP 工具

- \`sync_unified_fitness_database(days=30, include_sport_details=false)\`：同步小米身体成分、日常健康指标、运动摘要，以及练练官方训练记录。练练接口自动分页，全部页面确认 \`hasMore=false\` 后才写入；按来源 ID 幂等更新。
- \`sync_personal_fitness_data(days=30, include_sport_details=false)\`：仅同步小米数据。
- \`get_fitness_database_summary(days=30)\`：查看来源记录数和最近同步任务。
- `get_sync_status()`：查看各数据源最后成功同步、最近尝试、最新源记录时间和最近同步日志。
- `get_data_quality_report(days=30)`：查看身体成分字段覆盖率、力量训练组完成状态、健康指标覆盖和逐秒运动采样情况。
- `get_segmental_body_trends(days=30, metric="fat_mass", parts=null, smoothing_window=7)`：查看躯干、左右上肢和左右下肢脂肪量/肌肉量趋势；按每日最后一次测量聚合，缺失值不补零，移动平均按最近有效测量次数计算。下肢数据不是独立大腿数据。
- `get_strength_progression(exercise_name, days=90)`：查看指定动作的逐次力量进步、有效组数、训练容量和估算 1RM。
- `get_cardio_analysis(days=30)`：按有氧项目汇总次数、时长、热量、平均/最高心率，并检查逐秒采样可用性。
- \`get_fitness_dashboard(days=90, include_details=false)\`：默认读取轻量汇总，包含体重、体脂率、脂肪量、肌肉量的首末值和 7 日均值，以及按周训练次数、完成组数、有氧时长和热量；设置 \`include_details=true\` 才读取每日时间轴、动作进步和健康指标明细。
- \`get_fat_loss_summary(days=30)\`：专门用于最近减脂效果评价的紧凑接口，所有指标直接由 Neon 服务端聚合，不请求小米或练练健身上游接口。
- \`get_lianlian_skill_manifest()\`：安全读取练练当前技能说明。
- \`get_lianlian_training_records_page(start_date, end_date, page, page_size)\`：只读诊断练练原始分页，不写数据库。
- \`ingest_lianlian_workouts(records)\`：接收已标准化训练数据的底层导入工具。

练练自动同步使用官方 \`GET /api/v1/ai/training/records\`，按日期分页读取，保留会话、动作和每组原始 JSON。每组缺失重量或次数保持 NULL；无重量单位的自重动作不转换为 kg。重复同步会更新已有会话和子记录，并清理来源已删除的多余动作/组次索引。

### 同步策略

统一同步默认回看最近 30 天，采用重叠窗口幂等 upsert：可捕获迟到写入或最近修改的记录，同时避免重复。每日自动同步由 ChatGPT Automation 调用统一同步 MCP 工具执行；如果自动化尚未创建，需要在连接与部署验证完成后启用。

读取路径会在进程内缓存数据库初始化状态，避免每次 MCP 查询重复执行 DDL 和历史迁移。减脂摘要和默认仪表盘只返回聚合字段，原始身体成分与逐组 JSON 只在明细模式中读取。

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
