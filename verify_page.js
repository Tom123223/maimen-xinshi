const fs = require('fs');
const { execFileSync } = require('child_process');
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DIR = 'C:/Users/jsitc/WorkBuddy/2026-10-21-11-36-10/maimen-xinshi-web/';
const html = fs.readFileSync(DIR + 'index.html', 'utf8');
const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];

console.log('=== 1. 语法 ===');
try { new Function(code); console.log('OK'); }
catch (e) { console.log('FAIL:', e.message); process.exit(1); }

console.log('\n=== 2. 数据集与真实快照逐项核对 ===');
const real = JSON.parse(fs.readFileSync('C:/Users/jsitc/WorkBuddy/2026-10-21-11-36-10/maimen-xinshi/data/snapshot/real-joined-all.json', 'utf8'));
const a = code.indexOf('var MEALS');
const b = code.indexOf('/* 门店清单');
const api = new Function(code.slice(a, b) + '; return MEALS;')();
console.log('页面菜品数:', api.length);

let bad = 0;
api.forEach(m => {
  const r = real.find(x => x.code === m.code);
  if (!r) { console.log('  ✗ ' + m.name + ' 编码 ' + m.code + ' 不在真实数据中'); bad++; return; }
  const diffs = [];
  if (Math.abs(r.price - m.price) > 1e-6) diffs.push('价格 ' + m.price + '≠' + r.price);
  if (r.kcal !== m.kcal) diffs.push('kcal ' + m.kcal + '≠' + r.kcal);
  if (r.protein !== m.p) diffs.push('蛋白 ' + m.p + '≠' + r.protein);
  if (r.fat !== m.f) diffs.push('脂肪 ' + m.f + '≠' + r.fat);
  if (r.carb !== m.c) diffs.push('碳水 ' + m.c + '≠' + r.carb);
  if (r.na !== m.na) diffs.push('钠 ' + m.na + '≠' + r.na);
  if (r.name !== m.name) diffs.push('菜名 ' + m.name + '≠' + r.name);
  if (diffs.length) { console.log('  ✗ ' + m.name + ': ' + diffs.join('; ')); bad++; }
});
console.log(bad === 0 ? '  全部 ' + api.length + ' 项与 MCP 真实返回一致' : '  有 ' + bad + ' 项不一致');

console.log('\n=== 3. 卡片等宽 ===');
const cardFns = code.slice(code.indexOf('function dw('), code.indexOf('function buildCardHTML'));
const cf = new Function(cardFns + '; return {dw:dw, cardLines:cardLines};')();
const sample = {
  short: '方案被退回第三次',
  meal: { items: api.filter(x => x.cat === '主食').slice(0, 1).concat(api.filter(x => x.cat === '配餐').slice(0, 1), api.filter(x => x.cat === '饮品').slice(0, 1)) },
  nutri: { kcal: 682 }, happy: { comfort: 88 }, mayou: { verdict: '准吃但改一处' }
};
const lines = cf.cardLines(sample).split('\n');
lines.forEach(l => console.log('  ' + String(cf.dw(l)).padStart(3) + ' | ' + l));
const ws = lines.slice(0, lines.findIndex(l => l === '') === -1 ? 8 : lines.findIndex(l => l === '')).map(l => cf.dw(l));
console.log(new Set(ws).size === 1 ? '  等宽 ✓ ' + ws[0] : '  不等宽 ✗ ' + ws.join(','));

console.log('\n=== 4. 八种情绪端到端 ===');
const EMOS = ['疲惫', '挫败', '焦虑', '孤独', '委屈', '兴奋', '平静', '奖励自己'];
const test = `<script>
window.addEventListener('load', function(){
  var EMOS = ${JSON.stringify(EMOS)};
  var out = [];
  function wait(ms){ return new Promise(function(r){ setTimeout(r, ms); }); }
  function clearChips(){
    var c = document.querySelectorAll('#chips .chip');
    for (var i=0;i<c.length;i++) if (c[i].getAttribute('aria-pressed')==='true') c[i].click();
  }
  async function runAll(){
    document.getElementById('story').value='';
    for (var i=0;i<EMOS.length;i++){
      clearChips();
      var c = document.querySelectorAll('#chips .chip');
      for (var k=0;k<c.length;k++) if (c[k].getAttribute('data-emo')===EMOS[i]) { c[k].click(); break; }
      document.getElementById('goBtn').click();
      await wait(3000);
      var items = [].map.call(document.querySelectorAll('.meal-item'), function(e){
        return e.querySelector('.meal-name').textContent + '|' + e.querySelector('.meal-price').textContent;
      });
      var q = function(s){ var e=document.querySelector(s); return e?e.textContent:''; };
      out.push({emo:EMOS[i], items:items, why:q('.meal-why'),
        total:q('.nutri-total .n'), sum:q('.meal-why')&&document.querySelector('.panel-body div[style*="justify-content"] span:last-child')?document.querySelector('.panel-body div[style*="justify-content"] span:last-child').textContent:'',
        verdict:q('.verdict-txt'), sub:q('.verdict-sub'),
        mayou:[].map.call(document.querySelectorAll('.mayou-say'), function(e){return e.textContent;})});
    }
    var d=document.createElement('div'); d.id='__r'; d.textContent=JSON.stringify(out); document.body.appendChild(d);
  }
  setTimeout(runAll, 400);
});
</script>`;

fs.writeFileSync(DIR + 'tmp-t.html', html.replace('</body>', test + '</body>'), 'utf8');
const dom = execFileSync(EDGE, ['--headless=new', '--disable-gpu', '--no-sandbox', '--virtual-time-budget=60000',
  '--dump-dom', '--window-size=1200,1000', 'file:///' + DIR.replace(/\\/g, '/') + 'tmp-t.html'],
  { encoding: 'utf8', maxBuffer: 60 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] });
const m = dom.match(/id="__r">([\s\S]*?)<\/div>/);
if (!m) { console.log('  未取到结果'); process.exit(1); }
const data = JSON.parse(m[1].replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '&gt;'));

let problems = 0;
data.forEach(r => {
  const names = r.items.map(x => x.split('|')[0].trim());
  const prices = r.items.map(x => parseFloat(x.split('|')[1].replace('¥', '')));
  const sum = Math.round(prices.reduce((a, b) => a + b, 0) * 10) / 10;
  const calcs = r.items.map(n => { const key = n.split('|')[0].replace(/\s+/g, ''); const it = api.find(x => x.name.replace(/\s+/g, '') === key); return it ? it.kcal : null; });
  const kcalSum = calcs.reduce((a, b) => a + (b || 0), 0);
  const tags = [];
  if (Math.abs(sum - parseFloat(r.sum.replace('¥', ''))) > 0.05) tags.push('价格合计不符 ' + sum + ' vs ' + r.sum);
  if (Math.abs(kcalSum - parseFloat(r.total)) > 0.5) tags.push('热量合计不符 ' + kcalSum + ' vs ' + r.total);
  // 麦友不得建议餐里已有的品项
  const joinedItems = names.join(' ');
  r.mayou.forEach(s => {
    if (/苹果片/.test(s) && joinedItems.indexOf('苹果片') >= 0) tags.push('小满建议已有品项(苹果片)');
    if (/锡兰红茶/.test(s) && joinedItems.indexOf('锡兰红茶') >= 0) tags.push('小满建议已有品项(锡兰红茶)');
    if (/圆筒/.test(s) && joinedItems.indexOf('圆筒') >= 0) tags.push('老陈建议已有品项(圆筒)');
  });
  if (tags.length) problems++;

  console.log('\n  【' + r.emo + '】' + names.join(' + '));
  console.log('    合计 ¥' + sum + ' / ' + kcalSum + ' kcal  页面显示 ¥' + r.sum.replace('¥', '') + ' / ' + r.total + ' kcal');
  console.log('    理由: ' + r.why);
  console.log('    结论: ' + r.verdict + ' | ' + r.sub);
  r.mayou.forEach((s, i) => console.log('    麦友' + (i + 1) + ': ' + s));
  if (tags.length) console.log('    ✗ ' + tags.join('; '));
});
console.log('\n=== 汇总：' + (problems === 0 ? '八种情绪全部通过' : problems + ' 种存在问题') + ' ===');
