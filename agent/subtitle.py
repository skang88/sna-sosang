"""자막 — 대본을 읽기 좋은 줄로 쪼개고, 컷된 타임라인으로 시간을 다시 매핑한다.

두 가지 문제를 푼다.
  1) 줄바꿈: 한국어는 끊어 읽는 자리가 정해져 있다. 관형사(이·그·새로운)와
     뒤 체언, 의존명사(수·것)와 앞말을 떼면 읽기가 무너진다. 문장은 최대한 한 줄로,
     길면 절 경계·조사 뒤에서 끊는다.
  2) 시간 재매핑: 컷 후에는 원본 12:34 의 말이 8:10 에 있다.
     보존 구간(keeps)을 기준으로 원본시간 → 타임라인시간 변환이 필요하다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .asr import Transcript, Word

# ── 끊으면 안 되는 자리 ────────────────────────────────────────────────
# 관형사·관형어 — 뒤 체언을 수식한다. 이 뒤에서 끊으면 "이 | 학교에" 처럼 무너진다.
PRENOUN = {
    "이", "그", "저", "요",                       # 지시 관형사
    "이런", "그런", "저런", "어떤", "무슨", "웬", "아무", "다른", "여느",
    "새", "헌", "옛", "온", "온갖", "모든", "여러", "각", "전", "매", "딴",
    "뭇", "별", "갖은",
    "우리", "저희",                                # 우리 아이들 / 저희 직원
    "약", "총", "단", "제", "한", "두", "세", "네", "몇",   # 약 350km, 총 6개
}

# 의존명사 — 앞말과 붙는다. 이 앞에서 끊으면 "할 | 수", "하는 | 것" 이 된다.
BOUND_NOUNS = {
    "것", "거", "게", "걸", "수", "줄", "때", "데", "바", "뿐", "채", "지",
    "만큼", "대로", "듯", "척", "뻔", "따름", "나름", "터", "등", "및",
    "중", "쪽", "편", "측", "겸", "김", "차", "적", "번", "개", "명", "분",
    "년", "월", "일", "시", "초", "원", "말", "속", "안", "밖", "위", "아래",
}

# ── 끊기 좋은 자리 (점수가 높을수록 우선) ──────────────────────────────
_SENT_END = re.compile(r"[.!?…]$")
# 절 연결 어미 — 여기서 끊으면 통상적인 끊어읽기와 맞는다
_CLAUSE = re.compile(
    r"(?:고|며|서|는데|은데|지만|거나|든지|면서|니까|어서|아서|여서|도록|"
    r"라서|해서|더니|는지|은지|ㄴ지)[,]?$")
# 조사로 끝남 — 명사구가 닫혔다는 신호. 을/를/에/에서 등은 거의 확실히 조사.
_JOSA_STRONG = re.compile(r"(?:을|를|에|에서|에게|께|한테|으로|로|와|과|의|처럼|"
                          r"부터|까지|보다|마다|만큼)[.,!?…]?$")
# 은/는/이/가/도/만 — 조사일 수도, 관형·어미일 수도. 중간 점수.
_JOSA_WEAK = re.compile(r"(?:은|는|이|가|도|만|나|든)[.,!?…]?$")
# 관형형 어미로 끝남 — 뒤 체언을 수식한다. 여기서 끊으면 안 된다.
_ADNOM = re.compile(r"(?:운|린|긴|던|는|은)$")   # 새로운, 오래된, 있는 …

_TRAIL = re.compile(r"[\s.,!?…\"'”’]+$")

# 문장 끝 마침표 — 자막에서는 빼는 것이 관례다. 물음표·느낌표는 뜻이 있어 남긴다.
# 줄 끝뿐 아니라 한 줄 안에 문장이 두 개 들어간 경우도 함께 처리한다.
_PERIOD = re.compile(r"([다요죠까네군])\.(?=\s|$)")


def _drop_period(text: str) -> str:
    return _PERIOD.sub(r"\1", text).strip()


@dataclass
class Cue:
    text: str
    start: int  # us
    end: int


def _bare(w: str) -> str:
    return _TRAIL.sub("", w.strip())


def break_after_score(word: str, next_word: str | None) -> int:
    """word 뒤에서 줄을 바꾸는 것이 얼마나 자연스러운가. 높을수록 좋다.

    음수면 끊으면 안 되는 자리 (관형사 뒤, 의존명사 앞).
    """
    wb = _bare(word)
    if next_word is None:
        return 100                          # 세그먼트 끝
    nb = _bare(next_word)

    # 절대 끊지 말 것
    if wb in PRENOUN:                        # 이 | 학교  ← 막는다
        return -10
    if nb in BOUND_NOUNS:                    # 할 | 수    ← 막는다
        return -10

    if _SENT_END.search(word):
        return 100
    if _CLAUSE.search(wb):
        return 70
    if _JOSA_STRONG.search(word):
        return 45
    # 관형형(새로운·있는)은 뒤를 수식하므로, 조사 신호가 없을 때만 본다
    if _ADNOM.search(wb) and not _JOSA_WEAK.search(word):
        return -5
    if _JOSA_WEAK.search(word):
        return 30
    return 12                               # 평범한 어절 경계


def _split_segment(words: list[Word], target: int, hard: int) -> list[list[Word]]:
    """한 세그먼트의 단어열을 여러 줄로 나눈다.

    target 을 넘으면 '그 지점 이후 첫 좋은 자리'에서 끊고, hard 를 넘으면
    좋은 자리가 없어도 끊는다. 관형사 뒤·의존명사 앞은 점수가 음수라 안 걸린다.
    """
    lines: list[list[Word]] = []
    line: list[Word] = []
    length = 0
    for k, w in enumerate(words):
        nxt = words[k + 1].text if k + 1 < len(words) else None
        add = len(w.text) + (1 if line else 0)
        line.append(w)
        length += add
        score = break_after_score(w.text, nxt)

        if nxt is None:
            break
        # target 을 넘겼고 지금이 끊기 좋은 자리면 끊는다
        if length >= target and score > 0:
            lines.append(line)
            line, length = [], 0
        # hard 를 넘겼으면 (음수 자리만 아니면) 강제로 끊는다
        elif length >= hard and score > -10:
            lines.append(line)
            line, length = [], 0
    if line:
        lines.append(line)
    return lines


def chunk(tr: Transcript, max_chars: int = 24, hard_max: int = 32,
          fixes: list[tuple[str, str]] | None = None,
          drop_period: bool = True) -> list[Cue]:
    """세그먼트를 읽기 좋은 줄로 쪼갠다 (원본 시간 기준).

    문장이 max_chars 안에 들면 통째로 한 줄. 넘으면 끊어읽기 자리에서 나눈다.

    fixes: 고유명사 확정 치환. ★ 긴 세그먼트는 단어를 다시 이어붙여 줄을 만드는데,
      "결혼 아동" 처럼 두 단어에 걸친 치환은 단어 단위로는 안 걸린다. 그래서
      최종 줄 텍스트에 다시 한 번 치환한다 (단어 하나짜리 치환도 무해하게 재적용).

    drop_period: 문장 끝 마침표를 뺀다 ("…입니다." → "…입니다"). 자막 관례다.
      물음표·느낌표는 뜻을 담으므로 남긴다.
    """
    def render(words: list[Word]) -> str:
        text = " ".join(w.text for w in words)
        for wrong, right in fixes or ():
            text = text.replace(wrong, right)
        return _drop_period(text) if drop_period else text

    cues: list[Cue] = []
    for seg in tr.segments:
        text = seg.text.strip()
        if not text:
            continue
        if not seg.words or len(text) <= max_chars:
            start = seg.words[0].start if seg.words else seg.start
            end = seg.words[-1].end if seg.words else seg.end
            plain = _drop_period(text) if drop_period else text
            cues.append(Cue(render(seg.words) if seg.words else plain,
                            start, end))
            continue

        for line in _split_segment(seg.words, max_chars, hard_max):
            cues.append(Cue(render(line), line[0].start, line[-1].end))
    return [c for c in cues if c.end > c.start and c.text.strip()]


def remap(cues: list[Cue], keeps: list[tuple[int, int]],
          min_dur_us: int = 300_000, min_display_us: int = 1_200_000,
          max_display_us: int = 7_000_000) -> list[Cue]:
    """원본 시간의 자막을 컷된 타임라인 시간으로 옮긴다.

    ★ 무음 컷은 말 중간의 짧은 공백까지 잘라내는데, whisper 는 그걸 한 문장으로 묶는다.
      그래서 자막 하나가 컷을 여러 번 가로지른다. 예전처럼 '첫 조각만' 남기면 자막이
      0.4초 만에 사라지고 뒷말은 자막이 없어진다. 대신 자막의 시작·끝을 각각 컷 타임라인
      좌표로 옮겨, 제거된 공백만큼 압축된 한 덩어리로 만든다 (이어붙이기).

    min_display_us: 너무 빨리 지나가면 못 읽는다. 최소 이만큼은 띄우되, 다음 자막을
      침범하지 않는 선까지만 늘린다 (뒤 공백으로 확장).
    """
    # 원본구간 → 타임라인 오프셋
    spans: list[tuple[int, int, int]] = []  # (src_start, src_end, tgt_start)
    cursor = 0
    for s, e in keeps:
        spans.append((s, e, cursor))
        cursor += e - s
    total = cursor

    def to_cut(t: int) -> int:
        """원본 시각 t → 컷 타임라인 시각. 제거된 구간이면 다음 보존 구간 시작으로."""
        for s, e, tgt in spans:
            if t < s:
                return tgt
            if t <= e:
                return tgt + (t - s)
        return total

    out: list[Cue] = []
    for cue in cues:
        cs, ce = to_cut(cue.start), to_cut(cue.end)
        if ce - cs >= min_dur_us:
            out.append(Cue(cue.text, cs, ce))
    out.sort(key=lambda c: c.start)

    # 인접한 같은 자막 병합 — whisper 가 문장 끝을 반복 인식하면(예: "감사합니다"
    # 두 번) 중복 자막이 생기고, 뒤엣것이 앞 자막의 확장을 막는다. 한 덩어리로 합친다.
    merged: list[Cue] = []
    for c in out:
        if merged and c.text.strip() == merged[-1].text.strip():
            merged[-1].end = max(merged[-1].end, c.end)
        else:
            merged.append(c)
    out = merged

    # 너무 짧은 자막은 다음 자막 시작 직전까지 늘려 읽을 시간을 준다
    for i, c in enumerate(out):
        limit = out[i + 1].start if i + 1 < len(out) else c.end + min_display_us
        if c.end - c.start < min_display_us:
            c.end = min(max(c.end, c.start + min_display_us), limit)
        c.end = min(c.end, c.start + max_display_us)

    # 겹침 정리 — 앞 자막이 뒤 자막을 침범하면 잘라낸다
    for i in range(len(out) - 1):
        if out[i].end > out[i + 1].start:
            out[i].end = out[i + 1].start
    return [c for c in out if c.end - c.start >= min_dur_us]
