#!/usr/bin/env python3
"""드래프트 재생성 — 현재 코드/사전으로 다시 만든다.

    python3 regen.py <소스 영상 경로> [드래프트 이름]

드래프트 이름을 주면 그 이름으로 덮어쓴다. CapCut 에서 프로젝트 이름을 바꿨을 때
새 드래프트가 따로 생기지 않게 하려면 바꾼 이름을 그대로 넘긴다.

CapCut 이 꺼진 뒤에 실행해야 한다 (앱이 draft_info.json 을 덮어쓰므로).
"""
import asyncio
import sys
from pathlib import Path

from agent.pipeline import Job, run


async def main(src: Path, draft_name: str | None = None):
    job = Job(id="regen", src=src, name=src.name,
              draft_name=draft_name or "")
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
              f"{last['kept_us']/1e6:.0f}s (원본 {last['source_us']/1e6:.0f}s)",
              flush=True)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]),
                     sys.argv[2] if len(sys.argv) > 2 else None))
