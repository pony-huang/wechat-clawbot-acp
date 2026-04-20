# WeChat + ACP 技术文档

> 本文档基于 [ACP (Agent Client Protocol) Python SDK](https://github.com/agentclientprotocol/python-sdk) 分析生成

---

## 1. 项目概述

### 1.1 协议背景

ACP (Agent Client Protocol) 是一个基于 **JSON-RPC 2.0** 的协议，通过 **NDJSON** (换行分隔的 JSON) 流进行双向通信。协议支持 AI Agent 与 Client 之间的多种交互模式，包括会话管理、文件操作、终端控制等。

### 1.2 协议版本

- **Protocol Version**: `1`
- **JSON-RPC Version**: `2.0`

---

## 2. 目录结构

```
acp/
├── __init__.py              # 主包导出
├── core.py                  # 兼容性重导出及 run/connect 函数
├── meta.py                  # 协议版本和方法常量
├── schema.py                # 所有 Pydantic 模型 (~3200 行)
├── connection.py            # 核心 JSON-RPC 2.0 连接处理
├── router.py               # 消息路由逻辑
├── helpers.py               # 内容块和会话更新辅助函数
├── interfaces.py            # Agent 和 Client 协议接口
├── transports.py            # Stdio 传输进程生成
├── stdio.py                 # Stdio 连接辅助函数
├── utils.py                 # 模型处理工具函数
├── telemetry.py             # OpenTelemetry/Logfire 追踪
├── exceptions.py            # RequestError 异常类
├── py.typed                 # PEP 561 类型提示标记
├── agent/
│   ├── __init__.py
│   ├── connection.py        # AgentSideConnection
│   └── router.py            # build_agent_router 函数
├── client/
│   ├── __init__.py
│   ├── connection.py        # ClientSideConnection
│   └── router.py            # build_client_router 函数
├── task/
│   ├── __init__.py          # 任务相关类导出
│   ├── dispatcher.py        # 消息调度器
│   ├── queue.py             # 内存消息队列
│   ├── sender.py            # 异步队列消息发送器
│   ├── state.py             # 消息状态存储
│   └── supervisor.py        # 后台任务监督器
└── contrib/
    ├── __init__.py          # 实验性辅助函数
    ├── permissions.py       # PermissionBroker
    ├── session_state.py     # SessionAccumulator, SessionSnapshot
    └── tool_calls.py        # ToolCallTracker
```

---

## 3. 协议方法规范

### 3.1 Agent 方法 (服务端处理)

Agent 端作为服务端，接收来自 Client 的调用：

| 方法名 | 描述 |
|--------|------|
| `authenticate` | 身份验证 |
| `initialize` | 初始化连接 |
| `session/cancel` | 取消会话 |
| `session/close` | 关闭会话 |
| `session/fork` | 分叉会话 |
| `session/list` | 列出所有会话 |
| `session/load` | 加载会话 |
| `session/new` | 创建新会话 |
| `session/prompt` | 发送提示 |
| `session/resume` | 恢复会话 |
| `session/set_config_option` | 设置配置选项 |
| `session/set_mode` | 设置会话模式 |
| `session/set_model` | 设置会话模型 |

### 3.2 Client 方法 (Agent 调用 Client)

Agent 可以通过这些方法调用 Client：

| 方法名 | 描述 |
|--------|------|
| `fs/read_text_file` | 读取文本文件 |
| `fs/write_text_file` | 写入文本文件 |
| `session/request_permission` | 请求权限 |
| `session/update` | 会话更新通知 |
| `terminal/create` | 创建终端 |
| `terminal/kill` | 终止终端 |
| `terminal/output` | 获取终端输出 |
| `terminal/release` | 释放终端 |
| `terminal/wait_for_exit` | 等待终端退出 |

---

## 4. 消息格式

### 4.1 JSON-RPC 2.0 格式

**请求：**
```json
{"jsonrpc": "2.0", "id": 1, "method": "method_name", "params": {}}
```

**通知（无 id）：**
```json
{"jsonrpc": "2.0", "method": "method_name", "params": {}}
```

**响应：**
```json
{"jsonrpc": "2.0", "id": 1, "result": {}}
```
或错误：
```json
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
```

### 4.2 会话更新通知 (session_update)

支持实时更新：
- `user_message_chunk` - 用户消息片段
- `agent_message_chunk` - Agent 消息片段
- `agent_thought_chunk` - Agent 思考片段
- `tool_call` / `tool_call_update` - 工具调用更新
- `plan` - 任务计划更新
- `available_commands_update` - 可用命令更新
- `current_mode_update` - 当前模式更新
- `session_info_update` - 会话元数据更新
- `usage_update` - Token 使用量更新

---

## 5. 核心类

### 5.1 Connection 类

基础 JSON-RPC 2.0 连接处理类。

**主要方法：**
- `send_request(method, params)` - 发送请求，返回 Future
- `send_notification(method, params)` - 发送通知（fire-and-forget）
- `close()` - 优雅关闭

### 5.2 AgentSideConnection

Agent 端连接类，用于接收 Client 调用。

```python
AgentSideConnection(
    to_agent=Agent|callable,      # Agent 实例或工厂函数
    input_stream, output_stream,  # asyncio 流
    listening=bool,               # 默认 True
    use_unstable_protocol=bool    # 默认 False
)
```

### 5.3 ClientSideConnection

Client 端连接类，Agent 通过它调用 Client。

### 5.4 MessageRouter

消息路由器，将 JSON-RPC 调用分发给对应的处理方法。

---

## 6. 内容块类型

### 6.1 基础内容块

| 函数 | 类型 |
|------|------|
| `text_block(text)` | `TextContentBlock` |
| `image_block(data, mime_type, uri)` | `ImageContentBlock` |
| `audio_block(data, mime_type)` | `AudioContentBlock` |
| `resource_link_block(name, uri, ...)` | `ResourceContentBlock` |

### 6.2 嵌入资源块

| 函数 | 类型 |
|------|------|
| `embedded_text_resource(uri, text, ...)` | `TextResourceContents` |
| `embedded_blob_resource(uri, blob, ...)` | `BlobResourceContents` |
| `resource_block(resource)` | `EmbeddedResourceContentBlock` |

### 6.3 工具调用辅助

- `tool_content(block)` - 工具调用内容
- `tool_diff_content(path, new_text, old_text)` - 文件编辑差异
- `tool_terminal_ref(terminal_id)` - 终端引用

### 6.4 会话更新辅助

- `update_plan(entries)` - `AgentPlanUpdate`
- `update_user_message(content)` / `update_user_message_text(text)` - `UserMessageChunk`
- `update_agent_message(content)` / `update_agent_message_text(text)` - `AgentMessageChunk`
- `update_agent_thought(content)` / `update_agent_thought_text(text)` - `AgentThoughtChunk`
- `update_available_commands(commands)` - `AvailableCommandsUpdate`
- `update_current_mode(current_mode_id)` - `CurrentModeUpdate`
- `session_notification(session_id, update)` - `SessionNotification`

---

## 7. 协议接口

### 7.1 Agent 协议接口

```python
class Agent:
    async def initialize(protocol_version, client_capabilities, client_info) -> InitializeResponse
    async def new_session(cwd, mcp_servers) -> NewSessionResponse
    async def load_session(cwd, session_id, mcp_servers) -> LoadSessionResponse
    async def list_sessions(cursor, cwd) -> ListSessionsResponse
    async def set_session_mode(mode_id, session_id) -> SetSessionModeResponse
    async def set_session_model(model_id, session_id) -> SetSessionModelResponse
    async def set_config_option(config_id, session_id, value) -> SetSessionConfigOptionResponse
    async def authenticate(method_id) -> AuthenticateResponse
    async def prompt(prompt[], session_id, message_id) -> PromptResponse
    async def fork_session(cwd, session_id, mcp_servers) -> ForkSessionResponse
    async def resume_session(cwd, session_id, mcp_servers) -> ResumeSessionResponse
    async def close_session(session_id) -> CloseSessionResponse
    async def cancel(session_id)  # 通知
    async def ext_method(method, params)  # 扩展请求
    async def ext_notification(method, params)  # 扩展通知
```

### 7.2 Client 协议接口

```python
class Client:
    async def request_permission(options, session_id, tool_call) -> RequestPermissionResponse
    async def session_update(session_id, update)  # 通知
    async def write_text_file(content, path, session_id) -> WriteTextFileResponse
    async def read_text_file(path, session_id, limit, line) -> ReadTextFileResponse
    async def create_terminal(command, session_id, args, cwd, env, output_byte_limit) -> CreateTerminalResponse
    async def terminal_output(session_id, terminal_id) -> TerminalOutputResponse
    async def release_terminal(session_id, terminal_id) -> ReleaseTerminalResponse
    async def wait_for_terminal_exit(session_id, terminal_id) -> WaitForTerminalExitResponse
    async def kill_terminal(session_id, terminal_id) -> KillTerminalResponse
```

---

## 8. 错误处理

### 8.1 错误码

| 错误码 | 含义 |
|--------|------|
| -32700 | 解析错误 |
| -32600 | 无效请求 |
| -32601 | 方法未找到 |
| -32602 | 无效参数 |
| -32603 | 内部错误 |
| -32000 | 需要认证 |
| -32002 | 资源未找到 |

### 8.2 RequestError 异常

```python
class RequestError(Exception):
    code: int
    message: str
    data: Any | None

    @classmethod
    def parse_error(cls, data=None)
    @classmethod
    def invalid_request(cls, data=None)
    @classmethod
    def method_not_found(cls, method)
    @classmethod
    def invalid_params(cls, data=None)
    @classmethod
    def internal_error(cls, data=None)
    @classmethod
    def auth_required(cls, data=None)
    @classmethod
    def resource_not_found(cls, uri=None)
```

---

## 9. Stdio 传输

### 9.1 主要函数

```python
# 获取 stdio 流
stdio_streams(limit) -> (StreamReader, StreamWriter)

# 生成 stdio 连接
spawn_stdio_connection(handler, command, *args) -> ContextManager
spawn_agent_process(to_client, command, *args) -> ContextManager
spawn_client_process(to_agent, command, *args) -> ContextManager

# 启动传输
spawn_stdio_transport(command, *args, env, cwd) -> (reader, writer, process)
default_environment() -> dict
```

### 9.2 默认配置

```python
DEFAULT_STDIO_BUFFER_LIMIT_BYTES = 50 * 1024 * 1024  # 50MB
```

---

## 10. 任务/异步基础设施

### 10.1 组件

| 组件 | 描述 |
|------|------|
| `MessageQueue` | 基于 asyncio.Queue 的内存异步队列 |
| `MessageDispatcher` | 后台工作者，消费队列中的任务 |
| `MessageSender` | 异步消息发送器，带写入队列 |
| `MessageStateStore` | 追踪待处理请求和响应 |
| `TaskSupervisor` | 管理任务生命周期、错误处理、优雅关闭 |

---

## 11. 实验性功能 (contrib)

这些是不稳定的 API，可能会更改：

| 模块 | 类 |
|------|-----|
| `permissions.py` | `PermissionBroker`, `default_permission_options()` |
| `session_state.py` | `SessionAccumulator`, `SessionSnapshot`, `ToolCallView` |
| `tool_calls.py` | `ToolCallTracker`, `TrackedToolCallView`, `UNSET` |

---

## 12. 遥测 (Telemetry)

可选集成：
- **Logfire** - via `logfire.span`
- **OpenTelemetry** - via `opentelemetry.trace`

提供 `span_context()` 用于分布式追踪。

---

## 13. 设计模式

1. **协议格式**: 使用 Pydantic 模型（而非 Protobuf）
2. **异步优先**: 所有 I/O 使用 asyncio
3. **扩展机制**: 以 `_` 前缀的方法为扩展方法
4. **不稳定协议标志**: `use_unstable_protocol=True` 启用实验性功能
5. **向后兼容**: `@compatible_class` 装饰器提供旧版 API 支持
6. **Meta 字段**: `_meta` 属性保留用于所有 schema 模型的扩展

---

## 14. WeChat + ACP 集成要点

### 14.1 架构建议

```
WeChat Client <--JSON-RPC--> ACP Bridge <--stdio--> WeChat Agent
```

### 14.2 需要实现的核心功能

1. **消息转换层**
   - 将微信消息格式转换为 ACP JSON-RPC 格式
   - 实现 `session_update` 推送机制

2. **协议实现**
   - 实现 `Agent` 接口处理微信端的请求
   - 实现 `Client` 接口调用微信功能（消息发送、文件操作等）

3. **会话管理**
   - 维护微信会话与 ACP 会话的映射
   - 支持 `session/new`, `session/resume`, `session/prompt` 等

4. **传输层**
   - 使用 stdio 或自定义传输（取决于集成方式）
   - 实现 NDJSON 编解码

### 14.3 关键文件映射

| ACP 组件 | WeChat 实现建议 |
|----------|-----------------|
| `AgentSideConnection` | 微信消息接收处理器 |
| `ClientSideConnection` | 微信 API 调用封装 |
| `MessageRouter` | 消息分发器 |
| `session_update` | 微信消息推送 |
| `fs/read_text_file` | 微信临时文件读取 |
| `fs/write_text_file` | 微信临时文件写入 |
| `terminal/create` | 微信小程序/网页端交互 |

---

## 15. 参考

- [ACP Python SDK 源码](.venv/Lib/site-packages/acp)
- [ACP 官方仓库](https://github.com/agentclientprotocol/python-sdk)
