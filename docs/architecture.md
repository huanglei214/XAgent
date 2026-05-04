# XAgent v2 架构设计

本文档记录 XAgent v2 当前已经确定的架构边界和实现方向。它是一份持续演进的
living architecture，不是终局设计。面向使用者的入口说明在 `README.md`，面向
coding agent 的工作约束在 `AGENTS.md`。

## 设计目标

XAgent v2 是一个本地通用 AI Agent。它的核心目标是：

- 在用户级 `~/.xagent` 下管理配置、默认 workspace、session 和 trace。
- 读取和编辑 workspace 文件，执行命令，调用工具和 API。
- 通过 CLI 和未来外部聊天 channel 与用户协作。
- 保持 Agent 核心逻辑与消息来源解耦，让 CLI、飞书或其他 channel 共享同一套 AgentLoop。
- 用 OpenAI-compatible 消息和工具调用形态作为 Agent 内部协议，provider 在边界层做适配。

## 非目标

当前 v1 不追求这些能力：

- 不复用 `main-v1` 的源码结构。
- 不实现多个 provider backend；当前只支持 `openai_compat`。
- 不做 prompt 模拟工具调用；provider 需要原生支持 OpenAI-style tool calling。
- 不实现持久化 Bus 或事件数据库。
- 外部聊天平台第一版只实现 `lark` 长连接文本 channel；不做多账号、图片、文件、卡片或富文本。
- 不做外部插件自动发现；Skill 第一版只作为轻量提示词和工具包组合。

## 总体结构

源码包位于根目录 `xagent/`，不使用 `src/` 布局。

```text
xagent/
  agent/       AgentLoop、AgentRunner、session-bound Agent、权限、工具、记忆、技能和子 Agent
  bus/         进程内 inbound/outbound 消息邮局
  channels/    外部消息源抽象和 channel manager
  cli/         Typer root app、agent/gateway 子命令和 CLI 专属组装逻辑
  config/      用户级配置、默认值和解析
  providers/   模型 provider 协议、factory 和 openai_compat 实现
  prompts/     内置 Markdown prompt 模板
  session/     session 包、messages.jsonl、trace.jsonl 和 artifacts
```

核心依赖方向是：

```text
CLI / Channel
    -> Bus
      -> AgentLoop
        -> Agent
          -> AgentRunner
            -> Provider
            -> Tools
        -> Session
```

Agent 不反向依赖 CLI 或具体 channel。Channel 负责外部平台接入、消息处理和发送，
Bus 负责进程内投递，Session 负责持久化。XAgent 刻意拆成三层：`AgentLoop` 管 Bus
和多 session，`Agent` 管单 session 的上下文与持久化，`AgentRunner` 管 ReAct 执行内核。

## 用户数据目录

用户级数据只放在 `~/.xagent`，默认目录结构是：

```text
~/.xagent/
  config.yaml
  workspace/
    files/
    sessions/
      <channel>:<chat-id>/
        messages.jsonl
        trace.jsonl
        artifacts/
```

- `config.yaml` 保存模型、provider、workspace、权限、trace、tools、channels 等配置。
- `workspace/files` 是默认 workspace。
- `workspace/sessions` 保存所有 session 包。
- 外部项目可以通过 `--workspace <path>` 指定，但 session 状态仍写回用户级目录。

## CLI 和 Gateway

console script 是 `xagent`。

- `xagent` 不带子命令时显示帮助。
- `xagent agent` 启动默认 CLI chat。
- `xagent agent -m/--message "..."` 执行一次性消息。
- `xagent agent -r/--resume <id>` 恢复或创建指定 session。
- `xagent agent -w/--workspace <path>` 指定 workspace。
- `xagent gateway` 启动已配置的外部 channel，目前支持第一版 `lark` 长连接。

CLI chat 是类聊天流程，因此走 Bus 和 AgentLoop，用来验证未来 channel 的主路径。
一次性 `-m/--message` 是显式旁路，可以直接构建 Agent 并调用 `Agent.run()`，但仍创建
session 包并写 trace。

`xagent/cli/main.py` 只保留 root Typer app 和 console script 入口。`xagent/cli/agent.py`
承载 `xagent agent` 的 chat、one-shot、终端渲染和 CLI 专属 Agent 装配逻辑；
`xagent/cli/gateway.py` 承载 `xagent gateway` 的 channel manager 与 AgentLoop 长期运行逻辑。
`xagent/cli/workspace.py` 只放 agent/gateway 共享的 workspace 路径解析。

## Bus / Channel / Session

Bus 是进程内消息邮局，只做当前进程里的 inbound/outbound 路由，不负责持久化。

Channel 继承 `BaseChannel`，是外部平台和 Bus 之间的适配器。`BaseChannel` 持有
`MessageBus`；具体 channel 在 `handle_message()` 中解析一条平台消息，构造标准
`InboundMessage`，并 publish 到 Bus。它不包含 Agent/runtime 逻辑。

Session 身份默认由 `channel:chat_id` 派生，显式 `session_id` override 优先。
`SessionStore.open_for_chat()` 是打开或创建 chat session 的统一入口；CLI 默认会用它打开
`cli:default`，gateway 则在 AgentLoop 收到 inbound 后懒打开对应 channel session。

Channel 生命周期方法是：

- `start()`：准备资源，短暂执行，成功后返回。
- `run()`：长期监听外部平台消息。
- `handle_message()`：处理一条平台消息，并在需要时投递 Bus。
- `send()`：发送一条 `OutboundEvent` 给外部用户。
- `stop()`：清理连接、任务和其他资源。

`ChannelManager` 管理一组 channel 的生命周期和出站路由：

- `start()`：启动所有 channel。
- `stop()`：停止所有 channel。
- `run()`：gateway/channel manager 的长期运行入口。
- `dispatch_outbound()`：消费一条 Bus outbound，按 `event.channel` 找到 channel，并调用 `channel.send(event)`。

### Lark / 飞书 Channel

第一版 `LarkChannel` 使用官方 `lark-oapi` Python SDK，通过 WebSocket 长连接接收事件。
标准 channel 名是 `lark`，session 身份默认是 `lark:<chat_id>`。

`start()` 会读取 `config.yaml` 中的 Lark 配置，创建 SDK API client，调用
`/open-apis/bot/v3/info` 获取一次机器人 `open_id` 并缓存，然后构建事件 handler 和
WebSocket client。SDK 的 WebSocket `start()` 是阻塞调用，且当前没有稳定 public stop
API；`LarkChannel` 因此通过 SDK 私有 `_connect/_ping_loop/_disconnect` 组合出可清理的
长期 loop，并在 `stop()` 时关闭自动重连、断开连接、取消 SDK loop 里的后台任务。

入站处理规则：

- 只处理 `P2ImMessageReceiveV1` 文本消息。
- 私聊消息全部响应。
- 群聊在 `require_mention: true` 时只响应 mentions 中包含机器人 `open_id` 的消息。
- 默认会从文本中去掉 @ 机器人的占位内容。
- 忽略空文本、非文本、机器人自己发送的消息和未 @ 机器人的群消息。

出站处理规则：

- `supports_streaming` 保持 `False`。
- 忽略 `StreamKind.DELTA`，只在 `StreamKind.END` 或 `stream is None` 时发送完整文本。
- 按 `chat_id` 发送新消息，不回复原消息 thread。
- `metadata["error"]` 为真时同样发送可见错误文本。

### InboundMessage

`InboundMessage` 表示从外部进入系统的一条用户消息：

- `channel`：消息来源通道，例如 `cli`、未来的 `lark`。
- `chat_id`：共享上下文的会话空间，例如群聊 id、私聊 id 或 CLI 的 `default`。
- `sender_id`：具体发言人。群聊中多个 sender 可以共享同一个 `chat_id` 上下文。
- `session_id`：显式 session override，例如 `--resume <id>`。
- `external_message_id`：外部平台原始消息 id，用于审计、去重或未来平台回复 API，不参与 runtime 关联。

### OutboundEvent

`OutboundEvent` 是发往外部 channel 的出站消息 envelope：

- `channel`：目标通道。
- `chat_id`：目标会话空间。
- `reply_to`：回复目标，通常来自 inbound 的 `sender_id`。
- `session_id`：本轮所属 session。
- `stream`：流式状态。
- `metadata`：扩展信息，例如 `{"error": true}`。

出站事件不再使用 `kind` 区分业务语义。当前只表达“要发出去的一条消息”，流式状态由
`StreamState` 控制：

- `StreamKind.DELTA`：`content` 是增量文本。
- `StreamKind.END`：`content` 是本轮完整最终文本。

同一个 `channel:chat_id` 内消息串行处理，所以不需要 `InboundMessage.id` 或
`OutboundEvent.inbound_id` 这种内部关联键。

### Session Identity

默认 session id 由 `channel:chat_id` 派生。CLI 默认值是：

```python
channel = "cli"
chat_id = "default"
sender_id = "user"
session_id = "cli:default"
```

显式 `--resume/-r <id>` 优先于默认派生规则。如果 session 存在则打开，不存在则创建。

## AgentLoop / Agent / AgentRunner

Nanobot 的核心运行职责主要集中在 Agent loop 里。XAgent 保留同样的 loop 思路，但为了学习和
调试时边界更清楚，显式拆成三层：

- `AgentLoop`：面向 Bus 和多 session 的长期运行层。
- `Agent`：面向单个 session 的上下文、prompt、summary 和持久化层。
- `AgentRunner`：纯 ReAct 执行内核。

`AgentLoop` 负责：

- 从 Bus 消费 `InboundMessage`。
- 解析或创建 session。
- 按 session 复用 Agent 实例。
- 调用 `Agent.run()`。
- 将模型文本 delta 转成 `OutboundEvent(stream=DELTA)`。
- 将最终回答转成 `OutboundEvent(stream=END)`。
- 捕获异常并发布 `OutboundEvent(stream=END, metadata={"error": true})`。
- 它不构造 prompt、不执行工具，也不直接写 session message。

Bus 路径下，AgentLoop 会把 sender 信息写入模型可见用户消息，例如：

```text
[sender:user_a] 帮我看一下这个文件
```

这样 Agent 不需要理解 channel 细节，但模型仍能在群聊场景中知道是谁发言。

`Agent` 表示一个 session-bound agent，负责：

- 使用 `xagent/prompts/*.md` 构造 system、summary 和空回复重试 prompt。
- 构造 OpenAI-compatible 模型消息。
- 在收到用户文本后写入 `messages.jsonl`。
- 在上下文超过阈值后触发 summary 压缩。
- 把 Runner 的 message / trace callback 写入 session。

`AgentRunner` 负责 ReAct 执行：

- 调用 provider stream。
- 聚合文本和 tool call。
- 执行工具。
- 记录模型请求、最终响应、usage、工具输入输出、错误和耗时。
- 维护最大步数、最大耗时和重复工具调用预算。
- 空回复时追加一次修正重试。

`AgentRunner` 不 import `Session`、Bus、Channel 或 PromptRenderer；这些都由外层注入或通过
callback 连接。

模型可见消息使用 OpenAI 兼容形态：

- 用户消息：`{"role": "user", "content": "..."}`
- assistant tool call：`tool_calls`
- tool result：`{"role": "tool", ...}`

`messages.jsonl` 保存模型可见消息和 summary；`trace.jsonl` 保存完整调试信息。

## Provider

Provider 只暴露一个流式接口：

```python
async def stream(request: ModelRequest) -> AsyncIterator[ModelEvent]
```

`ModelRequest` 包含：

- `model`
- `messages`
- `tools`
- `temperature`
- `max_tokens`
- `metadata`

`ModelEvent` 只包含四类：

- `text_delta`
- `tool_call_delta`
- `message_done`
- `usage`

当前 provider factory 只允许 `openai_compat`。配置通过 `agents.defaults.provider` 和
`providers.openai_compat` 解析，CLI 只消费 factory 产出的 provider snapshot，不直接依赖
OpenAI SDK。

Provider 错误直接抛出，由 Agent 记录 trace；Bus 路径下再由 AgentLoop 转成用户可见的
outbound error。

## Tools 和权限

工具是 class，不是裸函数。工具 schema 通过类装饰器显式声明，并统一转换成 OpenAI
function schema。

工具注册时只注入实际需要的依赖，不传大而全的 AgentContext。权限检查由工具内部和
approver 协作完成。

`xagent/agent/tools/` 按能力平铺拆分：`base.py` / `registry.py` 是机制层，
`files.py` / `search.py` / `shell.py` / `web.py` / `interaction.py` 放具体工具，
`default_tools.py` 只负责默认工具集合装配。`ToolRegistry` 不 import 具体工具。

工具元信息包括：

- `read_only`
- `exclusive`

只读且非独占工具可以并行。写文件、shell、外部网络/API 默认串行或独占。
`read_file` / `search` 默认允许；`apply_patch` 继续走写文件权限确认。`web_fetch` /
`web_search` 通过 `permissions.web` 控制，第一版默认允许公开网页查询并写入 trace。
`permissions.network_default` 保留给更高风险的通用网络/API 能力。`shell` 默认允许普通命令，
但会先经过黑名单策略，命中规则时直接作为 tool error 返回，不会再请求用户授权覆盖。

Shell 黑名单使用 `shlex` 做词法切分。规则可以是单 token，例如 `rm`、`sudo`，也可以是连续
token 序列，例如 `npm install`、`uv pip install`。这只是第一版安全下限，不承诺覆盖所有
shell 方言或间接执行风险。`curl` / `wget` 继续保留在黑名单里，避免 shell 绕过 web tools
的权限和 trace 边界。

首批内置工具方向包括：

- 读文件
- 搜索
- 补丁式编辑
- 执行命令
- 询问用户
- Web URL 读取
- Web 搜索

第一版不提供低层 `http_request` / 通用 API POST/PUT 工具。已知 URL 使用 `web_fetch`；
未知公开信息先使用 `web_search`，再对具体 URL 使用 `web_fetch`。`web_fetch` 先走 Jina
Reader，失败后用受限 direct GET 兜底。direct GET 只支持 `http/https` 的 `GET`，使用基础
浏览器请求头并读取文本、JSON 和 HTML 正文；不执行 JavaScript，不处理二进制附件，也不等价于
通用 HTTP/API 工具。

## Trace 和 Memory

`trace.jsonl` 用于调试和审计，保存：

- 模型 request
- 模型 final message
- usage
- provider/model 错误
- 工具准备信息
- 工具输入输出
- 工具错误和耗时

逐 chunk `model_event` 默认关闭，避免 trace 过大。需要时可以设置：

```yaml
trace:
  model_events: true
```

上下文压缩由 Agent 负责。当模型可见上下文超过阈值时，Agent 使用同一 provider/model 生成
summary，并写入 `messages.jsonl`。后续构造上下文时，最新 summary 会作为 system message
进入模型可见消息。

Prompt 模板仍然是普通 Markdown + Jinja2。模板可以使用浅层 XML 风格标签做语义分区，
例如 `<identity>`、`<runtime_context>`、`<tool_use>`，但第一版不引入 XML parser 或机器校验。

## Config

第一版配置位于 `~/.xagent/config.yaml`，核心结构是：

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

permissions:
  web:
    default: "allow"
  shell:
    default: "allow"
    blacklist:
      - "rm"
      - "sudo"
      - "curl"
      - "npm install"
      - "uv pip install"
      - ">"

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
```

默认值偏保守：限制 ReAct 步数、限制耗时、默认不记录逐 chunk model events。`channels.lark`
默认关闭；启用前需要在飞书/Lark 开放平台配置机器人能力、事件订阅
`im.message.receive_v1`，以及消息发送权限。

## 当前已实现

当前代码已经包含：

- `xagent` Typer CLI。
- `xagent agent` / `xagent gateway` 命令结构。
- `xagent gateway` 可启动已启用的 channel；未启用 channel 时返回配置提示。
- CLI 默认 `cli:default` session。
- CLI chat 走 Bus 和 AgentLoop。
- CLI `-m/--message` 一次性直调 Agent。
- 用户级 config、workspace 和 session 包。
- `messages.jsonl` / `trace.jsonl` 持久化。
- OpenAI-compatible provider factory。
- Provider stream event 聚合。
- Jinja2 Markdown prompt 模板渲染。
- 工具 class、schema registry、权限 approver 和按类型平铺的基础内置工具。
- `BaseChannel` 生命周期抽象和 ChannelManager。
- 第一版 `lark` 长连接文本 channel。
- `InboundMessage` / `OutboundEvent` 的 channel/chat/sender/reply/stream 模型。

## 后续演进

后续可以按这些方向扩展：

- 支持 Lark 多账号和更完整的消息类型，例如图片、文件、卡片和富文本。
- 为 Lark channel 增加更稳健的重连、去重、ack 和健康检查策略。
- 增加 provider retry wrapper，但保持 provider 自身只负责 stream。
- 增加更多 provider backend，但不改变 Agent/provider 协议。
- 完善 Skill 的提示词和工具包组合能力。
- 为权限策略增加更细粒度的 session 授权记忆。
- 为 trace 增加更好的查询和回放工具。
- 在需要支持同 session 并发时，再引入 `turn_id` 或 `run_id`，不要提前复杂化当前消息模型。
