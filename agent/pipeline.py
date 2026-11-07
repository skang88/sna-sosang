"""처리 파이프라인 — 단계별 이벤트를 흘려보낸다.

단계는 STAGES 에 선언한다. 아직 구현되지 않은 단계는 enabled=False 로 두고
UI 에 '예정' 으로 표시한다 (있는 척하지 않는다).
  2단 현재: silence, draft  구현 / asr, filler  예정
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, AsyncIterator

from .asr import (MODEL, load_glossary, parse_glossary, transcribe,
                  transcribe_clip)
from .draft import build_draft
from .filler import (Cut, dedupe, find_fillers, find_ng, find_stumbles,
                     low_density, subtract)
from .probe import US, VideoInfo, probe
from .silence import (SilenceParams, keep_ranges, resolve_noise_db,
                      transcript_gaps)
from .subtitle import chunk, remap

MIN_STAGE_S = 0.5  # 캐시 hit 시에도 단계 전환이 보이도록 최소 지연

# ASR 은 numba 기반이라 동시 호출 시 segfault 위험 → 전역 직렬화
ASR_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    enabled: bool


STAGES: tuple[Stage, ...] = (
    Stage("silence", "무음 탐지", True),
    Stage("asr", "음성 인식", True),
    Stage("filler", "잔말·NG", True),
    Stage("draft", "드래프트 + 자막", True),
)


@dataclass
class Job:
    id: str
    src: Path
    name: str
    content_hash: str = ""
    glossary: list[str] = field(default_factory=list)
    aggressive: bool = False
    draft_name: str = ""          # 비우면 <파일명>_cut
    text_size: int = 21
    subtitle_y: float = -0.77
    bg_color: str | None = "#000000"
    bg_alpha: float = 0.70
    result: dict[str, Any] = field(default_factory=dict)


def content_hash(path: Path, chunk: int = 1 << 20) -> str:
    """앞/뒤 1MB + 크기로 해시. 전체 읽기보다 빠르고 업로드마다 안정적.

    mtime 기반 캐시는 업로드할 때마다 새 mtime 이라 매번 miss 가 난다.
    """
    h = hashlib.blake2b(digest_size=16)
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()


async def _paced(started: float) -> None:
    elapsed = time.monotonic() - started
    if elapsed < MIN_STAGE_S:
        await asyncio.sleep(MIN_STAGE_S - elapsed)


async def run(job: Job, params: SilenceParams | None = None
              ) -> AsyncIterator[dict[str, Any]]:
    """단계별 이벤트를 순서대로 방출한다."""
    params = params or SilenceParams()
    loop = asyncio.get_running_loop()

    yield {"type": "stages",
           "stages": [{"key": s.key, "label": s.label, "enabled": s.enabled}
                      for s in STAGES]}

    # --- 메타데이터 ---
    info: VideoInfo = await loop.run_in_executor(None, probe, job.src)
    job.content_hash = await loop.run_in_executor(None, content_hash, job.src)
    yield {"type": "meta", "meta": {
        "name": job.name, "width": info.width, "height": info.height,
        "fps": info.fps, "duration_us": info.duration_us,
        "has_audio": info.has_audio, "hash": job.content_hash[:12]}}

    # --- 무음 탐지 ---
    t0 = time.monotonic()
    yield {"type": "stage", "key": "silence", "status": "running"}
    noise_db = await loop.run_in_executor(None, resolve_noise_db, info, params)
    keeps, silences = await loop.run_in_executor(
        None, keep_ranges, info, params)
    await _paced(t0)
    kept_us = sum(e - s for s, e in keeps)
    removed_us = info.duration_us - kept_us
    yield {"type": "stage", "key": "silence", "status": "done",
           "detail": f"{noise_db:.0f}dB · 무음 {len(silences)}개 · "
                     f"제거 {removed_us / US:.0f}s"}

    # --- 음성 인식 (전체 대본 먼저) ---
    t0 = time.monotonic()
    yield {"type": "stage", "key": "asr", "status": "running"}
    if not info.has_audio:
        tr = None
        yield {"type": "stage", "key": "asr", "status": "skipped",
               "detail": "오디오 없음"}
    else:
        # numba 비안전 — 동시 호출 시 segfault. 전역 직렬화.
        # 파일 사전 + 이번 요청 사전 (요청분이 뒤에 와서 우선)
        gloss = load_glossary() + list(job.glossary)
        _, fixes = parse_glossary(gloss)
        async with ASR_LOCK:
            tr = await loop.run_in_executor(
                None, transcribe, job.src, job.content_hash, "ko",
                MODEL, gloss or None)
        await _paced(t0)
        yield {"type": "stage", "key": "asr", "status": "done",
               "detail": f"{len(tr.segments)}문장 · {len(tr.script)}자"}

    # --- 잔말 · NG (대본을 놓고 판단) ---
    t0 = time.monotonic()
    yield {"type": "stage", "key": "filler", "status": "running"}
    cuts: list = []
    flagged: list = []
    if tr is not None:
        fillers = find_fillers(tr, aggressive=job.aggressive)
        ngs = find_ng(tr)
        # 2차 통과 — 밀도 낮은 구간만 다시 인식해 헛시작을 찾는다.
        # numba 비안전이므로 1차와 같은 락 안에서 돌린다.
        async with ASR_LOCK:
            stumbles = await loop.run_in_executor(
                None, find_stumbles, tr, job.src, transcribe_clip)
        cuts = dedupe(fillers + ngs + stumbles)
        # 음량으로 못 잡는 문장 사이 공백(조용한 녹음)을 대본 기준으로 보충한다
        gaps = await loop.run_in_executor(
            None, transcript_gaps, info,
            [(s.start, s.end) for s in tr.segments], params)
        keeps = subtract(keeps, cuts + [Cut(a, b, "gap", "") for a, b in gaps])
        # 밀도는 낮은데 자를 근거를 못 찾은 구간 → 사람이 볼 수 있게 표시만
        cut_starts = {c.start for c in cuts}
        flagged = [s for s in low_density(tr) if s.start not in cut_starts]
        await _paced(t0)
        yield {"type": "stage", "key": "filler", "status": "done",
               "detail": f"잔말 {len(fillers)} · NG {len(ngs) + len(stumbles)}"
                         f" · 공백 {len(gaps)} · 확인필요 {len(flagged)}"}
    else:
        yield {"type": "stage", "key": "filler", "status": "skipped",
               "detail": "대본 없음"}

    # --- 자막 (컷된 타임라인으로 시간 재매핑) ---
    # fixes 를 넘겨 두 단어에 걸친 치환(예: "결혼 아동")도 최종 줄에서 잡는다
    cues = remap(chunk(tr, fixes=fixes), keeps) if tr is not None else []

    # --- 드래프트 생성 ---
    t0 = time.monotonic()
    yield {"type": "stage", "key": "draft", "status": "running"}
    # 사용자가 CapCut 에서 프로젝트 이름을 바꿨으면 그 이름 그대로 덮어써야
    # 새 드래프트가 따로 생기지 않는다. 지정이 없으면 <파일명>_cut.
    draft_name = job.draft_name or f"{Path(job.name).stem}_cut"
    folder = await loop.run_in_executor(
        None, partial(build_draft, info, keeps, draft_name, cues=cues,
                      subtitle_y=job.subtitle_y, text_size=job.text_size,
                      bg_color=job.bg_color, bg_alpha=job.bg_alpha))
    await _paced(t0)
    yield {"type": "stage", "key": "draft", "status": "done",
           "detail": f"{draft_name} · 자막 {len(cues)}"}

    kept_us = sum(e - s for s, e in keeps)
    removed_us = info.duration_us - kept_us
    job.result = {
        "draft_name": draft_name,
        "draft_path": str(folder),
        "segments": len(keeps),
        "silences": len(silences),
        "source_us": info.duration_us,
        "kept_us": kept_us,
        "removed_us": removed_us,
        "removed_pct": round(removed_us / info.duration_us * 100, 1)
        if info.duration_us else 0.0,
        "keeps": [{"start": s / US, "end": e / US} for s, e in keeps],
        "script": tr.script if tr else "",
        "cues": [{"text": c.text, "start": c.start / US, "end": c.end / US}
                 for c in cues],
        "cuts": [{"reason": c.reason, "text": c.text,
                  "start": c.start / US, "end": c.end / US} for c in cuts],
        "flagged": [{"text": s.text, "start": s.start / US, "end": s.end / US}
                    for s in flagged],
    }
    yield {"type": "done", "result": job.result}
