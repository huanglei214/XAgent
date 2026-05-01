# AGENTS.md

这份文件是给 Codex、Claude Code、以及其他 coding agent 使用的项目工作手册。
面向用户的介绍放在 `README.md`；完整架构设计放在 `docs/architecture.md`。

## 项目定位

XAgent v2 是一个从零开始设计的本地通用 AI Agent。它可以读取 workspace、编辑文件、
执行命令、调用工具和 API，并通过 CLI 或未来的外部聊天 channel 与用户协作。

当前实现不要参考或复用 `main-v1` 的代码结构。`main-v1` 只作为历史分支保留。

## 工作约定

- 使用 `uv` 管理项目和运行测试。
- 优先用 `rg` / `rg --files` 搜索代码和文件。
- 手工编辑文件时使用 patch 工具，不要用 shell 重写文件。
- 不要回退用户未提交的改动；如果工作区已有改动，先阅读并在其基础上继续。
- 解释性注释、README 和项目文档默认使用中文。
- 保持实现贴近现有结构，避免在没有明确收益时新增抽象层。

## 源码结构

- 源码包位于根目录 `xagent/`，不要重新引入 `src/` 目录。
- `xagent/agent/` 放核心 Agent runtime、ReAct 循环、工具、记忆和技能相关能力。
- `xagent/session/` 管 session 包、`messages.jsonl` 和 `trace.jsonl`。
- `xagent/bus/` 是进程内消息邮局，只做 inbound/outbound 路由。
- `xagent/channels/` 放外部消息源抽象；CLI 不放在这个包里。
- `xagent/providers/` 放模型 provider 适配层。
- `xagent/cli/` 放 Typer CLI 入口和 CLI 专属组装逻辑。
- `xagent/config/` 放用户级配置读取、默认值和解析逻辑。

## 架构边界

- Agent 负责智能逻辑：上下文构造、模型调用、工具调用、压缩、循环预算和 trace。
- Agent 不应该感知消息来自 CLI、飞书、还是其他 channel。
- Bus 是进程内邮局，不做持久化，不当事件数据库。
- 持久化由 Session 负责，主要是 `messages.jsonl` 和 `trace.jsonl`。
- Channel 负责外部消息源接入和消息发送，不包含 Agent 逻辑。
- CLI chat 走 Bus，用来验证 channel/bus/runtime 路径。
- CLI 一次性消息 `xagent agent -m "..."` 可以直接调用 Agent，不强制走 Bus。

## CLI 约定

- console script 是 `xagent`，不要恢复旧的 `agent` 命令。
- `xagent` 不带子命令时显示帮助。
- `xagent agent` 启动默认 CLI chat。
- `xagent agent -m/--message "..."` 执行一次性消息。
- `xagent agent -r/--resume <id>` 恢复或创建指定 session。
- `xagent agent -w/--workspace <path>` 指定 workspace。
- `xagent gateway` 是未来外部 channel 入口，目前可以保持 placeholder。

## Session 和消息约定

- 用户级数据只放在 `~/.xagent`。
- 默认 workspace 是 `~/.xagent/workspace/files`。
- session 身份默认由 `channel:chat_id` 派生。
- CLI 默认身份是 `channel="cli"`、`chat_id="default"`、`sender_id="user"`。
- `--resume/-r` 显式指定 session id 时优先。
- 同一个 `channel:chat_id` 内的消息串行处理，不支持同 session 多轮并发交错输出。
- `InboundMessage` 使用 `channel`、`chat_id`、`sender_id` 表达消息来源和发言人。
- `OutboundEvent` 使用 `channel`、`chat_id`、`reply_to` 表达回复路由。
- 出站流式状态放在 `StreamState`，用 `StreamKind.DELTA` / `StreamKind.END` 表达增量和结束。
- 外部平台原始消息 ID 放在 `external_message_id`，不参与 runtime 关联和路由。

## Provider 约定

- Provider 只暴露流式接口：`stream(request: ModelRequest)`。
- `ModelRequest` 不感知具体 provider 配置。
- `ModelEvent` 只保留 `text_delta`、`tool_call_delta`、`message_done`、`usage`。
- 当前只支持 `openai_compat` backend。
- Provider 错误直接抛出，由 Agent 或 AgentRuntime 捕获并写 trace / outbound error。
- 不做 prompt 模拟工具调用；provider 需要原生支持 OpenAI-style tool calling。

## Tools 约定

- 工具是 class，不是裸函数。
- schema 通过类装饰器显式声明，并统一转换成 OpenAI function schema。
- 工具注册时通过构造函数注入实际需要的依赖，不传大而全的 AgentContext。
- 工具内部负责具体权限检查。
- registry/Agent 统一记录工具输入、输出、错误和耗时。
- `read_only` 且非 `exclusive` 的工具可以并行；写文件、shell、外部网络/API 默认串行或独占。

## 测试和质量门禁

提交前优先跑完整门禁：

```bash
UV_CACHE_DIR=/private/tmp/xagent-uv-cache /Users/huanglei/.local/bin/uv run pytest -q
UV_CACHE_DIR=/private/tmp/xagent-uv-cache /Users/huanglei/.local/bin/uv run ruff check .
UV_CACHE_DIR=/private/tmp/xagent-uv-cache /Users/huanglei/.local/bin/uv run mypy xagent
UV_CACHE_DIR=/private/tmp/xagent-uv-cache /Users/huanglei/.local/bin/uv run python -m compileall -q xagent tests
```

如果只改文档，可以说明未跑完整门禁；如果改 runtime、provider、tools、session、bus 或 CLI，
需要跑完整门禁。
