"""The passenger-facing QR menu: one self-contained HTML page.

The same page is served by OBS at /menu and written into the offline package
for the onboard device, so both look identical. Nothing is loaded from the
internet. The page has no links: a passenger who scans the QR stays on the menu
(the browser's back button only closes an open picture).

Look and feel follow the printed Koseli menu: cream paper, green item names,
gold prices, the Koseli artwork as the banner and the airline logo on top.
"""

import json

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#FFFCF1">
<meta name="robots" content="noindex">
<title>__TITLE__</title>
<style>
:root{--paper:#FFFCF1;--paper2:#F3EDDD;--ink:#1F2A1F;--green:#2E6B3E;--gold:#B4862A;--gold2:#8F6A1C;--blue:#1B3F8F;--red:#D4452B;--muted:#6B6F66;--line:#E6DEC9;--card:#FFFDF8}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans SC","PingFang SC",Arial,sans-serif;overscroll-behavior-y:contain}
button{font-family:inherit}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;max-width:980px;margin:0 auto;padding:12px 16px 4px;padding-top:max(12px,env(safe-area-inset-top))}
.logo{height:34px;display:flex;align-items:center}
.logo img{height:34px;width:auto;max-width:60vw;object-fit:contain;display:block}
.logo b{color:var(--red);font-size:18px}
.lang{display:flex;background:var(--paper2);border:1px solid var(--line);border-radius:999px;padding:3px;flex:none}
.lang button{border:0;background:transparent;color:var(--green);font:600 13px/1 inherit;padding:8px 12px;border-radius:999px;cursor:pointer}
.lang button.on{background:var(--green);color:#fff}
.banner{max-width:980px;margin:0 auto;padding:0 12px}
.banner img{width:100%;display:block;height:auto}
.banner .txt{padding:14px 6px 2px;text-align:center}
.banner .txt b{display:block;font-size:26px;color:var(--blue);font-family:Georgia,"Times New Roman",serif;font-style:italic;letter-spacing:.5px}
.banner .txt span{display:block;font-size:13px;color:var(--muted);margin-top:2px}
nav{position:sticky;top:0;z-index:5;background:rgba(255,252,241,.96);backdrop-filter:saturate(1.2) blur(6px);border-bottom:1px solid var(--line)}
nav .row{display:flex;gap:8px;overflow-x:auto;padding:10px 16px;max-width:980px;margin:0 auto;scrollbar-width:none}
nav .row::-webkit-scrollbar{display:none}
nav button{flex:none;border:1px solid var(--line);background:var(--card);color:var(--green);font:600 14px/1 inherit;padding:10px 14px;border-radius:999px;cursor:pointer}
nav button.on{background:var(--green);border-color:var(--green);color:#fff}
main{max-width:980px;margin:0 auto;padding:4px 16px calc(96px + env(safe-area-inset-bottom))}
.note{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--gold);border-radius:10px;padding:10px 12px;font-size:13px;color:var(--muted);margin:14px 0 2px}
h2{font-size:19px;margin:26px 0 12px;color:var(--green);display:flex;align-items:center;gap:10px}
h2:after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--gold),transparent)}
h2 .tag{font-size:11px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;background:var(--red);color:#fff;padding:4px 8px;border-radius:6px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media(min-width:640px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:900px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
.item{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column;text-align:left;padding:0;font:inherit;color:inherit;box-shadow:0 1px 2px rgba(60,45,10,.06);transition:transform .12s}
.item:active{transform:scale(.98)}
.item .ph{aspect-ratio:1/1;background:#FFFCF1;display:flex;align-items:center;justify-content:center;color:#B8B09A;font-size:12px}
.item .ph img{width:100%;height:100%;object-fit:cover;display:block}
.item .tx{padding:10px 11px 12px;display:flex;flex-direction:column;gap:5px;flex:1}
.item .nm{font-size:14px;font-weight:700;line-height:1.3;color:var(--green)}
.item .ds{font-size:12px;color:var(--muted);line-height:1.35}
.item .pr{margin-top:auto;font-size:16px;font-weight:800;color:var(--gold);letter-spacing:.2px}
.posters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media(min-width:640px){.posters{grid-template-columns:repeat(4,minmax(0,1fr))}}
.poster{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--card);cursor:pointer;padding:0}
.poster img{width:100%;display:block}
.poster span{display:block;padding:8px 10px;font-size:13px;font-weight:600;text-align:left;color:var(--green)}
.empty{color:var(--muted);text-align:center;padding:40px 0}
footer{max-width:980px;margin:0 auto;color:var(--muted);font-size:12px;padding:6px 16px calc(100px + env(safe-area-inset-bottom));line-height:1.5}
footer ul{margin:0;padding:0 0 0 16px}
footer li{margin:4px 0}
footer .hr{height:1px;background:var(--line);margin:0 0 12px}
/* selection bar */
.bar{position:fixed;left:0;right:0;bottom:0;z-index:10;padding:10px 16px;padding-bottom:max(10px,env(safe-area-inset-bottom));background:rgba(255,252,241,.97);border-top:1px solid var(--line);display:none;align-items:center;gap:12px;max-width:100%}
.bar.on{display:flex}
.bar .sum{flex:1;min-width:0}
.bar .sum b{display:block;font-size:14px;color:var(--green)}
.bar .sum span{display:block;font-size:12px;color:var(--muted)}
.bar button{border:0;background:var(--green);color:#fff;font:700 14px/1 inherit;padding:12px 16px;border-radius:999px;cursor:pointer;flex:none}
/* overlay */
.ov{position:fixed;inset:0;background:rgba(20,24,16,.93);z-index:20;display:none;flex-direction:column}
.ov.open{display:flex}
.ov .top2{display:flex;justify-content:flex-end;padding:12px;padding-top:max(12px,env(safe-area-inset-top))}
.ov .x{border:0;background:rgba(255,255,255,.18);color:#fff;width:44px;height:44px;border-radius:22px;font-size:26px;line-height:1;cursor:pointer}
.ov .body{flex:1;overflow:auto;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 16px 24px;touch-action:pan-x pan-y pinch-zoom}
.ov .big{max-width:min(92vw,520px);width:100%;border-radius:18px;background:var(--card);overflow:hidden}
.ov .big img{width:100%;display:block;aspect-ratio:1/1;object-fit:cover;background:#FFFCF1}
.ov .big .tx{padding:16px 18px 18px}
.ov .big .nm{font-size:21px;font-weight:800;line-height:1.25;color:var(--green)}
.ov .big .nm2{font-size:15px;color:var(--muted);margin-top:4px}
.ov .big .pr{font-size:28px;font-weight:900;color:var(--gold);margin-top:10px}
.ov .big .ds{font-size:14px;color:var(--muted);margin-top:8px;line-height:1.45}
.ov .big .qty{display:flex;align-items:center;gap:10px;margin-top:14px}
.ov .big .qty button{width:42px;height:42px;border-radius:21px;border:1px solid var(--line);background:#fff;font-size:22px;color:var(--green);cursor:pointer}
.ov .big .qty b{min-width:28px;text-align:center;font-size:18px}
.ov .big .qty span{font-size:12px;color:var(--muted);margin-left:auto}
.ov .show{color:#fff;font-size:13px;opacity:.85;margin-top:12px;text-align:center}
.ov .poster-big{max-width:100%;width:auto;max-height:none}
.ov .list{max-width:min(92vw,520px);width:100%;background:var(--card);border-radius:18px;padding:18px}
.ov .list h3{margin:0 0 4px;color:var(--green);font-size:20px}
.ov .list p{margin:0 0 12px;color:var(--muted);font-size:13px}
.ov .list .ln{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}
.ov .list .ln img{width:44px;height:44px;border-radius:8px;object-fit:cover;background:#FFFCF1}
.ov .list .ln .n{flex:1;min-width:0;color:var(--ink)}
.ov .list .ln .n small{display:block;color:var(--muted)}
.ov .list .ln .p{font-weight:700;color:var(--gold);white-space:nowrap}
.ov .list .ln button{border:0;background:transparent;color:var(--red);font-size:20px;cursor:pointer;padding:4px}
.ov .list .tot{display:flex;justify-content:space-between;padding:12px 0 4px;font-weight:800;font-size:17px;color:var(--green)}
.ov .list .tot span{color:var(--gold)}
.ov .list .act{display:flex;gap:10px;margin-top:12px}
.ov .list .act button{flex:1;border:1px solid var(--line);background:#fff;color:var(--green);font:600 14px/1 inherit;padding:12px;border-radius:999px;cursor:pointer}
.ov .list .act button.pri{background:var(--green);color:#fff;border-color:var(--green)}
</style>
</head>
<body>
<div class="top">
  <div class="logo">__LOGO__</div>
  <div class="lang"><button data-l="en">EN</button><button data-l="zh">中文</button></div>
</div>
<div class="banner" id="bn"></div>
<nav><div class="row" id="tabs"></div></nav>
<main id="m"></main>
<footer id="ft"></footer>
<div class="bar" id="bar"><div class="sum"><b id="bar1"></b><span id="bar2"></span></div><button id="barb"></button></div>
<div class="ov" id="ov"><div class="top2"><button class="x" id="x" aria-label="Close">×</button></div><div class="body" id="ovb"></div></div>
<script>
(function(){
var D=__DATA__, M="__MEDIA__";
var UI={en:{all:"All",posters:"Printed menu",empty:"The menu is being updated. Please ask the cabin crew.",show:"Show this screen to the cabin crew to order",
            add:"Add to my selection",sel:"My selection",selsub:"Show this list to the cabin crew. Payment is made on board.",item:"item",items:"items",
            total:"Total",clear:"Clear",ok:"Done",qty:"Quantity"},
        zh:{all:"全部",posters:"印刷菜单",empty:"菜单正在更新，请咨询乘务员。",show:"请向乘务员出示此页面以点单",
            add:"加入我的选择",sel:"我的选择",selsub:"请向乘务员出示此清单，在机上付款。",item:"件",items:"件",
            total:"合计",clear:"清空",ok:"完成",qty:"数量"}};
var q=new URLSearchParams(location.search), lang=(q.get("lang")==="zh")?"zh":"en", tab="all", sel={};
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function t(o,k){var v=o[k+"_"+lang];return v||o[k+"_en"]||o[k+"_zh"]||""}
function money(n){return (D.settings.currency||"NPR")+" "+Number(n).toLocaleString("en-US",{maximumFractionDigits:2})}
function price(p){if(p==null||p==="")return"";return money(p)}
function img(src,alt){return src?'<img loading="lazy" src="'+M+esc(src)+'" alt="'+esc(alt)+'">':'<span>—</span>'}
function card(it){return '<button class="item" data-id="'+it.id+'"><div class="ph">'+img(it.photo,t(it,"name"))+'</div><div class="tx"><div class="nm">'+esc(t(it,"name"))+'</div>'+(t(it,"desc")?'<div class="ds">'+esc(t(it,"desc"))+'</div>':'')+'<div class="pr">'+esc(price(it.price))+'</div></div></button>'}
function isOffer(s){return /offer|dhamaka|优惠|特惠/i.test((s.name_en||"")+(s.name_zh||""))}
function render(){
  document.documentElement.lang=lang==="zh"?"zh-CN":"en";
  document.title=t(D.settings,"title");
  var bn="";
  if(D.settings.banner)bn+='<img src="'+M+esc(D.settings.banner)+'" alt="">';
  else bn+='<div class="txt"><b>'+esc(t(D.settings,"title"))+'</b>'+(t(D.settings,"subtitle")?'<span>'+esc(t(D.settings,"subtitle"))+'</span>':'')+'</div>';
  document.getElementById("bn").innerHTML=bn;
  Array.prototype.forEach.call(document.querySelectorAll(".lang button"),function(b){b.className=b.getAttribute("data-l")===lang?"on":""});
  var tabs=[{k:"all",l:UI[lang].all}].concat(D.sections.filter(function(s){return s.items.length}).map(function(s){return{k:"s"+s.id,l:t(s,"name")}}));
  if(D.posters.length)tabs.push({k:"posters",l:UI[lang].posters});
  document.getElementById("tabs").innerHTML=tabs.map(function(x){return'<button data-k="'+x.k+'" class="'+(x.k===tab?"on":"")+'">'+esc(x.l)+'</button>'}).join("");
  var h="";
  if(t(D.settings,"note"))h+='<div class="note">'+esc(t(D.settings,"note"))+'</div>';
  D.sections.forEach(function(s){
    if(tab!=="all"&&tab!=="s"+s.id)return;
    if(!s.items.length)return;
    h+='<h2>'+(isOffer(s)?'<span class="tag">'+(lang==="zh"?"优惠":"Offer")+'</span>':'')+esc(t(s,"name"))+'</h2><div class="grid">'+s.items.map(card).join("")+'</div>';
  });
  if(D.posters.length&&(tab==="all"||tab==="posters")){
    h+='<h2>'+esc(UI[lang].posters)+'</h2><div class="posters">'+D.posters.map(function(p,i){return'<button class="poster" data-p="'+i+'">'+img(p.image,p.title)+(p.title?'<span>'+esc(p.title)+'</span>':'')+'</button>'}).join("")+'</div>';
  }
  if(!D.sections.some(function(s){return s.items.length})&&!D.posters.length)h+='<div class="empty">'+esc(UI[lang].empty)+'</div>';
  document.getElementById("m").innerHTML=h;
  var f=t(D.settings,"footer");
  document.getElementById("ft").innerHTML=f?'<div class="hr"></div><ul>'+f.split(/\n+/).filter(function(x){return x.trim()}).map(function(x){return'<li>'+esc(x.replace(/^[-•*]\s*/,""))+'</li>'}).join("")+'</ul>':'';
  renderBar();
}
var byId={};D.sections.forEach(function(s){s.items.forEach(function(i){byId[i.id]=i})});
function count(){var n=0;for(var k in sel)n+=sel[k];return n}
function total(){var n=0;for(var k in sel)n+=sel[k]*(Number(byId[k].price)||0);return n}
function renderBar(){var n=count(),b=document.getElementById("bar");b.className="bar"+(n?" on":"");
  if(n){document.getElementById("bar1").textContent=UI[lang].sel+" · "+n+" "+(n===1?UI[lang].item:UI[lang].items);
        document.getElementById("bar2").textContent=UI[lang].total+" "+money(total());
        document.getElementById("barb").textContent=lang==="zh"?"查看":"View"}}
var ov=document.getElementById("ov"),ovb=document.getElementById("ovb");
function open(html){ovb.innerHTML=html;ov.className="ov open";history.pushState({ov:1},"")}
function close(){ov.className="ov";ovb.innerHTML=""}
function detail(it){
  var other=lang==="en"?it.name_zh:it.name_en, n=sel[it.id]||0;
  return '<div class="big" data-detail="'+it.id+'">'+(it.photo?'<img src="'+M+esc(it.photo)+'" alt="">':'')+'<div class="tx"><div class="nm">'+esc(t(it,"name"))+'</div>'+(other&&other!==t(it,"name")?'<div class="nm2">'+esc(other)+'</div>':'')+'<div class="pr">'+esc(price(it.price))+'</div>'+(t(it,"desc")?'<div class="ds">'+esc(t(it,"desc"))+'</div>':'')+
    '<div class="qty"><button data-q="-1">−</button><b id="qn">'+n+'</b><button data-q="1">+</button><span>'+esc(UI[lang].qty)+'</span></div></div></div><div class="show">'+esc(UI[lang].show)+'</div>';
}
function list(){
  var h='<div class="list"><h3>'+esc(UI[lang].sel)+'</h3><p>'+esc(UI[lang].selsub)+'</p>';
  for(var k in sel){var it=byId[k];h+='<div class="ln">'+(it.photo?'<img src="'+M+esc(it.photo)+'" alt="">':'<span style="width:44px"></span>')+'<div class="n">'+esc(t(it,"name"))+'<small>'+sel[k]+' × '+esc(price(it.price))+'</small></div><div class="p">'+esc(money(sel[k]*(Number(it.price)||0)))+'</div><button data-rm="'+k+'" aria-label="Remove">×</button></div>'}
  h+='<div class="tot"><div>'+esc(UI[lang].total)+'</div><span>'+esc(money(total()))+'</span></div><div class="act"><button data-clear="1">'+esc(UI[lang].clear)+'</button><button class="pri" data-ok="1">'+esc(UI[lang].ok)+'</button></div></div>';
  return h;
}
document.addEventListener("click",function(e){
  var el=e.target.closest("button");if(!el)return;
  if(el.parentNode&&el.parentNode.className==="lang"){lang=el.getAttribute("data-l");render();if(ov.className.indexOf("open")>=0){var d=ovb.querySelector("[data-detail]");if(d)ovb.innerHTML=detail(byId[d.getAttribute("data-detail")]);else if(ovb.querySelector(".list"))ovb.innerHTML=list()}return}
  if(el.hasAttribute("data-k")){tab=el.getAttribute("data-k");render();window.scrollTo(0,0);return}
  if(el.hasAttribute("data-id")){open(detail(byId[el.getAttribute("data-id")]));return}
  if(el.hasAttribute("data-p")){var p=D.posters[+el.getAttribute("data-p")];open('<img class="poster-big" src="'+M+esc(p.image)+'" alt="">');return}
  if(el.hasAttribute("data-q")){var d=el.closest("[data-detail]"),id=d.getAttribute("data-detail");var n=Math.max(0,Math.min(20,(sel[id]||0)+(+el.getAttribute("data-q"))));if(n)sel[id]=n;else delete sel[id];document.getElementById("qn").textContent=n;renderBar();return}
  if(el.hasAttribute("data-rm")){delete sel[el.getAttribute("data-rm")];renderBar();if(count())ovb.innerHTML=list();else history.back();return}
  if(el.hasAttribute("data-clear")){sel={};renderBar();history.back();return}
  if(el.id==="barb"){open(list());return}
  if(el.hasAttribute("data-ok")||el.id==="x"){history.back();return}
});
// Stay on the menu: the back button only closes an open picture.
history.replaceState({menu:1},"");history.pushState({menu:2},"");
window.addEventListener("popstate",function(){if(ov.className.indexOf("open")>=0){close()}else{history.pushState({menu:2},"")}});
render();
})();
</script>
</body>
</html>
"""


def render_menu(data: dict, media_base: str, logo_html: str = "") -> str:
    """`data` = {settings, sections:[{..., items:[...]}], posters:[...]}."""
    title = (data.get("settings") or {}).get("title_en") or "Onboard Menu"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (PAGE.replace("__DATA__", payload)
                .replace("__MEDIA__", media_base.replace('"', ""))
                .replace("__TITLE__", title.replace("<", "&lt;"))
                .replace("__LOGO__", logo_html))
