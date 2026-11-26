"""Parsers for podcast transcripts: WebVTT, SRT, Podcasting 2.0 JSON, Whisper JSON and text."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from ..models import TranscriptSegment, TranscriptWord

_TAG_RE = re.compile(r"<[^>]+>")
_VOICE_RE = re.compile(r"<v(?:\.[^>\s]+)*\s+([^>]+)>", re.I)
_TIME_RE = re.compile(
    r"(?:(\d{1,3}):)?(\d{2}):(\d{2})[.,](\d{1,3})"
)
_CUE_LINE_RE = re.compile(
    r"^\s*((?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{1,3})"
)


def _parse_timestamp(value: str) -> float | None:
    match = _TIME_RE.search(value.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis_str = match.group(4).ljust(3, "0")[:3]
    millis = int(millis_str)
    return hours * 3600.0 + minutes * 60.0 + seconds + millis / 1000.0


def _clean_cue_text(raw: str) -> tuple[str, str]:
    """Strip VTT/SRT markup and extract optional `<v Speaker>` voice tag."""
    speaker = ""
    voice = _VOICE_RE.search(raw)
    if voice:
        speaker = html.unescape(voice.group(1)).strip()
    text = _TAG_RE.sub("", raw)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = " ".join(lines)
    joined = re.sub(r"\s+", " ", joined).strip()
    if not speaker and ": " in joined:
        prefix, rest = joined.split(": ", 1)
        if 1 <= len(prefix) <= 32 and prefix.replace(" ", "").replace("-", "").isalpha():
            # Only split obvious "Speaker Name: text" prefixes when capitalized.
            if prefix[0].isupper() and not any(ch in prefix for ch in ".!?"):
                speaker = prefix.strip()
                joined = rest.strip()
    return joined, speaker


def parse_vtt_or_srt(text: str, *, source: str = "rss") -> list[TranscriptSegment]:
    """Parse WebVTT or SubRip (SRT) captions into ordered transcript segments."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n{2,}", text)
    segments: list[TranscriptSegment] = []

    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        first_upper = lines[0].strip().upper()
        if first_upper.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue

        cue_idx = -1
        match = None
        for idx, line in enumerate(lines[:2]):
            m = _CUE_LINE_RE.match(line)
            if m:
                cue_idx = idx
                match = m
                break
        if match is None:
            continue

        start = _parse_timestamp(match.group(1))
        end = _parse_timestamp(match.group(2))
        if start is None or end is None:
            continue
        if end < start:
            end = start

        body = "\n".join(lines[cue_idx + 1:])
        cleaned, speaker = _clean_cue_text(body)
        if not cleaned:
            continue
        segments.append(TranscriptSegment(
            start=round(start, 3),
            end=round(max(end, start + 0.2), 3),
            text=cleaned,
            speaker=speaker,
            source=source,
        ))

    segments.sort(key=lambda s: s.start)
    return segments


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_json_transcript(payload: Any, *, offset: float = 0.0,
                          source: str = "rss") -> list[TranscriptSegment]:
    """Parse Podcasting 2.0 JSON transcript or OpenAI/OpenRouter verbose_json."""
    if isinstance(payload, (bytes, bytearray)):
        payload = json.loads(payload.decode("utf-8", errors="replace"))
    elif isinstance(payload, str):
        payload = json.loads(payload)

    segments_raw: list[dict[str, Any]] = []
    top_words_raw: list[dict[str, Any]] = []

    if isinstance(payload, list):
        segments_raw = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        if isinstance(payload.get("segments"), list):
            segments_raw = [item for item in payload["segments"] if isinstance(item, dict)]
        elif isinstance(payload.get("results"), list):
            segments_raw = [item for item in payload["results"] if isinstance(item, dict)]
        if isinstance(payload.get("words"), list):
            top_words_raw = [item for item in payload["words"] if isinstance(item, dict)]

    top_words: list[TranscriptWord] = []
    for item in top_words_raw:
        word_text = str(item.get("word") or item.get("text") or "").strip()
        if not word_text:
            continue
        w_start = _float(item.get("start"), -1.0)
        w_end = _float(item.get("end"), w_start)
        if w_start < 0:
            continue
        top_words.append(TranscriptWord(
            word=word_text,
            start=round(offset + w_start, 3),
            end=round(offset + max(w_end, w_start), 3),
        ))

    segments: list[TranscriptSegment] = []
    for item in segments_raw:
        raw_text = str(item.get("body") or item.get("text") or "").strip()
        if not raw_text:
            continue
        cleaned = re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", raw_text))).strip()
        if not cleaned:
            continue
        start = _float(item.get("startTime", item.get("start")), 0.0) + offset
        end = _float(item.get("endTime", item.get("end")), start + 2.0) + offset
        if end < start:
            end = start + 0.2
        speaker = str(item.get("speaker") or "").strip()

        seg_words: list[TranscriptWord] = []
        if isinstance(item.get("words"), list):
            for w in item["words"]:
                if not isinstance(w, dict):
                    continue
                w_text = str(w.get("word") or w.get("text") or "").strip()
                if not w_text:
                    continue
                ws = _float(w.get("start"), start - offset) + offset
                we = _float(w.get("end"), ws - offset) + offset
                seg_words.append(TranscriptWord(word=w_text, start=round(ws, 3),
                                                end=round(max(we, ws), 3)))
        elif top_words:
            seg_words = [w for w in top_words if start - 0.15 <= w.start <= end + 0.15]

        segments.append(TranscriptSegment(
            start=round(start, 3),
            end=round(end, 3),
            text=cleaned,
            speaker=speaker,
            words=seg_words,
            source=source,
        ))

    # Fallback when payload only contains top-level `text` (non-verbose STT response)
    if not segments and isinstance(payload, dict):
        plain = str(payload.get("text") or "").strip()
        if plain:
            duration = _float(payload.get("duration"), 0.0)
            if not duration and isinstance(payload.get("usage"), dict):
                duration = _float(payload["usage"].get("seconds"), 0.0)
            if top_words:
                segments = _group_words_into_segments(top_words, source=source)
            else:
                segments = split_plain_text(plain, start_offset=offset,
                                            duration=duration or 30.0, source=source)

    segments.sort(key=lambda s: s.start)
    return segments


def _group_words_into_segments(words: list[TranscriptWord], *,
                               source: str = "openrouter") -> list[TranscriptSegment]:
    """Group word-level timestamps into natural readable sentences/phrases."""
    segments: list[TranscriptSegment] = []
    bucket: list[TranscriptWord] = []
    for word in words:
        bucket.append(word)
        elapsed = bucket[-1].end - bucket[0].start
        ends_sentence = word.word.endswith((".", "!", "?"))
        if (ends_sentence and len(bucket) >= 4) or len(bucket) >= 18 or elapsed >= 8.0:
            segments.append(TranscriptSegment(
                start=bucket[0].start,
                end=max(bucket[-1].end, bucket[0].start + 0.2),
                text=" ".join(w.word for w in bucket),
                words=list(bucket),
                source=source,
            ))
            bucket = []
    if bucket:
        segments.append(TranscriptSegment(
            start=bucket[0].start,
            end=max(bucket[-1].end, bucket[0].start + 0.2),
            text=" ".join(w.word for w in bucket),
            words=list(bucket),
            source=source,
        ))
    return segments


def split_plain_text(text: str, *, start_offset: float = 0.0, duration: float = 30.0,
                     source: str = "rss") -> list[TranscriptSegment]:
    """Split untimed text into proportional sentences so it remains navigable."""
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    # Split into sentences/paragraphs of reasonable study length
    raw_parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    parts = [re.sub(r"\s+", " ", part).strip() for part in raw_parts if part.strip()]
    if not parts:
        return []

    total_chars = sum(len(p) for p in parts) or 1
    total_dur = max(float(duration or 0.0), len(parts) * 3.0)
    cursor = start_offset
    segments: list[TranscriptSegment] = []
    for part in parts:
        span = total_dur * (len(part) / total_chars)
        end = cursor + max(span, 0.5)
        segments.append(TranscriptSegment(
            start=round(cursor, 3),
            end=round(end, 3),
            text=part,
            source=source,
        ))
        cursor = end
    return segments


def parse_transcript(data: bytes, mime_type: str = "", url: str = "",
                     duration: float = 0.0, *, source: str = "rss") -> list[TranscriptSegment]:
    """Auto-detect format (VTT, SRT, JSON, plain text) and parse into segments."""
    mime = (mime_type or "").lower().split(";")[0].strip()
    lower_url = (url or "").lower().split("?")[0]
    text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
    stripped = text.lstrip()

    if mime == "application/json" or lower_url.endswith(".json") or stripped.startswith(("{", "[")):
        try:
            return parse_json_transcript(stripped, source=source)
        except ValueError:
            pass

    if (mime in ("text/vtt", "application/x-subrip", "application/srt", "text/srt")
            or lower_url.endswith((".vtt", ".srt"))
            or stripped.upper().startswith("WEBVTT")
            or _CUE_LINE_RE.search(stripped[:600])):
        parsed = parse_vtt_or_srt(text, source=source)
        if parsed:
            return parsed

    return split_plain_text(text, duration=duration, source=source)
