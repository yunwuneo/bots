"""Check the local deployment using its .env without printing credentials or making model calls."""
import asyncio
from pathlib import Path

import httpx


async def main():
    root = Path(__file__).resolve().parent.parent
    values = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines() if line and not line.startswith("#") and "=" in line)
    url = "http://127.0.0.1:" + values.get("NEOBOTS_PORT", "18787")
    async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=60) as client:
        login = await client.post("/api/login", json={"password": values["NEOBOTS_PASSWORD"]})
        login.raise_for_status()
        state = (await client.get("/api/state")).json()
        health = (await client.get("/api/health")).json()
        assert health["computer"], "Computer service is unavailable"
        bot_id = state["bots"][0]["id"]
        result = await client.post(f"/api/computer/{bot_id}/action", json={"action": "navigate", "url": "https://example.com"})
        result.raise_for_status()
        page = result.json()
        assert page.get("title") == "Example Domain", page.get("error", "Unexpected browser page")
        screenshot = await client.get(f"/api/computer/{bot_id}/screen")
        screenshot.raise_for_status()
        assert screenshot.content.startswith(b"\x89PNG"), "Expected a PNG screenshot"
        Path("/tmp/neobots-computer.png").write_bytes(screenshot.content)
        files = await client.get("/api/files")
        files.raise_for_status()
        assert "files" in files.json()
        print("Deployment passed: authenticated UI/API, computer health, real Chromium navigation, screenshot, shared workspace access.")
        print("Address:", url)
        print("Model:", state["model"]["name"], "| credentials configured:", state["model"]["configured"])


if __name__ == "__main__":
    asyncio.run(main())
