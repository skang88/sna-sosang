"""잔말(filler)·NG(리테이크) 판정.

★ 원칙: 전체 대본을 놓고 판단한다.
  - 잔말: 어휘가 아닌 망설임 소리만 기본 제거. '그/저/뭐/좀' 같이 실제 뜻이 있는
    말은 기본적으로 건드리지 않는다 (오검출 피해가 미검출보다 크다).
  - NG: 같은 문장을 다시 말한 경우. 앞선 시도를 버리고 뒤엣것을 남긴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .asr import Transcript

# 1군 — 어휘가 아닌 망설임 소리. 단독으로 나오면 거의 확실히 잔말.
HESITATION = {
    "어", "음", "으", "엄", "에", "아", "우", "흠", "허", "은",
    "어어", "음음", "으음", "어음", "아아", "에에",
}

# 2군 — 실제 뜻이 있어 문맥에 따라 갈린다. aggressive=True 일 때만 대상.
DISCOURSE = {
    "그", "저", "뭐", "막", "좀", "약간", "이제", "인제",
    "그니까", "그러니까", "뭐지", "뭐랄까", "그와중에",
}

_PUNCT = re.compile(r"[\s.,!?…·\"'’”\-~]+")
_ELONG = re.compile(r"^(어|음|으|아|에)\1*$")  # 어어어, 음음음


def norm(text: str) -> str:
    return _PUNCT.sub("", text)


@dataclass
class Cut:
    start: int
    end: int
    reason: str   # "filler" | "ng"
    text: str


def _is_filler(word: str, aggressive: bool) -> bool:
    w = norm(word)
    if not w:
        return False
    if w in HESITATION or _ELONG.match(w):
        return True
    return aggressive and w in DISCOURSE


def find_fillers(tr: Transcript, aggressive: bool = False,
                 pad_us: int = 20_000) -> list[Cut]:
    """단독으로 서 있는 망설임 소리를 찾는다.

    한 세그먼트가 통째로 잔말이면 세그먼트째 잘라낸다.
    """
    cuts: list[Cut] = []
    for seg in tr.segments:
        if not seg.words:
            # 단어 타임스탬프가 없으면 세그먼트 전체로 판단
            if _is_filler(seg.text, aggressive):
                cuts.append(Cut(seg.start, seg.end, "filler", seg.text.strip()))
            continue

        content = [w for w in seg.words if not _is_filler(w.text, aggressive)]
        if not content:  # 세그먼트 전체가 잔말
            cuts.append(Cut(seg.start, seg.end, "filler", seg.text.strip()))
            continue

        for w in seg.words:
            if _is_filler(w.text, aggressive) and w.end > w.start:
                cuts.append(Cut(max(seg.start, w.start - pad_us),
                                min(seg.end, w.end + pad_us),
                                "filler", norm(w.text)))
    return cuts


def _common_prefix(a: str, b: str) -> int:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def find_ng(tr: Transcript, threshold: float = 0.75, min_chars: int = 6,
            min_prefix: int = 4, window: int = 2) -> list[Cut]:
    """같은 말을 다시 한 구간(리테이크)에서 앞선 시도를 버린다.

    두 가지 신호를 본다.
      1) 전체 유사도가 높다 → 거의 같은 말을 반복
      2) 앞머리가 겹친다 → 말하다 끊고 같은 말로 다시 시작

    ★ 2번은 **실제 공통 접두 길이**를 재야 한다. 고정 폭으로 잘라 비교하면
      한 글자 차이로 놓친다 (실측: '다음세대를세워가는일을' vs
      '다음세대를변화시키는일에…' 은 공통 5자인데 6자로 검사해 실패).
    """
    cuts: list[Cut] = []
    segs = tr.segments
    dropped: set[int] = set()

    for i, seg in enumerate(segs):
        if i in dropped:
            continue
        a = norm(seg.text)
        if len(a) < min_chars:
            continue
        for j in range(i + 1, min(i + 1 + window, len(segs))):
            if j in dropped:
                continue
            b = norm(segs[j].text)
            if len(b) < min_chars:
                continue
            ratio = SequenceMatcher(None, a, b).ratio()
            lcp = _common_prefix(a, b)
            # 다시 시작: 앞머리가 충분히 겹치고, 뒤엣것이 더 완성돼 있다
            restart = lcp >= min_prefix and len(b) >= len(a)
            if ratio >= threshold or restart:
                dropped.add(i)
                why = "반복" if ratio >= threshold else f"재시작 {lcp}자"
                cuts.append(Cut(seg.start, seg.end, "ng",
                                f"{seg.text.strip()}  [{why}]"))
                break
    return cuts


def density(seg) -> float:
    """글자/초. 낮으면 말은 오래 했는데 내용이 적다 = 버벅였을 가능성."""
    dur = (seg.end - seg.start) / 1e6
    return len(norm(seg.text)) / dur if dur > 0 else 0.0


def low_density(tr: Transcript, ratio: float = 0.62,
                min_dur_s: float = 1.5) -> list:
    """발화 밀도가 중앙값 대비 낮은 세그먼트.

    ★ 실측에서 이 지표가 버벅임을 4/4 로 골라냈다. 대본에는 흔적이 없는데도
      찾아낸다 — whisper 가 말더듬을 정리해도 **세그먼트 길이는 실제 오디오**라서다.
      (단어 타임스탬프는 세그먼트를 빈틈없이 채우도록 늘어나 있어 쓸모없다.)
    """
    rows = [(density(s), s) for s in tr.segments
            if (s.end - s.start) / 1e6 >= min_dur_s]
    if len(rows) < 5:
        return []
    med = sorted(d for d, _ in rows)[len(rows) // 2]
    return [s for d, s in rows if d < med * ratio]


def find_false_starts(words: list, min_run: int = 2,
                      threshold: float = 0.7) -> tuple[int, str] | None:
    """헛시작 뒤 '진짜 발화가 시작되는 지점'을 찾는다.

    뒤쪽 어절 묶음과 비슷한 묶음이 앞에 또 있으면, 앞엣것은 실패한 시도다.
    가장 늦은 재시작 지점을 택해 그 앞을 통째로 버린다.

        다른 세대, | 다음 세대, | 다음 세대를 세워가는 일을
                                 ↑ 여기부터가 진짜
    """
    if len(words) < min_run * 2:
        return None
    texts = [norm(w.text) for w in words]

    best: tuple[int, str] | None = None
    for k in range(1, len(words) - min_run + 1):
        tail = "".join(texts[k:k + min_run])
        if len(tail) < 3:
            continue
        for j in range(0, k):
            if j + min_run > k:
                break
            head = "".join(texts[j:j + min_run])
            if len(head) < 3:
                continue
            if SequenceMatcher(None, head, tail).ratio() >= threshold:
                best = (k, " ".join(w.text for w in words[:k]))
                break
    return best


def find_stumbles(tr: Transcript, video, transcribe_clip,
                  ratio: float = 0.62) -> list[Cut]:
    """밀도가 낮은 구간을 다시 인식해 헛시작을 찾아 잘라낸다 (2차 통과)."""
    cuts: list[Cut] = []
    for seg in low_density(tr, ratio=ratio):
        try:
            words = transcribe_clip(video, seg.start, seg.end)
        except Exception:
            continue
        hit = find_false_starts(words)
        if not hit:
            continue
        k, dropped = hit
        cuts.append(Cut(seg.start, words[k].start, "ng",
                        f"{dropped}  [헛시작]"))
    return cuts


def dedupe(cuts: list[Cut]) -> list[Cut]:
    """다른 컷에 완전히 포함된 컷을 지운다.

    세그먼트 전체를 NG 로 버리는 판정과, 그 안의 헛시작만 잘라내는 판정이
    같은 구간에서 동시에 걸릴 수 있다. 넓은 쪽만 남긴다.
    """
    out: list[Cut] = []
    for c in sorted(cuts, key=lambda x: (x.start, -(x.end - x.start))):
        if any(o.start <= c.start and c.end <= o.end for o in out):
            continue
        out.append(c)
    return out


def subtract(keeps: list[tuple[int, int]],
             cuts: list[Cut]) -> list[tuple[int, int]]:
    """보존 구간에서 잔말·NG 구간을 뺀다."""
    if not cuts:
        return keeps
    ranges = sorted((c.start, c.end) for c in cuts)
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    out: list[tuple[int, int]] = []
    for ks, ke in keeps:
        cursor = ks
        for cs, ce in merged:
            if ce <= cursor or cs >= ke:
                continue
            if cs > cursor:
                out.append((cursor, min(cs, ke)))
            cursor = max(cursor, ce)
            if cursor >= ke:
                break
        if cursor < ke:
            out.append((cursor, ke))
    return [(s, e) for s, e in out if e > s]
