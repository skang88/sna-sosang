"""음성 인식 — mlx-whisper (Apple Silicon).

★ 원칙: 전체 대본을 먼저 뽑는다. NG·자막 판단은 그 대본을 보고 한다.
  단어 하나씩 보고 즉석에서 자르지 않는다 — 문맥이 없으면 오판한다.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .probe import US

CACHE = Path(__file__).parent.parent / "cache" / "asr"
GLOSSARY_FILE = Path(__file__).parent.parent / "glossary.txt"
MODEL = "mlx-community/whisper-large-v3-turbo"


def load_glossary() -> list[str]:
    """glossary.txt 를 읽는다. 없으면 빈 목록."""
    if not GLOSSARY_FILE.exists():
        return []
    return [ln.strip() for ln in GLOSSARY_FILE.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


@dataclass
class Word:
    text: str
    start: int  # us
    end: int    # us


@dataclass
class Segment:
    text: str
    start: int
    end: int
    words: list[Word] = field(default_factory=list)


def parse_glossary(raw: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """사전 입력을 편향어와 치환쌍으로 나눈다.

        굿네이버스          → 편향 (initial_prompt 로 유도)
        결혼아동>결연아동    → 치환 (인식 후 확정 교체)

    편향은 유도일 뿐 강제력이 없다. 실측에서 '결혼→결연' 은 편향으로
    한 건도 고쳐지지 않았다. 반드시 고쳐야 하는 말은 치환을 쓴다.

    바꿀 말 안의 `\\n` 은 실제 줄바꿈으로 읽는다 — 자막을 두 줄로 띄우고 싶을 때
    쓴다 (예: `네시복구>메르시 보쿠\\n(대단히 감사합니다)`).
    """
    bias: list[str] = []
    fixes: list[tuple[str, str]] = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        if ">" in item:
            wrong, _, right = item.partition(">")
            if wrong.strip() and right.strip():
                fixes.append((wrong.strip(),
                              right.strip().replace("\\n", "\n")))
        else:
            bias.append(item)
    # 긴 것부터 치환해야 부분 겹침에서 사고가 안 난다
    fixes.sort(key=lambda p: len(p[0]), reverse=True)
    return bias, fixes


@dataclass
class Transcript:
    language: str
    segments: list[Segment]

    def apply_fixes(self, fixes: list[tuple[str, str]]) -> "Transcript":
        """오인식 확정 치환. 캐시된 대본에도 바로 적용된다.

        치환은 캐시 키에 넣지 않는다 — 규칙을 고칠 때마다 재인식(수 분)을
        다시 돌릴 이유가 없다.
        """
        if not fixes:
            return self
        for seg in self.segments:
            for wrong, right in fixes:
                seg.text = seg.text.replace(wrong, right)
            seg.words = _fix_words(seg.words, fixes)
        return self

    @property
    def script(self) -> str:
        """전체 대본 — 판단의 기준이 되는 원본."""
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def to_json(self) -> dict:
        return {"language": self.language,
                "segments": [asdict(s) for s in self.segments]}

    @classmethod
    def from_json(cls, d: dict) -> "Transcript":
        return cls(language=d["language"],
                   segments=[Segment(text=s["text"], start=s["start"],
                                     end=s["end"],
                                     words=[Word(**w) for w in s["words"]])
                             for s in d["segments"]])


_PUNCT_TAIL = re.compile(r"[.,!?…]+$")


def _fix_words(words: list[Word], fixes: list[tuple[str, str]]) -> list[Word]:
    """단어 시퀀스에 치환을 적용한다.

    한 단어짜리 치환은 그 단어 안에서 바꾼다(부분 문자열 포함: 화동품>학용품 이
    '화동품을'→'학용품을'). 여러 단어에 걸친 치환(예: '꿈을 피게 하지 않고')은
    연속한 단어 묶음이 통째로 맞을 때 그 구간의 타임스탬프를 유지한 채 교체한다.
    이렇게 해야 청킹으로 줄이 갈려도(단어를 다시 이어붙여도) 치환이 살아남는다.
    """
    for wrong, right in fixes:
        toks = wrong.split()
        if len(toks) <= 1:
            for w in words:
                w.text = w.text.replace(wrong, right)
            continue
        out: list[Word] = []
        i = 0
        while i < len(words):
            run = words[i:i + len(toks)]
            joined = " ".join(_PUNCT_TAIL.sub("", w.text) for w in run)
            if len(run) == len(toks) and joined == wrong:
                out.extend(_spread(right.split(), run[0].start, run[-1].end))
                i += len(toks)
            else:
                out.append(words[i])
                i += 1
        words = out
    return words


def _spread(tokens: list[str], start: int, end: int) -> list[Word]:
    """토큰들을 [start, end] 구간에 글자 수 비례로 배분한다."""
    total = sum(len(t) for t in tokens) or 1
    span = end - start
    out: list[Word] = []
    cur = start
    for t in tokens:
        w_end = cur + int(span * len(t) / total)
        out.append(Word(text=t, start=cur, end=w_end))
        cur = w_end
    if out:
        out[-1].end = end
    return out


def transcribe_clip(video: Path, start_us: int, end_us: int,
                    language: str = "ko", model: str = MODEL) -> list[Word]:
    """구간 하나만 떼어내 다시 인식한다 (2차 통과).

    ★ 장문 인식은 말더듬을 매끄럽게 정리해 버린다. 실측:
        장문  '다음 세대를 세워가는 일을'
        조각  '다른 세대, 다음 세대, 다음 세대를 세워가는 일을'
      헛시작 두 번이 장문에서는 통째로 사라졌다.
      짧은 조각으로 다시 들려주면 살아난다 — 그래야 어디를 자를지 알 수 있다.
    """
    import mlx_whisper

    CACHE.mkdir(parents=True, exist_ok=True)
    wav = CACHE / f"clip_{start_us}_{end_us}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start_us / US:.3f}",
         "-t", f"{(end_us - start_us) / US:.3f}", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
        check=True, capture_output=True)
    try:
        raw = mlx_whisper.transcribe(
            str(wav), path_or_hf_repo=model, language=language,
            word_timestamps=True, condition_on_previous_text=False,
            verbose=None)
    finally:
        wav.unlink(missing_ok=True)

    return [Word(text=w["word"].strip(),
                 start=start_us + int(w["start"] * US),
                 end=start_us + int(w["end"] * US))
            for s in raw.get("segments", []) for w in s.get("words", [])
            if w.get("word", "").strip()]


def extract_audio(video: Path, dest: Path) -> Path:
    """whisper 입력용 16kHz 모노 wav."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)],
        check=True, capture_output=True)
    return dest


def transcribe(video: Path, cache_key: str, language: str = "ko",
               model: str = MODEL, glossary: list[str] | None = None
               ) -> Transcript:
    """대본 추출. cache_key 는 콘텐츠 해시 (mtime 아님).

    glossary: 고유명사 목록. whisper 는 처음 듣는 이름을 음이 비슷한 흔한 말로
      바꿔버린다 (실측: 굿네이버스→'굿네고스', 결연아동→'결혼아동',
      프놈펜→'푸른편'). '틀린말>맞는말' 형식은 확정 치환, 그 외는 편향.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    bias, fixes = parse_glossary(glossary or [])

    key = cache_key
    if bias:  # 편향만 캐시 키에 반영 — 치환은 재인식이 필요 없다
        import hashlib
        key += "-" + hashlib.blake2b(
            ",".join(sorted(bias)).encode(), digest_size=4).hexdigest()
    cached = CACHE / f"{key}.json"
    if cached.exists():
        return Transcript.from_json(
            json.loads(cached.read_text())).apply_fixes(fixes)

    import mlx_whisper  # 무거우므로 실제 필요할 때만 로드

    wav = extract_audio(video, CACHE / f"{cache_key}.wav")
    try:
        raw = mlx_whisper.transcribe(
            str(wav), path_or_hf_repo=model, language=language,
            word_timestamps=True, condition_on_previous_text=False,
            initial_prompt=", ".join(bias) if bias else None,
            verbose=None)
    finally:
        wav.unlink(missing_ok=True)

    segments: list[Segment] = []
    for s in raw.get("segments", []):
        words = [Word(text=w["word"].strip(),
                      start=int(w["start"] * US), end=int(w["end"] * US))
                 for w in s.get("words", []) if w.get("word", "").strip()]
        text = s.get("text", "").strip()
        if not text:
            continue
        segments.append(Segment(text=text,
                                start=int(s["start"] * US),
                                end=int(s["end"] * US),
                                words=words))

    tr = Transcript(language=raw.get("language", language), segments=segments)
    cached.write_text(json.dumps(tr.to_json(), ensure_ascii=False))
    return tr.apply_fixes(fixes)
