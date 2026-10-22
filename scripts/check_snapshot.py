#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据新鲜度校验：页面用的菜品快照 vs 当前 MCP 实时返回

校验三件事：
1. 快照里每个编码，是否仍在门店在售菜单中
2. 快照价格 是否等于菜单挂牌价 currentPrice
3. 快照热量 是否等于营养库 energyKcal

用法：set MCD_MCP_TOKEN=xxx && python check_snapshot.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcd_client import McdMcpClient, McpError, _unwrap, get_menu, get_nutrition, parse_nutrition_text

STORE = "1990102"
ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "snapshot" / "real-joined-all.json"


def main() -> int:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    try:
        client = McdMcpClient()
    except McpError as exc:
        print(f"[FATAL] {exc}")
        return 2

    menu = _unwrap(get_menu(STORE, client=client))
    meals = menu.get("meals") or {}
    nutri_rows = parse_nutrition_text(get_nutrition(client).get("data") or "")
    nutri = {r["productName"]: r for r in nutri_rows}

    print(f"实时菜单 {len(meals)} 项 / 营养库 {len(nutri)} 条 / 快照 {len(snapshot)} 项\n")

    gone, price_diff, kcal_diff, ok = [], [], [], 0
    for row in snapshot:
        code = str(row.get("code"))
        if code not in meals:
            gone.append(f"{code}({row.get('name')})")
            continue
        listed = meals[code].get("currentPrice")
        snap_price = row.get("price")
        if snap_price is not None and listed is not None and abs(float(listed) - float(snap_price)) > 0.01:
            price_diff.append(f"{row.get('name')} 快照¥{snap_price} → 现价¥{listed}")
        kcal = row.get("kcal")
        n = nutri.get(row.get("mName") or "")
        if kcal is not None and n and n.get("energyKcal") and abs(int(n["energyKcal"]) - int(kcal)) > 1:
            kcal_diff.append(f"{row.get('name')} 快照{kcal} → 现{n['energyKcal']}")
        ok += 1

    print(f"仍在售: {ok}/{len(snapshot)}")
    for label, items in (("已下架", gone), ("价格变动", price_diff), ("热量变动", kcal_diff)):
        if items:
            print(f"{label} {len(items)} 项：")
            for it in items:
                print("   - " + it)
        else:
            print(f"{label}: 无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
