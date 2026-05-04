# FocalServer（新一代微内核程序共享终端）

在浏览器里运行并交互式控制 `app/` 目录下的任意 Python 脚本：支持实时 stdout/stderr、回车输入 stdin、多脚本入口切换，以及“设置”弹窗集中管理运行参数。

## 目录约定

- `app/`：放你要运行的脚本
  - 默认入口优先级：`app/main.py` → `app/app.py` → `app/` 下按文件名排序的第一个 `*.py`

## 快速启动（Windows / PowerShell）

1. 安装 Python 3.10+（建议 3.11/3.12/3.13 均可）
2. 在项目根目录运行：

```powershell
.\start.ps1
```

启动后访问：

- `http://127.0.0.1:8000/`

> 提示：改动 `web/templates/index.html` 后，建议手动重启一次（`Ctrl+C` 停止再运行 `.\start.ps1`），并在浏览器 `Ctrl+F5` 强刷避免模板缓存。

## 使用方式

### 运行脚本

1. 打开网页后会自动启动一次运行会话（等同于点击“运行”）
2. 需要重新启动脚本时，点击“运行”
3. 需要停止脚本时，点击“停止”（或在输入框里输入 `/stop` 回车）

### 交互输入（stdin）

- 在终端底部输入框输入内容并回车，会把该行发送给脚本的 stdin
- 适用于脚本内使用 `input()` / `sys.stdin.readline()` 等交互读取

### 设置（入口 / args / 超时 / Python 环境 / stderr）

右上角点击“设置”打开弹窗：

- **入口**：选择 `app/` 下的 `*.py` 脚本（留空则自动选择）
- **args**：空格分隔（复杂引号场景建议脚本自行解析或后续再增强）
- **超时**：用于一次性 `POST /api/run` 的超时参数（交互会话模式主要由脚本自行结束；“停止”会终止进程）
- **Python 环境**
  - `server`：使用服务端/虚拟环境 Python（`.\.venv\Scripts\python.exe`）
  - `system`：使用系统 Python（若在 `start.ps1` 中检测到）
- **stderr（最近输出）**：只读缓冲区，保留最近一段 stderr，便于排查报错

## 接口说明

### WebSocket（交互会话）

- `GET /ws/run`

消息协议（JSON）：

- 客户端 → 服务端
  - `{ "type": "start", "entry": "", "python_mode": "server|system", "args": ["..."], "timeout_sec": 10 }`
  - `{ "type": "stdin", "data": "hello\\n" }`
  - `{ "type": "stop" }`
- 服务端 → 客户端
  - `{ "type": "started", "used_entry": "...", "python_mode": "...", "cmd": [...] }`
  - `{ "type": "stdout", "data": "..." }`
  - `{ "type": "stderr", "data": "..." }`
  - `{ "type": "exit", "exit_code": 0 }`
  - `{ "type": "error", "error": "..." }`

### HTTP（兼容一次性执行）

- `POST /api/run`：一次性执行脚本并返回 stdout/stderr/exit_code（仍保留，便于脚本无需交互的快速执行）
- `GET /api/status`：服务状态与可用入口列表
- `GET /api/entries`：入口列表（`app/` 下的 `*.py`）
- `GET /api/files` / `GET /api/file?path=...`：列文件与读文件（用于调试/扩展）

## 常见问题

### 页面打不开/500

看 PowerShell 窗口里的 uvicorn 输出；必要时可设置环境变量开启调试：

```powershell
$env:SKILLBOTTLE_DEBUG=1
.\start.ps1
```

### 运行时出现编码问题（UnicodeDecodeError）

本项目已强制以 UTF-8 解码子进程输出并 `errors=replace`，通常不会再因为编码导致服务崩溃；如果你脚本自身按 GBK/其他编码输出，建议脚本侧统一使用 UTF-8。
