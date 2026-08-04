#!/usr/bin/env python3
"""截图 llm-arena 三页面 → docs/screenshots/"""
import asyncio, os, sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000"
OUT = os.path.expanduser("~/projects/llm-arena/docs/screenshots")
os.makedirs(OUT, exist_ok=True)

PAGES = [
    ("index", "/"),
    ("leaderboard", "/leaderboard"),
    ("history", "/history"),
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        for name, path in PAGES:
            await page.goto(BASE + path, wait_until="networkidle")
            await page.wait_for_timeout(800)
            await page.screenshot(path=os.path.join(OUT, f"{name}.png"))
            print(f"OK {name}.png")
        await browser.close()

asyncio.run(main())
