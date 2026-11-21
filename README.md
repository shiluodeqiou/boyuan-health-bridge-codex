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
- “列出亲友共享里有哪些人”
- “看看最近 7 天的睡眠和心率”
- “读取我最近 30 天的小米运动记录”
- “读取 workout_time 为……的运动详情”

## 提供的只读工具

- `list_family_members`：确认当前登录的小米账号本人。
- `get_health_snapshot`：读取本人最新健康快照和每日摘要。
- `get_health_history`：按日期读取心率、睡眠、步数、血氧、卡路里等历史数据。
- `get_sport_records`：读取本人逐次运动记录，含运动类型、起止时间、时长、距离、热量和心率等摘要。
- `get_sport_detail`：读取单次运动逐秒采样；已验证的户外跑步/骑行可返回心率、配速/速度、累计距离，GPS 轨迹默认关闭。
- `get_bridge_status`：检查桥接服务是否已配置，不返回任何密钥。

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

- 只读取亲友已主动分享给登录账号的数据。
- 不提供邀请、删除亲友或修改健康数据的工具。
- `MI_TOKEN_JSON` 只应放在部署平台的加密环境变量中。
- `/setup` 生成的 token 页面带有 `no-store`，且需要 `OWNER_SECRET`。
- 这是个人健康数据桥接工具，不用于医疗诊断或紧急情况。

## 上游与许可

健康数据基础访问由 [Misty02600/mi-fitness-python](https://github.com/Misty02600/mi-fitness-python) 提供；运动记录和 FDS 运动明细读取在本项目中扩展。本项目采用 GPL-3.0 许可。
