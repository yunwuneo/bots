import asyncio
import contextlib
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Config
from .runtime import Runtime
from .store import Store, uid


class BotInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="", max_length=12000)
    memory: str = Field(default="", max_length=12000)


class TaskInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=30000)


class SkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=20000)


class RoutineInput(BaseModel):
    bot_id: str
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=12000)
    interval_minutes: int = Field(ge=5, le=525600)


def create_app(config=None, start_worker=True):
    config = config or Config()
    store = Store(config.data_dir / "neobots.sqlite3")
    runtime = Runtime(store, config)
    web = Path(__file__).resolve().parent.parent / "web"

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        if not store.one("SELECT id FROM bots LIMIT 1"):
            store.create_bot("Neo", "你是我的个人助理。帮助我研究问题、整理信息并在共享电脑里完成任务。交付可核查的结果和文件。")
        worker = asyncio.create_task(runtime.loop()) if start_worker else None
        yield
        if worker:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        await runtime.close()
        store.close()

    app = FastAPI(title="NeoBots", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.runtime = store, runtime
    attempts = {}

    def session():
        timestamp = str(int(time.time()))
        signature = hmac.new(config.password.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
        return timestamp + "." + signature

    def authenticated(request):
        if not config.password:
            return request.client.host in {"127.0.0.1", "::1"} and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        try:
            timestamp, signature = request.cookies.get("neobots_session", "").split(".", 1)
            expected = hmac.new(config.password.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
            return 0 <= time.time() - int(timestamp) < 604800 and hmac.compare_digest(signature, expected)
        except (ValueError, TypeError):
            return False

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/login" and not authenticated(request):
            return JSONResponse({"detail": "请先登录"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/login")
    async def login(request: Request):
        host, now = request.client.host, time.time()
        recent = [t for t in attempts.get(host, []) if now - t < 60]
        if len(recent) >= 8:
            raise HTTPException(429, "尝试次数过多，请一分钟后重试")
        body = await request.json()
        if not config.password:
            if not authenticated(request):
                raise HTTPException(403, "远程访问前请设置 NEOBOTS_PASSWORD")
        elif not isinstance(body.get("password"), str) or not hmac.compare_digest(body["password"].encode(), config.password.encode()):
            attempts[host] = recent + [now]
            raise HTTPException(401, "密码不正确")
        attempts.pop(host, None)
        response = JSONResponse({"ok": True})
        response.set_cookie("neobots_session", session(), max_age=604800, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return response

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("neobots_session")
        return response

    @app.get("/api/state")
    async def state():
        return {"bots": store.all("SELECT * FROM bots ORDER BY created"),
            "tasks": store.all("SELECT id,bot_id,prompt,status,result,created,updated,parent_id FROM tasks ORDER BY created DESC LIMIT 100"),
            "approvals": store.all("SELECT a.* FROM approvals a JOIN tasks t ON a.task_id=t.id WHERE a.decision='pending' AND t.status='awaiting_approval' ORDER BY a.created"),
            "skills": store.all("SELECT * FROM skills ORDER BY name"),
            "routines": store.all("SELECT * FROM routines ORDER BY created DESC"),
            "model": {"configured": config.model_ready, "name": config.model}}

    @app.get("/api/health")
    async def health():
        try:
            result = await runtime.client.get(config.runtime_url + "/health", headers={"X-Runtime-Token": config.runtime_token}, timeout=3)
            computer = result.is_success
        except httpx.HTTPError:
            computer = False
        return {"computer": computer, "model": config.model_ready}

    @app.post("/api/bots")
    async def add_bot(body: BotInput):
        if not body.name.strip():
            raise HTTPException(422, "Bot 名称不能为空")
        bot = store.create_bot(body.name.strip(), body.role)
        store.execute("UPDATE bots SET memory=? WHERE id=?", (body.memory, bot["id"]))
        return store.bot(bot["id"])

    def require_bot(bot_id):
        if not store.bot(bot_id):
            raise HTTPException(404, "Bot 不存在")

    def require_task(task_id):
        task = store.task(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task

    @app.put("/api/bots/{bot_id}")
    async def update_bot(bot_id: str, body: BotInput):
        require_bot(bot_id)
        if not body.name.strip():
            raise HTTPException(422, "Bot 名称不能为空")
        store.execute("UPDATE bots SET name=?,role=?,memory=? WHERE id=?", (body.name.strip(), body.role, body.memory, bot_id))
        return store.bot(bot_id)

    @app.get("/api/bots/{bot_id}/messages")
    async def messages(bot_id: str):
        require_bot(bot_id)
        return list(reversed(store.all("SELECT * FROM messages WHERE bot_id=? ORDER BY created DESC LIMIT 100", (bot_id,))))

    @app.post("/api/bots/{bot_id}/tasks")
    async def add_task(bot_id: str, body: TaskInput):
        require_bot(bot_id)
        if not body.prompt.strip():
            raise HTTPException(422, "任务不能为空")
        if not config.model_ready:
            raise HTTPException(409, "请先在 .env 配置模型服务，再重启应用")
        task = store.create_task(bot_id, body.prompt.strip())
        return {"id": task["id"], "status": task["status"]}

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str):
        task = require_task(task_id)
        task.pop("state")
        return {**task, "events": store.all("SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,))}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        task = require_task(task_id)
        if task["status"] not in {"completed", "failed", "cancelled"}:
            await runtime.cancel(task_id)
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/resume")
    async def resume_task(task_id: str):
        task = require_task(task_id)
        if task["status"] != "interrupted":
            raise HTTPException(409, "仅中断的任务可以继续；失败任务请重新派单")
        runtime.resume(task_id)
        return {"ok": True}

    @app.post("/api/approvals/{approval_id}/{decision}")
    async def decide(approval_id: str, decision: str):
        if decision not in {"approved", "denied"}:
            raise HTTPException(400, "无效的审批结果")
        approval = store.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not approval or approval["decision"] != "pending":
            raise HTTPException(409, "审批已处理或不存在")
        if require_task(approval["task_id"])["status"] != "awaiting_approval":
            raise HTTPException(409, "任务当前不能审批")
        with store.db:
            store.db.execute("UPDATE approvals SET decision=? WHERE id=?", (decision, approval_id))
            store.db.execute("UPDATE tasks SET status='queued',updated=? WHERE id=?", (time.time(), approval["task_id"]))
        store.event(approval["task_id"], "decision", "用户批准操作" if decision == "approved" else "用户拒绝操作")
        return {"ok": True}

    @app.post("/api/skills")
    async def save_skill(body: SkillInput):
        store.execute("INSERT INTO skills VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET instructions=excluded.instructions", (uid(), body.name, body.instructions, time.time()))
        return {"ok": True}

    @app.post("/api/routines")
    async def add_routine(body: RoutineInput):
        require_bot(body.bot_id)
        now = time.time()
        store.execute("INSERT INTO routines(id,bot_id,name,prompt,interval_minutes,next_run,created) VALUES(?,?,?,?,?,?,?)",
            (uid(), body.bot_id, body.name, body.prompt, body.interval_minutes, now + body.interval_minutes * 60, now))
        return {"ok": True}

    @app.post("/api/routines/{routine_id}/toggle")
    async def toggle_routine(routine_id: str):
        routine = store.one("SELECT * FROM routines WHERE id=?", (routine_id,))
        if not routine:
            raise HTTPException(404, "Routine 不存在")
        store.execute("UPDATE routines SET enabled=?,next_run=? WHERE id=?", (not routine["enabled"], time.time() + routine["interval_minutes"] * 60, routine_id))
        return {"ok": True}

    async def computer_request(path, body=None):
        try:
            result = await runtime.client.request("POST" if body else "GET", config.runtime_url + path,
                headers={"X-Runtime-Token": config.runtime_token}, json=body, timeout=40)
            if result.is_error:
                raise HTTPException(result.status_code if result.status_code == 404 else 502, "电脑服务暂时不可用")
            return result
        except httpx.HTTPError:
            raise HTTPException(503, "无法连接电脑服务，请检查 Docker 中的 computer 容器")

    @app.get("/api/computer/{bot_id}/screen")
    async def screen(bot_id: str):
        require_bot(bot_id)
        result = await computer_request("/screen/" + bot_id)
        return Response(result.content, media_type="image/png")

    @app.post("/api/computer/{bot_id}/action")
    async def computer_action(bot_id: str, request: Request):
        require_bot(bot_id)
        # Human takeover is permitted only while no agent is using this Bot's page.
        if store.one("SELECT id FROM tasks WHERE bot_id=? AND status IN ('running','queued')", (bot_id,)):
            raise HTTPException(409, "请等待当前 Bot 暂停或完成，再接管浏览器")
        body = await request.json()
        if body.get("action") not in {"navigate", "mouse", "type", "press", "scroll", "inspect"}:
            raise HTTPException(400, "不支持的浏览器操作")
        result = await computer_request("/execute", {"task_id": uid(), "bot_id": bot_id, "tool": "browser", "arguments": body})
        return result.json()

    @app.get("/api/files")
    async def files(path: str = "."):
        query = str(httpx.QueryParams({"path": path}))
        result = await computer_request("/files?" + query)
        if result.headers.get("content-type", "").startswith("application/json"):
            return result.json()
        return Response(result.content, media_type="application/octet-stream", headers={"Content-Disposition": result.headers.get("content-disposition", "attachment")})

    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.get("/")
    async def index():
        return FileResponse(web / "index.html")

    return app


app = create_app()

