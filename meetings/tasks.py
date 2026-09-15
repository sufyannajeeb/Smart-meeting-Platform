import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path

from django.core.files.base import ContentFile
from django.utils import timezone

from .models import Recording, Transcript, Translation
from .services.ffmpeg_service import FFmpegError, burn_subtitles, check_ffmpeg, convert_to_mp4, extract_audio
from .services.pdf_service import build_transcript_pdf
from .services.subtitle_service import segments_to_srt
from .services.translate_service import translate_text
from .services.summarize_service import generate_meeting_summary
from .services.whisper_service import transcribe_audio

logger = logging.getLogger(__name__)


def _set_status(recording: Recording, status: str, message: str = "") -> None:
    recording.status = status
    recording.status_message = message[:2000] if message else ""
    recording.save(update_fields=["status", "status_message", "updated_at"])


def process_recording(recording_id: int, default_target_lang: str = "hi") -> None:
    recording = None
    work_dir = None

    try:
        recording = Recording.objects.select_related("meeting__room", "meeting__host").get(pk=recording_id)
    except Recording.DoesNotExist:
        return

    meeting = recording.meeting
    work_dir = Path(tempfile.mkdtemp(prefix="smartmeet_"))

    try:
        if not check_ffmpeg():
            raise FFmpegError("FFmpeg not found. Install FFmpeg and add to PATH.")

        if not recording.video_file:
            raise FFmpegError("No video file found. Please record and upload again.")

        raw_path = recording.video_file.path
        if not os.path.isfile(raw_path):
            raise FFmpegError(
                f"Video file missing on disk: {recording.video_file.name}. Record and upload again."
            )

        _set_status(recording, Recording.STATUS_PROCESSING, "Preparing media files...")

        audio_path = str(work_dir / "audio.wav")
        srt_path = work_dir / "subs.srt"
        mp4_path = work_dir / "video.mp4"
        subtitled_path = work_dir / "subtitled.mp4"

        existing = Transcript.objects.filter(recording=recording).first()
        reuse_transcript = bool(existing and existing.text and existing.text.strip())

        if reuse_transcript:
            _set_status(recording, Recording.STATUS_PROCESSING, "Using saved transcript (skipping Whisper)...")
            full_text = existing.text
            segments = existing.segments_json or []
            detected_lang = existing.language or "en"
            transcript = existing
        else:
            extract_audio(raw_path, audio_path)
            _set_status(recording, Recording.STATUS_TRANSCRIBING, "Running Whisper speech-to-text (may take several minutes)...")
            full_text, segments, detected_lang = transcribe_audio(audio_path)
            transcript, _ = Transcript.objects.update_or_create(
                recording=recording,
                defaults={
                    "text": full_text,
                    "segments_json": segments,
                    "language": detected_lang,
                },
            )

        if not segments and full_text.strip():
            duration = max(recording.duration_seconds, 10)
            segments = [{"start": 0.0, "end": float(duration), "text": full_text.strip()}]

        srt_content = segments_to_srt(segments)
        if not srt_content.strip():
            srt_content = f"1\n00:00:00,000 --> 00:00:10,000\n{full_text.strip()}\n"

        srt_path.write_text(srt_content, encoding="utf-8")
        transcript.segments_json = segments
        transcript.text = full_text
        transcript.language = detected_lang
        transcript.save(update_fields=["segments_json", "text", "language"])
        transcript.srt_file.save(f"{recording.id}.srt", ContentFile(srt_content.encode("utf-8")), save=True)

        if not transcript.summary_json or not transcript.summary_json.get("summary"):
            try:
                summary = generate_meeting_summary(full_text, detected_lang, segments)
                Transcript.objects.filter(pk=transcript.pk).update(summary_json=summary)
            except Exception as exc:
                logger.warning("Summary generation failed (non-fatal): %s", exc)

        _set_status(recording, Recording.STATUS_SUBTITLING, "Embedding subtitles into video (Windows-safe)...")
        convert_to_mp4(raw_path, str(mp4_path))
        burn_subtitles(str(mp4_path), str(srt_path), str(subtitled_path))

        if not subtitled_path.is_file():
            raise FFmpegError("Subtitled video was not created.")

        with open(subtitled_path, "rb") as f:
            recording.subtitled_video.save(f"subtitled_{recording.id}.mp4", ContentFile(f.read()), save=False)

        _set_status(recording, Recording.STATUS_TRANSLATING, "Creating PDF...")
        target = default_target_lang or "hi"

        from django.conf import settings as django_settings

        lang_map = dict(django_settings.SUPPORTED_LANGUAGES)

        translation_ok = True
        translated = full_text
        pdf_generated = False
        if target != detected_lang:
            try:
                translated = translate_text(full_text, target, detected_lang)
            except Exception as exc:
                logger.warning("Auto-translation failed, skipping PDF: %s", exc)
                translation_ok = False

        if translation_ok:
            try:
                pdf_bytes = build_transcript_pdf(
                    title=meeting.room.title,
                    room_code=meeting.room.code,
                    host_name=meeting.host.get_username(),
                    language_label=lang_map.get(target, target),
                    transcript_text=translated,
                    meeting_date=meeting.started_at,
                )
            except Exception as exc:
                logger.warning("PDF generation failed, skipping PDF: %s", exc)
                translation_ok = False

        if translation_ok:
            translation, _ = Translation.objects.update_or_create(
                transcript=transcript,
                language_code=target,
                defaults={"translated_text": translated},
            )
            translation.pdf_file.save(
                f"transcript_{recording.id}_{target}.pdf",
                ContentFile(pdf_bytes),
                save=True,
            )
            pdf_generated = True

        meeting.ended_at = meeting.ended_at or timezone.now()
        meeting.save(update_fields=["ended_at"])

        if pdf_generated:
            ready_message = (
                "Done! Download video (enable subtitles in VLC → Subtitle → Track 1) and PDF below."
            )
        else:
            ready_message = (
                "Transcript, summary and subtitled video are ready, but automatic PDF "
                "translation failed (Google rate limit). Retry translation from the "
                "meeting page."
            )
        _set_status(recording, Recording.STATUS_READY, ready_message)
        recording.save()

    except Exception as exc:
        logger.exception("Recording processing failed: %s", exc)
        short_err = str(exc)
        if len(short_err) > 500:
            short_err = short_err[-500:]
        if recording:
            _set_status(recording, Recording.STATUS_FAILED, short_err)
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def enqueue_recording_processing(recording_id: int, target_lang: str = "hi") -> None:
    recording = Recording.objects.filter(pk=recording_id).first()
    if not recording:
        return
    if recording.status in (
        Recording.STATUS_PROCESSING,
        Recording.STATUS_TRANSCRIBING,
        Recording.STATUS_SUBTITLING,
        Recording.STATUS_TRANSLATING,
    ):
        return

    thread = threading.Thread(
        target=process_recording,
        args=(recording_id, target_lang),
        daemon=True,
    )
    thread.start()