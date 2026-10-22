#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦门心事 · MCP 链路冒烟测试

用途：一条命令跑完「时间 → 门店 → 菜单 → 单品详情 → 价格 → 营养」全链路，
证明产品输出确实来自麦当劳中国 MCP，而非本地写死的数据。

用法：
    set MCD_MCP_TOKEN=xxxx        # Windows
    export MCD_MCP_TOKEN=xxxx     # macOS / Linux
    python mcp_smoke.py                    # 默认：南京 · 仙林
    python mcp_smoke.py 南京 新街口

输出：控制台逐段打印 + 同目录 mcp_smoke_result.json 落盘，便于截图取证。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcd_client import (
    McdMcpClient,
    McpError,
    _unwrap,
    get_meal_detail,
    get_menu,
    get_nearby_stores,
    get_nutrition,
    get_now,
    get_price,
    parse_nutrition_text,
)


def _stores(payload) -> list[dict]:
    data = _unwrap(payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("storeList", "list", "records", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def main(argv: list[str]) -> int:
    city = argv[1] if len(argv) > 1 else "南京"
    keyword = argv[2] if len(argv) > 2 else "仙林"
    trace: dict[str, object] = {"city": city, "keyword": keyword, "steps": []}

    try:
        client = McdMcpClient()
    except McpError as exc:
        print(f"[FATAL] {exc}")
        return 2

    # 1. 时间（决定推荐哪一餐）
    now = _unwrap(get_now(client))
    print("1) now-time-info →", now.get("formatted"), "| 时段字段:", now.get("period") or now.get("timePeriod") or "-")
    trace["steps"].append({"tool": "now-time-info", "ok": bool(now.get("datetime")), "datetime": now.get("datetime")})

    # 2. 门店
    store_list = _stores(get_nearby_stores(city, keyword, client=client))
    if not store_list:
        print(f"[FATAL] {city}{keyword} 未返回门店")
        return 3
    store = store_list[0]
    store_code = store.get("storeCode") or store.get("code")
    store_name = store.get("storeName") or store.get("name")
    print(f"2) query-nearby-stores → {store_name} ({store_code})，共 {len(store_list)} 家")
    trace["steps"].append({"tool": "query-nearby-stores", "ok": True, "store": store_name, "storeCode": store_code})

    # 3. 菜单（data.meals 是 code → {name, currentPrice, originalPrice, image} 的字典）
    menu = _unwrap(get_menu(store_code, client=client))
    categories = menu.get("categories") or []
    meals_map = menu.get("meals") if isinstance(menu.get("meals"), dict) else {}
    codes = list(meals_map.keys())
    print(f"3) query-meals → {len(categories)} 个分类 / {len(codes)} 个餐品")
    trace["steps"].append({"tool": "query-meals", "ok": bool(codes), "categories": len(categories), "meals": len(codes)})
    if not codes:
        print("[FATAL] 菜单为空，后续步骤跳过")
        return 4

    # 4. 单品详情：取 3 个单品码（9900 开头多为套餐码）
    singles = [c for c in codes if not c.startswith("9900")][:3]
    details = []
    for code in singles:
        try:
            d = _unwrap(get_meal_detail(code, store_code, client=client))
            details.append({"code": code, "name": d.get("name") or d.get("productName") or meals_map[code].get("name")})
        except McpError as exc:
            details.append({"code": code, "error": str(exc)})
    print("4) query-meal-detail →", " / ".join(f"{d.get('code')}={d.get('name') or 'ERR'}" for d in details))
    trace["steps"].append({"tool": "query-meal-detail", "ok": all(d.get("name") for d in details), "sample": details})

    # 5. 价格（逐项，单品码），并与菜单 currentPrice 交叉校验
    price_codes = [c for c in codes if not c.startswith("9900")][:6]
    prices = get_price(price_codes, store_code, client=client)
    ok_prices = [p for p in prices if p.get("price") is not None]
    print(f"5) calculate-price → {len(ok_prices)}/{len(prices)} 项取到价格（与菜单挂牌价交叉校验）")
    consistent = 0
    for p in prices:
        listed = meals_map.get(p["code"], {}).get("currentPrice")
        if p.get("price") is not None:
            same = listed is not None and abs(float(listed) - p["price"]) < 0.01
            consistent += 1 if same else 0
            print(f"     {p['code']} {p['name']} 计价 ¥{p['price']} / 挂牌 ¥{listed} {'✓' if same else '≠'}")
        else:
            print(f"     {p['code']} ✗ {p['error']}")
    trace["steps"].append({"tool": "calculate-price", "ok": bool(ok_prices), "prices": prices, "consistent": consistent})

    # 6. 营养
    raw = get_nutrition(client)
    rows = parse_nutrition_text(raw.get("data")) if isinstance(raw, dict) else []
    print(f"6) list-nutrition-foods → {len(rows)} 条营养记录，样例：", end="")
    if rows:
        s = rows[0]
        print(f"{s.get('productName')} {s.get('energyKcal')} kcal / 蛋白 {s.get('protein')} g")
    else:
        print("解析失败")
    trace["steps"].append({"tool": "list-nutrition-foods", "ok": bool(rows), "rows": len(rows)})

    out = Path(__file__).with_name("mcp_smoke_result.json")
    out.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n链路结果已写入 {out.name}")

    all_ok = all(s.get("ok") for s in trace["steps"])
    print("结论：" + ("6 个 MCP 工具全部真实返回，输出可溯源" if all_ok else "存在未通过步骤，见上"))
    return 0 if all_ok else 5


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
