"""ffprobe wrapper — 영상 메타데이터."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

US = 1_000_000  # CapCut 시간 단위는 마이크로초


@dataclass
class VideoInfo:
    path: Path
    duration_us: int
    width: int
    height: int
    fps: float
    has_audio: bool

    @property
    def name(self) -> str:
        return self.path.name


def probe(path: str | Path) -> VideoInfo:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"비디오 스트림 없음: {path}")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = float(data.get("format", {}).get("duration")
                     or video.get("duration") or 0.0)
    if duration <= 0:
        raise ValueError(f"길이를 읽을 수 없음: {path}")

    num, _, den = video.get("r_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0

    return VideoInfo(
        path=path,
        duration_us=int(round(duration * US)),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=round(fps, 3),
        has_audio=has_audio,
    )
