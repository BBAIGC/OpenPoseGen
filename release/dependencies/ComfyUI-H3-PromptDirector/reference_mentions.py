"""Normalize clear source-image mentions without rewriting spoken quotes."""
from __future__ import annotations

import re

_QUOTE = re.compile(r'''(<d(?:\s[^>]*)?>[\s\S]*?</d>|“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"(?:[^"\\]|\\.)*"|(?<!\w)'(?:[^'\\]|\\.)*'(?!\w))''', re.I)
_SMALL = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_NUMBERS = {word: index for index, word in enumerate(_SMALL)}
_NUMBERS.update({word: 20 + 10 * index for index, word in enumerate(_TENS)})
_CHINESE = {word: index for index, word in enumerate("零一二三四五六七八九")}
_CHINESE.update({"〇": 0, "两": 2})
_CHINESE_NUMBER = r"(?:\d+|[零〇一二两三四五六七八九十百]+)"
_ONES_WORD = "(?:" + "|".join(_SMALL[1:10]) + ")"
_BELOW_HUNDRED = "(?:" + "|".join(_TENS) + ")(?:[ -]+" + _ONES_WORD + ")?|(?:" + "|".join(sorted(_SMALL, key=len, reverse=True)) + ")"
_ENGLISH_NUMBER = r"(?:\d+|(?:(?:" + _ONES_WORD + r"[ -]+)?hundred)(?:[ -]+(?:and[ -]+)?(?:" + _BELOW_HUNDRED + r"))?|(?:" + _BELOW_HUNDRED + r"))"
_CHINESE_MENTION = re.compile(r"(?<![@A-Za-z0-9构镜意程])@?(?:参考(?:图片|图像|图)|素材图|图片|图像|图)\s*(" + _CHINESE_NUMBER + r")(?![\dA-Za-z零〇一二两三四五六七八九十百]|\s*[x×＊*]\s*\d)")
_ENGLISH_MENTION = re.compile(
    r"(?<![@A-Za-z0-9])@?(?:reference(?:\s+(?:image|picture|photo))?|picture|image|photo|ref|number)\s*(?:(?:no\.?|number)\s*)?#?\s*"
    r"(" + _ENGLISH_NUMBER + r")(?![A-Za-z0-9]|\s*[x×＊*]\s*\d)", re.I)


def _number(text):
    if text.isdecimal():
        return int(text)
    if re.fullmatch(r'[零〇一二两三四五六七八九十百]+', text):
        if not any(unit in text for unit in '十百'):
            return int(''.join(str(_CHINESE[char]) for char in text))
        total, last = 0, 0
        for char in text:
            if char in '十百':
                total += (last or 1) * (10 if char == '十' else 100)
                last = 0
            else:
                last = _CHINESE[char]
        return total + last
    total = 0
    for word in re.split(r'[ -]+', text.lower()):
        if word == 'and':
            continue
        total = (total or 1) * 100 if word == 'hundred' else total + _NUMBERS[word]
    return total


def normalize_reference_mentions(script, count):
    """Return effective planner script; keep the user's draft untouched."""
    if not isinstance(script, str) or type(count) is not int or not 0 <= count <= 101:
        raise ValueError("参考图数量或剧本内容无效。")
    parts = _QUOTE.split(script)
    mentions = []
    def replace(match):
        number = _number(match.group(1))
        mentions.append(number)
        return f'@图{number}'
    for index in range(0, len(parts), 2):
        if '@未引用[' in parts[index]:
            raise ValueError('剧本含未引用的参考图，请点击红色标记重新选择或删除后再运行。')
        parts[index] = _CHINESE_MENTION.sub(replace, parts[index])
        parts[index] = _ENGLISH_MENTION.sub(replace, parts[index])
    result = "".join(parts)
    if any(not 1 <= value <= count for value in mentions):
        raise ValueError(f"参考图编号超出范围；当前只有{count}张参考图。")
    if not mentions:
        if count == 0:
            return result
        if count == 1:
            return "@图1\n" + result
        raise ValueError("有多张参考图，请在剧本中用 @ 指定图片。")
    return result
