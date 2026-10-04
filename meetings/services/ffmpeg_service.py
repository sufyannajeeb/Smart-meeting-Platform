import os
import shutil
import subprocess
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
    """Burn the subtitles into the picture so they show in ANY player."""
    srt_esc = _escape_path_for_subtitles_filter(srt_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(Path(video_path).resolve()),
        "-vf",
        f"subtitles={srt_esc}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        # Players (QuickTime, phones, browsers) refuse non-4:2:0 H.264.
        "-pix_fmt",
        "yuv420p",
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


_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def _find_font_file() -> str:
    for candidate in _FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return ""


def _probe_video_size(video_path: str) -> tuple[int, int]:
    """Best-effort width/height via ffprobe, defaulting to 720p."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0:s=x",
                    str(Path(video_path).resolve()),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            width, height = result.stdout.strip().split("x")[:2]
            return int(width), int(height)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
    return 1280, 720


def burn_subtitles_drawtext(
    video_path: str,
    cues: list[dict],
    output_path: str,
    work_dir: str,
) -> None:
    """Burn subtitles with the drawtext filter.

    The `subtitles`/`ass` filters require libass, which many Windows static
    FFmpeg builds ship broken or omit entirely. drawtext (libfreetype) is far
    more commonly available, so each cue is drawn as its own timed text layer.
    Cue text is written to files in ``work_dir`` and referenced by relative
    filename (run with ``cwd=work_dir``) to sidestep Windows path escaping.
    """
    width, height = _probe_video_size(video_path)
    font_size = max(14, int(round(height * 0.042)))
    margin = max(10, int(round(height * 0.05)))

    font_file = _find_font_file()
    font_opt = (
        f"fontfile='{_escape_path_for_subtitles_filter(font_file)}':" if font_file else ""
    )

    chains = []
    cue_dir = Path(work_dir)
    for index, cue in enumerate(cues):
        text = (cue.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(cue.get("start", 0.0)))
            end = max(start + 0.2, float(cue.get("end", start + 2.0)))
        except (TypeError, ValueError):
            continue

        filename = f"smartmeet_cue_{index}.txt"
        cue_dir.joinpath(filename).write_bytes(
            text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        )
        chains.append(
            "drawtext="
            f"textfile={filename}:"
            "expansion=none:"
            f"{font_opt}"
            f"fontsize={font_size}:"
            "fontcolor=white:"
            "box=1:boxcolor=black@0.6:boxborderw=8:"
            "x=(w-text_w)/2:"
            f"y=h-text_h-{margin}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )

    if not chains:
        raise FFmpegError("No subtitle text to burn into the video.")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(Path(video_path).resolve()),
        "-vf",
        ",".join(chains),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(Path(output_path).resolve()),
    ]
    _run(cmd, cwd=str(cue_dir))


def apply_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    cues: list[dict] | None = None,
    work_dir: str | None = None,
) -> None:
    """Render a subtitled video that shows its subtitles without extra steps.

    Tries, in order: libass burn-in, drawtext burn-in, then a soft-muxed
    selectable subtitle track as the last resort.
    """
    try:
        burn_subtitles_hard(video_path, srt_path, output_path)
        return
    except FFmpegError:
        pass

    if cues and work_dir:
        try:
            burn_subtitles_drawtext(video_path, cues, output_path, work_dir)
            return
        except FFmpegError:
            pass

    mux_soft_subtitles(video_path, srt_path, output_path)


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    cues: list[dict] | None = None,
    work_dir: str | None = None,
) -> None:
    apply_subtitles(video_path, srt_path, output_path, cues=cues, work_dir=work_dir)


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
