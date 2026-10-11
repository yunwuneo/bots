"""Exercise the UI with a scripted model and an isolated temporary database.

No model API requests are made and no user data is modified.
"""
import asyncio
import json
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from playwright.async_api import async_playwright

from neobots.app import create_app
from neobots.config import Config


async def main():
    with tempfile.TemporaryDirectory(prefix="neobots-smoke-") as directory:
        app = create_app(Config(data_dir=Path(directory), password="smoke-test-password",
            api_key="test-only", model="scripted-test-model", model_url="https://unused.invalid/v1"))
        runtime = app.state.runtime

        async def complete(messages):
            if messages[-1]["role"] == "tool":
                return {"role": "assistant", "content": "自动化验证完成：审批后继续了同一个任务。"}
            return {"role": "assistant", "content": "准备执行测试命令，请审批。", "tool_calls": [{"id": "smoke_call", "type": "function",
                "function": {"name": "run_command", "arguments": json.dumps({"command": "printf smoke-test"})}}]}

        async def computer(task, name, args):
            return {"exit_code": 0, "output": "smoke-test (scripted computer for UI verification)"}

        runtime.complete, runtime.computer = complete, computer
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            assert server.started, "Test server did not start"
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(f"http://127.0.0.1:{port}")
                await page.locator('#login input').fill('smoke-test-password')
                await page.locator('#login button[type=submit]').click()
                await page.locator('.welcome h2').wait_for()
                await page.screenshot(path='/tmp/neobots-desktop.png', full_page=True, animations='disabled')
                await page.locator('[data-action="new-bot"]').click()
                await page.locator('#bot-form input[name=name]').fill('审阅员')
                await page.locator('#bot-form textarea[name=role]').fill('审阅研究报告并列出来源。')
                await page.locator('#bot-form textarea[name=memory]').fill('用户喜欢简短结论。')
                await page.locator('#bot-form button[type=submit]').click()
                await page.locator('.topbar h1').filter(has_text='审阅员').wait_for()
                await page.locator('#prompt').fill('执行一个测试命令')
                await page.locator('#send').click()
                await page.locator('.badge.awaiting_approval').wait_for(timeout=15000)
                await page.locator('nav [data-view=approvals]').click()
                await page.locator('[data-decision=approved]').click()
                await page.locator('.bot-item.selected').count()
                await page.locator('.bot-item').filter(has_text='审阅员').click()
                await page.locator('.message.assistant').filter(has_text='自动化验证完成：审批后继续了同一个任务。').wait_for(timeout=15000)
                await page.locator('nav [data-view=skills]').click()
                await page.locator('[data-action=new-skill]').click()
                await page.locator('#skill-form input[name=name]').fill('研究规范')
                await page.locator('#skill-form textarea').fill('查阅来源，核实结论，保存报告。')
                await page.locator('#skill-form button[type=submit]').click()
                await page.locator('.card h3').filter(has_text='研究规范').wait_for()
                await page.locator('nav [data-view=routines]').click()
                await page.locator('[data-action=new-routine]').click()
                await page.locator('#routine-form input[name=name]').fill('定期审阅')
                await page.locator('#routine-form textarea').fill('审阅共享目录的报告。')
                await page.locator('#routine-form button[type=submit]').click()
                await page.locator('[data-routine]').click()
                await page.locator('.badge').filter(has_text='已暂停').wait_for()
                await page.locator('.bot-item').filter(has_text='Neo').click()
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(path='/tmp/neobots-mobile.png', full_page=True, animations='disabled')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
                await page.locator('[data-action=menu]').click()
                await page.locator('nav [data-view=tasks]').click()
                await page.locator('.card [data-task]').first.click()
                await page.locator('#dialog .event').first.wait_for()
                assert not errors, errors
                assert app.state.store.one("SELECT memory FROM bots WHERE name='审阅员'")["memory"] == '用户喜欢简短结论。'
                assert app.state.store.one("SELECT status FROM tasks")["status"] == 'completed'
                await browser.close()
                print('Browser smoke passed: login, Bot + memory, task, approval/resume, skills, routine pause, task details, mobile layout; no JS errors.')
                print('Screenshots: /tmp/neobots-desktop.png /tmp/neobots-mobile.png')
        finally:
            server.should_exit = True
            await serving


if __name__ == "__main__":
    asyncio.run(main())
