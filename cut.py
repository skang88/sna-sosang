#!/usr/bin/env python3
"""1단 CLI — 무음 컷 드래프트 생성.

    python3 cut.py <영상> [-n 이름] [--noise -32] [--min-silence 0.45]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.draft import build_draft
from agent.probe import US, probe
from agent.silence import SilenceParams, keep_ranges


def fmt(us: int) -> str:
    s = us / US
    return f"{int(s // 60):d}:{s % 60:05.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-n", "--name", help="드래프트 이름 (기본: 파일명_cut)")
    ap.add_argument("--noise", type=float, default=-32.0)
    ap.add_argument("--min-silence", type=float, default=0.45)
    ap.add_argument("--pad", type=float, default=0.10)
    args = ap.parse_args()

    info = probe(args.video)
    print(f"입력   {info.name}  {info.width}x{info.height} "
          f"{info.fps}fps  {fmt(info.duration_us)}  "
          f"오디오={'있음' if info.has_audio else '없음'}")

    params = SilenceParams(noise_db=args.noise,
                           min_silence_s=args.min_silence,
                           pad_s=args.pad)
    keeps, silences = keep_ranges(info, params)

    total = sum(e - s for s, e in keeps)
    removed = info.duration_us - total
    print(f"무음   {len(silences)}개 탐지")
    print(f"보존   {len(keeps)}개 구간  {fmt(total)}")
    print(f"제거   {fmt(removed)}  ({removed / info.duration_us * 100:.1f}%)")
    for i, (s, e) in enumerate(keeps, 1):
        print(f"  {i:>2}. {fmt(s)} → {fmt(e)}   ({(e - s) / US:.2f}s)")

    name = args.name or f"{Path(info.name).stem}_cut"
    folder = build_draft(info, keeps, name)
    print(f"\n드래프트 {folder}")
    print("→ CapCut 을 열어 이 프로젝트를 재생해서 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
