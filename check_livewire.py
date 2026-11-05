import asyncio
import html
import json
import re
import httpx

async def main():
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as client:
        r = await client.get("https://anime3rb.com/episode/hunter-x-hunter-2011/1")
        snapshots = re.findall(r'wire:snapshot="([^"]+)"', r.text)
        for raw in snapshots:
            state = json.loads(html.unescape(raw))
            data = state.get("data", {})
            memo = state.get("memo", {})
            print("Component Name:", memo.get("name"))
            if "video_url" in data:
                print("video_url:", data.get("video_url"))
                print("data:", data)
                print("memo:", memo)

if __name__ == "__main__":
    asyncio.run(main())

