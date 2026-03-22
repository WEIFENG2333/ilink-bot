# iLink Bot SDK

**Standalone Python SDK for WeChat iLink Bot protocol — no OpenClaw dependency required.**

[![CI](https://github.com/WEIFENG2333/ilink-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/WEIFENG2333/ilink-bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/ilink-bot)](https://pypi.org/project/ilink-bot/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## What is this?

2026年3月，微信推出 ClawBot 官方插件，底层使用腾讯自研的 **iLink（智联）协议**，这是微信历史上首次开放合法的个人账号 Bot 消息通道。

现有的开源项目全部依赖 OpenClaw 框架。**iLink Bot SDK 是第一个完全独立的 Python SDK**，无需任何第三方框架依赖，直接对接 iLink 协议。

### Features

- **三层架构**：协议层 → Bot 框架层 → 生态接入层，各层独立可用
- **异步优先**：基于 `asyncio` + `httpx`，天然支持高并发
- **强类型**：Pydantic 数据模型，完整的类型标注
- **装饰器路由**：`@bot.on_message(filters.text)` 风格，开发体验接近 python-telegram-bot
- **可组合过滤器**：支持 `&` `|` `~` 运算符自由组合
- **富媒体消息**：图片、文件、视频、语音收发，CDN 加密自动处理 (AES-128-ECB)
- **智能上下文**：`context_token` 自动缓存与回填，无需手动管理会话关联
- **发送限流**：内置 Token Bucket 速率限制器，默认 1 msg/s，防止触发微信限制
- **MCP Server**：一键暴露为 AI 工具（Claude Desktop / Claude Code / Cursor）
- **Webhook Gateway**：长轮询转 HTTP POST，语言无关
- **CLI 工具**：一行命令发微信，CI/CD 通知利器

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│            Layer 3: Ecosystem Adapters            │
│    MCP Server  │  Webhook Gateway  │  CLI Tool    │
├──────────────────────────────────────────────────┤
│            Layer 2: Bot Framework                 │
│  Event-driven  │  @on_message  │  Filters         │
│  Long-poll mgmt │  Cursor persistence │  Backoff  │
├──────────────────────────────────────────────────┤
│            Layer 1: ILinkClient                   │
│  HTTP API  │  Token mgmt  │  Typed models         │
│  CDN media │  Rate limiter │  context_token cache  │
└──────────────────────────────────────────────────┘
```

---

## Quick Start

### Install

```bash
pip install ilink-bot          # Core SDK
pip install ilink-bot[cli]     # + CLI tool
pip install ilink-bot[mcp]     # + MCP Server
pip install ilink-bot[all]     # Everything
```

### Login

```bash
ilink-bot login
# Scan the QR code with WeChat → token saved to ~/.ilink-bot/token.json
```

### Build a Bot (5 lines)

```python
from ilink_bot import WeChatBot, filters

bot = WeChatBot()

@bot.on_message(filters.text)
async def echo(msg):
    await msg.reply(f"You said: {msg.text}")

bot.run()
```

### Send from CLI

```bash
ilink-bot send --to "user@im.wechat" "Hello from CLI!"

# Pipe support
echo "Build passed!" | ilink-bot send --to "user@im.wechat" -
```

---

## Usage Guide

### Layer 1: Direct API Access

For scripts, one-off sends, or custom implementations:

```python
import asyncio
from ilink_bot import ILinkClient

async def main():
    async with ILinkClient() as client:
        # Send a text message
        await client.send_text("user@im.wechat", "Hello!")

        # Send an image (auto CDN upload + AES encryption)
        image_bytes = open("photo.jpg", "rb").read()
        await client.send_image("user@im.wechat", image_bytes)

        # Send a file
        pdf_bytes = open("report.pdf", "rb").read()
        await client.send_file("user@im.wechat", pdf_bytes, "report.pdf")

        # Poll for messages (context_token auto-cached)
        resp = await client.get_updates()
        for msg in resp.msgs:
            print(f"From: {msg.from_user_id}")

            # Download received media
            if msg.item_list and msg.item_list[0].type == 2:  # IMAGE
                media = msg.item_list[0].image_item.media
                data = await client.download_media(
                    media.encrypt_query_param, media.aes_key
                )
                open("received.jpg", "wb").write(data)

asyncio.run(main())
```

### Layer 2: Bot Framework

Event-driven bot with filters:

```python
from ilink_bot import WeChatBot, filters

bot = WeChatBot()

# Text messages
@bot.on_message(filters.text)
async def on_text(msg):
    await msg.reply(f"Echo: {msg.text}")

# Images
@bot.on_message(filters.image)
async def on_image(msg):
    await msg.reply("Got your image!")

# Slash commands
@bot.on_message(filters.command("help"))
async def on_help(msg):
    await msg.reply("Available: /help, /ping")

# Combined filters
@bot.on_message(filters.text & filters.contains("urgent"))
async def on_urgent(msg):
    await msg.reply("I see this is urgent!")

# Error handling
@bot.on_error
async def on_error(exc, msg):
    print(f"Error: {exc}")

bot.run()
```

#### Available Filters

| Filter | Description |
|--------|-------------|
| `filters.text` | Text messages |
| `filters.image` | Image messages |
| `filters.voice` | Voice messages |
| `filters.file` | File messages |
| `filters.video` | Video messages |
| `filters.all` | All messages |
| `filters.contains("kw")` | Text contains keyword |
| `filters.regex(r"\d+")` | Regex match |
| `filters.command("help")` | `/help` command |
| `filters.from_user("id")` | Specific sender |
| `f1 & f2` | AND |
| `f1 \| f2` | OR |
| `~f1` | NOT |

### Layer 3a: MCP Server

Expose WeChat as AI tools:

```bash
# stdio mode (Claude Desktop / Claude Code)
ilink-bot mcp

# HTTP mode
ilink-bot mcp --transport http --port 8080
```

Claude Desktop / Claude Code configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "wechat": {
      "command": "uvx",
      "args": ["--from", "ilink-bot[mcp]", "ilink-bot", "mcp"]
    }
  }
}
```

Or if installed globally:

```json
{
  "mcpServers": {
    "wechat": {
      "command": "ilink-bot",
      "args": ["mcp"]
    }
  }
}
```

**Exposed tools:**
- `wechat_send_message` — Send a text message
- `wechat_get_messages` — Get recent messages
- `wechat_bot_status` — Check connection status

### Layer 3b: Webhook Gateway

Convert long-poll to HTTP webhooks:

```bash
ilink-bot webhook --url https://your-server.com/wechat --secret your_hmac_secret
```

Webhook payload format:

```json
{
  "id": "msg_id",
  "from_user": "user@im.wechat",
  "type": "text",
  "content": "message text",
  "timestamp": 1742000000000,
  "context_token": "ctx_..."
}
```

Headers include `X-ILink-Signature` (HMAC-SHA256) for verification.

### Layer 3c: CLI

```bash
ilink-bot login                    # QR code login
ilink-bot status                   # Connection status
ilink-bot send --to ID "message"   # Send message
ilink-bot mcp                      # Start MCP server
ilink-bot webhook --url URL        # Start webhook gateway
```

**GitHub Actions example:**

```yaml
- name: Notify WeChat
  run: |
    pip install ilink-bot[cli]
    ilink-bot send --to "${{ secrets.WECHAT_USER }}" "Deploy complete: ${{ github.sha }}"
  env:
    ILINK_TOKEN: ${{ secrets.ILINK_TOKEN }}
```

---

## Configuration

### Token sources (priority order)

1. Constructor parameter: `ILinkClient(token="...")`
2. Environment variable: `ILINK_TOKEN`
3. Token file: `~/.ilink-bot/token.json` (default)

**已有 token 的用户无需扫码**，直接配置即可：

```bash
# 方式一：环境变量（推荐在 CI/CD 中使用）
export ILINK_TOKEN="your_bot_token_here"
ilink-bot send --to "user@im.wechat" "Hello!"

# 方式二：命令行参数
ilink-bot send --token "your_bot_token_here" --to "user@im.wechat" "Hello!"

# 方式三：代码中直接传入
from ilink_bot import ILinkClient
client = ILinkClient(token="your_bot_token_here")
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `ILINK_TOKEN` | Bot token (alternative to token file / QR login) |
| `ILINK_BASE_URL` | API base URL override (default: `https://ilinkai.weixin.qq.com`) |
| `ILINK_TOKEN_FILE` | Token file path override (default: `~/.ilink-bot/token.json`) |

---

## Project Structure

```
src/ilink_bot/
├── __init__.py              # Public API exports
├── client/
│   ├── __init__.py
│   ├── client.py            # Layer 1: ILinkClient (protocol + media)
│   ├── cdn.py               # AES-128-ECB CDN upload / download
│   └── rate_limiter.py      # Async token-bucket rate limiter
├── models/
│   ├── __init__.py
│   └── messages.py          # Pydantic data models
├── bot/
│   ├── __init__.py
│   ├── bot.py               # Layer 2: WeChatBot (framework)
│   └── filters.py           # Filter system
├── mcp/
│   ├── __init__.py
│   └── server.py            # Layer 3a: MCP Server (FastMCP)
├── webhook/
│   ├── __init__.py
│   └── gateway.py           # Layer 3b: Webhook Gateway
└── cli/
    ├── __init__.py
    └── main.py              # Layer 3c: CLI (Typer)
```

---

## Development

```bash
# Clone
git clone https://github.com/WEIFENG2333/ilink-bot.git
cd ilink-bot

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/ilink_bot --ignore-missing-imports
```

---

## Important Notes

1. **ClawBot limitations**: No group chat support, no forwarded messages, no proactive push (user must message first), one bot per account
2. **Rate limiting**: Built-in send queue recommended at 1 msg/s to comply with WeChat terms
3. **Token security**: Token file is saved with `chmod 600`; use `ILINK_TOKEN` env var in CI/CD
4. **Protocol version**: Based on `channel_version: 0.1.0`, protocol may change with updates
5. **Compliance**: iLink is an official protocol — follow the [WeChat ClawBot Terms of Service](https://weixin.qq.com/), no bulk messaging or marketing

---

## Credits

- Protocol details derived from [`hao-ji-xing/openclaw-weixin`](https://github.com/hao-ji-xing/openclaw-weixin)
- Architecture inspired by [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot)

## License

[MIT](LICENSE)
