# Tasks: 0001-simplify-bus 实施任务清单

本文档把 [proposal.md](./proposal.md) + [design.md](./design.md) 的变更拆成可独立验证的阶段。**每个阶段完成后必须跑 `pytest` 全量测试并确保通过**，否则不进入下一阶段。

**重要：本文档仅描述任务。用户发出明确实施指令（如"开始阶段 1"）后才能动代码。**

---

## 阶段 0：准备（文档 & baseline）

- [ ] 0.1 用户审阅 `proposal.md` + `design.md`，确认方案
- [ ] 0.2 `git status` 干净；记录当前 `pytest` 全量结果作为 baseline（用例数、通过数）
- [ ] 0.3 在当前分支基础上切一个 feature 分支：`feat/0001-simplify-bus`

**验收**：用户书面确认"方案通过，可以开始"。

---

## 阶段 1：搬迁 `types.py` 与 `errors.py`（最低风险）

目标：把 `bus/types.py` 和 `bus/errors.py` 移出 bus 包，但保留 shim 让旧 import 不破坏。

- [ ] 1.1 新建 `src/xagent/provider/types.py`，把原 `bus/types.py` 内容原样搬过去
- [ ] 1.2 新建 `src/xagent/agent/errors.py`，把 `WorkspaceEscapeError` 搬过去
- [ ] 1.3 把原 `bus/types.py` 改为 shim（`from xagent.provider.types import *` + DeprecationWarning）
- [ ] 1.4 把原 `bus/errors.py` 改为 shim（`from xagent.agent.errors import *` + DeprecationWarning）
- [ ] 1.5 更新 `bus/__init__.py` 导出（仍然包含这些名字，但指向新位置）
- [ ] 1.6 跑测试；应全部通过（因为 shim 保证兼容）

**验收**：`pytest` 全绿；`grep -r "from xagent.bus.types" src/` 仍能工作（通过 shim）。

---

## 阶段 2：全仓替换 import 路径（把 shim 替换为直接 import）

目标：把所有使用 `xagent.bus.types` / `xagent.bus.errors` 的位置改为直接 import 新位置。

- [ ] 2.1 把以下文件的 import 从 `xagent.bus.types` 改为 `xagent.provider.types`：
  - `agent/core/loop.py`、`agent/core/middleware.py`
  - `agent/memory.py`、`agent/policies.py`、`agent/skills.py`、`agent/traces.py`
  - `agent/session/store.py`、`agent/todos/system.py`
  - `agent/compaction/service.py`
  - `agent/runtime/manager.py`、`runtime/serialization.py`、`runtime/session_runtime.py`
  - `agent/runtime/message_boundary.py`、`runtime/workspace_agent.py`
  - `provider/openai.py`、`provider/anthropic.py`、`provider/ark.py`、`provider/__init__.py`
  - `cli/config.py`、`cli/runtime.py`、`cli/tui/tui.py`
  - `cli/commands/run.py`、`cli/commands/schedule.py`
  - `tests/*.py` 中涉及的
- [ ] 2.2 把以下文件的 import 从 `xagent.bus.errors` 改为 `xagent.agent.errors`：
  - `agent/paths.py`、`agent/tools/base.py`
  - `tests/*.py` 中涉及的
- [ ] 2.3 删除 `bus/types.py` 和 `bus/errors.py` 两个 shim
- [ ] 2.4 更新 `bus/__init__.py`：不再导出 `Message`/`WorkspaceEscapeError` 等
- [ ] 2.5 跑测试

**验收**：`pytest` 全绿；`grep -r "xagent.bus.types\|xagent.bus.errors" src/ tests/` 无结果。

---

## 阶段 3：引入新 `MessageBus`（双轨期）

目标：新建 `bus/queue.py::MessageBus`，但旧的 `InMemoryMessageBus` / `TypedMessageBus` 暂不删，二者并存。

- [ ] 3.1 新建 `src/xagent/bus/queue.py`，实现 `MessageBus`（见 design §2.1）
- [ ] 3.2 在 `bus/messages.py` 扩展 metadata 约定与 `make_progress` / `make_terminal` 等辅助函数（见 design §2.2）
- [ ] 3.3 更新 `bus/__init__.py` 导出 `MessageBus`
- [ ] 3.4 新增测试 `tests/test_message_bus.py`，覆盖 publish/consume/FIFO 顺序
- [ ] 3.5 跑测试

**验收**：`pytest` 全绿；新测试覆盖 ≥90% `queue.py` 行数。

---

## 阶段 4：引入 `ChannelManager` + per-request queue

目标：在不拆除旧 `message_boundary` 的前提下，新建 `ChannelManager`，先让 CLI run 走新路径。

- [ ] 4.1 新建 `src/xagent/channel/base.py::BaseChannel`（见 design §2.4）
- [ ] 4.2 新建 `src/xagent/agent/runtime/channel_manager.py::ChannelManager`（见 design §2.3）
- [ ] 4.3 新增测试 `tests/test_channel_manager.py`：
  - 单 request send_and_wait（终端消息）
  - open_response_stream 多条 chunk 直到 terminal
  - 并发两个 request 的 correlation_id 隔离
  - ResponseRegistry 的 scope 清理
- [ ] 4.4 让 `cli/commands/run.py` 用 `ChannelManager.send_and_wait` 跑一条端到端（需要 SessionRuntime 发 `_terminal=True` 的 OutboundMessage，见阶段 5）
- [ ] 4.5 **此阶段不删旧 boundary**；可在 `cli/runtime.py` 提供"new_runtime / legacy_runtime"切换 flag 方便对比
- [ ] 4.6 跑测试

**验收**：`pytest` 全绿；新测试通过；`cli run --prompt "hi"` 手工冒烟成功。

---

## 阶段 5：SessionRuntime 切换到 MessageBus

目标：让 `SessionRuntime` 发 outbound 走 `MessageBus`，并给 turn 结束发 `_terminal=True`。

- [ ] 5.1 `SessionRuntime.__init__` 改接 `MessageBus`（替换 `InMemoryMessageBus`）
- [ ] 5.2 把原来的 `bus.publish(Event(topic=..., payload=...))` 全部改写为 `bus.publish_outbound(make_progress(event=..., ...))`
- [ ] 5.3 每个 turn 收尾时发 `make_terminal(...)`
- [ ] 5.4 新增 `PostTurnHook` 机制（见 design §2.5）
- [ ] 5.5 `AutoCompactService` 改为 hook，删除 bus 依赖
- [ ] 5.6 `runtime/manager.py`、`runtime/scheduler.py`、`runtime/serialization.py`、`runtime/session_runtime.py` 全部切到新接口
- [ ] 5.7 `scheduler.py` 的事件广播改为"直接 publish_inbound"（见 design §3.4）
- [ ] 5.8 更新/重写对应测试：
  - `test_session_runtime.py`、`test_runtime_manager*.py`、`test_auto_compact.py`、`test_scheduler*.py`
- [ ] 5.9 跑测试

**验收**：`pytest` 全绿；`test_event_bus.py` 可以临时 xfail。

---

## 阶段 6：上游全部接入 ChannelManager

目标：飞书 / HTTP / TUI / CLI / Scheduler 全部走 `BaseChannel` + `ChannelManager`。

- [ ] 6.1 `TuiChannel`：实现 `BaseChannel`（见 design §3.3），替换 `cli/tui/tui.py` 原有 bus 订阅逻辑
- [ ] 6.2 `FeishuChannel`：`channel/feishu/adapter.py` 继承 `BaseChannel`，替换 `TypedMessageBus` 依赖
- [ ] 6.3 `HttpChannel`：`gateway/http/server.py` 使用 `ChannelManager.open_response_stream`；或把 HTTP 也包装为 `BaseChannel`（取决于"是请求-响应还是常驻"）
- [ ] 6.4 `CliRunChannel`：CLI `run --prompt` 走 `ChannelManager.send_and_wait`
- [ ] 6.5 `ScheduleChannel`（可选）：把 scheduler 包装为注册 inbound 的 channel（目前阶段 5 已完成直接 publish_inbound，本步可跳过）
- [ ] 6.6 `TraceChannel`（可选，推荐）：把 trace 文件落盘包装为 channel（见 design §6）
- [ ] 6.7 跑测试；手工冒烟各条外部通道

**验收**：`pytest` 全绿；`cli chat` / `cli run` / `gateway` 三种启动方式手工各跑一次，观察 TUI/SSE/飞书正常。

---

## 阶段 7：删除旧 bus 组件

目标：彻底移除 `events.py` / `typed_bus.py` / `message_boundary.py` 的旧实现。

- [ ] 7.1 删除 `src/xagent/bus/events.py`
- [ ] 7.2 删除 `src/xagent/bus/typed_bus.py`
- [ ] 7.3 删除 `src/xagent/agent/runtime/message_boundary.py`（如果其职责已全部迁到 `channel_manager.py`）
- [ ] 7.4 删除 `tests/test_event_bus.py`、`tests/test_runtime_message_boundary.py`（或重写为对 ChannelManager 的测试，若阶段 4.3 未覆盖）
- [ ] 7.5 更新 `bus/__init__.py` 最终导出清单
- [ ] 7.6 `grep -r "InMemoryMessageBus\|TypedMessageBus" src/ tests/` 应无结果
- [ ] 7.7 跑测试

**验收**：`pytest` 全绿；`bus/` 目录只剩 `__init__.py` / `queue.py` / `messages.py` 三个文件。

---

## 阶段 8：收尾与文档

- [ ] 8.1 更新 `AGENTS.md` / `CLAUDE.md` / `README.md` 中关于架构的描述
- [ ] 8.2 更新 `config.example.yaml` 中与 bus / channel 相关的示例（如有）
- [ ] 8.3 `openspec/changes/0001-simplify-bus/proposal.md` 状态改为 Done，加上完成日期与 PR/commit 链接
- [ ] 8.4 汇总此次重构的代码行数变化、文件数变化（大致量化"精简"效果）
- [ ] 8.5 提交最终 commit；等待 review / merge

**验收**：文档同步；度量数据已记录。

---

## 回退策略

- 每个阶段都是独立 commit，若某阶段发现方案问题，可 `git revert` 回到上一阶段的稳定点。
- 阶段 1~4 对运行时行为**无感知改变**，回退成本最低。
- 阶段 5~6 是行为切换点，若出问题应优先回退阶段 5/6，而非继续硬改。
- 阶段 7 之后旧路径删除，回退需要 cherry-pick 旧实现——建议只在全部冒烟通过后进入阶段 7。

---

## 依赖关系

```
阶段 0 (审阅)
   │
   ▼
阶段 1 (搬 types/errors + shim)
   │
   ▼
阶段 2 (删 shim)
   │
   ▼
阶段 3 (新建 MessageBus，双轨)
   │
   ▼
阶段 4 (新建 ChannelManager，单路冒烟)
   │
   ▼
阶段 5 (SessionRuntime 切换，auto-compact 改 hook)
   │
   ▼
阶段 6 (上游全部接入)
   │
   ▼
阶段 7 (删旧)
   │
   ▼
阶段 8 (文档收尾)
```

**阶段 1 到 4 可在一个 PR 内（低风险铺垫）；阶段 5~6 建议单独 PR；阶段 7~8 单独 PR。**
