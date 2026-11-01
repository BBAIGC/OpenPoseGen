"""Bounded caption grouping from ASR word or segment timestamps, in seconds."""
from __future__ import annotations

import math
import re


TIMING_VERSION = 2
CAPTION_CHARS = 10
_TOKENS = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[^\s]")
_PUNCTUATION = re.compile(r"[。！？!?；;，,、：:.…]$")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _length(text):
    return sum(char.isalnum() for char in text)


def split_caption_text(text, limit=CAPTION_CHARS):
    """Prefer punctuation breaks and retain complete English words."""
    if not isinstance(text, str) or not text.strip():
        return []
    if type(limit) is not int or limit < 1:
        raise ValueError("字幕长度应为正整数。")
    tokens = list(_TOKENS.finditer(text))
    result, start, used = [], 0, 0
    for index, token in enumerate(tokens):
        length = _length(token.group())
        if used and length and used + length > limit:
            result.append(text[tokens[start].start():token.start()].strip())
            start, used = index, 0
        used += length
        if _PUNCTUATION.search(token.group()) or index == len(tokens) - 1:
            result.append(text[tokens[start].start():token.end()].strip())
            start, used = index + 1, 0
    return [part for part in result if part]


def _timestamp(row):
    value = row.get("timestamp")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value
    return row.get("start"), row.get("end")


def _join(left, right):
    separator = " " if re.search(r"[A-Za-z0-9]$", left) and re.match(r"[A-Za-z0-9]", right) else ""
    return left + separator + right


def caption_lines(chunks, duration, *, offset=0.0, timestamp_unit="seconds", source="segment", limit=CAPTION_CHARS):
    """Build short non-overlapping lines without inventing exact word times.

    Whisper timestamps are seconds. Milliseconds are converted only when the
    caller explicitly identifies that unit. Segment-only (or long single-word)
    splits are proportional within their original interval and say so in the
    receipt; word boundaries otherwise remain the ASR boundaries.
    """
    if not isinstance(chunks, list) or not _number(duration) or duration < 0 or not _number(offset) or offset < 0:
        raise ValueError("语音识别字幕与时长无效。")
    scales = {"seconds": 1.0, "s": 1.0, "milliseconds": 0.001, "ms": 0.001}
    if timestamp_unit not in scales or source not in ("word", "segment"):
        raise ValueError("字幕时间单位或识别时间来源无效。")
    scale = scales[timestamp_unit]
    entries = []
    for row in chunks:
        if not isinstance(row, dict):
            continue
        words = row.get("words")
        if isinstance(words, list) and words:
            entries.extend((word, "word") for word in words if isinstance(word, dict))
        else:
            entries.append((row, row.get("timing_source", source)))
    intervals, previous_end = [], 0.0
    for index, (row, origin) in enumerate(entries):
        text = row.get("text", row.get("word", ""))
        if not isinstance(text, str) or not text.strip():
            continue
        start, end = _timestamp(row)
        inferred = not _number(start) or not _number(end)
        start = max(0.0, float(start) * scale) if _number(start) else previous_end
        if not _number(end):
            next_start = next((_timestamp(other)[0] for other, _ in entries[index + 1:]
                               if _number(_timestamp(other)[0]) and _timestamp(other)[0] * scale > start), None)
            end = float(next_start) * scale if next_start is not None else float(duration)
        else:
            end = float(end) * scale
        end = min(float(duration), end)
        if start >= duration or end <= start:
            # Whisper may emit punctuation with a zero-length timestamp. Keep
            # it with the preceding word so punctuation still breaks captions.
            if (end == start and not _length(text) and intervals and
                    0 <= start - intervals[-1]["end"] <= 0.65):
                intervals[-1]["text"] += text.strip()
            continue
        intervals.append({"start": start, "end": end, "text": text.strip(),
                          "timing_source": origin + "-inferred" if inferred else origin})
        previous_end = end
    intervals.sort(key=lambda row: row["start"])
    bounded = []
    for row in intervals:
        if bounded and row["start"] < bounded[-1]["end"]:
            previous = bounded[-1]
            if row["text"] == previous["text"] and row["start"] == previous["start"]:
                continue
            if row["start"] > previous["start"]:
                previous["end"] = row["start"]
            else:
                row["start"] = previous["end"]
            if row["end"] <= row["start"]:
                # Alignment can assign adjacent tokens the same interval. Do
                # not discard recognized words merely to eliminate overlap;
                # their order is known but their individual times are not.
                previous["text"] = _join(previous["text"], row["text"])
                previous["timing_source"] = "overlap-proportional"
                continue
        bounded.append(row)
    units = []
    for row in bounded:
        parts = split_caption_text(row["text"], limit)
        weights = [max(1, _length(part)) for part in parts]
        total, consumed = sum(weights), 0
        for index, (part, weight) in enumerate(zip(parts, weights)):
            start = row["start"] + (row["end"] - row["start"]) * consumed / total
            consumed += weight
            end = row["end"] if index == len(parts) - 1 else row["start"] + (row["end"] - row["start"]) * consumed / total
            units.append({"start": offset + start, "end": offset + end, "text": part,
                          "timing_source": row["timing_source"] if len(parts) == 1 else row["timing_source"] + "-proportional"})
    lines = []
    for row in units:
        if (lines and not _PUNCTUATION.search(lines[-1]["text"]) and
                row["start"] - lines[-1]["end"] <= 0.65 and
                _length(lines[-1]["text"]) + _length(row["text"]) <= limit):
            current = lines[-1]
            current["text"] = _join(current["text"], row["text"])
            current["end"] = row["end"]
            if current["timing_source"] != row["timing_source"]:
                current["timing_source"] = "mixed"
        else:
            lines.append(dict(row))
    return lines
