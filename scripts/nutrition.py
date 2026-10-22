#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热量报告计算模块。

设计原则：
1. 只做聚合与占比换算，绝不生成/估算营养数值。
2. 缺失数据的品项必须显式标注，不允许插值填充。
3. 每日参考摄入与时段占比来自 data/emotions.yaml，不硬编码在代码里。

用法：
    python nutrition.py --items "麦辣鸡腿堡,中薯条,可口可乐(中)" --slot 晚市
    python nutrition.py --json input.json --slot 深夜 --format text
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# 营养字段中文名 -> 内部键
NUTRIENT_FIELDS = {
    "能量": "kcal",
    "calories": "kcal",
    "energy": "kcal",
    "蛋白质": "protein_g",
    "protein": "protein_g",
    "脂肪": "fat_g",
    "fat": "fat_g",
    "碳水化合物": "carb_g",
    "carbohydrate": "carb_g",
    "钠": "sodium_mg",
    "sodium": "sodium_mg",
    "钙": "calcium_mg",
    "calcium": "calcium_mg",
}

FIELD_LABEL = {
    "kcal": "能量(kcal)",
    "protein_g": "蛋白质(g)",
    "fat_g": "脂肪(g)",
    "carb_g": "碳水化合物(g)",
    "sodium_mg": "钠(mg)",
    "calcium_mg": "钙(mg)",
}

# 缺省参考值（与 data/emotions.yaml 中 daily_reference 保持一致，
# YAML 不可读时作为保底，保证脚本可独立运行）
DEFAULT_REFERENCE = {
    "kcal": 2000,
    "protein_g": 60,
    "fat_g": 65,
    "carb_g": 300,
    "sodium_mg": 2000,
}

DEFAULT_SLOT_SHARE = {
    "早餐": 0.25,
    "午市": 0.40,
    "下午茶": 0.12,
    "晚市": 0.35,
    "深夜": 0.20,
}


def load_reference(yaml_path: Path | None = None) -> tuple[dict[str, float], dict[str, float]]:
    """从 emotions.yaml 读取每日参考摄入与时段占比。"""
    if yaml_path is None:
        yaml_path = Path(__file__).resolve().parent.parent / "data" / "emotions.yaml"

    if yaml is None or not yaml_path.exists():
        return dict(DEFAULT_REFERENCE), dict(DEFAULT_SLOT_SHARE)

    try:
        doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return dict(DEFAULT_REFERENCE), dict(DEFAULT_SLOT_SHARE)

    ref_block = doc.get("daily_reference") or {}
    reference = dict(DEFAULT_REFERENCE)
    for key in DEFAULT_REFERENCE:
        if isinstance(ref_block.get(key), (int, float)):
            reference[key] = float(ref_block[key])

    slot_share = dict(DEFAULT_SLOT_SHARE)
    raw_share = ref_block.get("slot_share") or {}
    for slot, ratio in raw_share.items():
        if isinstance(ratio, (int, float)):
            slot_share[str(slot)] = float(ratio)

    return reference, slot_share


def normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    """把 MCP 返回的品项规整为统一结构。

    支持两种输入：
    - 扁平的 {name, kcal, protein_g, ...}
    - 嵌套的 {name, nutrition: {能量: ..., 蛋白质: ...}}
    """
    name = str(raw.get("name") or raw.get("productName") or raw.get("餐品名称") or "未命名品项")
    flat: dict[str, Any] = {"name": name}

    sources = []
    if isinstance(raw.get("nutrition"), dict):
        sources.append(raw["nutrition"])
    if isinstance(raw.get("nutrients"), dict):
        sources.append(raw["nutrients"])
    sources.append(raw)

    for source in sources:
        for src_key, value in source.items():
            key = NUTRIENT_FIELDS.get(str(src_key).strip().lower()) or NUTRIENT_FIELDS.get(str(src_key).strip())
            if key and key not in flat:
                num = _to_number(value)
                if num is not None:
                    flat[key] = num

    return flat


def _to_number(value: Any) -> float | None:
    """把 '340 kcal' / '12.5g' / 340 统一转 float，无法解析返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    cleaned = value.strip().lower()
    for unit in ("kcal", "kj", "mg", "g", "千卡", "克", "毫克", "千焦"):
        cleaned = cleaned.replace(unit, "")
    cleaned = cleaned.strip()

    try:
        return float(cleaned)
    except ValueError:
        return None


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总多品项营养数据，同时记录缺失项。"""
    totals: dict[str, float] = {}
    missing: list[str] = []

    # 判定规则：必须拿到正数的 kcal 才视为该品项数据有效。
    # 0 / 缺失 / 无法解析 一律归入 missing，避免把"0 kcal"这种占位值当成真实数据。
    for item in items:
        norm = normalize_item(item)
        kcal = norm.get("kcal")
        if not isinstance(kcal, (int, float)) or kcal <= 0:
            missing.append(norm["name"])
            continue
        for key, value in norm.items():
            if key in ("name",):
                continue
            if key in NUTRIENT_FIELDS.values():
                totals[key] = totals.get(key, 0.0) + float(value)

    return {
        "item_count": len(items),
        "valid_count": len(items) - len(missing),
        "totals": totals,
        "missing": missing,
    }


def build_report(
    items: list[dict[str, Any]],
    slot: str = "晚市",
    yaml_path: Path | None = None,
) -> dict[str, Any]:
    """生成热量报告数据。"""
    reference, slot_share = load_reference(yaml_path)
    agg = aggregate(items)
    totals = agg["totals"]

    ref_kcal = reference["kcal"] * slot_share.get(slot, 0.35)
    kcal = totals.get("kcal", 0.0)

    shares: dict[str, float] = {}
    for key in ("protein_g", "fat_g", "carb_g", "sodium_mg"):
        if key in totals and reference.get(key):
            shares[key] = round(totals[key] / reference[key] * 100, 1)

    return {
        "slot": slot,
        "kcal": round(kcal, 1),
        "items": agg["valid_count"],
        "totals": {k: round(v, 1) for k, v in totals.items()},
        "daily_share_pct": round(kcal / reference["kcal"] * 100, 1) if reference["kcal"] else 0.0,
        "slot_share_pct": round(kcal / ref_kcal * 100, 1) if ref_kcal else 0.0,
        "nutrient_share_pct": shares,
        "missing": agg["missing"],
        "reference": reference,
    }


def check_ceiling(kcal: float, slot: str, yaml_path: Path | None = None) -> dict[str, Any]:
    """对照时段热量上限，返回是否超限。上限来自 emotions.yaml 的 time_slot。"""
    ceilings = {"早餐": 650, "午市": 1100, "下午茶": 600, "晚市": 1050, "深夜": 850}

    if yaml_path is None:
        yaml_path = Path(__file__).resolve().parent.parent / "data" / "emotions.yaml"
    if yaml is not None and yaml_path.exists():
        try:
            doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            for name, block in (doc.get("time_slot") or {}).items():
                if isinstance(block, dict) and isinstance(block.get("calorie_ceiling"), (int, float)):
                    ceilings[str(name)] = int(block["calorie_ceiling"])
        except Exception:
            pass

    ceiling = ceilings.get(slot)
    if ceiling is None:
        return {"ceiling": None, "exceeded": False, "over_by": 0.0}

    return {
        "ceiling": ceiling,
        "exceeded": kcal > ceiling,
        "over_by": round(max(0.0, kcal - ceiling), 1),
    }


def format_text(report: dict[str, Any]) -> str:
    """按 SKILL.md 规定的输出格式渲染热量报告。"""
    totals = report["totals"]
    lines = [
        f"热量报告   合计 {report['kcal']:.0f} kcal",
        "           "
        + "　".join(
            f"{FIELD_LABEL[k].split('(')[0]} {totals[k]:.1f} {FIELD_LABEL[k].split('(')[1][:-1]}"
            for k in ("protein_g", "fat_g", "carb_g", "sodium_mg")
            if k in totals
        ),
        f"           占每日参考摄入约 {report['daily_share_pct']:.0f}%，"
        f"约为{report['slot']}参考量的 {report['slot_share_pct']:.0f}%",
    ]

    if report["missing"]:
        lines.append(f"           注：{'、'.join(report['missing'])} 营养数据暂未获取，合计为已获取品项之和")

    return "\n".join(lines)


def load_items_from_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items") or data.get("products") or []
    if not isinstance(data, list):
        raise ValueError("JSON 需为数组，或包含 items/products 数组的对象")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="麦门心事 · 热量报告计算")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--items", help="逗号分隔的品项名，如 '麦辣鸡腿堡,中薯条'，需配合 --json 提供营养数据")
    group.add_argument("--json", help="品项营养数据 JSON 文件路径")
    parser.add_argument("--slot", default="晚市", choices=list(DEFAULT_SLOT_SHARE), help="时段，默认晚市")
    parser.add_argument("--format", default="text", choices=["text", "json"], help="输出格式")
    args = parser.parse_args(argv)

    if args.json:
        items = load_items_from_json(Path(args.json))
    else:
        # 仅给名称无法计算，必须由 MCP 提供数据；此处明确报错而非编造
        print(
            "错误：仅提供品项名称无法计算热量。请先用 list-nutrition-foods 获取营养数据，"
            "保存为 JSON 后通过 --json 传入。",
            file=sys.stderr,
        )
        return 2

    report = build_report(items, slot=args.slot)
    ceiling = check_ceiling(report["kcal"], args.slot)
    report["ceiling_check"] = ceiling

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
        if ceiling["ceiling"] is not None and ceiling["exceeded"]:
            print(f"           提示：已超过{args.slot}参考上限 {ceiling['ceiling']} kcal，超出 {ceiling['over_by']:.0f} kcal")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
