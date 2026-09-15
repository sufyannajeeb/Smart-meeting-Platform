import os
import shutil
import subprocess
import sys
from pathlib import Path


class FFmpegError(Exception):
    pass


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_audio(video_path: str, audio_path: str) -> None:
    """Extract a 16 kHz mono WAV tuned for Whisper.

    A light band-pass (60 Hz–7.6 kHz) strips room rumble/hiss, and loudness
    normalization lifts quiet speech to a consistent level — measurably
    improves Whisper accuracy on conversation recordings. Timestamps are
    unaffected.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-af",
        "highpass=f=60,lowpass=f=7600,loudnorm=I=-16:TP=-1.5:LRA=11",
        audio_path,
    ]
    _run(cmd)


def mux_soft_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    """Embed SRT track in MP4 — works on Windows (no C: path in -vf filter)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(Path(video_path).resolve()),
        "-i",
        str(Path(srt_path).resolve()),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map",
        "1:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        "-metadata:s:s:0",
        "language=eng",
        "-disposition:s:0",
        "default",
        str(Path(output_path).resolve()),
    ]
    _run(cmd)


def burn_subtitles_hard(video_path: str, srt_path: str, output_path: str) -> None:
    srt_esc = _escape_path_for_subtitles_filter(srt_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(Path(video_path).resolve()),
        "-vf",
        f"subtitles={srt_esc}",
        "-c:a",
        "copy",
        str(Path(output_path).resolve()),
    ]
    _run(cmd)


def _escape_path_for_subtitles_filter(path: str) -> str:
    normalized = Path(path).resolve().as_posix()
    if len(normalized) >= 2 and normalized[1] == ":":
        normalized = normalized[0] + r"\:" + normalized[2:]
    return normalized


def apply_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    """Windows: soft mux only. Other OS: try hard burn, then soft mux."""
    if sys.platform == "win32":
        mux_soft_subtitles(video_path, srt_path, output_path)
        return
    try:
        burn_subtitles_hard(video_path, srt_path, output_path)
    except FFmpegError:
        mux_soft_subtitles(video_path, srt_path, output_path)


def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    apply_subtitles(video_path, srt_path, output_path)


def convert_to_mp4(input_path: str, output_path: str) -> None:
    inp = Path(input_path).resolve()
    out = Path(output_path).resolve()
    if inp.suffix.lower() == ".mp4" and inp.exists():
        shutil.copyfile(inp, out)
        return
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(inp),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        str(out),
    ]
    _run(cmd)


def _run(cmd: list[str], cwd: str | None = None) -> None:
    if not check_ffmpeg():
        raise FFmpegError(
            "FFmpeg is not installed or not on PATH. Install FFmpeg and restart the server."
        )
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "FFmpeg failed").strip()
        if len(err) > 1500:
            err = err[-1500:]
        raise FFmpegError(err)
