"""The passenger-facing QR menu: one self-contained HTML page.

The same page is served by OBS at /menu and written into the offline package
for the onboard device, so both look identical. Nothing is loaded from the
internet. The page has no links: a passenger who scans the QR stays on the menu
(the browser's back button only closes an open picture).
"""

import json

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0B4F8A">
<meta name="robots" content="noindex">
<title>__TITLE__</title>
<style>
:root{--navy:#0B1F3A;--blue:#0B4F8A;--red:#D62E2E;--bg:#F4F6F9;--card:#fff;--text:#16202B;--muted:#5B6778;--line:#DCE2EA}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans SC","PingFang SC",Arial,sans-serif;overscroll-behavior-y:contain}
header{background:linear-gradient(135deg,var(--navy),var(--blue));color:#fff;padding:18px 16px 14px;padding-top:max(18px,env(safe-area-inset-top))}
.top{display:flex;align-items:center;gap:12px;max-width:960px;margin:0 auto}
.logo{width:44px;height:44px;border-radius:12px;background:#fff;display:flex;align-items:center;justify-content:center;flex:none;overflow:hidden}
.logo img{width:100%;height:100%;object-fit:contain}
.brand{flex:1;min-width:0}
.brand b{display:block;font-size:18px;line-height:1.2}
.brand span{display:block;font-size:13px;opacity:.85;margin-top:2px}
.lang{display:flex;background:rgba(255,255,255,.15);border-radius:999px;padding:3px;flex:none}
.lang button{border:0;background:transparent;color:#fff;font:600 13px/1 inherit;padding:8px 12px;border-radius:999px;cursor:pointer}
.lang button.on{background:#fff;color:var(--blue)}
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
nav .row{display:flex;gap:8px;overflow-x:auto;padding:10px 16px;max-width:960px;margin:0 auto;scrollbar-width:none}
nav .row::-webkit-scrollbar{display:none}
nav button{flex:none;border:1px solid var(--line);background:#fff;color:var(--text);font:600 14px/1 inherit;padding:10px 14px;border-radius:999px;cursor:pointer}
nav button.on{background:var(--blue);border-color:var(--blue);color:#fff}
main{max-width:960px;margin:0 auto;padding:6px 16px calc(28px + env(safe-area-inset-bottom))}
.note{background:#fff;border:1px solid var(--line);border-left:4px solid var(--red);border-radius:10px;padding:10px 12px;font-size:13px;color:var(--muted);margin:12px 0 4px}
h2{font-size:18px;margin:22px 0 10px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media(min-width:640px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(min-width:900px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
.item{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column;text-align:left;padding:0;font:inherit;color:inherit}
.item .ph{aspect-ratio:1/1;background:#EEF2F7;display:flex;align-items:center;justify-content:center;color:#9AA6B6;font-size:12px}
.item .ph img{width:100%;height:100%;object-fit:cover;display:block}
.item .tx{padding:10px 10px 12px;display:flex;flex-direction:column;gap:6px;flex:1}
.item .nm{font-size:14px;font-weight:600;line-height:1.3}
.item .ds{font-size:12px;color:var(--muted);line-height:1.35}
.item .pr{margin-top:auto;font-size:15px;font-weight:700;color:var(--blue)}
.posters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media(min-width:640px){.posters{grid-template-columns:repeat(3,minmax(0,1fr))}}
.poster{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff;cursor:pointer;padding:0}
.poster img{width:100%;display:block}
.poster span{display:block;padding:8px 10px;font-size:13px;font-weight:600;text-align:left}
.empty{color:var(--muted);text-align:center;padding:40px 0}
footer{color:var(--muted);font-size:12px;text-align:center;padding:10px 16px 24px}
.ov{position:fixed;inset:0;background:rgba(6,14,26,.92);z-index:20;display:none;flex-direction:column}
.ov.open{display:flex}
.ov .bar{display:flex;justify-content:flex-end;padding:12px;padding-top:max(12px,env(safe-area-inset-top))}
.ov .x{border:0;background:rgba(255,255,255,.15);color:#fff;width:44px;height:44px;border-radius:22px;font-size:24px;cursor:pointer}
.ov .body{flex:1;overflow:auto;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:0 16px 24px;touch-action:pan-x pan-y pinch-zoom}
.ov .big{max-width:min(92vw,560px);width:100%;border-radius:16px;background:#fff;overflow:hidden}
.ov .big img{width:100%;display:block;aspect-ratio:1/1;object-fit:cover}
.ov .big .tx{padding:16px}
.ov .big .nm{font-size:22px;font-weight:700;line-height:1.25}
.ov .big .nm2{font-size:16px;color:var(--muted);margin-top:4px}
.ov .big .pr{font-size:26px;font-weight:800;color:var(--blue);margin-top:10px}
.ov .big .ds{font-size:14px;color:var(--muted);margin-top:8px}
.ov .show{color:#fff;font-size:13px;opacity:.8;margin-top:12px;text-align:center}
.ov .poster-big{max-width:100%;width:auto;max-height:none}
</style>
</head>
<body>
<header><div class="top">
  <div class="logo">__LOGO__</div>
  <div class="brand"><b id="t"></b><span id="st"></span></div>
  <div class="lang"><button data-l="en">EN</button><button data-l="zh">中文</button></div>
</div></header>
<nav><div class="row" id="tabs"></div></nav>
<main id="m"></main>
<footer id="ft"></footer>
<div class="ov" id="ov"><div class="bar"><button class="x" id="x" aria-label="Close">×</button></div><div class="body" id="ovb"></div></div>
<script>
(function(){
var D=__DATA__, M="__MEDIA__";
var UI={en:{all:"All",posters:"Menu posters",empty:"The menu is being updated. Please ask the cabin crew.",show:"Show this screen to the cabin crew"},
        zh:{all:"全部",posters:"菜单海报",empty:"菜单正在更新，请咨询乘务员。",show:"请向乘务员出示此页面"}};
var q=new URLSearchParams(location.search), lang=(q.get("lang")==="zh")?"zh":"en", tab="all";
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function t(o,k){var v=o[k+"_"+lang];return v||o[k+"_en"]||o[k+"_zh"]||""}
function price(p){if(p==null||p==="")return"";var n=Number(p);return (D.settings.currency||"NPR")+" "+n.toLocaleString("en-US",{maximumFractionDigits:2})}
function img(src,alt){return src?'<img loading="lazy" src="'+M+esc(src)+'" alt="'+esc(alt)+'">':'<span>—</span>'}
function card(it){return '<button class="item" data-id="'+it.id+'"><div class="ph">'+img(it.photo,t(it,"name"))+'</div><div class="tx"><div class="nm">'+esc(t(it,"name"))+'</div>'+(t(it,"desc")?'<div class="ds">'+esc(t(it,"desc"))+'</div>':'')+'<div class="pr">'+esc(price(it.price))+'</div></div></button>'}
function render(){
  document.documentElement.lang=lang==="zh"?"zh-CN":"en";
  document.getElementById("t").textContent=t(D.settings,"title");
  document.getElementById("st").textContent=t(D.settings,"subtitle");
  document.getElementById("ft").textContent=t(D.settings,"footer");
  document.title=t(D.settings,"title");
  Array.prototype.forEach.call(document.querySelectorAll(".lang button"),function(b){b.className=b.getAttribute("data-l")===lang?"on":""});
  var tabs=[{k:"all",l:UI[lang].all}].concat(D.sections.map(function(s){return{k:"s"+s.id,l:t(s,"name")}}));
  if(D.posters.length)tabs.push({k:"posters",l:UI[lang].posters});
  document.getElementById("tabs").innerHTML=tabs.map(function(x){return'<button data-k="'+x.k+'" class="'+(x.k===tab?"on":"")+'">'+esc(x.l)+'</button>'}).join("");
  var h="";
  if(t(D.settings,"note"))h+='<div class="note">'+esc(t(D.settings,"note"))+'</div>';
  D.sections.forEach(function(s){
    if(tab!=="all"&&tab!=="s"+s.id)return;
    if(!s.items.length)return;
    h+='<h2>'+esc(t(s,"name"))+'</h2><div class="grid">'+s.items.map(card).join("")+'</div>';
  });
  if(D.posters.length&&(tab==="all"||tab==="posters")){
    h+='<h2>'+esc(UI[lang].posters)+'</h2><div class="posters">'+D.posters.map(function(p,i){return'<button class="poster" data-p="'+i+'">'+img(p.image,p.title)+(p.title?'<span>'+esc(p.title)+'</span>':'')+'</button>'}).join("")+'</div>';
  }
  if(!D.sections.some(function(s){return s.items.length})&&!D.posters.length)h+='<div class="empty">'+esc(UI[lang].empty)+'</div>';
  document.getElementById("m").innerHTML=h;
}
var byId={};D.sections.forEach(function(s){s.items.forEach(function(i){byId[i.id]=i})});
var ov=document.getElementById("ov"),ovb=document.getElementById("ovb");
function open(html){ovb.innerHTML=html;ov.className="ov open";history.pushState({ov:1},"")}
function close(){ov.className="ov";ovb.innerHTML=""}
document.addEventListener("click",function(e){
  var el=e.target.closest("button");if(!el)return;
  if(el.parentNode&&el.parentNode.className==="lang"){lang=el.getAttribute("data-l");render();return}
  if(el.hasAttribute("data-k")){tab=el.getAttribute("data-k");render();window.scrollTo(0,0);return}
  if(el.hasAttribute("data-id")){var it=byId[el.getAttribute("data-id")];
    var other=lang==="en"?it.name_zh:it.name_en;
    open('<div class="big">'+(it.photo?'<img src="'+M+esc(it.photo)+'" alt="">':'')+'<div class="tx"><div class="nm">'+esc(t(it,"name"))+'</div>'+(other&&other!==t(it,"name")?'<div class="nm2">'+esc(other)+'</div>':'')+'<div class="pr">'+esc(price(it.price))+'</div>'+(t(it,"desc")?'<div class="ds">'+esc(t(it,"desc"))+'</div>':'')+'</div></div><div class="show">'+esc(UI[lang].show)+'</div>');return}
  if(el.hasAttribute("data-p")){var p=D.posters[+el.getAttribute("data-p")];open('<img class="poster-big" src="'+M+esc(p.image)+'" alt="">');return}
  if(el.id==="x"){history.back();return}
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
