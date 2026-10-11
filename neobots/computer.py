"""Run this service inside the computer container, never directly on your personal host."""
import asyncio
import contextlib
import os
import re
import secrets
import signal
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field


class Execution(BaseModel):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    bot_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    tool: str
    arguments: dict = Field(default_factory=dict)


def workspace_path(root: Path, relative: str):
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path must remain inside the shared workspace")
    return target


class Computer:
    def __init__(self, root, profile):
        self.root, self.profile = Path(root), Path(profile)
        self.root.mkdir(parents=True, exist_ok=True)
        self.context = None
        self.playwright = None
        self.pages = {}
        self.browser_lock = asyncio.Lock()
        self.processes = {}

    async def page(self, bot_id):
        if not self.context:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.profile), headless=True, viewport={"width": 1280, "height": 800},
                accept_downloads=True, downloads_path=str(self.root / "downloads"),
                args=["--disable-dev-shm-usage"])
            self.context.set_default_timeout(15000)
            self.context.set_default_navigation_timeout(30000)
        if bot_id not in self.pages or self.pages[bot_id].is_closed():
            self.pages[bot_id] = await self.context.new_page()
        return self.pages[bot_id]

    async def browser(self, bot_id, args):
        async with self.browser_lock:
            page = await self.page(bot_id)
            action = args.get("action", "inspect")
            if action == "navigate":
                if urlparse(args.get("url", "")).scheme not in {"http", "https"}:
                    raise ValueError("Only HTTP(S) page URLs are supported")
                await page.goto(args["url"], wait_until="domcontentloaded")
            elif action == "click":
                await page.locator(args["selector"]).first.click()
            elif action == "fill":
                await page.locator(args["selector"]).first.fill(args["text"])
            elif action == "press":
                await page.keyboard.press(args["key"])
            elif action == "mouse":
                x, y = float(args["x"]), float(args["y"])
                if not 0 <= x <= 1280 or not 0 <= y <= 800:
                    raise ValueError("Click is outside the viewport")
                await page.mouse.click(x, y)
            elif action == "type":
                await page.keyboard.insert_text(args["text"])
            elif action == "scroll":
                await page.mouse.wheel(0, max(-800, min(800, int(args.get("delta", 600)))))
            elif action != "inspect":
                raise ValueError("Unknown browser action")
            return await page.evaluate("""() => {
                const elements = [...document.querySelectorAll('a,button,input,textarea,select,[role="button"]')]
                    .filter(e => e.getClientRects().length).slice(0,150);
                return {url: location.href, title: document.title,
                    text: (document.body?.innerText || '').slice(0,14000),
                    elements: elements.map((e,i) => {
                        e.setAttribute('data-neobot-id', String(i));
                        return {id:i, tag:e.tagName.toLowerCase(), type:e.type || '',
                            label:(e.innerText || e.getAttribute('aria-label') || e.placeholder || e.name || '').slice(0,160)};
                    })};
            }""")

    async def cancel(self, task_id):
        process = self.processes.get(task_id)
        if process and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    async def execute(self, request):
        args, name = request.arguments, request.tool
        if name == "browser":
            return await self.browser(request.bot_id, args)
        if name in {"list_files", "read_file", "write_file"}:
            path = workspace_path(self.root, args.get("path", "."))
            if name == "list_files":
                return {"files": [{"name": p.name, "directory": p.is_dir()} for p in sorted(path.iterdir())[:300]]}
            if name == "read_file":
                if path.stat().st_size > 64000:
                    raise ValueError("File exceeds 64KB; use a command to inspect a smaller range")
                return {"path": str(path.relative_to(self.root)), "content": path.read_text()}
            if len(args["content"].encode()) > 256000:
                raise ValueError("File content exceeds 256KB")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"])
            return {"saved": str(path.relative_to(self.root)), "bytes": path.stat().st_size}
        if name == "run_command":
            with tempfile.TemporaryFile() as output:
                process = await asyncio.create_subprocess_exec("/bin/bash", "-c", args["command"],
                    cwd=self.root, stdout=output, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/home/agent", "LANG": "C.UTF-8", "TMPDIR": "/tmp"})
                self.processes[request.task_id] = process
                timed_out = False
                try:
                    await asyncio.wait_for(process.wait(), timeout=45)
                except asyncio.TimeoutError:
                    timed_out = True
                    await self.cancel(request.task_id)
                except asyncio.CancelledError:
                    await self.cancel(request.task_id)
                    raise
                finally:
                    self.processes.pop(request.task_id, None)
                output.seek(0)
                content = output.read(24001)
                return {"exit_code": process.returncode, "output": content[:24000].decode(errors="replace"),
                    "truncated": len(content) > 24000, "timed_out": timed_out}
        raise ValueError("Unsupported computer tool")

    async def close(self):
        for key in list(self.processes):
            await self.cancel(key)
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()


def create_app():
    computer = Computer(os.getenv("NEOBOTS_WORKSPACE", "/workspace"), os.getenv("NEOBOTS_BROWSER_PROFILE", "/browser-profile"))
    token = os.getenv("NEOBOTS_RUNTIME_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        if not token:
            raise RuntimeError("NEOBOTS_RUNTIME_TOKEN is required")
        yield
        await computer.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if not token or not secrets.compare_digest(request.headers.get("x-runtime-token", ""), token):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/execute")
    async def execute(body: Execution):
        try:
            return await computer.execute(body)
        except Exception as exc:
            if isinstance(exc, (ValueError, FileNotFoundError, IsADirectoryError, NotADirectoryError, UnicodeError, KeyError)):
                return {"error": str(exc)[:1000]}
            return {"error": f"Computer action failed: {type(exc).__name__}. Inspect the current page before retrying."}

    @app.get("/screen/{bot_id}")
    async def screen(bot_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", bot_id):
            raise HTTPException(400, "Invalid Bot ID")
        async with computer.browser_lock:
            page = await computer.page(bot_id)
            return Response(await page.screenshot(type="png"), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/files")
    async def files(path: str = "."):
        try:
            target = workspace_path(computer.root, path)
            if target.is_dir():
                return {"path": path, "files": [{"name": p.name, "directory": p.is_dir(), "size": p.stat().st_size if p.is_file() else 0}
                    for p in sorted(target.iterdir())[:300] if not p.is_symlink()]}
            return FileResponse(target, filename=target.name, media_type="application/octet-stream")
        except (ValueError, FileNotFoundError, NotADirectoryError):
            raise HTTPException(404, "File not found")

    @app.post("/cancel/{task_id}")
    async def cancel(task_id: str):
        await computer.cancel(task_id)
        return {"ok": True}

    return app

