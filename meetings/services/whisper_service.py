import os
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_model = None


def get_whisper_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        model_name = os.getenv("WHISPER_MODEL") or getattr(settings, "WHISPER_MODEL", "base")
        device = os.getenv("WHISPER_DEVICE") or getattr(settings, "WHISPER_DEVICE", "cpu")
        compute_type = os.getenv("WHISPER_COMPUTE_TYPE") or getattr(settings, "WHISPER_COMPUTE_TYPE", "int8")

        logger.info("Loading Whisper model: %s (device=%s, compute=%s)", model_name, device, compute_type)
        _model = WhisperModel(model_name, device=device, compute_type=compute_type)
        logger.info("Whisper model loaded successfully")
    return _model


def _resolve_language(language: str | None) -> str | None:
    """language argument wins, then WHISPER_LANGUAGE, then auto-detect."""
    return language or os.getenv("WHISPER_LANGUAGE") or None


def _resolve_initial_prompt(lang: str | None) -> str | None:
    """
    Pick a Whisper initial_prompt.

    Whisper's prompt strongly biases the audio language, so we only inject an
    English punctuation prompt for English audio. For any other language (or
    auto-detect), an English prompt can degrade accuracy — we leave it unset
    unless the user explicitly configures WHISPER_INITIAL_PROMPT.
    """
    explicit = os.getenv("WHISPER_INITIAL_PROMPT") or getattr(settings, "WHISPER_INITIAL_PROMPT", "")
    if explicit:
        return explicit
    if lang and lang.lower() in ("en", "eng", "english"):
        return getattr(
            settings,
            "WHISPER_ENGLISH_PROMPT",
            "The following is a conversation between two or more speakers. "
            "Use proper punctuation, capitalization, and paragraph breaks.",
        )
    return None


def transcribe_audio(audio_path: str, language: str | None = None) -> tuple[str, list[dict], str]:
    """Return (full_text, segments, detected_language)."""
    model = get_whisper_model()

    lang = _resolve_language(language)
    beam_size = int(os.getenv("WHISPER_BEAM_SIZE") or getattr(settings, "WHISPER_BEAM_SIZE", 5))
    best_of = int(os.getenv("WHISPER_BEST_OF") or getattr(settings, "WHISPER_BEST_OF", 5))
    initial_prompt = _resolve_initial_prompt(lang)

    kwargs = dict(
        beam_size=beam_size,
        best_of=best_of,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
            threshold=0.35,
        ),
        word_timestamps=False,
        condition_on_previous_text=True,
        no_speech_threshold=0.5,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        language_detection_threshold=0.5,
    )
    if lang:
        kwargs["language"] = lang
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt

    logger.info("Starting transcription: %s (lang=%s, beam=%d)", audio_path, lang, beam_size)

    segments_iter, info = model.transcribe(audio_path, **kwargs)

    detected = getattr(info, "language", None) or lang or "en"

    segments = []
    texts = []
    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": text,
            }
        )
        texts.append(text)

    # Newline-joined so the transcript reads like a back-and-forth
    # conversation (one line per detected speech segment).
    full_text = "\n".join(texts)
    logger.info("Transcription complete: %d segments, language=%s", len(segments), detected)

    return full_text, segments, detected