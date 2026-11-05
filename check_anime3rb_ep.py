import asyncio
import html
import json
import re
import httpx
from urllib.parse import urlsplit

async def main():
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
        r = await client.get("https://anime3rb.com/episode/hunter-x-hunter-2011/1")
        print("Status code:", r.status_code)
        
        # 1. Wire snapshots check
        snapshots = re.findall(r'wire:snapshot="([^"]+)"', r.text)
        print("Wire snapshots count:", len(snapshots))
        for raw in snapshots:
            try:
                state = json.loads(html.unescape(raw))
                data = state.get("data", {}) if isinstance(state, dict) else {}
                if isinstance(data, dict):
                    print("Snapshot data keys:", list(data.keys()))
                    if "video_url" in data:
                        print("FOUND video_url:", data.get("video_url"))
                    if "video_number" in data:
                        print("FOUND video_number:", data.get("video_number"))
                    if "videos" in data:
                        print("FOUND videos:", data.get("videos"))
                    if "servers" in data:
                        print("FOUND servers:", data.get("servers"))
            except Exception as e:
                print("Snapshot parse error:", e)

        embeds = re.findall(r"https://anime3rb\.com/embed/[^\s\"'<>]+", r.text)
        print("Embeds found:", embeds)
        for emb in embeds:
            r_emb = await client.get(emb)
            print("Embed status:", r_emb.status_code)
            print("Embed content snippet:", r_emb.text[:400])

if __name__ == "__main__":
    asyncio.run(main())
