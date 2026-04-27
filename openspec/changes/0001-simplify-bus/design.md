# Design: 精简 bus 为两条队列的详细设计

本文档是 [proposal.md](./proposal.md) 的技术细节补充，说明关键接口形态、数据流与边界条件。不含实施步骤（那部分见 [tasks.md](./tasks.md)）。

---

## 1. 数据流总览

```
┌─────────────────────────────────────────────────────────────┐
│  Channel / Gateway / CLI / TUI / Scheduler (inbound 入口)    │
└───────────────────┬─────────────────────────────────────────┘
                    │ bus.publish_inbound(InboundMessage)
                    ▼
            ┌───────────────┐
            │ MessageBus    │
            │  .inbound     │  ← asyncio.Queue[InboundMessage]
            └───────┬───────┘
                    │
                    ▼
        AgentRunner (单协程/多协程消费)
            ├─ SessionRuntimeManager 路由到对应 SessionRuntime
            ├─ SessionRuntime.handle(inbound):
            │     while not done:
            │        bus.publish_outbound(progress OutboundMessage)  ← turn/tool/thinking
            │        call provider, run tool, etc
            │     bus.publish_outbound(final OutboundMessage, _terminal=True)
            │     for hook in post_turn_hooks: await hook(ctx)   ← auto-compact 在这里
            ▼
            ┌───────────────┐
            │ MessageBus    │
            │  .outbound    │  ← asyncio.Queue[OutboundMessage]
            └───────┬───────┘
                    │ ChannelManager 唯一消费方
                    ▼
          ┌──────────────────────────────────────────┐
          │ ChannelManager._dispatch_outbound        │
          │   1. 若 correlation_id 在 ResponseRegistry│
          │      → fan-out 一份到 per-request queue  │
          │   2. 按 msg.channel 找到 Channel 实例     │
          │      → await channel.send(msg)           │
          └──────────┬───────────────────────────────┘
                     │
         ┌───────────┼────────────┬────────────┐
         ▼           ▼            ▼            ▼
       Feishu      HTTP        CLI TUI      Gateway
       Channel    Channel       Channel     (SSE)
                  (polling)    (stdout)   (per-req stream)
```

关键点：
- **bus 自己没有业务逻辑**，只是两条 Queue + 放/取方法。
- **AgentRunner** 是唯一的 inbound 消费方（未来也可能是 per-session 多协程，见 §5）。
- **ChannelManager** 是唯一的 outbound 消费方，它承担"fan-out 到 per-request queue"+"转发到 channel"两件事。

---

## 2. 关键接口

### 2.1 `MessageBus`

```python
# src/xagent/bus/queue.py
from __future__ import annotations

import asyncio
from xagent.bus.messages import InboundMessage, OutboundMessage


class MessageBus:
    """进程内消息总线。仅包含 inbound / outbound 两条 asyncio.Queue。"""

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """外部通道把用户消息推入 inbound 队列。"""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """AgentRunner 从 inbound 队列取下一条待处理消息。"""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Agent runtime 把进度/最终回复推入 outbound 队列。"""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """ChannelManager 从 outbound 队列取下一条待转发消息。"""
        return await self.outbound.get()
```

整个 bus 包不依赖 xagent 内任何模块（除 `messages.py`），保证最底层的纯净。

### 2.2 `InboundMessage` / `OutboundMessage`

保持现有字段（见 [bus/messages.py](file:///Users/huanglei/repos/src/github.com/huanglei214/XAgent/src/xagent/bus/messages.py)），**但明确 metadata 的约定**，并提供构造辅助函数：

```python
# src/xagent/bus/messages.py 扩展
from typing import Literal, TypedDict

EventKind = Literal[
    "turn_start", "turn_end",
    "tool_use", "tool_result",
    "thinking_delta", "text_delta",
    "scheduler_fired",
    "compact_started", "compact_finished",
]


class OutboundMeta(TypedDict, total=False):
    """OutboundMessage.metadata 的约定键（非强制，但建议使用辅助函数构造）。"""
    _progress: bool
    _tool_hint: bool
    _event: EventKind
    _stream: bool
    _terminal: bool
    _source: str  # 例如 "scheduler"、"heartbeat"


def make_progress(*, correlation_id: str, session_id: str, session_key: str,
                  channel: str, chat_id: str, source: str,
                  event: EventKind, content: str = "", **extra) -> OutboundMessage:
    """构造一条中间进度 OutboundMessage。"""
    ...


def make_terminal(*, correlation_id: str, session_id: str, session_key: str,
                  channel: str, chat_id: str, source: str,
                  content: str, **extra) -> OutboundMessage:
    """构造本 correlation_id 的最终 OutboundMessage（打 _terminal=True）。"""
    ...
```

这些辅助函数减少手工拼 metadata 的出错面。

### 2.3 `ChannelManager`

```python
# src/xagent/agent/runtime/channel_manager.py
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from xagent.bus.queue import MessageBus
from xagent.bus.messages import InboundMessage, OutboundMessage


class ChannelManager:
    """唯一的 outbound 消费者。负责 per-request fan-out + 转发给 Channel。"""

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._channels: dict[str, "BaseChannel"] = {}
        self._response_registry: dict[str, asyncio.Queue[OutboundMessage]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None

    def register_channel(self, name: str, channel: "BaseChannel") -> None:
        self._channels[name] = channel

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _dispatch_loop(self) -> None:
        while self._running:
            msg = await self._bus.consume_outbound()
            # 1. fan-out per-request
            q = self._response_registry.get(msg.correlation_id)
            if q is not None:
                await q.put(msg)
            # 2. forward to channel（可按 metadata 过滤进度消息）
            channel = self._channels.get(msg.channel)
            if channel is not None:
                try:
                    await channel.send(msg)
                except Exception as exc:  # 日志并吞异常，避免整体崩溃
                    logger.error("Channel %s send failed: %s", msg.channel, exc)

    @asynccontextmanager
    async def _response_scope(self, correlation_id: str) -> asyncio.Queue[OutboundMessage]:
        """为某个 request 注册一条 per-request queue，离开作用域自动清理。"""
        q: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        async with self._lock:
            self._response_registry[correlation_id] = q
        try:
            yield q
        finally:
            async with self._lock:
                self._response_registry.pop(correlation_id, None)

    async def send_and_wait(self, inbound: InboundMessage,
                            *, timeout: float | None = None) -> OutboundMessage:
        """同步等待本 inbound 的最终 OutboundMessage（_terminal=True）。"""
        async with self._response_scope(inbound.correlation_id) as q:
            await self._bus.publish_inbound(inbound)
            while True:
                msg = await asyncio.wait_for(q.get(), timeout=timeout)
                if msg.metadata.get("_terminal"):
                    return msg

    async def open_response_stream(self, inbound: InboundMessage
                                   ) -> AsyncIterator[OutboundMessage]:
        """异步生成器：逐条 yield 属于本 inbound 的 outbound，直到 _terminal。"""
        async def _gen():
            async with self._response_scope(inbound.correlation_id) as q:
                await self._bus.publish_inbound(inbound)
                while True:
                    msg = await q.get()
                    yield msg
                    if msg.metadata.get("_terminal"):
                        return
        return _gen()
```

ChannelManager 同时承担：
- 过去 `message_boundary.LocalRuntimeBoundary` / `ManagedRuntimeBoundary` 的对外 API（`send_and_wait` / `open_response_stream`）。
- 过去 `TypedMessageBus[OutboundMessage]` 的 per-request 路由（由 ResponseRegistry + fan-out 实现，无需 predicate）。
- 过去 `InMemoryMessageBus` 的广播订阅能力（由"fan-out"自然覆盖，Channel 自己就是一种订阅者）。

### 2.4 `BaseChannel`

```python
# src/xagent/channel/base.py （新增，抽公共基类）
from abc import ABC, abstractmethod
from xagent.bus.messages import InboundMessage, OutboundMessage


class BaseChannel(ABC):
    name: str  # "feishu" / "http" / "cli" / "tui" ...

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None: ...

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self._bus.publish_inbound(msg)
```

这是 nanobot 的 `BaseChannel` 精简版。飞书适配器、HTTP gateway、TUI、CLI run 都应收敛到这个接口。

### 2.5 SessionRuntime 的 post-turn hooks

```python
# src/xagent/agent/runtime/session_runtime.py 扩展
from typing import Awaitable, Callable

PostTurnHook = Callable[["PostTurnContext"], Awaitable[None]]


@dataclass
class PostTurnContext:
    session_id: str
    session_key: str
    turn_index: int
    messages: list[Message]


class SessionRuntime:
    def __init__(self, ..., post_turn_hooks: list[PostTurnHook] | None = None):
        ...
        self._post_turn_hooks = post_turn_hooks or []

    async def _run_turn(self, inbound: InboundMessage) -> None:
        ...
        for hook in self._post_turn_hooks:
            try:
                await hook(ctx)
            except Exception:
                logger.exception("post_turn_hook failed")
```

`AutoCompactService` 注册为 hook，不再持有 bus 引用。

---

## 3. 典型交互示例

### 3.1 CLI `run --prompt "hi"`

```python
# cli/commands/run.py 伪代码
bus = MessageBus()
cm = ChannelManager(bus)
runtime = SessionRuntimeManager(bus, ...)
await cm.start()
await runtime.start()

inbound = InboundMessage(content="hi", source="cli", channel="cli")
final = await cm.send_and_wait(inbound, timeout=120)
print(final.content)

await runtime.stop()
await cm.stop()
```

### 3.2 HTTP SSE `/sessions/{id}/messages`

```python
# gateway/http/server.py 伪代码
inbound = InboundMessage(content=body.text, source="http", channel="http",
                         chat_id=session_id, correlation_id=req_id)
async for msg in cm.open_response_stream(inbound):
    yield f"event: {msg.metadata.get('_event', 'message')}\ndata: {json.dumps(msg.dict())}\n\n"
    if msg.metadata.get("_terminal"):
        return
```

### 3.3 TUI 渲染

```python
# cli/tui/tui.py 伪代码（变成一个 Channel）
class TuiChannel(BaseChannel):
    name = "tui"

    async def send(self, msg: OutboundMessage) -> None:
        event = msg.metadata.get("_event")
        if event == "tool_use":
            self._render_tool_start(msg)
        elif event == "tool_result":
            self._render_tool_end(msg)
        elif event == "thinking_delta":
            self._append_thinking(msg.content)
        elif msg.metadata.get("_terminal"):
            self._render_final(msg)
        else:
            self._render_progress(msg)
```

### 3.4 Scheduler 到点触发

```python
# runtime/scheduler.py 伪代码
async def on_fire(job):
    inbound = InboundMessage(
        content=job.prompt,
        source="scheduler",
        channel=job.channel,
        chat_id=job.chat_id,
        metadata={"_source": "scheduler", "_job_id": job.id},
        correlation_id=uuid4().hex,
    )
    await self._bus.publish_inbound(inbound)
```

### 3.5 AutoCompact 作为 post-turn hook

```python
# agent/compaction/service.py 伪代码
class AutoCompactService:
    async def on_post_turn(self, ctx: PostTurnContext) -> None:
        if self._should_compact(ctx):
            await self._compact(ctx)

# 注册
runtime.register_post_turn_hook(autocompact.on_post_turn)
```

---

## 4. 过渡策略

为了让大改不一次性破坏所有上游，建议中间阶段保留一层 **deprecation shim**：

1. `src/xagent/bus/types.py`（临时）：
   ```python
   # 过渡期 shim：仍允许 `from xagent.bus.types import Message`
   from xagent.provider.types import *  # noqa: F401,F403
   import warnings
   warnings.warn(
       "xagent.bus.types is deprecated, import from xagent.provider.types",
       DeprecationWarning, stacklevel=2,
   )
   ```
2. `src/xagent/bus/errors.py`（临时）：同理 re-export `xagent.agent.errors`。
3. `src/xagent/bus/events.py` / `typed_bus.py`：**不保留 shim**（行为变了，保留 shim 反而误导），直接删除。

过渡 shim 在所有上游切完之后一次性删除（见 tasks.md 阶段 5）。

---

## 5. 并发与顺序保证

| 场景 | 顺序要求 | 当前方案 |
| --- | --- | --- |
| 单个 session 内 turn 内的 outbound 顺序 | 必须严格按产生顺序到达 channel | outbound 是一条 `asyncio.Queue`，FIFO 天然保证 |
| 多个 session 并发 turn | 不同 session 之间无序 | AgentRunner 按 session_key 派生 per-session 协程（可后续做） |
| per-request queue 与 channel 并行 | fan-out 和 forward 在 ChannelManager 里顺序执行 | 可接受；若发现瓶颈再异步化 |
| Scheduler 触发的 inbound 与用户 inbound | 同一 session 串行处理 | 进入同一 `inbound` queue，FIFO |

本次重构**不改变**任何并发语义。未来若要做 per-session 并发（如 nanobot PR #9），应作为独立 proposal。

---

## 6. 可观测性

删除 `InMemoryMessageBus` 后，原来通过"订阅 `*` topic"做全局 trace 的路径（`TraceMiddleware`）应改为：

- `TraceMiddleware` 依旧挂在 middleware pipeline 内，直接对 `SessionRuntime` 产生的 outbound 做 tap（在 `publish_outbound` 前调用）。
- 或：增加一个 `TraceChannel`（BaseChannel 实现），把所有 outbound 记录到 trace 文件，与 TUI/Feishu 并列为 channel。后者更干净。

推荐后者：**把 trace 当成一个 channel**，沿用同一套 fan-out 机制。
