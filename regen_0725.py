#!/usr/bin/env python3
"""0725_cut 재생성 — 새 사전 + 새 청킹 + 커버.

CapCut 이 꺼진 뒤에 실행해야 한다 (앱이 draft_info.json 을 덮어쓰므로).
"""
import asyncio
from pathlib import Path

from agent.pipeline import Job, run

SRC = Path("/Users/dd/capcut/uploads/6773c349e944/0725.mp4")


async def main():
    job = Job(id="regen", src=SRC, name="0725.mp4")
    last = None
    async for ev in run(job):
        if ev["type"] == "stage" and ev["status"] == "done":
            print(f"  [{ev['key']}] {ev.get('detail','')}", flush=True)
        elif ev["type"] == "error":
            print("ERROR", ev["message"], flush=True)
        elif ev["type"] == "done":
            last = ev["result"]
    if last:
        print(f"\n완료: {last['draft_name']}  자막 {len(last['cues'])}줄  "
              f"{last['kept_us']/1e6:.0f}s", flush=True)


asyncio.run(main())
