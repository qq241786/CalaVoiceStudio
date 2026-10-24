"""台词文本规范化与分段（纯标准库，不依赖 PySide6 / 第三方库）。"""

from __future__ import annotations

import re

# 参与"连续标点折叠"的字符集合
_PUNCT_CHARS = "，。！？；：、,.!?;:~—…⋯（）()[]「」『』《》【】" + "\"'\u201c\u201d\u2018\u2019"
_PUNCT_RUN = re.compile("[%s]+" % re.escape(_PUNCT_CHARS))
# 句末标点（补句号时认为已经收尾）
_SENTENCE_END = "。！？!?…"
# 分段时优先在这些标点后断开
_BREAK_CHARS = "。！？；，、!?;,"


def _is_word_char(char: str) -> bool:
    """是否算"有内容"的字符（中英文、数字、字母）。"""
    if char.isspace():
        return False
    if char.isalnum():
        return True
    return "\u4e00" <= char <= "\u9fff"


def _collapse_punct(match) -> str:
    """把一段连续标点折叠成一个：优先保留句末标点。"""
    run = match.group(0)
    for char in "。！？!?":
        if char in run:
            return char
    return run[0]


def normalize_for_tts(text: str) -> str:
    """把台词整理成适合合成的一行文本。

    "…" → "，";连续标点折叠;丢掉纯标点/空白片段;首尾去空白;句末无标点补 "。"。
    正常中英文与数字原样保留（如 3.14、AI、第2章）。
    """
    raw = str(text or "")
    if not raw.strip():
        return ""

    # 1) 省略号统一成逗号（"……" 折叠后就是单个逗号）
    step = raw.replace("\u2026", "，").replace("\u22ef", "，")
    # 2) 连续标点折叠
    step = _PUNCT_RUN.sub(_collapse_punct, step)
    # 3) 按空白切片，丢掉纯标点片段
    fragments = [item for item in step.split() if any(_is_word_char(char) for char in item)]
    step = " ".join(fragments).strip()
    if not step:
        return ""
    # 4) 句末收尾
    if step[-1] in "，、,；;":
        step = step[:-1] + "。"
    elif step[-1] not in _SENTENCE_END:
        step += "。"
    return step


def split_for_tts(text: str, max_chars: int = 48) -> list[str]:
    """先规范化，再按标点切成不超过 max_chars 的若干段（尽量在标点处断开）。"""
    normalized = normalize_for_tts(text)
    limit = int(max_chars or 48)
    if limit <= 0:
        limit = 48
    if not normalized:
        return [normalized]
    if len(normalized) <= limit:
        return [normalized]

    # 先按标点切碎
    pieces: list[str] = []
    buffer = ""
    for char in normalized:
        buffer += char
        if char in _BREAK_CHARS:
            pieces.append(buffer)
            buffer = ""
    if buffer:
        pieces.append(buffer)

    # 再贪心合并，单段超长则硬切
    segments: list[str] = []
    pending = ""
    for piece in pieces:
        if not piece:
            continue
        if len(pending) + len(piece) <= limit:
            pending += piece
            continue
        if pending:
            segments.append(pending)
            pending = ""
        if len(piece) <= limit:
            pending = piece
            continue
        start = 0
        while len(piece) - start > limit:
            segments.append(piece[start:start + limit])
            start += limit
        pending = piece[start:]

    if pending:
        segments.append(pending)
    segments = [item for item in segments if item]
    return segments or [normalized]
