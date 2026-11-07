#!/usr/bin/env python3
"""로컬 웹 서버 — 드래그 앤 드롭 → SSE 진행 표시 → 캡컷 드래프트.

    .venv/bin/uvicorn server:app --port 8420
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from agent.pipeline import Job, run
from agent.silence import SilenceParams

BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
UPLOADS.mkdir(exist_ok=True)

ALLOWED = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}

app = FastAPI(title="캡컷 에이전트")
JOBS: dict[str, Job] = {}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE / "static" / "index.html")


@app.post("/api/jobs")
async def create_job(file: UploadFile) -> dict[str, str]:
    name = Path(file.filename or "video.mp4").name
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(400, f"지원하지 않는 형식: {Path(name).suffix}")

    job_id = uuid.uuid4().hex[:12]
    dest_dir = UPLOADS / job_id
    dest_dir.mkdir(parents=True)
    dest = dest_dir / name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    JOBS[job_id] = Job(id=job_id, src=dest, name=name)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str, cut: float = 9.0,
                 min_silence: float = 0.40, pad: float = 0.10,
                 glossary: str = "", aggressive: str = "",
                 size: int = 21, ypos: float = -0.77,
                 bg: str = "", bgalpha: float = 0.70):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "존재하지 않는 작업")

    job.glossary = [w.strip() for w in glossary.split(",") if w.strip()]
    job.aggressive = aggressive == "1"
    job.text_size = max(8, min(60, size))
    job.subtitle_y = max(-1.0, min(1.0, ypos))
    job.bg_color = None if bg == "off" else "#000000"
    job.bg_alpha = max(0.0, min(1.0, bgalpha))
    # cut = 컷 강도. 영상 평균음량 아래로 이만큼(dB)을 무음으로 본다.
    # 높을수록 확실한 침묵만, 낮을수록 공격적으로 자른다. (적응식 — 고정 dB 아님)
    params = SilenceParams(noise_offset_db=max(3.0, min(15.0, cut)),
                           min_silence_s=min_silence, pad_s=pad)

    async def gen():
        try:
            async for event in run(job, params):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # 실패를 삼키지 않고 그대로 UI 로 보낸다
            payload = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/reveal")
async def reveal(payload: dict) -> dict[str, bool]:
    """Finder 에서 드래프트 폴더 열기."""
    import subprocess
    path = Path(payload.get("path", ""))
    if not path.exists():
        raise HTTPException(404, "경로 없음")
    subprocess.run(["open", "-R", str(path)], check=False)
    return {"ok": True}
