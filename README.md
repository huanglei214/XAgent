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
