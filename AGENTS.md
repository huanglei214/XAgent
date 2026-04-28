# AGENTS.md

本文件为在本仓库中工作的编码 Agent 提供约定和上下文。

## 项目概览

XAgent 是一个基于 Python 3.11+ 的 workspace-aware assistant runtime 和 CLI。
项目代码位于 `src/`，并通过 Typer 暴露 `xagent` 命令行入口。

主要目录：

- `src/xagent/agent/`：agent loop、tools、policies、traces、memory、compaction、
  todos 和 session runtime。
- `src/xagent/bus/`：消息队列和 runtime message 类型。
- `src/xagent/channel/`：channel contracts、routing、access control、trace
  channel，以及飞书集成。
- `src/xagent/cli/`：Typer CLI 命令和 Textual TUI。
- `src/xagent/gateway/`：HTTP gateway。
- `src/xagent/provider/`：provider 适配器和共享的模型、消息类型。
- `tests/`：覆盖 runtime、CLI、channels、providers、tools、scheduler 和 gateway
  行为的单元测试与偏集成测试。

当前 runtime 已经收敛到单一路径：

- `MessageBus` 维护 inbound 和 outbound 两条队列。
- `SessionRouter` 是 inbound 的唯一消费者。
- `SessionRuntime.handle(inbound)` 执行 turn，并将中间进度和最终结果写入
  outbound。
- `ChannelManager` 分发 outbound 事件，供 CLI/TUI、HTTP、飞书和其他 channel
  消费。
- `TraceChannel` 以 observer 方式旁路记录 runtime outbound 事件。

不要重新引入旧的 event bus / message boundary 双轨架构。

## 常用命令

本项目使用 `uv` 进行本地开发。

```bash
uv sync
uv run pytest
uv run pytest tests/test_agent_loop.py
uv run ruff check .
uv run mypy src
uv run xagent --help
uv run xagent config init
uv run xagent run "Say hello"
```

对于范围较小的改动，优先运行最相关的测试文件；如果改动触及共享 runtime、CLI、
provider、channel 或 tool contract，再运行更大范围的测试。

## 编码约定

- 优先遵循现有模块边界和本地代码模式，再考虑新增抽象。
- 行为变更应尽量小，并配套聚焦的测试。
- 保持 async 边界清晰。runtime、provider、scheduler 和 channel 代码大量使用异步，
  不要在热路径中引入阻塞调用。
- 当周边代码已经使用 Pydantic 表达结构化 tool/provider 数据时，继续使用 Pydantic
  模型。
- CLI 代码保持轻量。可复用逻辑应放在 runtime 或 service 模块中，而不是堆在 Typer
  command 函数里。
- provider adapter 是边界代码：应将外部 API 细节规整到 `xagent.provider.types`，
  不要让供应商特定结构泄漏到内部。
- channel 实现应遵循 `src/xagent/channel/` 下的 contracts。
- 修复具体 bug 时避免顺手做大范围重构。
- 不要提交本地密钥或 `.xagent/` 下生成的项目配置。

## 工具与文件

- `pyproject.toml` 定义依赖、可选开发工具、包布局和 Ruff 设置。
- `config.example.yaml` 是公开配置模板。
- `README.md` 记录安装方式和当前飞书 channel 的使用预期。
- `CLAUDE.md` 指向本文件，因此共享的 Agent 指令应维护在这里。

## 测试预期

修改以下内容时，应新增或更新测试：

- agent loop 终止条件、middleware、tool execution 或 tool result 行为；
- session routing、runtime manager、message bus 或 channel contracts；
- provider 请求与响应映射；
- CLI 命令行为，或持久化的 scheduler/session 状态；
- access control、guardrails、approvals、traces、飞书或 gateway 行为。

优先编写确定性的测试，使用 fake provider、fake channel 和临时目录。避免依赖真实
API key、网络访问或在线飞书应用。
