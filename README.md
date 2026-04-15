# XAgent

XAgent is a Python workspace-aware assistant runtime and CLI.

## Quick Start

### 前置要求
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (现代化 Python 包管理器，替代 pip/venv 等传统工具)

1. 安装 uv（如果尚未安装）：
   ```bash
   # Linux/macOS
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Windows (PowerShell)
   irm https://astral.sh/uv/install.ps1 | iex
   ```

2. 克隆仓库并进入项目目录：
   ```bash
   git clone https://github.com/huanglei214/XAgent.git
   cd XAgent
   ```

3. 安装项目依赖并以开发模式安装 XAgent：
   ```bash
   uv sync
   ```
   > 该命令会自动创建虚拟环境并安装所有依赖，无需手动创建 venv。

4. （可选）激活虚拟环境：
   如果不想每次执行命令都加 `uv run` 前缀，可以激活虚拟环境：
   ```bash
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

5. 初始化 XAgent 配置：
   ```bash
   # 已激活虚拟环境时使用
   xagent config init
   
   # 未激活虚拟环境时使用
   uv run xagent config init
   ```

6. 更新生成的项目本地配置文件 `.xagent/config.yaml` 为您偏好的设置。

   仓库根目录还会生成一个 `config.example.yaml`，方便查看推荐结构或重新拷贝模板。

7. 在项目本地 `.env` 文件中添加您的 OpenAI（或其他 LLM 提供商）API 密钥：
   ```env
   OPENAI_API_KEY=your-api-key-here
   ```

8. 运行简单任务测试配置是否正确：
   ```bash
   # 已激活虚拟环境时使用
   xagent run "Say hello and explain what you can do in this workspace"
   
   # 未激活虚拟环境时使用
   uv run xagent run "Say hello and explain what you can do in this workspace"
   ```

## 配置真实飞书 Bot

当前仓库已经接入了一个 v1 版 Feishu channel，入口命令是：

```bash
xagent channel feishu serve
```

这版能力边界是：

- 支持文本消息
- 支持私聊和群聊
- 群聊默认只接收 `mention_only`
- 支持访问控制
- 支持把会话路由到 XAgent 的 `SessionRuntime`
- 支持把 `assistant.delta` 转成“逐步可见”的文本输出
- 不支持图片、文件、卡片、表情回执等富消息

### 1. 在飞书开放平台创建应用

建议使用飞书开放平台里的自建应用。

需要完成的最小配置：

- 开启机器人能力
- 给应用添加接收消息事件权限
- 给应用添加发送消息权限
- 安装应用到你的测试租户
- 开启事件订阅，并选择长连接模式
- 订阅消息接收事件，至少要让应用能收到文本消息事件

按当前实现，最关键的是让应用同时具备：

- 接收文本消息事件
- 用 `app_id/app_secret` 换取 tenant access token
- 通过开放平台消息发送接口发回文本消息

### 1.1 需要开通哪些权限

按当前这版 XAgent 的实现，最小可运行权限可以理解成三类：

1. 机器人能力
2. 事件订阅能力
3. 服务端接口权限

建议你在飞书开放平台里按“**和下面事件 / 接口对应的权限**”去勾选。  
控制台里的中文权限名会随着版本微调，但只要和下面这些能力一一对应即可。

**必需项**

- 机器人能力
  - 让应用可以作为机器人出现在私聊或群聊里
- 事件订阅
  - 订阅 `im.message.receive_v1`
  - 这是当前 XAgent 接收用户文本消息的核心事件
- 服务端鉴权
  - 允许应用使用 `app_id/app_secret` 获取 tenant access token
  - 当前实现对应的是飞书官方文档里的 `tenant_access_token/internal`
- 发送消息
  - 允许应用调用发送消息接口
  - 当前实现对应的是飞书官方文档里的 `im/v1/messages`

如果你想按**精确权限标识**来开通，当前这版实现建议至少开下面这些 tenant scope：

```text
im:message
im:message.p2p_msg:readonly
im:message.group_at_msg:readonly
im:message:send_as_bot
```

这四项分别对应：

- `im:message`
  - 消息基础权限
  - 一般建议一起开，作为消息能力的基础 scope
- `im:message.p2p_msg:readonly`
  - 接收发给机器人的私聊消息
- `im:message.group_at_msg:readonly`
  - 接收群聊中 @ 机器人的消息
  - 这和当前默认 `FEISHU_GROUP_MODE=mention_only` 对应
- `im:message:send_as_bot`
  - 以机器人身份发送文本消息

**如果你准备把群聊模式切成 `all_text`**

再额外开：

```text
im:message.group_msg:readonly
```

它对应“接收机器人所在群聊中的所有群消息”。  
如果不开这个权限，当前最稳妥的做法就是继续使用默认的 `mention_only`。

**建议同时确认的配置**

- 给应用安装到你的测试租户
- 在事件订阅里启用长连接模式
- 如果要在群里使用，确认机器人被允许加入目标群
- 如果只想让 bot 在群里响应被 @ 的消息，保留 `FEISHU_GROUP_MODE=mention_only`

**和当前实现的映射关系**

- 接收消息：SDK 长连接 + `im.message.receive_v1`
- 发送消息：官方服务端消息发送接口 `im/v1/messages`
- 应用鉴权：官方 tenant access token 接口

相关官方文档入口：

- tenant access token：
  [Get custom app tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)
- 发送消息：
  [Send message](https://open.feishu.cn/document/server-docs/im-v1/message/create)

如果你在控制台里一时找不到完全同名的中文权限，优先按上面的 scope 字符串去搜；这版代码并没有依赖联系人、日历、云文档、卡片流式更新之类的额外权限。

### 2. 准备项目本地 `.env`

当前 Feishu channel 读取的是项目根目录 `.env`。

最小示例：

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx

# 可选：如果你接的是 Lark 国际版，可改成 https://open.larksuite.com
FEISHU_API_BASE_URL=https://open.feishu.cn

# 可选：群聊 mention 过滤时，用来识别“是否 @ 了 bot”
FEISHU_BOT_OPEN_ID=ou_xxxxxxxxxxxxx

# mention_only | all_text
FEISHU_GROUP_MODE=mention_only

# 访问控制
FEISHU_ALLOW_ALL=false
FEISHU_ALLOWED_USER_IDS=ou_xxx1,ou_xxx2
FEISHU_ALLOWED_CHAT_IDS=oc_xxx1,oc_xxx2

# 可选：默认会自动退避重连
FEISHU_RECONNECT_INITIAL_SECONDS=1.0
FEISHU_RECONNECT_CAP_SECONDS=30.0

# 可选：增量输出节流
FEISHU_PARTIAL_EMIT_CHARS=32

# 可选：拒绝访问时给用户返回的提示
FEISHU_DENY_MESSAGE=Access denied.
```

各字段含义：

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：开放平台应用凭据
- `FEISHU_API_BASE_URL`：开放平台域名，飞书默认 `https://open.feishu.cn`
- `FEISHU_BOT_OPEN_ID`：群聊 `mention_only` 模式下，用来判断是否真的 @ 了当前 bot
- `FEISHU_GROUP_MODE`：群聊消息接收策略
  - `mention_only`：只有 @ bot 的群消息才进入 XAgent
  - `all_text`：群里的普通文本消息也会进入 XAgent
- `FEISHU_ALLOW_ALL`：是否关闭访问控制，允许所有用户/群
- `FEISHU_ALLOWED_USER_IDS` / `FEISHU_ALLOWED_CHAT_IDS`：白名单

当前实现已经改成更接近官方/常见 agent 的接法：

- 长连接接收侧使用飞书官方 Python SDK `lark-oapi`
- SDK 会用 `FEISHU_APP_ID/FEISHU_APP_SECRET` 自动生成长连接地址并维护连接
- 用户不再需要手填 `FEISHU_WS_URL`
- 发送消息这侧也改成通过官方 SDK client 调用消息发送接口

### 3. 启动方式

```bash
# 已激活虚拟环境
xagent channel feishu serve

# 未激活虚拟环境
uv run xagent channel feishu serve
```

如果启动时：

- `FEISHU_APP_ID` 缺失
- `FEISHU_APP_SECRET` 缺失
- 初始鉴权或初始连接失败

当前实现会直接失败退出，而不是静默降级。

### 4. 推荐先做一轮真实环境 smoke test

建议至少验证下面几项：

1. 私聊给 bot 发文本，XAgent 能收到并回复
2. 群聊里 `@bot` 发文本，`mention_only` 模式能收到并回复
3. 群聊里不 `@bot` 发文本，`mention_only` 模式不会误触发
4. 把 `FEISHU_GROUP_MODE=all_text` 后，群普通文本也会进入
5. 不在白名单里的用户或群，会收到 `FEISHU_DENY_MESSAGE`
6. 启动时配错 `APP_ID/APP_SECRET`，进程会 fail fast
7. 长连接断开后，会按退避策略自动重连

### 5. 当前实现的真实接入前提

这点很重要，README 里说明清楚，避免误解：

- 当前代码已经把 XAgent 这一侧的 channel/runtime/CLI/测试骨架补齐了
- 长连接接收和发送消息两侧都已经切到飞书官方 Python SDK
- 当前需要你提供的是稳定凭据和策略配置，而不是长连接 URL

也就是说，这版已经不再要求你自己维护底层 WebSocket 握手地址。  
如果你所在的飞书应用环境还有额外的租户、权限、事件订阅或国际版域名差异，优先通过开放平台应用配置和 `FEISHU_API_BASE_URL` 来解决。

### 6. 相关代码入口

- CLI 启动入口：`src/xagent/cli/commands/channel.py`
- Feishu 配置读取：`src/xagent/channel/feishu/config.py`
- Feishu 官方 SDK 封装：`src/xagent/channel/feishu/client.py`
- Feishu adapter：`src/xagent/channel/feishu/adapter.py`
- Runtime bridge：`src/xagent/agent/runtime/channel_bridge.py`
