# ACP 协议快速参考

> 基于官方 acp 包分析生成

## 核心概念

- **协议**: JSON-RPC 2.0 over NDJSON
- **版本**: Protocol v1, JSON-RPC v2.0
- **通信模式**: 双向异步，通过 stdin/stdout 流

## 入口函数

```python
from agent import run_agent, connect_to_agent

# 运行 Agent
await run_agent(agent)

# 连接 Agent
conn = await connect_to_agent(client, input_stream, output_stream)
```

## 消息格式

```json
// 请求
{"jsonrpc": "2.0", "id": 1, "method": "method_name", "params": {}}

// 通知
{"jsonrpc": "2.0", "method": "method_name", "params": {}}

// 响应
{"jsonrpc": "2.0", "id": 1, "result": {}}
```

## Agent 方法（服务端）

| 方法 | 说明 |
|------|------|
| `initialize` | 初始化连接 |
| `session/new` | 创建新会话 |
| `session/prompt` | 发送提示 |
| `session/resume` | 恢复会话 |
| `session/close` | 关闭会话 |
| `session/cancel` | 取消会话 |
| `session/list` | 列出会话 |
| `session/fork` | 分叉会话 |
| `session/set_mode` | 设置模式 |
| `session/set_model` | 设置模型 |
| `authenticate` | 身份验证 |

## Client 方法（Agent 调用）

| 方法 | 说明 |
|------|------|
| `fs/read_text_file` | 读文件 |
| `fs/write_text_file` | 写文件 |
| `session/update` | 推送会话更新 |
| `session/request_permission` | 请求权限 |
| `terminal/create` | 创建终端 |
| `terminal/output` | 获取终端输出 |
| `terminal/kill` | 终止终端 |
| `terminal/wait_for_exit` | 等待终端退出 |

## 内容块辅助函数

```python
from agent.helpers import (
    text_block, image_block, audio_block,
    tool_content, tool_diff_content,
    update_user_message_text, update_agent_message_text,
    update_agent_thought_text, update_plan
)
```

## 错误码

| 码 | 含义 |
|----|------|
| -32700 | 解析错误 |
| -32600 | 无效请求 |
| -32601 | 方法未找到 |
| -32602 | 无效参数 |
| -32603 | 内部错误 |
| -32000 | 需要认证 |
| -32002 | 资源未找到 |

## Stdio 连接

```python
from agent.stdio import stdio_streams, spawn_stdio_connection

# 获取标准流
reader, writer = await stdio_streams()

# 生成子进程连接
async with spawn_stdio_connection(handler, "python", "agent.py") as conn:
    ...
```

## Schema 模型位置

`schema.py` 包含所有 Pydantic 模型，核心包括：

- `InitializeRequest` / `InitializeResponse`
- `NewSessionRequest` / `NewSessionResponse`
- `PromptRequest` / `PromptResponse`
- `SessionUpdate`
- `TextContentBlock` / `ImageContentBlock` / `AudioContentBlock`
- 各种工具调用相关模型
