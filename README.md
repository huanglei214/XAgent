# XAgent v2

XAgent v2 是一个从零开始设计的本地通用 AI Agent。它可以使用默认托管
workspace、编辑文件、调用工具、执行命令，并把会话级 trace 保存到用户级
`~/.xagent` 目录下。

这个分支不会复用 `main-v1` 的实现结构。旧代码仍保留在 `main-v1` 分支。

## 用户数据目录

```text
~/.xagent/
  config.yaml
  workspace/
    files/
    sessions/
      <channel>:<chat-id>/
        messages.jsonl
        summary.jsonl
        session_state.json
        trace.jsonl
        artifacts/
  memory/
    user.md
    soul.md
    workspaces/
      <workspace-id>/
        memory.md
        dream_state.json
  cron/
    tasks.json
```

`messages.jsonl` 保存 OpenAI 兼容的原始会话消息。`summary.jsonl` 保存上下文压缩结果，
`session_state.json` 保存当前 compact 游标。
`trace.jsonl` 保存模型请求、最终响应、usage、工具输入输出、错误和耗时，方便调试。
默认不记录逐 chunk 的 provider stream 事件；如果需要记录 `model_event`，可以设置
`trace.model_events: true`。

长期 memory 使用 Markdown：`user.md` 记录用户个人信息和长期偏好，`soul.md` 记录 Agent 沟通方式，
workspace 级 `memory.md` 记录当前 workspace 的长期项目事实。会话中输入 `/dream` 会让模型基于新的
compact summary 生成 JSON operations，再由程序更新 `user.md`、`soul.md` 和 workspace memory；
`/dream --compact` 会先强制 compact 当前 session。新 memory 文件从
`xagent/templates/memory/*.md` 初始化；模型 prompt 模板位于 `xagent/templates/prompts/`。

`cron/tasks.json` 保存 Agent 通过 `cron` tool 创建的定时任务。每个定时任务使用独立
`cron:<task_id>` session 执行，结果按任务中的 `target.channel/chat_id` 发回 Lark 或 Weixin。

## CLI

```bash
xagent agent
xagent agent -m "explain this workspace"
xagent agent -r cli:experiment
xagent agent -w /path/to/project
xagent channels login weixin
xagent gateway
```

`xagent` 不带子命令时显示帮助。`xagent agent` 启动 CLI chat，并使用默认
`cli:default` session。`xagent agent -m/--message` 会直接调用 Agent 执行一条
一次性消息，同时仍然写入 session 包和 trace。`xagent channels login weixin` 用于个人微信
二维码登录。`xagent gateway` 启动已配置的外部聊天 channel，目前支持 `lark` 长连接和
实验性的 `weixin` long-poll。`-r/--resume` 按 session 目录名恢复会话；如果 session 不存在则新建。
`-w/--workspace` 为新建 session 指定 workspace 路径。

CLI chat、Lark 和 Weixin 都支持会话内 slash command。第一版支持 `/dream`、`/dream --compact`
和 `/help`。

## Docker

Docker 部署默认复用宿主机 `~/.xagent`，容器内挂载为 `/root/.xagent`。因此不需要维护第二份
config；先确认宿主机 `~/.xagent/config.yaml` 已经填好 provider，并至少启用一个 channel。
配置里的 workspace 路径建议写成 `~/.xagent/...`，避免写入某台机器专属的绝对路径。

```bash
docker compose up -d --build
docker compose logs -f xagent
```

修改代码、依赖或 Dockerfile 后，重新 build 并启动：

```bash
docker compose up -d --build
```

只修改 `~/.xagent/config.yaml` 后，不需要 rebuild，重启容器即可：

```bash
docker compose restart xagent
```

如果启用 `weixin` channel，先用同一个挂载目录完成二维码登录：

```bash
docker compose run --rm xagent channels login weixin
docker compose up -d
```

## Bus 和 Channels

类聊天流程通过进程内 `MessageBus` 连接消息 channel 和 Agent runtime。session
身份默认是 `<channel>:<chat_id>`；CLI chat 使用 `cli:default`，未来外部平台可以使用
类似 `lark:<chat_id>` 的身份。

入站消息携带 `channel`、`chat_id` 和 `sender_id`。出站消息通过 `channel`、
`chat_id`、`reply_to` 和 stream state 完成路由和流式控制。`channels` 包定义
`BaseChannel` 生命周期抽象和 manager，并提供第一版 `lark` / `weixin` adapter。

## Lark / 飞书 Channel

`xagent gateway` 会读取 `channels.lark` 配置。未启用任何 channel 时，命令会给出配置提示并返回非 0。

```yaml
channels:
  lark:
    enabled: false
    app_id: null
    app_secret: null
    verification_token: null
    encrypt_key: null
    domain: "feishu"
    require_mention: true
    strip_mention: true
    auto_reconnect: true
    log_level: "info"
    reactions_enabled: true
    working_reaction: "OnIt"
    done_reaction: "DONE"
    message_format: "auto"
```

第一版只处理文本消息：私聊全部响应，群聊默认只有 @ 机器人时响应，并按 `chat_id`
发送新消息，不做原消息 thread 回复，也不做原生流式更新。`message_format: auto`
会把普通回复作为文本发送，把 Markdown 结构明显的回复作为飞书 interactive markdown 卡片发送。
收到有效消息后会给原消息添加
`OnIt` reaction，回复完成后移除 `OnIt` 并添加 `DONE` reaction；reaction 失败不会影响文本回复。
开放平台侧需要启用机器人能力、事件订阅 `im.message.receive_v1`，并授予消息发送和消息表情回复相关权限。

## Weixin / 个人微信 Channel

`weixin` 是实验性个人微信 channel，参考 nanobot 的做法，使用 ilinkai 个人微信 HTTP
long-poll API 和二维码登录。它不是公众号、企业微信或官方微信开放平台 API；稳定性和账号风险
不应按官方机器人能力假设。

```yaml
channels:
  weixin:
    enabled: false
    allow_from: []
    base_url: "https://ilinkai.weixin.qq.com"
    route_tag: null
    token: null
    state_dir: null
    poll_timeout_seconds: 35
```

第一版只支持私聊文本收发。`allow_from: []` 默认拒绝所有发送者；设置为 `["*"]` 可允许所有人，
也可以填入日志或 trace 中看到的微信用户 ID。首次使用先运行：

```bash
xagent channels login weixin
```

登录成功后 token、cursor 和回复所需的 context token 默认保存在
`~/.xagent/channels/weixin/account.json`。`xagent gateway` 只加载已保存状态，不会在长期运行入口里弹二维码。

## Provider 配置

XAgent 当前只支持一个 provider backend：`openai_compat`。

```yaml
agents:
  defaults:
    model: "gpt-4o-mini"
    provider: "openai_compat"
    temperature: null
    max_tokens: null

providers:
  openai_compat:
    api_key: null
    api_base: null
    extra_headers: {}
    extra_body: {}
    timeout_seconds: 120
```

`providers.openai_compat.api_key` 直接从配置读取。如果没有配置，XAgent 会向
OpenAI-compatible SDK client 传入 `no-key`，方便本地无鉴权 endpoint 运行。

## Memory 配置

```yaml
memory:
  enabled: true
  inject_user: true
  inject_soul: true
  inject_workspace: true
```

关闭 `memory.enabled` 后不会读取或初始化长期 memory；三个 `inject_*` 字段只控制 system
prompt 是否注入对应 Markdown 内容，不删除已有 memory 文件。

## Cron 定时任务

定时任务不是通过 CLI 手工管理，而是让 Agent 在对话中调用 `cron` tool 创建、更新、删除或查询。
例如在飞书群里说“每天早上 9 点帮我收集 AI 热门新闻发到这个群”，Agent 会把目标群作为
`target` 写入 `~/.xagent/cron/tasks.json`。`xagent gateway` 运行时会启动 `CronService`，
到点后把任务投递进 Bus，由 AgentLoop 使用 `cron:<task_id>` 独立 session 执行。

第一版只支持标准 cron 表达式，不支持 interval、once、delay、retry 或自定义 misfire 策略。
gateway 启动时会跳过已经错过的历史触发时间，并重新计算下一次执行时间。

```yaml
cron:
  enabled: true
  tasks_path: "~/.xagent/cron/tasks.json"
  poll_interval_seconds: 30.0
  default_timezone: "Asia/Shanghai"
```

`cron.enabled: false` 会同时关闭 `cron` tool 和 gateway 中的定时任务循环。`cron create/update/delete`
是未来自动执行动作，默认需要确认：

```yaml
permissions:
  cron:
    default: "ask"  # allow | ask | deny
```

## Tools 配置

Web 工具通过 `tools.web.enabled` 控制。已知 URL 使用 `web_fetch`；未知公开信息先用
`web_search` 找候选页面，再按需用 `web_fetch` 读取具体 URL。

```yaml
tools:
  web:
    enabled: true
    fetch_backend: "jina"
    search_backend: "auto"
    timeout_seconds: 30
    max_fetch_chars: 20000
    max_search_results: 5
    jina:
      api_key: null
      reader_base_url: "https://r.jina.ai"
    tavily:
      api_key: null
    duckduckgo:
      enabled: true
```

`web_fetch` 默认先使用 Jina Reader，未配置 API key 时也可使用基础模式；Jina 失败后会用
受限 direct GET 兜底。direct GET 只支持 `http/https` 的 `GET`，会使用基础浏览器请求头，
并读取文本、JSON 和 HTML 正文；它不执行 JavaScript，也不是通用 HTTP/API 工具。
`web_search` 在配置 Tavily API key 时优先使用 Tavily，否则使用 DuckDuckGo fallback。

## 权限和 Shell 策略

只读工具 `read_file` / `search` 默认允许。`apply_patch` 继续按写文件风险走确认。
`web_fetch` / `web_search` 用 `permissions.web` 控制，默认允许公开网页查询并写入 trace。
`shell` 第一版采用“默认允许加黑名单直接拒绝”：普通只读命令不会反复弹授权，
命中高风险规则时也不会提供授权覆盖。

```yaml
permissions:
  web:
    default: "allow"  # allow | ask | deny
  shell:
    default: "allow"  # allow | ask | deny
    blacklist:
      - "rm"
      - "sudo"
      - "curl"
      - "npm install"
      - "uv pip install"
      - ">"
```

黑名单使用 `shlex` 词法切分，支持单 token 和连续 token 序列，例如 `npm install`。
`curl` / `wget` 继续保留在黑名单里，避免 shell 绕过 web tools 的权限和 trace 边界。
这是基础安全下限，不是完整 shell 沙箱；只读探索仍建议优先使用专门的文件工具。
