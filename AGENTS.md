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
- `xagent/agent/` 放 `AgentLoop`、session-bound `Agent`、`AgentRunner`、工具、记忆和技能相关能力。
- `xagent/session/` 管 session 包、`messages.jsonl`、`summary.jsonl`、`session_state.json` 和 `trace.jsonl`。
- `xagent/bus/` 是进程内消息邮局，只做 inbound/outbound 路由。
- `xagent/channels/` 放外部消息源抽象；CLI 不放在这个包里。
- `xagent/providers/` 放模型 provider 适配层。
- `xagent/cli/` 放 Typer CLI：`main.py` 只保留 root app 和 console script 入口，
  `agent.py` / `gateway.py` 分别放同名子命令逻辑，`workspace.py` 放共享 workspace 路径解析。
- `xagent/config/` 放用户级配置读取、默认值和解析逻辑。
- `xagent/cron/` 放 Agent 可管理的 cron 定时任务模型和 `CronService`。
- `xagent/templates/prompts/` 放模型 prompt 模板，`xagent/templates/memory/` 放 memory 初始化模板。
- `xagent/agent/tools/` 使用平铺模块组织：`base.py` / `registry.py` 是机制层，
  `files.py` / `search.py` / `shell.py` / `web.py` / `interaction.py` / `cron.py` 是具体能力，
  `default_tools.py` 只做默认工具注册装配。

## 架构边界

- 三层运行边界是 `AgentLoop -> Agent -> AgentRunner`。
- `AgentLoop` 负责消费 Bus inbound、按 session 复用 Agent、截断 slash command、发布 outbound，不处理 prompt 和工具细节。
- `Agent` 绑定单个 session，负责用户消息写入、memory 注入、上下文构造、summary 压缩和 session trace/message 持久化。
- `AgentRunner` 是纯 ReAct 执行内核，负责 provider stream、工具调用、空回复重试和循环预算；
  不 import Session、Bus、Channel 或 PromptRenderer。
- Agent 不应该感知消息来自 CLI、飞书、还是其他 channel。
- Bus 是进程内邮局，不做持久化，不当事件数据库。
- 持久化由 Session 负责，主要是 `messages.jsonl`、`summary.jsonl`、`session_state.json` 和 `trace.jsonl`。
- `SessionStore.open_for_chat()` 是 `channel/chat_id/session_id override` 到 session 包的统一入口；
  CLI 和 AgentLoop 都应该复用它。
- Channel 继承 `BaseChannel`，负责外部消息源接入、消息处理和出站发送，不包含 Agent 逻辑。
- `BaseChannel` 持有 Bus；具体 channel 在 `handle_message()` 中构造 `InboundMessage` 并 publish 到 Bus。
- channel 生命周期是 `start()`、`run()`、`handle_message()`、`send()`、`stop()`。
- `ChannelManager.run()` 是 gateway/channel manager 的长期入口。
- `ChannelManager.dispatch_outbound()` 只处理单条 Bus outbound 路由，不包含 Agent/runtime 逻辑。
- CLI chat 走 Bus，用来验证 channel/bus/AgentLoop 路径。
- CLI 一次性消息 `xagent agent -m "..."` 可以直接调用 Agent，不强制走 Bus。
- CLI 专属的 `build_agent()` 放在 `xagent/cli/agent.py`；通用 session 规则不要放回 CLI helper。
- 会话内 slash command 在 AgentLoop 层截断，CLI、Lark 和 Weixin 都应复用同一路径。
- `/dream` 和 `/dream --compact` 由 `xagent/agent/commands.py` 解析，具体 memory 整理由
  `xagent/agent/memory.py` 中的 `Dream` 执行。
- Cron 定时任务通过 `CronService` 到点向 Bus 投递 `InboundMessage`，不直接调用 Agent 或 channel。
- 每个 cron task 使用独立 `cron:<task_id>` session；回复路由仍按 task 的 `target.channel/chat_id`。
- Cron 管理通过一个 `cron` tool 完成，不提供 CLI 管理命令。

## CLI 约定

- console script 是 `xagent`，不要恢复旧的 `agent` 命令。
- `xagent` 不带子命令时显示帮助。
- `xagent agent` 启动默认 CLI chat。
- `xagent agent -m/--message "..."` 执行一次性消息。
- `xagent agent -r/--resume <id>` 恢复或创建指定 session。
- `xagent agent -w/--workspace <path>` 指定 workspace。
- `xagent channels login weixin` 用于实验性个人微信 channel 二维码登录。
- `xagent gateway` 是外部 channel 长期运行入口；当前支持 `lark` 和实验性 `weixin` channel。

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
- Lark/飞书 channel 名固定为 `lark`；私聊全部响应，群聊默认只响应 @ 机器人的文本消息。
- Lark 出站默认 `message_format=auto`：普通回复发文本，Markdown 结构明显的最终回复发
  interactive markdown 卡片；`progress` 和 `error` 仍发文本。
- Lark reaction 只属于 channel 体验层：有效入站消息加 `OnIt`，最终回复后移除 `OnIt` 并加
  `DONE`；Agent/Runner 不应感知这个细节。
- Weixin/个人微信 channel 名固定为 `weixin`；第一版只支持私聊文本，默认 session 是
  `weixin:<from_user_id>`，需要先通过 `xagent channels login weixin` 保存登录态。
- 非流式 channel 可以忽略 `DELTA`，只用 `END.content` 发送完整最终回答。
- `/dream` 不进入 `Agent.run()`，不写普通 `messages.jsonl`；用户可见输出固定为
  `dreaming...` 和 `dream done.`。

## Provider 约定

- Provider 只暴露流式接口：`stream(request: ModelRequest)`。
- `ModelRequest` 不感知具体 provider 配置。
- `ModelEvent` 只保留 `text_delta`、`tool_call_delta`、`message_done`、`usage`。
- 当前只支持 `openai_compat` backend。
- Provider 错误直接抛出，由 Agent 记录 trace，或由 AgentLoop 转成 outbound error。
- 不做 prompt 模拟工具调用；provider 需要原生支持 OpenAI-style tool calling。
- system、runtime context、summary、dream、empty retry prompt 来自 `xagent/templates/prompts/*.md`，通过 Jinja2 严格渲染。
- 当前日期、时间和时区不要写进稳定 system prompt；`Agent` 会把短 runtime context 作为本轮
  ephemeral 内容附加到当前 user message，避免破坏长 system/memory 前缀的 token cache。
- prompt 模板可以使用浅层 XML 风格标签分区；标签只作为结构约定，不做 parser 校验。

## Memory 约定

- 长期 memory 使用 Markdown，位于 `~/.xagent/memory`。
- 新 memory 文件从 `xagent/templates/memory/*.md` 初始化；已有 memory 文件不会被覆盖或补齐。
- `user.md` 记录用户个人信息和长期偏好；`soul.md` 记录 Agent 沟通方式；workspace 级 `memory.md`
  记录当前 workspace 的长期事实、架构决策、约定、已完成事项和待处理事项。
- 用户明确提供的生日、姓名、常用称呼、所在地、时区等稳定个人事实应写入 `user.md` 的
  `个人信息` 分区，不要写入 workspace memory。
- workspace memory 目录使用 `<workspace-name>-<path-hash>` 隔离多个 workspace，并用
  `meta.json` 记录真实 workspace path。
- Agent 构造 system prompt 时注入 `<memory><soul>`、`<user>`、`<workspace>` 分区。
- session compact 结果写入 `summary.jsonl`，`session_state.json` 保存 compact 游标；
  `messages.jsonl` 只保存原始消息，不再写新的 summary 记录。
- `/dream` 只消费 `dream_state.json` 之后的新 `summary.jsonl`，不读取尚未 compact 的新消息。
- `/dream --compact` 先强制 compact 当前 session，再执行 dream。
- `/dream` 让模型输出 JSON operations，再由程序应用到 workspace `memory.md`、`user.md`
  和 `soul.md`。
- dream 只支持 `append` / `update` / `delete`；`update/delete` 必须精确匹配旧 memory 中的完整条目。
- dream 细节通过 `trace.jsonl` 审计；没有新 summary 不算错误。

## Tools 约定

- 工具是 class，不是裸函数。
- schema 通过类装饰器显式声明，并统一转换成 OpenAI function schema。
- 工具注册时通过构造函数注入实际需要的依赖，不传大而全的 AgentContext。
- 工具内部负责具体权限检查。
- `ToolRegistry` 只负责注册、schema 输出、参数解析和执行调度，不 import 具体工具。
- registry/Agent 统一记录工具输入、输出、错误和耗时。
- `read_only` 且非 `exclusive` 的工具可以并行；写文件、shell、外部网络/API 默认串行或独占。
- `read_file` / `search` 默认允许；只读探索优先使用这两个工具。
- `shell` 默认允许普通命令，但会先经过 `permissions.shell.blacklist`；命中黑名单时直接返回
  tool error，不再请求授权覆盖。
- `permissions.shell.default` 支持 `allow` / `ask` / `deny`，第一版默认是 `allow`。
- `curl` / `wget` 继续保留在 shell 黑名单里，不用 shell 网络命令绕过 web tools。
- `web_fetch` / `web_search` 由 `tools.web.enabled` 控制；已知 URL 用 `web_fetch`，
  未知公开信息先 `web_search` 再按需 `web_fetch`。
- `web_fetch` / `web_search` 由 `permissions.web.default` 控制风险确认，第一版默认是 `allow`。
- `web_fetch` 先走 Jina Reader；Jina 失败后用受限 direct GET 兜底，只读取 `http/https`
  的文本、JSON 和 HTML，不执行 JavaScript，也不提供通用 HTTP/API 能力。
- 不提供低层 `http_request` 工具；第一版 web 能力只面向网页读取和搜索。
- `cron` tool 通过 `action=list/create/update/delete` 管理 `~/.xagent/cron/tasks.json`。
- `cron list` 默认允许；`cron create/update/delete` 使用 `permissions.cron.default`，第一版默认是 `ask`。
- 从 Lark/Weixin 消息中创建 cron task 时，target 默认使用当前消息的 `channel/chat_id/reply_to`；
  CLI 中创建 cron task 必须显式指定 Lark 或 Weixin target。

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
