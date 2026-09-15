import re
from collections import Counter

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…\u0964\u3002])\s+|\n+", re.UNICODE)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_TRAILING_PUNCT = re.compile(r"[.,!?;:'\"…\u0964\u3002]+$")

_STOP_WORDS = frozenset(
    "a about above after again against all am an and any are aren't as at be because been "
    "before being below between both but by can't cannot could couldn't did didn't do does "
    "doesn't doing don't down during each few for from further had hadn't has hasn't have "
    "haven't having he he'd he'll he's her here here's hers herself him himself his how how's "
    "i i'd i'll i'm i've if in into is isn't it it's its itself me more most mustn't my "
    "myself no nor not of off on once only or other ought our ours ourselves out over own "
    "same shan't she she'd she'll she's should shouldn't so some such than that that's the "
    "their theirs them themselves then there there's these they they'd they'll they're they've "
    "this those through to too under until up very was wasn't we we'd we'll we're we've were "
    "weren't what what's when when's where where's which while who who's whom why why's with "
    "won't would wouldn't you you'd you'll you're you've your yours yourself yourselves "
    "can will just also like really very much many more even well still go going got get "
    "make know think right see use way things thing kind bit".split()
)

_DECISION_RE = re.compile(
    r"\b(decided|decides?|agree(?:d|s|ment)?|decision|finali[sz]ed?|final|confirmed?|"
    r"concluded?|we will|we'll|we're going (?:with|to)|move forward|approved?|settle[ds]?|"
    r"settling|decide|proceed|go with|confirmed|confirm)\b",
    re.IGNORECASE,
)

_ACTION_RE = re.compile(
    r"\b(next step|action item|to[- ]do|follow[- ]up|assign(?:ed|ing|s)?|responsib(?:le|ility)|"
    r"deadline|due (?:by|date)|by (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|end of|next week|eod|eow|eom|end of day|end of week)|"
    r"someone (?:needs?|should|has to)|we need to|we should|"
    r"please (?:send|share|prepare|update|check|review|book|schedule|create|write|draft|set up|fix|send out)|"
    r"i will|i'll|i (?:am going|plan) to|must|need to|make sure|handle|take care|get it|send|share|"
    r"prepare|create|draft|book|schedule|organise|organize|fix|update)\b",
    re.IGNORECASE,
)


def _normalise(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def _split_sentences(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = _SENT_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip() and len(p.strip()) > 4]


def _words(text):
    return [w for w in _WORD_RE.findall(text.lower()) if len(w) > 1 and w not in _STOP_WORDS]


def _word_freq(sents):
    counter = Counter()
    for s in sents:
        for w in _words(s):
            counter[w] += 1
    return counter


def _score_sentences(sents, freq):
    scored = []
    for idx, s in enumerate(sents):
        words = _words(s)
        if not words:
            continue
        total = sum(freq.get(w, 0) for w in words)
        score = total / max(1, len(words)) ** 0.5
        scored.append((idx, s, score))
    return scored


def _build_summary(scored, max_sents=6, min_sents=3, ratio=0.2):
    n = max(min_sents, min(max_sents, int(round(len(scored) * ratio))))
    top = sorted(scored, key=lambda t: t[2], reverse=True)[:n]
    top = sorted(top, key=lambda t: t[0])
    return [_TRAILING_PUNCT.sub("", s).strip() for _, s, _ in top]


def _build_highlights(scored, max_count=5, threshold_ratio=0.5):
    if not scored:
        return []
    top_score = max(t[2] for t in scored)
    threshold = top_score * threshold_ratio
    pool = sorted([t for t in scored if t[2] >= threshold], key=lambda t: t[2], reverse=True)
    if not pool:
        pool = sorted(scored, key=lambda t: t[2], reverse=True)[:2]
    chosen = []
    seen = set()
    for idx, s, score in pool:
        norm = _normalise(s)
        if norm in seen or len(s) < 10:
            continue
        chosen.append((idx, _TRAILING_PUNCT.sub("", s).strip()))
        seen.add(norm)
        if len(chosen) >= max_count:
            break
    chosen.sort(key=lambda t: t[0])
    return [s for _, s in chosen]


def _main_topic(scored, sents):
    if not scored:
        raw = sents[0] if sents else ""
    else:
        _, raw, _ = max(scored, key=lambda t: t[2])
    raw = _TRAILING_PUNCT.sub("", raw).strip()
    words = raw.split()
    if len(words) > 15:
        raw = " ".join(words[:15]).rstrip(",;: ' \"") + "..."
    return raw


def _deduplicate(items):
    seen = set()
    out = []
    for it in items:
        norm = _normalise(it)
        if norm and norm not in seen:
            out.append(it)
            seen.add(norm)
    return out


def generate_meeting_summary(text, language="en", segments=None):
    text = (text or "").strip()
    if not text:
        return {"main_topic": "", "summary": "", "highlights": [], "minutes": []}

    sents = _split_sentences(text)
    if not sents:
        return {"main_topic": "", "summary": text[:300], "highlights": [], "minutes": []}

    freq = _word_freq(sents)
    scored = _score_sentences(sents, freq)

    summary_sents = _build_summary(scored)
    summary_text = " ".join(summary_sents) if summary_sents else " ".join(sents[:2])

    highlights = _build_highlights(scored)
    topic = _main_topic(scored, sents)

    decisions = []
    actions = []
    for s in sents:
        s_clean = _TRAILING_PUNCT.sub("", s).strip()
        if _DECISION_RE.search(s):
            decisions.append(s_clean)
        elif _ACTION_RE.search(s):
            actions.append(s_clean)

    decisions = _deduplicate(decisions)[:5]
    actions = _deduplicate(actions)[:5]

    highlight_set = {h.lower() for h in highlights}
    decision_set = {d.lower() for d in decisions}
    action_set = {a.lower() for a in actions}

    discussion = [
        h for h in highlights
        if h.lower() not in decision_set and h.lower() not in action_set
    ][:4]
    if not discussion and highlights:
        discussion = highlights[:3]

    minutes = []
    if discussion:
        minutes.append({"heading": "Key Discussion Points", "items": discussion})
    if decisions:
        minutes.append({"heading": "Decisions Made", "items": decisions})
    if actions:
        minutes.append({"heading": "Action Items", "items": actions})

    return {
        "main_topic": topic,
        "summary": summary_text,
        "highlights": highlights,
        "minutes": minutes,
    }
