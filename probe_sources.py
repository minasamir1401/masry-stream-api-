import asyncio
import httpx

async def main():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        # Test mega embed
        r_mega = await client.get("https://mega.nz/embed/0nBBSACJ#yD8e6Lgc7V-qdlP64GYY8kllea3b50bBKvBiZ3OUqoM", follow_redirects=True)
        print("Mega embed status:", r_mega.status_code)
        
        # Test vadbam embed
        r_vadbam = await client.get("https://vadbam.com/embed-a8tfc6jcpwlp.html", follow_redirects=True)
        print("Vadbam embed status:", r_vadbam.status_code)

        # Test share4max embed
        r_s4m = await client.get("https://share4max.com/iframe/PnPYiGc1SJj4T", follow_redirects=True)
        print("Share4Max status:", r_s4m.status_code, "Final URL:", r_s4m.url)

if __name__ == "__main__":
    asyncio.run(main())
