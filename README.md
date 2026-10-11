# NeoBots

面向个人使用的常驻 Agent 工作室，参考 [Grok Bot](https://docs.x.ai/grok-bot/overview) 的产品行为实现。你可以创建有独立职责和记忆的 Bots，通过聊天交付任务，让它们在共享执行环境中使用浏览器、文件和终端。

当前为 v0.1：提供可运行的任务闭环。官方功能调研与后续范围见 [调研文档](docs/grok-bot-research.md)。本项目与 Grok Bot 官方无关联。

## 已实现

- Bot 创建和编辑，独立对话、职责与可编辑的长期记忆。
- 后台队列、工具调用记录、任务取消、服务重启后的中断恢复。
- 独立执行容器：持久化共享目录、Chromium 登录态、每 Bot 独立标签页。
- 浏览器文本观察、导航、点击、输入、截图，以及人工接管登录操作。
- Bot 间委派、等待和结果回传，限制循环委派及深度。
- 共享技能库；固定间隔的定时任务，暂停/启用与最近执行记录。
- 命令执行、浏览器点击/输入/按键及 Bot 创建定时任务需要对准确参数进行审批。
- 兼容 `/chat/completions` 和 function tool calling 的模型服务。
- 中文界面、手机屏幕适配、单用户登录、共享文件浏览和下载。

## 在 WSL2 运行

需要 Docker Engine + Compose，或已启用 WSL2 集成的 Docker Desktop。建议将项目保存在 WSL 的 Linux 文件系统，例如 `~/NeoBots`。

```bash
python3 scripts/setup.py --base-url https://你的接口地址/v1 --model 你的模型名
```

如果本地 `.env` 已存在，初始化脚本会保留它。打开 `.env`：

- 填写 `MODEL_API_KEY`。
- 检查 `MODEL_BASE_URL` 和 `MODEL_NAME`，模型须支持工具调用。
- `NEOBOTS_PASSWORD` 是自动生成的网页登录密码，可以改成自己的密码。
- `NEOBOTS_RUNTIME_TOKEN` 用于两个服务之间认证，保持随机值即可。

`.env` 被 Git 和 Docker 构建上下文忽略；初始化文件权限为 `600`。不要把 Key 填进聊天或 Bot 记忆。

```bash
docker compose up -d --build
docker compose ps
```

在 Windows 浏览器打开 **http://localhost:18787**，使用 `.env` 中的 `NEOBOTS_PASSWORD` 登录。可以通过 `NEOBOTS_PORT` 更改宿主机端口。首次构建会下载 Python 依赖、Chromium 和浏览器运行库。

服务默认只监听宿主机回环地址。`computer` 没有对宿主机发布端口，应用不访问 Docker socket。模型 Key 只交给 `app` 容器。`computer` 以普通用户运行，工作命令只在该容器执行。

模型服务在宿主机时，容器内不能用 `localhost` 访问它；使用 `http://host.docker.internal:端口/v1`，并确认模型服务接受来自 Docker 网络的连接。使用远程 HTTPS 地址则无需此调整。

修改 `.env` 后运行 `docker compose up -d` 重建受影响的容器。查看日志：

```bash
docker compose logs --tail=100 app computer
```

## 试用一个完整任务

1. 登录后打开默认的 Neo Bot。
2. 发消息：“打开 https://example.com，读取页面标题和内容，把摘要保存到 reports/example.md，并告诉我文件路径。”
3. 在“任务记录”查看工具调用，在“共享文件”下载报告。
4. 创建另一位负责审阅的 Bot，让 Neo 委派它审阅文件并返回意见。
5. 让 Bot 执行一个简单命令，在“待审批”检查准确参数，批准后观察任务继续。
6. 为稳定任务保存技能；通过“定时任务”设置执行间隔。

点击右上角“共享电脑”可以查看所选 Bot 的浏览器截图。通过点击画面、输入框、Tab/Enter 和滚动按钮操作。人工接管仅在该 Bot 没有正在运行或排队的任务时开放；输入不会写入对话。操作后会刷新截图，也可以手动刷新。

## 状态与恢复

```text
排队 → 执行 → 完成 / 失败
          ├→ 等待审批 → 排队
          └→ 等待 Bot → 排队
服务重启时的执行中任务 → 中断 → 用户继续 → 排队
```

队列、审批和模型上下文保存在 SQLite。已完成的工具调用不会因为一次审批暂停而重新执行。服务在操作执行中被停止时，该操作结果可能不明；恢复会把“不确定是否已执行”的情况交给 Agent 检查，避免系统自动重放外部写操作。

“停止任务”会取消后续模型执行并请求终止正在运行的命令，同时取消其未完成的委派任务；已完成的操作不能撤销，已经发出的浏览器操作可能完成。

定时任务默认间隔至少 5 分钟。前一轮仍在运行或等审批时跳过重叠触发。停机后的漏跑只合并为一次，避免恢复后集中执行。

## WSL2 常开与数据

Docker 的 `restart: unless-stopped` 在 Docker 启动后恢复服务。**Windows 休眠、关机、WSL 终止或 Docker 停止时，任务也会停止。**需要持续运行时，在 Windows 设置中关闭自动睡眠，并确保 WSL/Docker 随你的使用方式启动。应用不会修改系统电源设置。

| Docker 卷 | 内容 |
| --- | --- |
| `app-data` | SQLite：Bot、对话、任务、审批、记忆、技能和定时任务 |
| `workspace` | Bot 共享文件、产物 |
| `browser-profile` | Chromium profile、cookies、登录态 |

`docker compose down` 保留上述卷；附加 `-v` 会删除它们。备份应包含三个卷以及本机 `.env`，恢复时保留原有 token。浏览器 cookies 会持久化；网站自身过期或重新验证仍需登录。服务重启后会创建新的 Bot 标签页，不恢复标签页的页面地址。

## 当前边界

- 提供浏览器级电脑操作；尚无完整 Linux 图形桌面、实时桌面视频和原生客户端。
- 尚未接 MCP/OAuth connectors、语音、群聊和演示录制学习；首版支持文字技能与 Bot 间委派。
- 工作进展按工具/步骤更新，暂不提供逐 token 的流式文字。
- 记忆通过 `remember` 工具显式保存；没有向量检索或无限上下文压缩。
- 单用户、单个应用进程；一次最多 3 个活跃任务，同一 Bot 一次一个任务。每个任务最多 30 次模型请求；模型请求超时 180 秒，命令执行超时 45 秒。
- shell、浏览器交互审批采用代码中的明确规则，尚未实现自然语言授权规则及独立审查模型。
- 浏览器下载管理、文件上传、任务运行中插入新指令、完整日志检索和任务删除尚待后续实现。新消息作为新任务排队。
- 浏览器和共享文件属于同一位用户的 Bots；Bot 之间不是凭据隔离边界。
- 模型接入是否成功，取决于服务的模型权限和标准工具调用兼容性；未配置 Key 时可以管理工作室，任务派发会提示先配置模型。

## 开发与验证

```bash
uv sync --extra dev --extra computer
uv run pytest -q
node --check web/app.js
```

已安装 Chromium 时，可运行 `uv run python scripts/browser_smoke.py` 验证界面。它使用临时数据库、模拟模型和模拟电脑验证交互，不调用实际模型 API，也不改动运行中的工作室。

测试覆盖审批恢复、拒绝、精确参数绑定、多工具续跑、结果不明操作的恢复、Bot 委派、循环检测、调度去重、认证、跨站请求及工作目录边界。

本地调试 Web/API（电脑服务仍推荐在 Docker 内运行）：

```bash
uv run uvicorn neobots.app:app --host 127.0.0.1 --port 8787
```

本地命令不会自动读取 `.env`；通过进程环境提供模型配置及 `NEOBOTS_RUNTIME_URL` / `NEOBOTS_RUNTIME_TOKEN`。没有设置登录密码时，仅允许回环地址访问。

依赖锁定在 `uv.lock`；Docker 使用从中导出的 `requirements.txt` 和 `requirements-computer.txt`。不要启动多个 Uvicorn workers，它们会重复启动调度器。
