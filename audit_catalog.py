"""Resumable catalog inventory and real-media checks, with explicit coverage.

Examples:
  python audit_catalog.py --inventory-only
  python audit_catalog.py --ids-file ../.runtime/known-anime.json --all-episodes 21459 1535
  python audit_catalog.py                 # every inventoried title, first episode
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import httpx

from app.services.animetna import playable_hls


def checkpoint_rows(path):
    if not path.exists():
        return []
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    rows = []
    for index, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except (ValueError, UnicodeError):
            if index != len(lines) - 1 or line.endswith(b"\n"):
                raise
            # An interrupted write must not lose the completed scan or join
            # a partial JSON record to the next result on resume.
            path.write_bytes(b"".join(lines[:index]))
            print(f"Recovered checkpoint: retained {len(rows)} complete results", flush=True)
    return rows


async def inventory(client, base, output):
    path = output / "inventory.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"pages": {}, "items": {}}
    page, pages = 1, 1
    while page <= pages:
        data = saved["pages"].get(str(page))
        if data is None:
            for attempt in range(5):
                try:
                    response = await client.get(f"{base}/api/anime/catalog", params={"page": page, "page_size": 50})
                    response.raise_for_status()
                    data = response.json()
                    break
                except httpx.HTTPError:
                    if attempt == 4:
                        raise
                    await asyncio.sleep(min(30, 3 * 2 ** attempt))
            saved["pages"][str(page)] = {key: value for key, value in data.items() if key != "items"}
            for item in data.get("items", []):
                saved["items"][str(item["id"])] = {key: item.get(key) for key in (
                    "id", "id_mal", "title", "total_episodes", "status", "media_type")}
            path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            await asyncio.sleep(1.2)
        pages = int(data.get("total_pages", 1))
        declared_total = saved["pages"].get("1", {}).get("total")
        if declared_total:
            pages = min(pages, math.ceil(declared_total / 50))
        if page % 10 == 0 or page == pages:
            print(f"INVENTORY {page}/{pages} pages; {len(saved['items'])} titles", flush=True)
        if not data.get("has_next_page", page < pages):
            break
        page += 1
    saved["completed_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return saved["items"]


async def check(client, base, item, episode):
    started = time.monotonic()
    row = {"anime_id": item["id"], "title": item.get("title"), "episode": episode,
           "checked_at": datetime.now(timezone.utc).isoformat(), "status": "unverified"}
    try:
        response = await client.get(f"{base}/api/anime/{item['id']}/stream/{episode}?refresh=true")
        response.raise_for_status()
        data = response.json()
        row.update(diagnostics=data.get("diagnostics", {}), servers=len(data.get("servers", [])))
        if not data.get("servers"):
            row["status"] = "no_playable_source"
        else:
            for server in data["servers"]:
                try:
                    if server["type"] == "hls":
                        valid = await playable_hls(client, server["url"])
                    else:
                        url = server.get("playback_url", "")
                        if not url.startswith(f"/api/anime/{item['id']}/video/{episode}?"):
                            continue
                        sample = await client.get(base + url, headers={"Range": "bytes=0-63"})
                        valid = sample.status_code == 206 and sample.content[4:8] == b"ftyp"
                    if valid:
                        row.update(status="media_verified", source=server["name"], source_type=server["type"])
                        break
                except (httpx.HTTPError, ValueError):
                    continue
    except Exception as error:
        row.update(status="request_failed", error=type(error).__name__)
    row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return row


async def main(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=65) as client:
        if args.ids_file:
            values = json.loads(Path(args.ids_file).read_text(encoding="utf-8"))
            items = {str(item["id"]): item for item in (values.values() if isinstance(values, dict) else values)}
        else:
            items = await inventory(client, args.base, output)
        if args.inventory_only:
            return
        for anime_id in args.all_episodes:
            response = await client.get(f"{args.base}/api/anime/{anime_id}")
            response.raise_for_status()
            items[str(anime_id)] = response.json()
        path = output / "results.jsonl"
        prior = checkpoint_rows(path)
        latest = {(row["anime_id"], row["episode"]): row for row in prior}
        prior = list(latest.values())
        done = {key for key, row in latest.items() if not args.retry_failed or row["status"] == "media_verified"}
        jobs = []
        for item in items.values():
            if item.get("status") == "NOT_YET_RELEASED":
                continue
            count = (item.get("total_episodes") or 1) if item["id"] in args.all_episodes else 1
            jobs.extend((item, episode) for episode in range(1, count + 1) if (item["id"], episode) not in done)
        semaphore = asyncio.Semaphore(args.concurrency)
        results = latest.copy()
        checked = 0
        async def run(item, episode):
            nonlocal checked
            async with semaphore:
                row = await check(client, args.base, item, episode)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                results[(item["id"], episode)] = row
                checked += 1
                print(f"CHECK {checked}/{len(jobs)} {item['id']}:{episode} {row['status']}", flush=True)
                return row
        await asyncio.gather(*(run(item, episode) for item, episode in jobs))
        results = list(results.values())
        (output / "latest-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {"completed_at": datetime.now(timezone.utc).isoformat(), "inventoried_titles": len(items),
                   "checked_titles": len({row["anime_id"] for row in results}), "checked_episodes": len(results),
                   "statuses": dict(Counter(row["status"] for row in results)),
                   "coverage": "First episode per title; every episode only for explicitly selected series. Media bytes checked; browser decoding is a separate check."}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="data/playback_audit")
    parser.add_argument("--ids-file")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--all-episodes", type=int, nargs="*", default=[])
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=3)
    asyncio.run(main(parser.parse_args()))
