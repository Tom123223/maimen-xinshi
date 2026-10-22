#!/usr/bin/env node
/**
 * 麦门心事 · 本地体验服务器
 *
 * 作用：把麦当劳 MCP 的调用放在服务端，浏览器只跟本机通信。
 * 这样 Token 不出现在页面里，也不会撞 CORS。
 *
 * 启动：node server.js  →  http://localhost:8765
 * 接口：
 *   GET  /api/live?codes=1450,1406,...   取这些编码的实时菜名/挂牌价/营养
 *   GET  /api/health                     健康检查
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PORT = Number(process.env.PORT || 8765);
const STORE = process.env.MCD_STORE || '1990102';
const WEB_DIR = path.join(__dirname, '..', 'maimen-xinshi-web');

// ---------- 读取 Token（与 WorkBuddy 共用同一份配置，不复制、不落盘） ----------
function readToken() {
  const cfgPath = path.join(os.homedir(), '.workbuddy', 'mcp.json');
  const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
  const auth = cfg?.mcpServers?.['mcd-mcp']?.headers?.Authorization || '';
  const token = auth.replace(/^Bearer\s+/i, '').trim();
  if (!token) throw new Error('mcp.json 里没找到 mcd-mcp 的 Authorization');
  return token;
}

// ---------- 极简 MCP 客户端（Streamable HTTP / JSON-RPC 2.0） ----------
const ENDPOINT = 'https://mcp.mcd.cn';
const PROTO = '2025-06-18';
let sessionId = null;
let rpcId = 0;

function parseBody(text) {
  const t = (text || '').trim();
  if (!t) return {};
  if (t.startsWith('{')) return JSON.parse(t);
  for (const line of t.split('\n')) {
    if (line.startsWith('data:')) {
      const chunk = line.slice(5).trim();
      if (chunk && chunk !== '[DONE]') return JSON.parse(chunk);
    }
  }
  throw new Error('无法解析 MCP 响应');
}

async function rpc(method, params, notify) {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
    Authorization: 'Bearer ' + readToken(),
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
  };
  if (sessionId) headers['Mcp-Session-Id'] = sessionId;
  const body = { jsonrpc: '2.0', method };
  if (params !== undefined) body.params = params;
  if (!notify) body.id = ++rpcId;

  const res = await fetch(ENDPOINT, { method: 'POST', headers, body: JSON.stringify(body) });
  const sid = res.headers.get('mcp-session-id');
  if (sid) sessionId = sid;
  const text = await res.text();
  if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + text.slice(0, 160));
  if (notify) return {};
  const data = parseBody(text);
  if (data.error) throw new Error('MCP ' + JSON.stringify(data.error).slice(0, 160));
  return data.result || {};
}

async function init() {
  if (sessionId) return;
  await rpc('initialize', {
    protocolVersion: PROTO,
    capabilities: {},
    clientInfo: { name: 'maimen-xinshi-demo', version: '1.0.0' }
  });
  await rpc('notifications/initialized', undefined, true);
}

async function callTool(name, args) {
  await init();
  const result = await rpc('tools/call', { name, arguments: args || {} });
  return result.structuredContent || result;
}

// ---------- 营养表解析（接口返回的是紧凑表格文本，不是 JSON） ----------
function parseNutrition(text) {
  if (typeof text !== 'string' || !text.trim().startsWith('[')) return [];
  const nl = text.indexOf('\n');
  const head = nl === -1 ? text : text.slice(0, nl);
  const body = nl === -1 ? '' : text.slice(nl + 1);
  const cols = head.slice(head.indexOf('{') + 1, head.lastIndexOf('}')).split(',').map(s => s.trim());
  const rows = [];
  for (const line of body.split('\n')) {
    const s = line.trim();
    if (!s) continue;
    const cells = s.split(',').map(c => c.trim());
    if (cells.length !== cols.length) continue;
    const row = {};
    cols.forEach((c, i) => { row[c] = cells[i] === 'null' ? null : cells[i]; });
    rows.push(row);
  }
  return rows;
}

// ---------- 菜名归一化匹配 ----------
// 原则：宁可判为「未获取」，也不能错配到别的品项 —— 热量报错比缺数据更糟。
function norm(s) {
  return String(s || '')
    .replace(/[（(].*?[）)]/g, '')
    .replace(/[™·\s\u3000]/g, '')
    .replace(/汉堡/g, '')
    .replace(/套餐/g, '')
    .toLowerCase();
}

// 去掉规格词后的「基名」，用于同品项不同规格的匹配（中薯条 ↔ 薯条）
function base(s) {
  return norm(s).replace(/(中杯|大杯|小杯|中份|大份|小份|盒装|瓶装|单份|中|大|小|杯)$/g, '');
}

function buildNutritionIndex(rows) {
  const idx = new Map();
  for (const r of rows) {
    const name = r.productName;
    if (!name) continue;
    if (!idx.has(name)) idx.set(name, r);
    if (!idx.has('#' + norm(name))) idx.set('#' + norm(name), r);
    if (!idx.has('~' + base(name))) idx.set('~' + base(name), r);
  }
  return idx;
}

function matchNutrition(idx, name) {
  if (idx.has(name)) return { row: idx.get(name), how: 'exact' };
  const n = norm(name);
  if (n && idx.has('#' + n)) return { row: idx.get('#' + n), how: 'normalized' };
  const b = base(name);
  if (b && idx.has('~' + b)) return { row: idx.get('~' + b), how: 'size-tolerant' };
  return { row: null, how: 'none' };
}

// ---------- 同日快照（用作营养回落的二级来源） ----------
// 换档后菜单会改菜名（如「中薯条」→「薯条」），名称匹配会失效；
// 但门店编码是餐品唯一标识，此时按编码回落到同日快照里「精确匹配」过的营养值。
const SNAPSHOT_PATH = path.join(__dirname, '..', 'maimen-xinshi', 'data', 'snapshot', 'real-joined-all.json');
let snapshotByCode = new Map();
try {
  const rows = JSON.parse(fs.readFileSync(SNAPSHOT_PATH, 'utf8'));
  for (const r of rows) {
    if (Number(r.mScore) === 100 && r.kcal !== null && r.kcal !== undefined) {
      snapshotByCode.set(String(r.code), r);
    }
  }
  console.log('已载入同日营养快照：' + snapshotByCode.size + ' 项（仅保留精确匹配）');
} catch (e) {
  console.warn('未载入营养快照，名称匹配失败的品项将显示「暂未获取」：' + e.message);
}

// ---------- 业务接口：按编码取实时数据 ----------
const cache = new Map();
const TTL = 90 * 1000;

async function getLive(codes) {
  const key = STORE + '|' + codes.join(',');
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL) return Object.assign({ cached: true }, hit.data);

  const [menuRes, nutriRes] = await Promise.all([
    callTool('query-meals', { storeCode: STORE, orderType: 1, beType: 1 }),
    callTool('list-nutrition-foods', {})
  ]);

  const menuData = menuRes.data || {};
  const meals = menuData.meals || {};
  const nutriRows = parseNutrition(nutriRes.data);
  const nutriIdx = buildNutritionIndex(nutriRows);

  const items = {};
  const stat = { exact: 0, normalized: 0, 'size-tolerant': 0, none: 0, absent: 0 };
  let missingPrice = 0;
  let missingKcal = 0;

  for (const code of codes) {
    const m = meals[code];
    if (!m) { items[code] = { absent: true }; stat.absent++; continue; }
    let hit = matchNutrition(nutriIdx, m.name);
    let n = hit.row;
    let how = hit.how;

    // 名称匹配不上时，按编码回落同日快照（不跨品项猜测）
    if (!n) {
      const snap = snapshotByCode.get(String(code));
      if (snap) {
        n = { productName: snap.mName, energyKcal: snap.kcal, protein: snap.protein, fat: snap.fat, carbohydrate: snap.carb, sodium: snap.na };
        how = 'code-cache';
      }
    }
    stat[how] = (stat[how] || 0) + 1;

    const price = Number(m.currentPrice);
    if (!Number.isFinite(price)) missingPrice++;
    const kcal = n && n.energyKcal !== null ? Number(n.energyKcal) : null;
    if (kcal === null) missingKcal++;
    items[code] = {
      name: m.name,
      price: Number.isFinite(price) ? price : null,
      originalPrice: Number(m.originalPrice) || null,
      canWithOrder: m.canWithOrder,
      image: m.image || null,
      kcal,
      p: n ? Number(n.protein) : null,
      f: n ? Number(n.fat) : null,
      c: n ? Number(n.carbohydrate) : null,
      na: n ? Number(n.sodium) : null,
      matchedAs: n ? n.productName : null,
      matchHow: how
    };
  }

  const payload = {
    at: new Date().toISOString(),
    store: STORE,
    totalMenuItems: Object.keys(meals).length,
    nutritionRows: nutriRows.length,
    match: stat,
    missingPrice,
    missingKcal,
    items
  };
  cache.set(key, { at: Date.now(), data: payload });
  return Object.assign({ cached: false }, payload);
}

// ---------- HTTP ----------
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.json': 'application/json; charset=utf-8', '.md': 'text/markdown; charset=utf-8' };

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost:' + PORT);
  const send = (code, obj) => {
    const body = typeof obj === 'string' ? obj : JSON.stringify(obj);
    res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(body);
  };

  if (url.pathname === '/api/health') {
    return send(200, { ok: true, store: STORE, session: !!sessionId });
  }

  if (url.pathname === '/api/live') {
    const codes = (url.searchParams.get('codes') || '').split(',').map(s => s.trim()).filter(Boolean);
    if (!codes.length) return send(400, { ok: false, error: '缺少 codes' });
    try {
      const data = await getLive(codes);
      return send(200, Object.assign({ ok: true }, data));
    } catch (err) {
      console.error('[live]', err.message);
      sessionId = null;
      return send(502, { ok: false, error: err.message });
    }
  }

  // 静态文件
  const rel = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname.slice(1));
  const file = path.join(WEB_DIR, rel);
  if (!file.startsWith(WEB_DIR)) return send(403, { ok: false });
  fs.readFile(file, (err, buf) => {
    if (err) return send(404, { ok: false, error: 'not found' });
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream' });
    res.end(buf);
  });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('麦门心事 · 体验版已启动');
  console.log('  地址：http://localhost:' + PORT);
  console.log('  门店：' + STORE + '（到店自取）');
  console.log('  数据：后端实时调用 mcp.mcd.cn，Token 取自 ~/.workbuddy/mcp.json');
});
