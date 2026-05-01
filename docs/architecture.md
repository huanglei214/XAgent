# XAgent v2 架构设计

本文档记录 XAgent v2 当前已经确定的架构边界和实现方向。它是一份持续演进的
living architecture，不是终局设计。面向使用者的入口说明在 `README.md`，面向
coding agent 的工作约束在 `AGENTS.md`。

## 设计目标

XAgent v2 是一个本地通用 AI Agent。它的核心目标是：

- 在用户级 `~/.xagent` 下管理配置、默认 workspace、session 和 trace。
- 读取和编辑 workspace 文件，执行命令，调用工具和 API。
- 通过 CLI 和未来外部聊天 channel 与用户协作。
- 保持 Agent 核心逻辑与消息来源解耦，让 CLI、飞书或其他 channel 共享同一套 runtime。
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
  agent/       Agent 核心逻辑、runtime、权限、工具、记忆、技能和子 Agent
  bus/         进程内 inbound/outbound 消息邮局
  channels/    外部消息源抽象和 channel manager
  cli/         Typer CLI 入口和 CLI 专属组装逻辑
  config/      用户级配置、默认值和解析
  providers/   模型 provider 协议、factory 和 openai_compat 实现
  session/     session 包、messages.jsonl、trace.jsonl 和 artifacts
```

核心依赖方向是：

```text
CLI / Channel
    -> Bus
      -> AgentRuntime
        -> Agent
          -> Provider
          -> Tools
        -> Session
```

Agent 不反向依赖 CLI 或具体 channel。Channel 负责外部平台接入、消息处理和发送，
Bus 负责进程内投递，Session 负责持久化，Agent 负责智能循环。

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

CLI chat 是类聊天流程，因此走 Bus 和 AgentRuntime，用来验证未来 channel 的主路径。
一次性 `-m/--message` 是显式旁路，可以直接构建 Agent 并调用 `Agent.run()`，但仍创建
session 包并写 trace。

## Bus / Channel / Session

Bus 是进程内消息邮局，只做当前进程里的 inbound/outbound 路由，不负责持久化。

Channel 继承 `BaseChannel`，是外部平台和 Bus 之间的适配器。`BaseChannel` 持有
`MessageBus`；具体 channel 在 `handle_message()` 中解析一条平台消息，构造标准
`InboundMessage`，并 publish 到 Bus。它不包含 Agent/runtime 逻辑。

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

## Agent Runtime

`AgentRuntime` 是 Bus 面向 Agent 的运行层。它负责：

- 从 Bus 消费 `InboundMessage`。
- 解析或创建 session。
- 按 session 复用 Agent 实例。
- 调用 `Agent.run()`。
- 将模型文本 delta 转成 `OutboundEvent(stream=DELTA)`。
- 将最终回答转成 `OutboundEvent(stream=END)`。
- 捕获异常并发布 `OutboundEvent(stream=END, metadata={"error": true})`。

Bus 路径下，runtime 会把 sender 信息写入模型可见用户消息，例如：

```text
[sender:user_a] 帮我看一下这个文件
```

这样 Agent 不需要理解 channel 细节，但模型仍能在群聊场景中知道是谁发言。

## Agent Core

`Agent` 是核心智能逻辑，负责 ReAct 循环：

- 构造 OpenAI-compatible 模型消息。
- 调用 provider stream。
- 聚合文本和 tool call。
- 执行工具。
- 记录模型请求、最终响应、usage、工具输入输出、错误和耗时。
- 维护最大步数、最大耗时和重复工具调用预算。
- 在上下文超过阈值后触发 summary 压缩。

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

Provider 错误直接抛出，由 Agent 记录 trace；Bus runtime 再把错误转换成用户可见的
outbound error。

## Tools 和权限

工具是 class，不是裸函数。工具 schema 通过类装饰器显式声明，并统一转换成 OpenAI
function schema。

工具注册时只注入实际需要的依赖，不传大而全的 AgentContext。权限检查由工具内部和
approver 协作完成。

工具元信息包括：

- `read_only`
- `exclusive`

只读且非独占工具可以并行。写文件、shell、外部网络/API 默认串行或独占。

首批内置工具方向包括：

- 读文件
- 搜索
- 补丁式编辑
- 执行命令
- 询问用户
- 基础 HTTP/API 调用

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
- CLI chat 走 Bus 和 AgentRuntime。
- CLI `-m/--message` 一次性直调 Agent。
- 用户级 config、workspace 和 session 包。
- `messages.jsonl` / `trace.jsonl` 持久化。
- OpenAI-compatible provider factory。
- Provider stream event 聚合。
- 工具 class、schema registry、权限 approver 和基础内置工具。
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
