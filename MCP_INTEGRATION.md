# MCP 集成说明

本文档说明麦门心事实际调用的麦当劳 MCP Server、Tool、调用流程与业务价值。

---

## 一、接入信息

| 项目 | 内容 |
|---|---|
| MCP Server | 麦当劳中国 MCP Server |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 获取 | <https://open.mcd.cn/mcp> → 登录 → 控制台 → 激活 |
| 官方文档 | <https://github.com/M-China/mcd-mcp-server> |
| 限流 | 每 Token 每分钟 600 次请求，超出返回 429 |
| 错误码 | 401 Token 无效/过期/未提供；429 触发限流 |

**安全约定**：本仓库所有配置一律使用环境变量占位符 `${MCD_MCP_TOKEN}`，不含任何真实 Token、密钥或账号凭证（符合参赛声明第三章）。

---

## 二、实际调用的 Tool 清单

本 Skill 只使用麦当劳 MCP Server 官方已开放的 Tool，不调用任何未公开接口。下表列出**必需**与**可选**两类。

### 2.1 必需 Tool

| Tool Name | 在本项目中的作用 | 调用时机 |
|---|---|---|
| `query-meals` | 查询当前门店可售餐品菜单（分类、餐品编码、标签），作为配餐候选池 | 配餐前 |
| `query-meal-detail` | 按餐品编码查详情与套餐可替换项，用于确认套餐组成并做"改一处"建议 | 选定候选后 |
| `list-nutrition-foods` | 获取餐品营养成分（能量、蛋白质、脂肪、碳水化合物、钠、钙），是**热量报告的唯一数据来源** | 生成热量报告时 |
| `calculate-price` | 按商品列表（可含优惠券）计算金额、配送费、优惠额与应付总价 | 配餐定稿后 |

### 2.2 可选 Tool（增强体验）

| Tool Name | 在本项目中的作用 |
|---|---|
| `now-time-info` | 获取当前时间，用于时段适配（早餐/午市/下午茶/晚市/深夜）与卡片日期 |
| `campaign-calendar` | 查询当月营销活动日历，用于在快乐报告里追加一条"当期活动彩蛋"建议 |
| `query-store-coupons` | 查询当前门店可用优惠券，用于"预算内自动用券"（Roadmap v2.1） |
| `available-coupons` / `auto-bind-coupons` | 麦麦省领券，用于进一步压降推荐餐的实付价（Roadmap v2.1） |
| `delivery-query-addresses` | 用户未提供门店时，取其已有配送地址以定位门店 |
| `delivery-query-stores` | 外送场景下按地址查可配送门店 |
| `query-nearby-stores` | 到店场景下按地址查附近门店 |

### 2.3 明确不使用的 Tool

`create-order`、`cancel-order`、`query-order`、`order-list`、`mall-*`、`draw-lottery`、`party-*` 等**下单、支付、积分、抽奖、活动预约类 Tool 一律不调用**。

原因：麦门心事只负责"决定吃什么"，不代替用户完成交易。把下单权交还给用户是产品边界，也避免误扣款风险。

---

## 三、调用流程

```
用户输入（心事文本）
        │
        ▼
[1] 情绪中台（LLM 侧，无 MCP 调用）
    抽取 情绪 / 能量 / 进食动机 / 质感关键词
        │
        ▼
[2] now-time-info ──────────────► 判定时段，加载对应 calorie_ceiling
        │
        ▼
[3] 门店定位（条件触发）
    ├─ 用户给了门店编码 ─────────► 直接使用
    ├─ 用户给了地址 ─────────────► delivery-query-addresses
    │                              → delivery-query-stores / query-nearby-stores
    └─ 均无（未登录）────────────► 降级：跳过门店，仅用 list-nutrition-foods
                                    的通用餐品做搭配，输出标注"以门店实际供应为准"
        │
        ▼
[4] query-meals ────────────────► 候选餐品池（按分类/标签过滤）
        │
        ▼
[5] 搭配引擎（本地规则，见 data/emotions.yaml）
    质感关键词匹配 → 情绪约束过滤 → 时段热量上限 → 预算约束
    产出：主食 + 配餐 + 饮品（3 件）
        │
        ▼
[6] query-meal-detail ──────────► 确认套餐组成与可替换项
        │
        ▼
[7] list-nutrition-foods ───────► 逐品项营养数据
        │
        ├─────► scripts/nutrition.py 汇总 → 热量报告
        │
[8] calculate-price ────────────► 应付金额
        │
        ▼
[9] campaign-calendar（可选）───► 当期活动彩蛋，追加进快乐报告
        │
        ▼
[10] 麦友审批（LLM 侧，加载 data/mayou.yaml）
     三位虚拟麦友独立表态 + 综合意见
        │
        ▼
[11] scripts/card.py ───────────► 心事卡片（含 20 分钟可见提示）
        │
        ▼
输出到对话
```

### 关键调用约束

1. **单次会话 MCP 调用次数上限设为 8 次**，避免触发 600 次/分钟限流并控制延迟；
2. `query-meals` 每会话最多调用 1 次，结果在会话内缓存复用；
3. `list-nutrition-foods` 若返回缺失某品项，报告中明确标注"该品项营养数据暂未获取"，**不得插值估算**；
4. 任一 MCP 调用返回 401 时，立即停止流程并提示用户检查 Token，不得用记忆数据兜底。

---

## 四、业务价值

### 4.1 对用户

点餐决策的成本不在"选哪家"，而在"我这个状态该吃什么"。麦门心事把情绪作为第一输入变量，把一次冷冰冰的选品变成一次被理解的体验。三位麦友提供的不是答案，是**"有人在"的感觉**——这是现有推荐系统完全缺失的一层。

### 4.2 对麦当劳 MCP 生态

本项目是 MCP 能力的**非交易型用法示范**：不调用 `create-order`，只调用查询与计算类 Tool，却完整跑通了"菜单—详情—营养—价格"四段链路。这证明 MCP 除了支撑自动下单，也能支撑**决策辅助与内容生成**这类场景，为 MCP 的能力边界提供了一个新样本。

具体而言，本项目把 `list-nutrition-foods` 从"查一个数"提升为"生成一份可分享的报告"，把 `query-meals`+`query-meal-detail` 从"取菜单"提升为"按情绪做组合推荐"，是对既有 Tool 的**组合式复用**。

### 4.3 数据使用合规

- 仅读取公开菜单与营养数据，不写入用户账户，不创建订单；
- 用户心事原文仅在当次会话内用于情绪抽取，不落盘、不上传第三方；
- 营养数据来源标注为 `list-nutrition-foods`，不引入外部数据源。

---

## 五、降级策略

| 异常 | 降级行为 |
|---|---|
| Token 未配置 / 401 | 停止，提示用户前往 <https://open.mcd.cn/mcp> 申请，不输出任何餐品数据 |
| 429 限流 | 等待并在 3 次内重试，仍失败则输出已完成的步骤并说明中断位置 |
| 门店无法定位 | 使用通用餐品搭配，明确标注"实际供应以门店为准" |
| 营养数据缺失 | 标注缺失，热量报告只汇总已获取项，并注明"合计为已获取品项之和" |
| `query-meals` 返回为空 | 提示该门店当前无可售餐品，建议换门店或换时段 |

---

## 六、实测参数与返回结构（2026-10-21 全链路校验）

以下内容均为实际调用 `https://mcp.mcd.cn`（Store 1990102 南京仙林金鹰餐厅）后**逐字段确认**的结果，与工具 description 中的描述存在若干差异，以本节为准。

### 6.1 三个必传参数

| Tool | 必传参数 | 说明 |
|---|---|---|
| `query-meals` | `storeCode` + `orderType` + `beType` | 只传 `storeCode` 会直接报"缺少参数" |
| `query-meal-detail` | `storeCode` + `orderType` + `beType` + `code` | 单品编码字段名是 **`code`**，不是 `productCode` |
| `query-nearby-stores` | `beType` + `searchType` | `searchType=2` 为按位置搜索，需同时给 `city` / `keyword` |
| `calculate-price` | `storeCode` + `orderType` + `beType` + `items` | `items=[{productCode, quantity}]`，`quantity` 必填且 ≥1 |

`orderType`：1=到店（含到店自取与得来速），2=外送；`beType`：1=到店自取，2=麦乐送到家，5=得来速，6=企业团餐。
到店自取（`beType=1`）**不要传 `beCode`**，传了会报错。

### 6.2 返回信封结构

所有 `tools/call` 的 `structuredContent` 均为统一信封：

```json
{ "success": true, "code": 200, "message": "请求成功", "datetime": "...", "traceId": "...", "data": {...} }
```

`data` 的实际类型**不固定**，是踩过的坑：

| Tool | `data` 类型 | 取数方式 |
|---|---|---|
| `query-meals` | dict | `data.meals` 是 **code → {name, image, currentPrice, originalPrice, canWithOrder}** 的字典（111 项）；`data.categories[].meals` 只有 code + tags |
| `query-meal-detail` | dict | `data.name` / `data.description` |
| `query-nearby-stores` | **list** | 直接遍历，元素含 `storeCode` / `storeName` |
| `calculate-price` | dict | `data.productList[0].subtotal`，单位为**分**，需 ÷100 |
| `list-nutrition-foods` | **紧凑表格文本** | 不是 JSON，形如 `[160]{productName,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:` 后接逗号分隔行，需按列名解析 |
| `now-time-info` | dict | `data.datetime` / `data.formatted` |

### 6.3 价格口径

- 菜单 `currentPrice` 与 `calculate-price` 的 `subtotal`（÷100）在**单品上完全一致**，已逐项交叉校验；
- **套餐码（`9900` 开头）例外**：不带 `roundList` 直接计价时 `productList` 恒为空；而菜单挂牌 `currentPrice` 为活动价，与套餐原价不同（实测 17 项差异，全部为套餐）；
- 结论：**展示用挂牌价取 `data.meals[code].currentPrice`；确需实付金额再用 `calculate-price`**，且只对单品码调用。

### 6.4 可复现校验脚本

| 脚本 | 作用 |
|---|---|
| `scripts/mcp_smoke.py` | 一条命令跑通「时间 → 门店 → 菜单 → 详情 → 价格 → 营养」六段链路，结果落盘 `mcp_smoke_result.json` |
| `scripts/check_snapshot.py` | 校验前端快照与实时数据的一致性：在售状态、价格、热量三维比对 |

实测结果（2026-10-21 14:53）：6 个 Tool 全部真实返回；111 项餐品全部在售；营养库 158 项；前端 22 项展示菜品与实时挂牌价**逐项一致**。
