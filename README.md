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
        trace.jsonl
        artifacts/
```

`messages.jsonl` 保存 OpenAI 兼容的、模型可见的会话消息。
`trace.jsonl` 保存模型请求、最终响应、usage、工具输入输出、错误和耗时，方便调试。
默认不记录逐 chunk 的 provider stream 事件；如果需要记录 `model_event`，可以设置
`trace.model_events: true`。

## CLI

```bash
xagent agent
xagent agent -m "explain this workspace"
xagent agent -r cli:experiment
xagent agent -w /path/to/project
xagent gateway
```

`xagent` 不带子命令时显示帮助。`xagent agent` 启动 CLI chat，并使用默认
`cli:default` session。`xagent agent -m/--message` 会直接调用 Agent 执行一条
一次性消息，同时仍然写入 session 包和 trace。`xagent gateway` 预留给未来的外部
聊天 channel。`-r/--resume` 按 session 目录名恢复会话；如果 session 不存在则新建。
`-w/--workspace` 为新建 session 指定 workspace 路径。

## Bus 和 Channels

类聊天流程通过进程内 `MessageBus` 连接消息 channel 和 Agent runtime。session
身份默认是 `<channel>:<chat_id>`；CLI chat 使用 `cli:default`，未来外部平台可以使用
类似 `lark:<chat_id>` 的身份。

入站消息携带 `channel`、`chat_id` 和 `sender_id`。出站消息通过 `channel`、
`chat_id`、`reply_to` 和 stream state 完成路由和流式控制。`channels` 包目前只定义
共享的 channel 协议和 manager，还没有启用真实外部平台 adapter。

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
    api_key_env: "OPENAI_API_KEY"
    api_base: null
    extra_headers: {}
    extra_body: {}
    timeout_seconds: 120
```

`providers.openai_compat.api_key` 可以直接配置，也可以让 XAgent 从 `api_key_env`
指定的环境变量读取。如果两者都没有配置，XAgent 会向 OpenAI-compatible SDK client
传入 `no-key`，方便本地无鉴权 endpoint 运行。
