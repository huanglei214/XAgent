# Proposal: 精简 bus 为 nanobot 风格的两条队列

- **ID**：0001-simplify-bus
- **状态**：Draft（待审阅）
- **作者**：Assistant（基于用户需求整理）
- **语言**：中文
- **影响范围**：`src/xagent/bus/`、`src/xagent/agent/*`、`src/xagent/cli/*`、`src/xagent/gateway/*`、`src/xagent/channel/*`、`src/xagent/provider/*`、`tests/*`

---

## 1. 背景

当前 `xagent.bus` 包名义上是"消息总线"，但实际上是一个聚合包，内部混杂了五类语义不同的东西：

| 文件 | 实际职责 | 与"总线"关系 |
| --- | --- | --- |
| `types.py` | LLM 消息结构 + Provider 协议（`Message`、`ContentPart`、`ModelRequest`、`ModelProvider`） | 无关，本质是 domain / provider 层 |
| `errors.py` | `WorkspaceEscapeError`，仅被 `agent/paths.py` 和 `agent/tools/base.py` 使用 | 无关，本质是 agent/workspace 错误 |
| `events.py` | `InMemoryMessageBus`：topic + `*` 通配符的 pub/sub | 真正的"总线 ①"（内部事件广播） |
| `typed_bus.py` | `TypedMessageBus[MessageT]`：predicate 过滤的类型化总线 | 真正的"总线 ②"，实际只实例化为 `TypedMessageBus[OutboundMessage]` |
| `messages.py` | `InboundMessage` / `OutboundMessage` 边界 DTO | 总线载荷 |

这导致：

1. "两套总线"语义重叠——`InMemoryMessageBus` 广播运行时事件，`TypedMessageBus` 分发出站响应，两者加起来的表达力可以被一条 `outbound queue + metadata 约定` 覆盖。
2. `bus` 包耦合面虚高——`types.py` 和 `errors.py` 跟"总线"概念无关却住在同一个包里，让下游误以为"用了 bus"。
3. 流式响应 / 同步等待的实现链路过长——目前依赖 `TypedMessageBus.predicate` 做 correlation_id 匹配，新同学不易理解，且只有 `message_boundary` 一个使用点。

对标 HKUDS/nanobot 的做法（已在前置讨论中确认）：

```python
# nanobot/bus/queue.py
class MessageBus:
    inbound:  asyncio.Queue[InboundMessage]
    outbound: asyncio.Queue[OutboundMessage]
```

- 没有 Event、没有 topic、没有 subscribe
- 所有"运行时事件"（tool_use / progress / thinking delta）都编码为 `OutboundMessage.metadata` 字段
- `ChannelManager` 是 `outbound` 队列的唯一消费方，按 metadata 决定是否转发给 channel
- 流式输出通过 channel 的 `send_delta()` 实现，bus 层不感知"流"的存在

本次变更把 xagent 的 bus 精简到同一形态，同时把 `types.py` / `errors.py` 搬离 bus 包。

---

## 2. 目标与非目标

### 2.1 目标

1. **`bus/` 只保留两条 `asyncio.Queue`**（`inbound` / `outbound`）与对应的 `InboundMessage` / `OutboundMessage` DTO。
2. **删除 `InMemoryMessageBus` 与 `TypedMessageBus`**，所有事件/响应统一走 `OutboundMessage + metadata`。
3. **把 `types.py` 搬出 bus 包**，落到 provider/domain 层的合适位置。
4. **把 `errors.py::WorkspaceEscapeError` 搬出 bus 包**，归位到 agent 层。
5. **保持现有外部能力不退化**：
   - TUI 的实时工具/turn 可视化
   - HTTP SSE 的 per-request 流式响应
   - CLI `run --prompt` 的同步等待最终结果
   - 飞书 channel 的 inbound/outbound 闭环
   - 调度器（scheduler）触发任务
   - auto-compact 在合适时机触发
6. **测试全部跑通**（`tests/` 目录），并对新结构补充必要测试。

### 2.2 非目标

- **不改变 `agent/core` 内部的 ReAct loop 语义**。只改它对外通信的方式。
- **不引入多进程/多节点的 bus**——仍然是单进程 `asyncio.Queue`。
- **不改变 Provider 接口/能力**——只是搬位置，不改实现。
- **不重写 `channel/feishu` 的协议适配细节**——只替换它访问 bus 的入口。
- **不引入 openspec 工具链**——本仓库目前没有 openspec 工具；本次仅按 openspec 风格组织文档。

---

## 3. 设计概要（详细方案见 [design.md](./design.md)）

### 3.1 新的 `bus/` 包

```
src/xagent/bus/
├── __init__.py          # 只导出 MessageBus, InboundMessage, OutboundMessage
├── queue.py             # MessageBus: 两条 asyncio.Queue + publish/consume 方法
└── messages.py          # InboundMessage / OutboundMessage（扩展 metadata 约定）
```

**删除**：`events.py`、`typed_bus.py`、`types.py`、`errors.py`。

### 3.2 新的 domain/provider 归属

- `Message / ContentPart / TextPart / ToolUsePart / ToolResultPart / message_text / ModelConfig / ModelRequest / ModelProvider / ProviderName` 搬到 `src/xagent/provider/types.py`（保持单文件、零 runtime 依赖）。
- `WorkspaceEscapeError` 搬到 `src/xagent/agent/errors.py`。

### 3.3 OutboundMessage 的新 metadata 约定（nanobot 风格）

在现有 `OutboundMessage` 字段基础上，约定以下 `metadata` 键：

| metadata 键 | 语义 | 典型发出者 |
| --- | --- | --- |
| `_progress: True` | 这是一条中间进度消息，不是最终回复 | SessionRuntime turn 内 |
| `_tool_hint: True`（需 `_progress=True`） | 工具调用相关提示（开始/结束/ hint） | core.loop / tools middleware |
| `_event: "turn_start" \| "turn_end" \| "tool_use" \| "tool_result" \| "thinking_delta" \| "text_delta" \| "scheduler_fired" \| "compact_started" \| "compact_finished"` | 事件子类型 | SessionRuntime / scheduler / compaction |
| `_stream: True` | 这是一条流式增量 chunk，消费者应累积而非替换 | provider streaming / loop |
| `_terminal: True` | 本 correlation_id 的最后一条 outbound，等待方可以关闭响应流 | SessionRuntime turn 收尾 |

注意：`kind` 字段保留（当前已有），用于粗粒度区分（`message` / `error` / `event`），与 metadata 互补。

### 3.4 per-request 响应路由（替代 TypedMessageBus）

新增 `src/xagent/agent/runtime/channel_manager.py`（或复用 `message_boundary.py`），承担：

1. 唯一消费 `bus.outbound` 的协程。
2. 持有 `ResponseRegistry: dict[correlation_id, asyncio.Queue[OutboundMessage]]`。
3. 对每条 outbound：若其 `correlation_id` 在 registry，fan-out 一份到对应 per-request queue；然后按普通流程派发到 channel（或丢弃，如 TUI 本身就是 channel 的情况）。
4. 提供 `send_and_wait(inbound) → OutboundMessage`（等到 `_terminal=True`）与 `open_response_stream(inbound) → AsyncIterator[OutboundMessage]`（逐条 yield 直到 `_terminal=True`）两个高层 API。

### 3.5 Scheduler 的新角色

Scheduler 不再 publish Event。**到点直接 `bus.publish_inbound(InboundMessage(...))`**，把定时任务伪装成用户消息，让 AgentLoop 以统一路径处理。（nanobot cron 的做法。）

### 3.6 AutoCompact 的新角色

不再订阅 bus。改为 `SessionRuntime` 的 `post_turn_hook`：每个 turn 结束后，SessionRuntime 在内部调用一个 hook 列表，AutoCompact 作为其中一个 hook。这属于"进程内回调"，和 bus 完全解耦。

### 3.7 TUI 的新角色

TUI 从"订阅 InMemoryMessageBus topic"改为**一个本地 Channel**：注册为 `bus.outbound` 的消费方之一（由 ChannelManager 分发），按 `metadata._event` 渲染不同区块（工具面板、思考流、最终回答）。

### 3.8 HTTP Gateway 的新角色

HTTP Server 收到请求后：
1. 构造 InboundMessage，调用 `channel_manager.open_response_stream(inbound)` 拿到异步迭代器。
2. 迭代器内部从 per-request queue 取消息。
3. 把每条 OutboundMessage 映射成 SSE event 推给客户端。

---

## 4. 影响范围

### 4.1 需要改的文件（已盘点）

**新增**：
- `src/xagent/bus/queue.py`
- `src/xagent/provider/types.py`（承接原 `bus/types.py`）
- `src/xagent/agent/errors.py`（承接原 `bus/errors.py`）
- `src/xagent/agent/runtime/channel_manager.py`（承接原 `message_boundary.py` 的部分职责）
- 相应测试

**删除**：
- `src/xagent/bus/events.py`
- `src/xagent/bus/typed_bus.py`
- `src/xagent/bus/types.py`
- `src/xagent/bus/errors.py`

**修改**（按目录）：
- `bus/__init__.py`：改导出
- `bus/messages.py`：补充 `correlation_id` 约定文档、kind 枚举值扩展
- `agent/paths.py`：`WorkspaceEscapeError` import 路径
- `agent/memory.py`、`agent/policies.py`、`agent/skills.py`、`agent/traces.py`、`agent/tool_result_runtime.py`：types.py 的 import 路径
- `agent/session/store.py`、`agent/todos/system.py`、`agent/compaction/service.py`：types.py import 路径；compaction 同时改成 hook 模式
- `agent/tools/base.py`、`agent/tools/workspace/*`：errors.py import 路径
- `agent/core/loop.py`、`agent/core/middleware.py`：types.py import 路径；loop 发事件改为 `bus.publish_outbound(OutboundMessage(metadata=...))`
- `agent/runtime/session_runtime.py`：去掉 `InMemoryMessageBus` 参数，改用 `MessageBus`；事件发送改为 outbound
- `agent/runtime/manager.py`：事件类型替换
- `agent/runtime/scheduler.py`：触发时直接 `publish_inbound`
- `agent/runtime/serialization.py`：Event 序列化改为 OutboundMessage 序列化
- `agent/runtime/message_boundary.py`：重写为 ChannelManager + ResponseRegistry
- `agent/runtime/workspace_agent.py`：types.py import 路径
- `cli/runtime.py`：构造 `MessageBus` 而非两条 bus
- `cli/tui/tui.py`：重构为 Channel 消费 outbound queue
- `cli/commands/run.py`：调用 `channel_manager.send_and_wait`
- `cli/commands/schedule.py`、`cli/commands/trace.py`：import 路径调整
- `channel/feishu/adapter.py`：`publish_inbound` / 消费 outbound 方式
- `gateway/http/server.py`：使用 `channel_manager.open_response_stream`
- `provider/openai.py`、`provider/anthropic.py`、`provider/ark.py`、`provider/__init__.py`：types.py import 路径

**测试**（影响清单）：
- `test_event_bus.py`：删除或改写（现在没有 bus.events 了）
- `test_runtime_message_boundary.py`：重写
- `test_scheduler.py`、`test_scheduler_persistent.py`：事件断言改为 inbound 断言
- `test_auto_compact.py`：改为 hook 测试
- `test_session_runtime.py`、`test_runtime_manager.py`、`test_runtime_manager_close.py`：事件断言改 outbound metadata 断言
- `test_cli_chat.py`、`test_cli_run.py`、`test_cli_schedule*.py`、`test_feishu_adapter.py`、`test_gateway.py`：import 路径 + 一些 API 调用调整
- 其余涉及 `xagent.bus.*` 的测试：按新路径调整

初步统计涉及约 40 个文件（含测试）。

### 4.2 不受影响

- Provider 的协议实现（`openai.py` / `anthropic.py` / `ark.py`）：只改 import 位置，不改逻辑。
- Tools（`agent/tools/workspace/*`）：只改 `WorkspaceEscapeError` 的 import。
- core 的 ReAct loop 语义：只改事件发出方式，不改决策逻辑。

---

## 5. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 大量测试失败，回归面大 | 分阶段提交（见 tasks.md），每一阶段保证测试通过再进入下一阶段 |
| `metadata` 约定成为"弱 schema"，未来演化易乱 | 在 `bus/messages.py` 里用 TypedDict 或 `Literal` 定义 `_event` 枚举；提供小工具函数 `make_progress(...)`, `make_tool_event(...)`, `make_terminal(...)` |
| TUI 退化为 channel 后，实时性/富渲染能力可能缩水 | TUI 在 ChannelManager 中仍享有"full fan-out"，能拿到所有 OutboundMessage；只要 metadata 语义足够表达 turn/tool/thinking，表现力不变 |
| per-request queue 的清理/超时 | `ResponseRegistry` 提供 `register(correlation_id)` 返回 context manager，离开作用域自动清理；为 send_and_wait 提供超时参数 |
| `types.py` 搬位置影响下游 import，合并期间可能冲突 | 先以 re-export shim（`bus/types.py` 临时保留并 `from xagent.provider.types import *`）做过渡，合并后统一删 shim |
| Scheduler 改为 publish_inbound 后，inbound 可能被节流/滤掉 | Scheduler 产生的 InboundMessage 设定 `metadata["_source"]="scheduler"`；AgentLoop/ChannelManager 对其放行（不走 allowFrom 校验） |

---

## 6. 对齐点

以下决策**已在 proposal 之前与用户对齐**（问答中确认）：

1. 运行时事件的去向：**收敛进 OutboundMessage**（推荐项，用户选择）。
2. per-request 实现：**在 ChannelManager 里加钩子**（推荐项，用户选择）。
3. 实施节奏：**先出 openspec proposal**，由用户审阅通过再实施（推荐项，用户选择）。
4. Proposal 存放位置：`openspec/changes/0001-simplify-bus/`（推荐项，用户选择）。

---

## 7. 审阅后的下一步

审阅通过后，按 [tasks.md](./tasks.md) 分阶段实施，每阶段提交 commit 并跑全量测试。用户发出明确指令（例如 "按 proposal 开始实施第 1 阶段"）后才动代码。
