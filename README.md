# WeChat Clawbot ACP

A Python implementation of the WeChat ACP bridge for the Agent Client Protocol (ACP).

## 安装

```bash
# 克隆仓库
git clone <repository-url>
cd wechat-clawbot-acp

# 安装依赖到项目虚拟环境
uv pip install -e .
```

## 使用

```bash
# 直接通过 uv 在项目虚拟环境中运行
uv run wca --agent "Claude Code"

# 或先激活 .venv 再运行
# Windows cmd
.venv\Scripts\activate
wca --agent "Claude Code"

# 查看可用代理
uv run wca --list-agents

# 指定工作目录
uv run wca --agent "Claude Code" --cwd /path/to/project

# 启用详细日志
uv run wca --verbose
```

## CLI 选项

| 选项                    | 说明             | 默认值                      |
|-----------------------|----------------|--------------------------|
| `--agent`, `-a`       | 使用的代理后端        | `Claude Code`            |
| `--list-agents`, `-l` | 列出可用代理         | `false`                  |
| `--config`, `-c`      | agents.json 路径 | `src/config/agents.json` |
| `--verbose`, `-v`     | 启用详细日志         | `false`                  |
| `--cwd`               | 代理工作目录         | 当前目录                     |
| `--login`             | 强制登录           | 空                        |

## 可用代理

- Claude Code
- Gemini CLI
- GitHub Copilot
- Qwen Code
- Auggie CLI
- Qoder CLI
- Codex CLI
- OpenCode
- OpenClaw
