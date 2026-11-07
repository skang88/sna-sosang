"""무음 구간 탐지 → 보존(keep) 구간 계산.

ffmpeg silencedetect 로 무음을 찾고, 뒤집어서 "남길 구간"을 만든다.
전부 마이크로초 단위.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .probe import US, VideoInfo

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
_MEAN = re.compile(r"mean_volume:\s*(-?[\d.]+)")


@dataclass
class SilenceParams:
    # ★ 고정 임계값은 영상마다 녹음 환경이 달라 안 통한다. 조용한 실내는 -32dB 로
    #   충분하지만 시끄러운 야외는 소음 바닥이 높아 아무것도 안 잡힌다(실측: mean -18.6dB
    #   영상에서 -32dB → 무음 1개). 그래서 noise_db 를 직접 쓰지 않고, 영상의 평균
    #   음량에서 offset 만큼 아래를 무음 바닥으로 잡는다 (적응식).
    noise_offset_db: float = 9.0  # mean_volume 아래로 이만큼을 무음 경계로
    noise_db: float | None = None  # 지정하면 적응식 대신 이 고정값 사용
    min_silence_s: float = 0.40   # 이보다 짧은 무음은 무시
    pad_s: float = 0.10           # 보존 구간 앞뒤 여유 (말 잘림 방지)
    min_keep_s: float = 0.30      # 이보다 짧은 보존 구간은 버림
    # ★ 말이 끝난 뒤 고개 숙여 인사하는 장면은 소리가 없어도 살려야 한다.
    #   끝쪽 무음은 자르지 않고 영상 끝까지 보존한다. (앞쪽은 말 시작 전 빈 공간이라
    #   잘라내는 편이 낫다 — 그래서 뒤에만 적용한다.)
    keep_tail: bool = True
    # 대본상 문장 사이 공백을 자를 때, 그 구간이 말소리보다 이만큼(dB) 이상 작아야 한다.
    # 조용하지 않으면 대본에 안 적힌 말(웅얼거림 등)이 있을 수 있어 자르지 않는다.
    gap_drop_db: float = 6.0


def mean_volume_db(path: Path) -> float | None:
    """volumedetect 로 평균 음량(dB)을 잰다. 없으면 None."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = _MEAN.search(proc.stderr)
    return float(m.group(1)) if m else None


def resolve_noise_db(video: VideoInfo, p: SilenceParams) -> float:
    """이 영상에 쓸 무음 임계값(dB)을 정한다."""
    if p.noise_db is not None:
        return p.noise_db
    mean = mean_volume_db(video.path)
    if mean is None:
        return -32.0                      # 못 재면 보수적 기본값
    # 적응식: 평균에서 offset 아래. 너무 높거나 낮지 않게 가둔다.
    return max(-40.0, min(-18.0, mean - p.noise_offset_db))


def detect_silences(video: VideoInfo, p: SilenceParams) -> list[tuple[int, int]]:
    """무음 구간 [(start_us, end_us)] 반환."""
    if not video.has_audio:
        return []

    noise_db = resolve_noise_db(video, p)
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video.path),
         "-af", f"silencedetect=noise={noise_db}dB:d={p.min_silence_s}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    log = proc.stderr

    silences: list[tuple[int, int]] = []
    pending: float | None = None
    for line in log.splitlines():
        if m := _START.search(line):
            pending = max(0.0, float(m.group(1)))
        if m := _END.search(line):
            end = float(m.group(1))
            start = pending if pending is not None else 0.0
            if end > start:
                silences.append((int(start * US), int(end * US)))
            pending = None
    # 파일 끝까지 무음이 이어진 경우 silence_end 가 안 나온다
    if pending is not None:
        silences.append((int(pending * US), video.duration_us))

    return silences


def invert(silences: list[tuple[int, int]], total_us: int) -> list[tuple[int, int]]:
    """무음의 여집합 = 발화 구간."""
    keeps: list[tuple[int, int]] = []
    cursor = 0
    for s, e in sorted(silences):
        if s > cursor:
            keeps.append((cursor, min(s, total_us)))
        cursor = max(cursor, e)
    if cursor < total_us:
        keeps.append((cursor, total_us))
    return [(s, e) for s, e in keeps if e > s]


def _pad_and_merge(keeps: list[tuple[int, int]], pad_us: int,
                   total_us: int) -> list[tuple[int, int]]:
    padded = [(max(0, s - pad_us), min(total_us, e + pad_us)) for s, e in keeps]
    merged: list[tuple[int, int]] = []
    for s, e in padded:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def keep_ranges(video: VideoInfo,
                p: SilenceParams | None = None) -> tuple[list[tuple[int, int]],
                                                         list[tuple[int, int]]]:
    """(보존 구간, 탐지된 무음 구간) 반환.

    오디오가 없거나 무음이 없으면 영상 전체를 한 덩어리로 보존한다.
    """
    p = p or SilenceParams()
    silences = detect_silences(video, p)
    if not silences:
        return [(0, video.duration_us)], []

    keeps = invert(silences, video.duration_us)
    keeps = _pad_and_merge(keeps, int(p.pad_s * US), video.duration_us)
    min_keep_us = int(p.min_keep_s * US)
    keeps = [(s, e) for s, e in keeps if e - s >= min_keep_us]

    if not keeps:  # 전부 걸러졌으면 원본 유지가 안전
        return [(0, video.duration_us)], silences

    # 끝인사(무음이지만 화면엔 남아야 하는 장면)를 살린다
    if p.keep_tail and keeps[-1][1] < video.duration_us:
        keeps[-1] = (keeps[-1][0], video.duration_us)
    return keeps, silences


def loudness_db(path: Path, win_s: float = 0.05):
    """50ms 창 단위 RMS 음량(dB) 배열."""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = int(16000 * win_s)
    if len(x) < n:
        return np.array([])
    x = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((x ** 2).mean(1)) + 1e-9)


def transcript_gaps(video: VideoInfo, spans: list[tuple[int, int]],
                    p: SilenceParams | None = None) -> list[tuple[int, int]]:
    """대본상 문장 사이 공백 중 실제로 조용한 곳 → 자를 구간.

    ★ 음량 기반 탐지(silencedetect)는 조용하게 녹음된 영상에서 무너진다.
      실측: 평균 -30.4dB 영상에서 무음 0개 → 컷 0개. 말소리와 배경 소음의
      간격이 18dB 밖에 안 돼 어떤 기준으로도 공백이 안 갈라진다.
      반면 whisper 세그먼트 사이 간격은 녹음 크기와 무관하게 정확하다.

    단, 대본에 안 적힌 말(웅얼거림·흘린 말)이 공백에 숨어 있을 수 있으므로
    그 구간이 말소리보다 gap_drop_db 이상 작을 때만 자른다.
    말은 세그먼트 안에 있으므로, 세그먼트 사이만 자르면 말을 건드리지 않는다.
    """
    p = p or SilenceParams()
    spans = sorted(spans)
    if not video.has_audio or len(spans) < 2:
        return []

    import numpy as np

    d = loudness_db(video.path)
    if d.size == 0:
        return []
    win_us = 50_000

    def level(a: int, b: int) -> float:
        i = a // win_us
        seg = d[i: max(i + 1, b // win_us)]
        return float(np.median(seg)) if seg.size else 0.0

    speech = float(np.median([level(s, e) for s, e in spans]))
    pad = int(p.pad_s * US)
    out: list[tuple[int, int]] = []
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if start - end < p.min_silence_s * US:
            continue
        if level(end, start) > speech - p.gap_drop_db:
            continue                          # 조용하지 않음 → 말이 있을 수 있다
        a, b = end + pad, start - pad         # 앞뒤 여유를 남겨 말끝·말머리 보호
        if b - a >= 100_000:
            out.append((a, b))
    return out
