#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""心事卡片生成器。

生成一张可分享的文本卡片，含 20 分钟可见提示与心事后记位。

用法：
    python card.py --summary "方案被退回第三次" --meal "麦辣鸡腿堡,中薯条,可乐" \
        --kcal 890 --happiness 82 --verdict "准吃但改一处"

    # 从 JSON 读取（便于 Skill 串联调用）
    python card.py --json session.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CARD_TOTAL = 44  # 卡片总显示宽度（含左右边框）
CARD_INNER = CARD_TOTAL - 1  # "│" 占 1 宽，内容区 = 总宽 - 左边框
MAX_SUMMARY_LEN = 13  # 心事概括的字数上限（不含标点）
VISIBLE_MINUTES = 20


def char_width(char: str) -> int:
    """单字符显示宽度：CJK/全角按 2，其余按 1。"""
    code = ord(char)
    if code < 0x1100:
        return 1
    if (
        0x1100 <= code <= 0x115F  # 谚文字母
        or 0x2E80 <= code <= 0xA4CF  # CJK 部首、汉字、日文假名
        or 0xAC00 <= code <= 0xD7A3  # 谚文音节
        or 0xF900 <= code <= 0xFAFF  # CJK 兼容汉字
        or 0xFE30 <= code <= 0xFE6F  # CJK 兼容形式
        or 0xFF00 <= code <= 0xFF60  # 全角形式
        or 0xFFE0 <= code <= 0xFFE6  # 全角符号
        or 0x20000 <= code <= 0x3FFFD  # 扩展区汉字
    ):
        return 2
    return 1


def display_width(text: str) -> int:
    """计算字符串显示宽度。"""
    return sum(char_width(c) for c in text)


def pad_to(text: str, target: int) -> str:
    """右侧补空格到指定显示宽度；超宽时按宽度截断，绝不破坏边框。"""
    if display_width(text) > target:
        text = truncate(text, target)
    gap = target - display_width(text)
    return text + (" " * max(0, gap))


def truncate(text: str, limit: int) -> str:
    """按显示宽度截断，超出以单字节 '.' 省略，保证总宽不超 limit。"""
    if display_width(text) <= limit:
        return text
    out = ""
    width = 0
    for char in text:
        w = char_width(char)
        if width + w > limit - 1:
            break
        out += char
        width += w
    return out + "."


def row(label: str, value: str) -> str:
    """拼一行带左右边框的卡片行。"""
    prefix = f"│ {label}："
    body = pad_to(value, CARD_INNER - display_width(prefix))
    return prefix + body + "│"


def rows_multiline(label: str, items: list[str], separator: str = " + ") -> list[str]:
    """品项较多时按可用宽度折行，避免被截断成 '可口可乐(.'。"""
    prefix = f"│ {label}："
    avail = CARD_INNER - display_width(prefix)
    cont_prefix = "│ " + " " * display_width(label) + "　"  # 续行缩进对齐到"："之后的取值起始位

    if not items:
        return [prefix + pad_to("—", avail) + "│"]

    # 先把超宽的单项按可用宽度切开，保证每一段都能放下
    chunks: list[str] = []
    for item in items:
        while display_width(item) > avail:
            head = truncate(item, avail)
            # truncate 会在末尾补 "."，这里去掉后作为硬切断点
            cut = head[:-1] if head.endswith(".") else head
            chunks.append(cut)
            item = item[display_width(cut):]
        chunks.append(item)

    lines: list[str] = []
    current = ""
    for index, chunk in enumerate(chunks):
        separator_needed = bool(current)
        trial = current + (separator if separator_needed else "") + chunk
        if display_width(trial) <= avail:
            current = trial
            continue
        if current:
            lines.append(prefix + pad_to(current, avail) + "│")
            prefix = cont_prefix
        current = chunk

    if current:
        lines.append(prefix + pad_to(current, avail) + "│")

    return lines


def happiness_label(score: int) -> str:
    if score >= 85:
        return "很足"
    if score >= 70:
        return "够用"
    if score >= 55:
        return "将就"
    return "勉强"


def build_card(
    summary: str,
    meal: list[str],
    kcal: float,
    happiness: int,
    verdict: str,
    date: str | None = None,
    visible_minutes: int = VISIBLE_MINUTES,
) -> str:
    """按 SKILL.md 规定格式生成卡片。"""
    now = datetime.now()
    date_text = date or now.strftime("%Y-%m-%d")
    expire_at = (now + timedelta(minutes=visible_minutes)).strftime("%H:%M")

    summary_short = truncate(summary, MAX_SUMMARY_LEN * 2 - 4)

    head_title = f" 麦门心事 · {date_text} "
    # "┌─" 占 3 宽，右侧补满线，末尾 "┐"。总宽 = 3 + 标题 + 补线 + 1
    fill = max(1, CARD_TOTAL - display_width("┌─") - display_width(head_title) - display_width("┐"))
    header = "┌─" + head_title + "─" * fill + "┐"
    footer = "└" + "─" * (CARD_TOTAL - display_width("└") - display_width("┘")) + "┘"

    rows = [
        row("今天", summary_short),
        *rows_multiline("这一餐", meal),
        *rows_multiline("麦友意见", [verdict]),
        row("热量", f"{kcal:.0f} kcal　快乐指数 {happiness}（{happiness_label(happiness)}）"),
        row("可见时间", f"{visible_minutes} 分钟（{expire_at} 归档）"),
    ]

    postscript = f"20 分钟后，这顿吃完了。想补一句吗？"

    return "\n".join([header, *rows, footer, "", postscript])


def build_card_from_dict(data: dict[str, Any]) -> str:
    meal = data.get("meal") or []
    if isinstance(meal, str):
        meal = [m.strip() for m in meal.split(",") if m.strip()]

    return build_card(
        summary=str(data.get("summary", "今天不太好说")),
        meal=meal,
        kcal=float(data.get("kcal", 0)),
        happiness=int(data.get("happiness", 60)),
        verdict=str(data.get("verdict", "准吃")),
        date=data.get("date"),
        visible_minutes=int(data.get("visible_minutes", VISIBLE_MINUTES)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="麦门心事 · 心事卡片生成")
    parser.add_argument("--json", help="会话数据 JSON 文件路径")
    parser.add_argument("--summary", help="一句话心事概括（建议 20 字内）")
    parser.add_argument("--meal", help="餐食，逗号分隔")
    parser.add_argument("--kcal", type=float, default=0, help="合计热量")
    parser.add_argument("--happiness", type=int, default=60, help="快乐指数 0-100")
    parser.add_argument("--verdict", default="准吃", help="麦友综合意见")
    parser.add_argument("--minutes", type=int, default=VISIBLE_MINUTES, help="可见分钟数")
    args = parser.parse_args(argv)

    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
        print(build_card_from_dict(data))
        return 0

    if not args.summary:
        parser.error("需提供 --summary，或使用 --json 传入完整会话数据")

    meal = [m.strip() for m in (args.meal or "").split(",") if m.strip()]
    print(
        build_card(
            summary=args.summary,
            meal=meal,
            kcal=args.kcal,
            happiness=args.happiness,
            verdict=args.verdict,
            visible_minutes=args.minutes,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
