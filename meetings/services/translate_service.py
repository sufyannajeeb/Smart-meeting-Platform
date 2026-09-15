import logging
import re
import time

from deep_translator import GoogleTranslator, MyMemoryTranslator

logger = logging.getLogger(__name__)

# Google's free endpoint allows roughly 5 requests per second, and can also
# return an empty string (rather than raising) when it silently rate-limits
# or blocks a request from a server/cloud IP. We split long transcripts into
# sentence-bounded chunks, space out calls, retry with backoff, and fall
# back to a second engine if Google keeps coming back empty.

_GOOGLE_CHUNK_LIMIT = 4500
_FALLBACK_CHUNK_LIMIT = 480  # MyMemory's free tier caps requests around 500 chars
_BASE_DELAY = 1.2
_MAX_ATTEMPTS = 3
_SENT_END_RE = re.compile(r"(?<=[.!?…\u0964\u3002])\s+")

# deep_translator expects full language names, not ISO codes.
# Map the short codes used in SUPPORTED_LANGUAGES to the names
# that GoogleTranslator / MyMemoryTranslator recognise.
_LANG_CODE_TO_NAME = {
    "en": "english",
    "hi": "hindi",
    "ta": "tamil",
    "te": "telugu",
    "ml": "malayalam",
    "kn": "kannada",
    "mr": "marathi",
    "bn": "bengali",
    "fr": "french",
    "de": "german",
    "es": "spanish",
    "pt": "portuguese",
    "ar": "arabic",
    "zh": "chinese (simplified)",
    "ja": "japanese",
    "ko": "korean",
    "ru": "russian",
    "it": "italian",
    "nl": "dutch",
    "pl": "polish",
    "tr": "turkish",
    "vi": "vietnamese",
    "th": "thai",
    "id": "indonesian",
    "ms": "malay",
    "sv": "swedish",
    "da": "danish",
    "fi": "finnish",
    "no": "norwegian",
    "uk": "ukrainian",
    "cs": "czech",
    "el": "greek",
    "he": "hebrew",
    "ro": "romanian",
    "hu": "hungarian",
    "bg": "bulgarian",
    "hr": "croatian",
    "sk": "slovak",
    "sl": "slovenian",
    "et": "estonian",
    "lv": "latvian",
    "lt": "lithuanian",
    "fa": "persian",
    "ur": "urdu",
    "sw": "swahili",
    "fil": "filipino",
}


def _resolve_lang(code: str) -> str:
    """Resolve a short ISO-639-1 code to a full language name for deep_translator."""
    if code == "auto":
        return "auto"
    return _LANG_CODE_TO_NAME.get(code, code)


def _split_chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = _SENT_END_RE.split(text)
    chunks, current = [], ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(sentence), limit):
                chunks.append(sentence[i : i + limit])
            continue
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = (current + " " + sentence) if current else sentence
    if current:
        chunks.append(current)
    return chunks


def _google_chunk(source_lang: str, target_lang: str, chunk: str) -> str:
    src = _resolve_lang(source_lang)
    tgt = _resolve_lang(target_lang)
    result = GoogleTranslator(source=src, target=tgt).translate(chunk)
    if not result or not result.strip():
        raise ValueError("Google returned an empty translation (likely rate-limited/blocked)")
    return result


def _mymemory_chunk(source_lang: str, target_lang: str, chunk: str) -> str:
    src = "auto" if source_lang == "auto" else _resolve_lang(source_lang)
    tgt = _resolve_lang(target_lang)
    result = MyMemoryTranslator(source=src, target=tgt).translate(chunk)
    if not result or not result.strip():
        raise ValueError("MyMemory returned an empty translation")
    return result


def _translate_chunk_with_retry(engine_fn, source_lang: str, target_lang: str, chunk: str) -> str:
    last_err = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return engine_fn(source_lang, target_lang, chunk)
        except Exception as exc:
            last_err = exc
            logger.warning(
                "Translation chunk attempt %d/%d failed (%s -> %s): %s",
                attempt + 1, _MAX_ATTEMPTS, source_lang, target_lang, exc,
            )
            time.sleep(_BASE_DELAY * (attempt + 1))
    raise last_err


def _translate_with_fallback(text: str, target_lang: str, source_lang: str) -> str:
    try:
        chunks = _split_chunks(text, _GOOGLE_CHUNK_LIMIT)
        parts = []
        for i, chunk in enumerate(chunks):
            if i:
                time.sleep(_BASE_DELAY)
            parts.append(_translate_chunk_with_retry(_google_chunk, source_lang, target_lang, chunk))
        return " ".join(parts)
    except Exception as exc:
        logger.warning("Google translation failed after retries, falling back to MyMemory: %s", exc)

    chunks = _split_chunks(text, _FALLBACK_CHUNK_LIMIT)
    parts = []
    for i, chunk in enumerate(chunks):
        if i:
            time.sleep(_BASE_DELAY)
        parts.append(_translate_chunk_with_retry(_mymemory_chunk, source_lang, target_lang, chunk))
    return " ".join(parts)


def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    if not text.strip():
        return ""
    if target_lang == source_lang:
        return text
    return _translate_with_fallback(text, target_lang, source_lang)