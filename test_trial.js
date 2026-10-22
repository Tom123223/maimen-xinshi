/* 「想吃点什么 · 先过三个陌生人」端到端逻辑测试
   用无头 Edge 的虚拟时间驱动，不依赖人工点击。
   覆盖：初始渲染 / 空表单拦截 / 菜单只能来自麦当劳且最多 5 样 /
        提交与加速 / 陌生人陆续审 / 三个动作门禁 / 理由与动作矛盾拦截 /
        加点扣点的菜单演算 / 两人不批即否 / 撤回 / 重置 */
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const base = __dirname;
const html = fs.readFileSync(path.join(base, "index.html"), "utf8");

const TEST = `
<script>
(function(){
  var log=[], fails=0;
  function L(k,v){ log.push({k:k,v:v}); }
  function ok(k,cond,detail){ if(!cond) fails++; log.push({k:k,v:cond?"PASS":"FAIL",d:detail}); }
  function q(s){ return document.querySelector(s); }
  function qa(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function fire(el,type){ el.dispatchEvent(new Event(type,{bubbles:true})); }
  function txt(el){ return el?el.textContent.replace(/\\s+/g," ").trim():""; }
  function poolItem(kw){
    return qa("#trPool .tr-pool-item").filter(function(el){ return txt(el).indexOf(kw)>=0; })[0];
  }
  function at(ms,fn){ setTimeout(fn, ms); }

  window.addEventListener("error",function(e){ L("js.error", e.message+" @"+e.lineno); });

  /* 从界面上把一份审议单的票型和菜单演算读出来，和印章对账 */
  function auditCard(card,label){
    if(!card){ ok("audit.missing["+label+"]",false,"没有这张单"); return; }
    var seats=Array.prototype.slice.call(card.querySelectorAll(".tr-seat.filled"));
    var addN=0,cutN=0,noN=0;
    seats.forEach(function(s){
      var t=txt(s.querySelector(".act"));
      if(t.indexOf("加点")===0) addN++;
      else if(t.indexOf("扣点")===0) cutN++;
      else if(t.indexOf("不批")===0) noN++;
    });
    var rows=card.querySelectorAll(".tr-menu-row").length;
    var cutRows=card.querySelectorAll(".tr-menu-row.cut").length;
    var addRows=card.querySelectorAll(".tr-menu-row .flag.add").length;
    var finalN=rows-cutRows;
    var seal=txt(card.querySelector(".tr-seal"));

    var expect;
    if(seats.length<3) expect="wait";
    else if(noN>=2) expect="reject";
    else if(finalN===0) expect="reject";
    else expect="pass";

    var got = seal.indexOf("不批")===0?"reject":(seal.indexOf("定稿")===0?"pass":"wait");
    ok("audit.verdict["+label+"]", got===expect,
      "票型 add"+addN+"/cut"+cutN+"/no"+noN+" 菜单行"+rows+" 扣"+cutRows+" 加"+addRows+" expect="+expect+" got="+seal);
    if(expect==="pass"){
      ok("audit.menu["+label+"]", finalN>=1 && finalN<=5, "定稿样数="+finalN);
      ok("audit.total["+label+"]", /¥\\d/.test(txt(card.querySelector(".tr-menu-total"))), txt(card.querySelector(".tr-menu-total")));
    }
  }

  /* ---------- 0. 初始渲染 ---------- */
  at(300, function(){
    L("init.mine", qa("#trMine .tr-case").length);
    L("init.pool", qa("#trPool .tr-pool-item").length);
    ok("init.mine=2", qa("#trMine .tr-case").length===2);
    ok("init.pool=3", qa("#trPool .tr-pool-item").length===3);
    ok("init.judged.hidden", q("#trJudgedWrap").style.display==="none");
    ok("init.steps5", qa("#trSteps .tr-step").length===5, "steps="+qa("#trSteps .tr-step").length);
    ok("init.seats3", qa("#trMine .tr-case")[0].querySelectorAll(".tr-seat").length===3);

    var rej=qa("#trMine .tr-case").filter(function(c){ return /不批/.test(txt(c.querySelector(".tr-seal"))); })[0];
    ok("init.rejected", !!rej && /这顿没有了/.test(txt(rej.querySelector(".tr-verdict"))),
      rej?txt(rej.querySelector(".tr-verdict")):"none");

    var appr=qa("#trMine .tr-case").filter(function(c){ return /定稿/.test(txt(c.querySelector(".tr-seal"))); })[0];
    ok("init.approved", !!appr && /菜单定稿/.test(txt(appr.querySelector(".tr-verdict"))),
      appr?txt(appr.querySelector(".tr-verdict")):"none");
    if(appr){
      /* 历史那单：板烧 23.5 + 锡兰红茶 10.5 + 苹果片 7 = 41；391+2+32 = 425 */
      ok("init.approvedMath", /¥41/.test(txt(appr.querySelector(".tr-menu-total"))) && /425/.test(txt(appr.querySelector(".tr-menu-total"))),
        txt(appr.querySelector(".tr-menu-total")));
    }
    auditCard(qa("#trMine .tr-case")[0], "seed-newest");
  });

  /* ---------- 1. 商品硬限制：没有自填入口，菜单只有 22 项真实菜品 ---------- */
  at(450, function(){
    ok("limit.noFreeText", q("#trItem")===null && q("#trPrice")===null, "页面不应存在自填商品/价格输入框");
    var tabs=qa("#trTabs [data-tab]");
    ok("limit.tabs3", tabs.length===3, "tabs="+tabs.length);
    /* 点一次 tab 会重建整条 tab 栏，所以每次都按序号重新取节点 */
    var total=0;
    for(var i=0;i<3;i++){
      qa("#trTabs [data-tab]")[i].click();
      total+=qa("#trPick .tr-mchip").length;
    }
    ok("limit.menu22", total===22, "菜单项合计="+total);
    var codes=qa("#trPick .tr-mchip").map(function(b){ return b.getAttribute("data-pickchip"); });
    ok("limit.codeShape", codes.every(function(c){ return /^[0-9]+$/.test(c); }), codes.slice(0,5).join(","));
    qa("#trTabs [data-tab]")[0].click();
  });

  /* ---------- 2. 空表单必须拦下 ---------- */
  at(520, function(){
    q("#trSubmit").click();
    var bad=qa(".tr-field.bad").length;
    ok("empty.blocked", bad===2 && qa("#trMine .tr-case").length===2, "badFields="+bad);
  });

  /* ---------- 3. 最多 5 样：选满后其余置灰 ---------- */
  at(600, function(){
    for(var i=0;i<5;i++){
      var c=q("#trPick .tr-mchip:not([aria-pressed='true'])");
      if(c) c.click();
    }
    ok("cap.count5", txt(q("#cMenu"))==="5/5", txt(q("#cMenu")));
    ok("cap.disableRest", qa("#trPick .tr-mchip[disabled]").length===qa("#trPick .tr-mchip").length-5,
      "disabled="+qa("#trPick .tr-mchip[disabled]").length);
    ok("cap.sum", /已选 5 样/.test(txt(q("#trSum"))), txt(q("#trSum")));
    /* 清空 */
    for(var k=0;k<8;k++){ var d=q("#trSel [data-drop]"); if(d) d.click(); }
    ok("cap.cleared", qa("#trSel .tr-sel-row").length===0);
  });

  /* ---------- 4. 合法提交 + 演示加速 ---------- */
  at(800, function(){
    q("#trStory").value="今天被主管当众说了两句，回到工位手一直在抖，想点一堆东西把这种感觉压下去。";
    fire(q("#trStory"),"input");
    q("#trFast").checked=true; fire(q("#trFast"),"change");
    q("#trPick .tr-mchip:not([aria-pressed='true'])").click();
    q("#trPick .tr-mchip:not([aria-pressed='true'])").click();
    q("#trSubmit").click();

    var n=qa("#trMine .tr-case").length;
    ok("submit.created", n===3, "cards="+n);
    var first=q("#trMine .tr-case");
    ok("submit.pending", /0\\/3|1\\/3/.test(txt(first.querySelector(".tr-seal"))), txt(first.querySelector(".tr-seal")));
    ok("submit.clock24", /^00:2[0-9]$/.test(txt(first.querySelector(".tr-clock"))), txt(first.querySelector(".tr-clock")));
    ok("submit.seatsEmpty", first.querySelectorAll(".tr-seat").length===3);
    L("submit.id", txt(first.querySelector(".tr-case-top .no")));
    L("submit.story", txt(first.querySelector(".tr-story")).slice(0,20));
    /* 提交后表单应被清空，步骤条推进到第 4 步 */
    ok("submit.formCleared", q("#trStory").value==="" && qa("#trSel .tr-sel-row").length===0);
    ok("submit.step4", q("#trSteps .tr-step.on") && /三个人给出结论/.test(txt(q("#trSteps .tr-step.on"))),
      txt(q("#trSteps .tr-step.on")));
  });

  /* ---------- 5. 陌生人陆续进来（18% / 42% / 66% 时间轴） ---------- */
  at(5200, function(){
    var first=q("#trMine .tr-case");
    var t=txt(first.querySelector(".tr-tally"));
    L("t5s.tally", t);
    ok("bot.first", first.querySelectorAll(".tr-seat.filled").length>=1, "已审="+first.querySelectorAll(".tr-seat.filled").length);
  });
  at(17000, function(){
    var first=q("#trMine .tr-case");
    var filled=first.querySelectorAll(".tr-seat.filled").length;
    L("t17s.seats", filled);
    ok("bot.three", filled===3, "已审="+filled);
    auditCard(first, "my-fast");
    ok("bot.settled", !/\\/3/.test(txt(first.querySelector(".tr-seal"))), txt(first.querySelector(".tr-seal")));
  });

  /* ---------- 6. 加点：菜单演算必须对得上账 ---------- */
  at(20000, function(){
    var item=poolItem("麦辣鸡腿汉堡");
    ok("vote.pool.hasOldZhou", !!item);
    if(!item) return;
    item.querySelector("[data-vote]").click();
    at(120, function(){
      ok("vote.modalOpen", q("#trModal").classList.contains("on"));
      var sub=q("#trVoteSubmit");
      ok("vote.gate.noSide", sub.disabled===true);
      q("#trDialogBody [data-side=add]").click();
      ok("vote.gate.noPick", q("#trVoteSubmit").disabled===true);
      var chip=q("#trVotePick [data-vpick]");
      ok("vote.add.pickable", !!chip && qa("#trVotePick [data-vpick]").length>0);
      /* 不能把已有的菜重复加进去 */
      ok("vote.add.noDup", qa('#trVotePick [data-vpick="1440"]').length===0);
      q('#trVotePick [data-vpick="6102"]').click();   /* 苹果片 */
      ok("vote.gate.noReason", q("#trVoteSubmit").disabled===true);
      var ta=q("#trVoteReason");
      ta.value="不该吃"; fire(ta,"input");
      ok("vote.gate.shortReason", q("#trVoteSubmit").disabled===true, "len="+ta.value.length);
      /* 选加点却写劝退 → 必须拦下 */
      ta.value="别吃了，真的不该吃这一顿"; fire(ta,"input");
      q("#trVoteSubmit").click();
      ok("vote.contra", q("#trModal").classList.contains("on") && /否单/.test(txt(q("#trVoteErr"))), txt(q("#trVoteErr")));

      ta.value="加个苹果片，这顿好歹有菜，心里能踏实一点"; fire(ta,"input");
      q("#trVoteSubmit").click();
      at(150, function(){
        ok("vote.add.closed", !q("#trModal").classList.contains("on"));
        /* 这一单是别人的，去「我审过的」里找 */
        var judged=qa("#trJudged .tr-pool-item").filter(function(el){ return /麦辣鸡腿汉堡/.test(txt(el)); })[0];
        ok("vote.add.judged", !!judged, judged?txt(judged):"没有");
        if(judged){
          var jt=txt(judged);
          ok("vote.add.approved", /定稿/.test(jt), jt);
          /* 原 4 样 → 扣掉中薯条 → 加纯悦 + 我加的苹果片 = 5 样
             22.5 + 14 + 13.9 + 7.5 + 7 = 64.9；485+213+135+0+32 = 865 */
          ok("vote.add.math", /¥64\\.9/.test(jt) && /865 kcal/.test(jt) && /5 样/.test(jt), jt);
          ok("vote.add.cutApplied", !/中薯条/.test(jt), "被扣的中薯条不应出现在定稿里：" + jt);
          ok("vote.add.addApplied", /苹果片/.test(jt) && /纯悦/.test(jt), jt);
        }
        var stillPool=poolItem("麦辣鸡腿汉堡");
        ok("vote.add.leftPool", !stillPool);
      });
    });
  });

  /* ---------- 7. 不批：两人以上不批 → 这顿没有 ---------- */
  at(20600, function(){
    var item=poolItem("板烧鸡腿堡");
    ok("vote.pool.hasMay", !!item);
    if(!item) return;
    item.querySelector("[data-vote]").click();
    at(120, function(){
      ok("vote.no.modal", q("#trModal").classList.contains("on"));
      q("#trDialogBody [data-side=no]").click();
      /* 不批不需要挑菜，只要理由 */
      ok("vote.no.noPickNeeded", qa("#trVotePick [data-vpick]").length===0);
      var ta=q("#trVoteReason");
      ta.value="这顿可以吃，去吧"; fire(ta,"input");
      q("#trVoteSubmit").click();
      ok("vote.no.contra", q("#trModal").classList.contains("on") && /放行/.test(txt(q("#trVoteErr"))), txt(q("#trVoteErr")));
      ta.value="吵架不该用吃解决，先回去把话说完"; fire(ta,"input");
      q("#trVoteSubmit").click();
      at(150, function(){
        ok("vote.no.closed", !q("#trModal").classList.contains("on"));
        var judged=qa("#trJudged .tr-pool-item").filter(function(el){ return /板烧鸡腿堡/.test(txt(el)); })[0];
        ok("vote.no.judged", !!judged, judged?txt(judged):"没有");
        if(judged){
          ok("vote.no.rejected", /不批/.test(txt(judged)), txt(judged));
          ok("vote.no.agree", /你那一审和结果一致/.test(txt(judged)), txt(judged));
        }
      });
    });
  });

  /* ---------- 8. 我审过的必须离开待审池 ---------- */
  at(21300, function(){
    ok("pool.noStale", !poolItem("板烧鸡腿堡") && !poolItem("麦辣鸡腿汉堡"));
    L("pool.remain", qa("#trPool .tr-pool-item").map(function(el){ return txt(el.querySelector(".t")); }));
    ok("pool.remainOne", qa("#trPool .tr-pool-item").length===1, "剩 "+qa("#trPool .tr-pool-item").length);
    ok("stats.cells8", qa("#trStats .tr-stat").length===8, "cells="+qa("#trStats .tr-stat").length);
  });

  /* ---------- 9. 撤回 ---------- */
  at(21600, function(){
    q("#trStory").value="又是加班到十点，楼下这家还开着，想随便点点什么。";
    fire(q("#trStory"),"input");
    q("#trPick .tr-mchip:not([aria-pressed='true'])").click();
    q("#trSubmit").click();
    at(120, function(){
      var w=q("#trMine [data-withdraw]");
      ok("withdraw.hasBtn", !!w, "cards="+qa("#trMine .tr-case").length);
      if(!w) return;
      w.click();
      at(150, function(){
        ok("withdraw.ok", /已撤回/.test(txt(q("#trMine .tr-case"))), txt(q("#trMine .tr-case .tr-verdict")));
        ok("withdraw.wording", /不计通过率/.test(txt(q("#trMine .tr-case .tr-verdict"))), txt(q("#trMine .tr-case .tr-verdict")));
      });
    });
  });

  /* ---------- 10. 重置 ---------- */
  at(22100, function(){
    q("[data-reset]").click();
    at(200, function(){
      ok("reset.pool", qa("#trPool .tr-pool-item").length===3, "pool="+qa("#trPool .tr-pool-item").length);
      ok("reset.mine", qa("#trMine .tr-case").length===2, "mine="+qa("#trMine .tr-case").length);
      ok("reset.stats", qa("#trStats .tr-stat").length===8);
      ok("reset.judgedHidden", q("#trJudgedWrap").style.display==="none");
      ok("reset.steps", qa("#trSteps .tr-step").length===5);
      var pre=document.createElement("pre");
      pre.id="TESTLOG";
      pre.textContent=JSON.stringify({fails:fails, log:log});
      document.body.appendChild(pre);
    });
  });
})();
</script>`;

const testPage = html.replace("</body>", TEST + "</body>");
fs.writeFileSync(path.join(base, "_test.html"), testPage, "utf8");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const out = execFileSync(EDGE, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars",
  "--window-size=1440,900", "--virtual-time-budget=24000",
  "--dump-dom", "http://localhost:8765/_test.html",
], { encoding: "utf8", maxBuffer: 1 << 27 });

const m = out.match(/<pre id="TESTLOG">([\s\S]*?)<\/pre>/);
if (!m) {
  console.log("!! 没有拿到测试日志，页面可能在早期就报错了");
  const err = out.match(/js\.error[^"]*/);
  console.log(err ? err[0] : "(no js.error found)");
  process.exit(1);
}
const data = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
let fails = 0;
data.log.forEach(function (it) {
  if (it.v === "PASS") { console.log("  ✅ " + it.k); }
  else if (it.v === "FAIL") { fails++; console.log("  ❌ " + it.k + "   " + (it.d || "")); }
  else { console.log("     · " + it.k + " = " + JSON.stringify(it.v)); }
});
console.log("\n" + (fails ? "失败 " + fails + " 项" : "全部通过"));
process.exit(fails ? 1 : 0);
