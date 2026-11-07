#!/usr/bin/env python3
"""ASR + 잔말/NG 판정 실측 — 결과를 눈으로 확인하기 위한 스크립트."""
from __future__ import annotations

import sys
from pathlib import Path

from agent.asr import transcribe
from agent.filler import find_fillers, find_ng, subtract
from agent.pipeline import content_hash
from agent.probe import US, probe
from agent.silence import SilenceParams, keep_ranges

src = Path(sys.argv[1])
info = probe(src)
key = content_hash(src)
print(f"입력 {info.name}  {info.duration_us/US:.1f}s  hash={key[:12]}", flush=True)

print("대본 추출 중...", flush=True)
tr = transcribe(src, key)
print(f"\n언어={tr.language}  세그먼트={len(tr.segments)}  단어={len(tr.words)}")
print(f"대본 {len(tr.script)}자\n")
print("─" * 70)
for s in tr.segments:
    print(f"{s.start/US:7.2f} {s.end/US:7.2f}  {s.text}")
print("─" * 70)

fillers = find_fillers(tr)
ngs = find_ng(tr)
print(f"\n잔말 {len(fillers)}건")
for c in fillers[:40]:
    print(f"  {c.start/US:7.2f}–{c.end/US:7.2f}  {c.text!r}")
print(f"\nNG {len(ngs)}건")
for c in ngs:
    print(f"  {c.start/US:7.2f}–{c.end/US:7.2f}  {c.text}")

keeps, sil = keep_ranges(info, SilenceParams())
after = subtract(keeps, fillers + ngs)
k0 = sum(e - s for s, e in keeps)
k1 = sum(e - s for s, e in after)
print(f"\n원본      {info.duration_us/US:7.2f}s")
print(f"무음컷후  {k0/US:7.2f}s  ({len(keeps)}구간)")
print(f"잔말NG후  {k1/US:7.2f}s  ({len(after)}구간)")
print(f"총 제거   {(info.duration_us-k1)/US:7.2f}s "
      f"({(info.duration_us-k1)/info.duration_us*100:.1f}%)")
