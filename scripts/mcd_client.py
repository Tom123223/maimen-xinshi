#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦当劳中国 MCP Client 封装（Streamable HTTP）。

仅依赖标准库，便于在 WorkBuddy Skill 环境中零安装运行。

安全约定：
- Token 只从环境变量 MCD_MCP_TOKEN 读取，绝不写入文件或日志
- 不提供下单类工具的便捷方法（本 Skill 不下单）

用法：
    export MCD_MCP_TOKEN=xxxx           # Windows: set MCD_MCP_TOKEN=xxxx
    python mcd_client.py --list-tools
    python mcd_client.py --call query-meals --args '{"storeCode":"..."}'

作为库使用：
    from mcd_client import McdMcpClient
    client = McdMcpClient()
    tools = client.list_tools()
    result = client.call_tool("list-nutrition-foods", {})
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
TOKEN_ENV = "MCD_MCP_TOKEN"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "maimen-xinshi"
CLIENT_VERSION = "1.0.0"

# 明确禁止调用的工具：本项目只做决策辅助，不代用户交易
FORBIDDEN_TOOLS = frozenset(
    {
        "create-order",
        "cancel-order",
        "mall-create-order",
        "party-order-create",
        "draw-lottery",
        "auto-bind-coupons",
    }
)


class McpError(RuntimeError):
    """MCP 调用异常。"""


class McdMcpClient:
    """极简 MCP over Streamable HTTP 客户端。"""

    # 模拟浏览器 UA，与 WorkBuddy 内 MCP 连接器行为一致
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

    def __init__(
        self,
        token: str | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.token = token or os.environ.get(TOKEN_ENV, "")
        if not self.token:
            raise McpError(
                f"未找到 MCP Token。请设置环境变量 {TOKEN_ENV}，"
                "Token 可在 https://open.mcd.cn/mcp 登录后于控制台激活获取。"
            )
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._session_id: str | None = None
        self._request_id = 0
        self._initialized = False

    # ---------- 底层 HTTP ----------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": self.USER_AGENT,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> tuple[str, str | None]:
        """发送一次 JSON-RPC 请求，返回 (响应体, session_id)。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers=self._headers(),
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    session_id = response.headers.get("Mcp-Session-Id")
                    return raw, session_id
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code == 401:
                    raise McpError(
                        "MCP Token 无效、已过期或未提供(401)。"
                        "请到 https://open.mcd.cn/mcp 重新获取，并更新 MCD_MCP_TOKEN。"
                    ) from exc
                if exc.code == 429:
                    last_error = McpError(f"触发限流(429)，第 {attempt} 次重试")
                    time.sleep(2 * attempt)
                    continue
                raise McpError(f"HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                last_error = McpError(f"网络错误: {exc.reason}")
                time.sleep(1 + attempt)
                continue

        raise last_error or McpError("MCP 请求失败，原因未知")

    @staticmethod
    def _parse_response(raw: str) -> dict[str, Any]:
        """兼容 JSON 与 SSE 两种响应体。"""
        text = raw.strip()
        if not text:
            return {}

        if text.startswith("{"):
            return json.loads(text)

        # SSE: 逐行找 data: 开头的 JSON
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
        raise McpError(f"无法解析 MCP 响应: {text[:300]}")

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> dict[str, Any]:
        self._request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = self._request_id

        raw, session_id = self._post(payload)
        if session_id:
            self._session_id = session_id

        if notify:
            return {}

        data = self._parse_response(raw)
        if "error" in data:
            err = data["error"]
            raise McpError(f"MCP 错误 {err.get('code')}: {err.get('message')}")
        return data.get("result") or {}

    # ---------- 协议层 ----------

    def initialize(self, force: bool = False) -> dict[str, Any]:
        if self._initialized and not force:
            return {"status": "already_initialized"}

        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        self._rpc("notifications/initialized", notify=True)
        self._initialized = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list", {})
        return result.get("tools") or []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name in FORBIDDEN_TOOLS:
            raise McpError(
                f"拒绝调用 {name}：本 Skill 只做点餐决策辅助，不代用户完成下单、支付或积分操作。"
            )
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

        # 优先返回结构化内容
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result["structuredContent"]

        contents = result.get("content") or []
        texts = [c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text"]
        joined = "\n".join(t for t in texts if t)
        if not joined:
            return result

        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined


# ---------- 常用快捷方法（供 Skill 内部脚本调用） ----------
# 参数口径依据 2026-10-21 实测 35 个工具的 inputSchema：
#   query-meals      required: storeCode + orderType + beType
#   query-meal-detail required: storeCode + orderType + beType + code
#   calculate-price  items:[{productCode, quantity}]，单次批量易被判空，建议逐项调用
#   query-nearby-stores required: beType + searchType（searchType=2 按位置搜索）

DEFAULT_ORDER_TYPE = 1  # 1-到店（含到店自取+得来速），2-外送
DEFAULT_BE_TYPE = 1     # 1-到店取餐，2-麦乐送到家，5-得来速，6-企业团餐


def get_menu(
    store_code: str,
    order_type: int = DEFAULT_ORDER_TYPE,
    be_type: int = DEFAULT_BE_TYPE,
    be_code: str | None = None,
    client: McdMcpClient | None = None,
) -> Any:
    """查询门店菜单。到店自取(beType=1)不要传 beCode。"""
    client = client or McdMcpClient()
    args: dict[str, Any] = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    return client.call_tool("query-meals", args)


def get_meal_detail(
    code: str,
    store_code: str,
    order_type: int = DEFAULT_ORDER_TYPE,
    be_type: int = DEFAULT_BE_TYPE,
    client: McdMcpClient | None = None,
) -> Any:
    """查询单品详情。注意参数名是 code，不是 productCode。"""
    client = client or McdMcpClient()
    return client.call_tool(
        "query-meal-detail",
        {"code": code, "storeCode": store_code, "orderType": order_type, "beType": be_type},
    )


def get_nutrition(client: McdMcpClient | None = None) -> Any:
    client = client or McdMcpClient()
    return client.call_tool("list-nutrition-foods", {})


def _unwrap(payload: Any) -> Any:
    """call_tool 返回完整信封 {success, code, message, datetime, traceId, data}，这里取出 data。

    注意 data 的实际类型不固定：多为 dict，个别工具（如 query-nearby-stores）为 list，
    list-nutrition-foods 甚至是紧凑表格文本。
    """
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def get_price(
    codes: list[str],
    store_code: str,
    order_type: int = DEFAULT_ORDER_TYPE,
    be_type: int = DEFAULT_BE_TYPE,
    quantities: dict[str, int] | None = None,
    client: McdMcpClient | None = None,
) -> list[dict[str, Any]]:
    """逐项查询价格。

    实测要点：
    - 套餐码（9900xxxxxxx）不带 roundList 时 productList 恒为空，属正常现象；
    - 单品码（5xxxxx / 4 位数）可直接取价；
    - 返回价格单位为「分」，此处统一换算为元。
    """
    client = client or McdMcpClient()
    quantities = quantities or {}
    out: list[dict[str, Any]] = []
    for code in codes:
        payload = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": [{"productCode": code, "quantity": quantities.get(code, 1)}],
        }
        try:
            data = _unwrap(client.call_tool("calculate-price", payload))
            rows = (data.get("productList") if isinstance(data, dict) else None) or []
            if rows:
                row = rows[0]
                out.append(
                    {
                        "code": code,
                        "name": row.get("productName") or row.get("name"),
                        "price": round(float(row.get("subtotal", 0)) / 100, 2),
                        "error": None,
                    }
                )
            else:
                out.append({"code": code, "name": None, "price": None, "error": "productList 为空（多为套餐码，需 roundList）"})
        except McpError as exc:
            out.append({"code": code, "name": None, "price": None, "error": str(exc)})
        time.sleep(0.12)  # 规避限流
    return out


def parse_nutrition_text(text: str) -> list[dict[str, Any]]:
    """解析 list-nutrition-foods 的 data 字段。

    该字段不是 JSON，而是紧凑表格文本：
        [160]{productName,energyKj,energyKcal,...}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
    """
    if not isinstance(text, str) or not text.strip().startswith("["):
        return []
    head, _, body = text.partition("\n")
    cols_part = head[head.find("{") + 1: head.rfind("}")]
    cols = [c.strip() for c in cols_part.split(",") if c.strip()]
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(cols):
            continue
        rows.append(dict(zip(cols, [None if c in ("null", "") else c for c in cells])))
    return rows


def get_nearby_stores(
    city: str,
    keyword: str,
    be_type: int = DEFAULT_BE_TYPE,
    client: McdMcpClient | None = None,
) -> Any:
    """按城市+关键词搜索到店自提门店（searchType=2 按位置搜索）。"""
    client = client or McdMcpClient()
    return client.call_tool(
        "query-nearby-stores",
        {"beType": be_type, "searchType": 2, "city": city, "keyword": keyword},
    )


def get_campaign_calendar(client: McdMcpClient | None = None) -> Any:
    client = client or McdMcpClient()
    return client.call_tool("campaign-calendar", {})


def get_now(client: McdMcpClient | None = None) -> Any:
    client = client or McdMcpClient()
    return client.call_tool("now-time-info", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="麦当劳中国 MCP Client（麦门心事）")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="MCP 接入地址")
    parser.add_argument("--list-tools", action="store_true", help="列出服务端可用工具")
    parser.add_argument("--call", help="要调用的工具名")
    parser.add_argument("--args", default="{}", help="工具参数 JSON")
    args = parser.parse_args(argv)

    try:
        client = McdMcpClient(endpoint=args.endpoint)

        if args.list_tools:
            tools = client.list_tools()
            print(f"共 {len(tools)} 个工具：\n")
            for tool in tools:
                print(f"- {tool.get('name')}: {tool.get('description', '')[:80]}")
            return 0

        if args.call:
            params = json.loads(args.args)
            result = client.call_tool(args.call, params)
            print(json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result)
            return 0

        parser.print_help()
        return 1

    except McpError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"参数 JSON 解析失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
