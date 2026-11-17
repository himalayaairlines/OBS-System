"""The passenger-facing QR menu: one self-contained HTML page.

The same page is served by OBS at /menu and written into the offline package
for the onboard device, so both look identical. Nothing is loaded from the
internet. The page's only link is "Download Menu", a PDF of the printed posters and the
price list the passenger can keep for the flight; the browser's back button
only closes an open picture. There is no basket: purchases are made at the
sales counter, so an item opens in full and that is all.

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
.acts{display:flex;gap:8px;align-items:center;flex:none}
.lang{display:flex;background:var(--paper2);border:1px solid var(--line);border-radius:999px;padding:3px;flex:none}
.dl{display:inline-flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:7px 12px 7px 9px;color:var(--green);font:600 13px/1 inherit;text-decoration:none;white-space:nowrap}
.dl svg{width:18px;height:18px;flex:none}
.dl .pdf{color:var(--red)}
.dl .sep{width:1px;height:18px;background:var(--line)}
@media(max-width:420px){.dl .lbl{display:none}}
.how{background:var(--paper2);border:1px solid var(--line);border-radius:14px;padding:16px 16px 14px;margin:16px 0 4px}
.how h3{margin:0 0 4px;font-size:20px;color:var(--green);font-family:Georgia,"Times New Roman",serif}
.how p{margin:0 0 12px;font-size:13px;color:var(--muted);line-height:1.45}
.how .steps{display:grid;grid-template-columns:1fr;gap:12px}
@media(min-width:640px){.how .steps{grid-template-columns:repeat(3,minmax(0,1fr))}}
.how .st{display:flex;align-items:center;gap:12px}
.how .ic{width:54px;height:54px;border-radius:27px;display:flex;align-items:center;justify-content:center;flex:none}
.how .ic svg{width:24px;height:24px}
.how .st b{display:block;font-size:14px;color:var(--ink);font-family:Georgia,"Times New Roman",serif}
.how .st span{display:block;font-size:12.5px;color:var(--muted);line-height:1.4;margin-top:2px}
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
/* the featured offer: first in the bar, red, with a gift */
nav button.feat{background:var(--red);border-color:var(--red);color:#fff;display:inline-flex;align-items:center;gap:8px;padding:10px 18px 10px 14px;box-shadow:0 2px 6px rgba(212,69,43,.35)}
nav button.feat.on{background:#B0351F;border-color:#B0351F}
nav button.feat svg{width:16px;height:16px;flex:none}
h2.feat{color:var(--red)}
h2.feat:after{background:linear-gradient(90deg,var(--red),transparent)}
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
  <div class="acts">
    <div class="lang"><button data-l="en">EN</button><button data-l="zh">中文</button></div>
    <a class="dl" id="dl" href="__PDF__" download="koseli-menu.pdf"><svg class="pdf" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 1.5V9h5.5L13 3.5zM7.5 13v6h1.4v-2.1h.9c1.4 0 2.3-.8 2.3-2s-.9-1.9-2.3-1.9H7.5zm1.4 1.1h.8c.6 0 1 .3 1 .8s-.4.8-1 .8h-.8v-1.6zM13 13v6h2c1.9 0 3-1.2 3-3s-1.1-3-3-3h-2zm1.4 1.1h.6c1 0 1.6.7 1.6 1.9s-.6 1.9-1.6 1.9h-.6v-3.8z"/></svg><span class="lbl" id="dll"></span><span class="sep"></span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/></svg></a>
  </div>
</div>
<div class="banner" id="bn"></div>
<nav><div class="row" id="tabs"></div></nav>
<main id="m"></main>
<footer id="ft"></footer>
<div class="ov" id="ov"><div class="top2"><button class="x" id="x" aria-label="Close">×</button></div><div class="body" id="ovb"></div></div>
<script>
(function(){
var D=__DATA__, M="__MEDIA__";
var UI={en:{all:"All",empty:"The menu is being updated. Please ask the cabin crew.",dl:"Download Menu",
            how:"How Koseli Works",
            howp:"Explore the Koseli collection, pre-purchase your favourites at the sales counter, present your receipt onboard, and enjoy your journey with Himalaya Airlines.",
            s1:"1. Explore the Menu",s1d:"Discover our selection of snacks, drinks, souvenirs and more.",
            s2:"2. Purchase at Counter",s2d:"Pre-purchase your favourites at the sales counter.",
            s3:"3. Show Receipt & Enjoy",s3d:"Present your receipt onboard and enjoy your items inflight."},
        zh:{all:"全部",empty:"菜单正在更新，请咨询乘务员。",dl:"下载菜单",
            how:"Koseli 如何使用",
            howp:"浏览 Koseli 精选，在销售柜台预购您喜爱的商品，登机后出示收据，与喜马拉雅航空一起享受旅程。",
            s1:"1. 浏览菜单",s1d:"发现我们的小吃、饮品、纪念品等。",
            s2:"2. 柜台购买",s2d:"在销售柜台预购您喜爱的商品。",
            s3:"3. 出示收据，尽情享用",s3d:"登机后出示收据，机上领取您的商品。"}};
var ICONS={search:'<svg viewBox="0 0 24 24" fill="none" stroke="#2E6B3E" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
           card:'<svg viewBox="0 0 24 24" fill="none" stroke="#1B3F8F" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18"/><path d="M7 15h3"/></svg>',
           receipt:'<svg viewBox="0 0 24 24" fill="none" stroke="#1B3F8F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h14v18l-2.3-1.5L14.3 21 12 19.5 9.7 21 7.3 19.5 5 21z"/><path d="M9 8h6"/><path d="M9 12h6"/><path d="M9 16h4"/></svg>'};
var q=new URLSearchParams(location.search), lang=(q.get("lang")==="zh")?"zh":"en", tab="all";
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
function t(o,k){var v=o[k+"_"+lang];return v||o[k+"_en"]||o[k+"_zh"]||""}
function money(n){return (D.settings.currency||"NPR")+" "+Number(n).toLocaleString("en-US",{maximumFractionDigits:2})}
function price(p){if(p==null||p==="")return"";return money(p)}
function img(src,alt){return src?'<img loading="lazy" src="'+M+esc(src)+'" alt="'+esc(alt)+'">':'<span>—</span>'}
function card(it){return '<button class="item" data-id="'+it.id+'"><div class="ph">'+img(it.photo,t(it,"name"))+'</div><div class="tx"><div class="nm">'+esc(t(it,"name"))+'</div>'+(t(it,"desc")?'<div class="ds">'+esc(t(it,"desc"))+'</div>':'')+'<div class="pr">'+esc(price(it.price))+'</div></div></button>'}
var GIFT='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 12v10H4V12"/><path d="M2 7h20v5H2z"/><path d="M12 22V7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>';
/* Three kinds of section: the featured offer (Dashain Dhamaka) goes first, with a
   gift on a red tab; combos go last; everything else keeps the menu order. */
function isCombo(s){return /combo|套餐/i.test((s.name_en||"")+(s.name_zh||""))}
function isOffer(s){return !isCombo(s)&&/offer|dhamaka|优惠|特惠/i.test((s.name_en||"")+(s.name_zh||""))}
function ordered(){var live=D.sections.filter(function(s){return s.items.length});return live.filter(isOffer).concat(live.filter(function(s){return !isOffer(s)&&!isCombo(s)}),live.filter(isCombo))}
function render(){
  document.documentElement.lang=lang==="zh"?"zh-CN":"en";
  document.title=t(D.settings,"title");
  var bn="";
  if(D.settings.banner)bn+='<img src="'+M+esc(D.settings.banner)+'" alt="">';
  else bn+='<div class="txt"><b>'+esc(t(D.settings,"title"))+'</b>'+(t(D.settings,"subtitle")?'<span>'+esc(t(D.settings,"subtitle"))+'</span>':'')+'</div>';
  document.getElementById("bn").innerHTML=bn;
  Array.prototype.forEach.call(document.querySelectorAll(".lang button"),function(b){b.className=b.getAttribute("data-l")===lang?"on":""});
  var live=ordered();
  var tabs=live.filter(isOffer).map(function(s){return{k:"s"+s.id,l:t(s,"name"),feat:true}});
  tabs.push({k:"all",l:UI[lang].all});
  tabs=tabs.concat(live.filter(function(s){return !isOffer(s)}).map(function(s){return{k:"s"+s.id,l:t(s,"name")}}));
  document.getElementById("tabs").innerHTML=tabs.map(function(x){return'<button data-k="'+x.k+'" class="'+(x.feat?"feat ":"")+(x.k===tab?"on":"")+'">'+(x.feat?GIFT:"")+esc(x.l)+'</button>'}).join("");
  document.getElementById("dll").textContent=UI[lang].dl;
  var h="";
  if(tab==="all"){
    h+='<div class="how"><h3>'+esc(UI[lang].how)+'</h3><p>'+esc(t(D.settings,"note")||UI[lang].howp)+'</p><div class="steps">'
      +'<div class="st"><div class="ic" style="background:#E3EEDF">'+ICONS.search+'</div><div><b>'+esc(UI[lang].s1)+'</b><span>'+esc(UI[lang].s1d)+'</span></div></div>'
      +'<div class="st"><div class="ic" style="background:#E2EAF7">'+ICONS.card+'</div><div><b>'+esc(UI[lang].s2)+'</b><span>'+esc(UI[lang].s2d)+'</span></div></div>'
      +'<div class="st"><div class="ic" style="background:#FBE9DC">'+ICONS.receipt+'</div><div><b>'+esc(UI[lang].s3)+'</b><span>'+esc(UI[lang].s3d)+'</span></div></div>'
      +'</div></div>';
  }
  live.forEach(function(s){
    if(tab!=="all"&&tab!=="s"+s.id)return;
    h+='<h2'+(isOffer(s)?' class="feat"':'')+'>'+(isOffer(s)?'<span class="tag">'+(lang==="zh"?"优惠":"Offer")+'</span>':'')+esc(t(s,"name"))+'</h2><div class="grid">'+s.items.map(card).join("")+'</div>';
  });
  if(!D.sections.some(function(s){return s.items.length}))h+='<div class="empty">'+esc(UI[lang].empty)+'</div>';
  document.getElementById("m").innerHTML=h;
  var f=t(D.settings,"footer");
  document.getElementById("ft").innerHTML=f?'<div class="hr"></div><ul>'+f.split(/\n+/).filter(function(x){return x.trim()}).map(function(x){return'<li>'+esc(x.replace(/^[-•*]\s*/,""))+'</li>'}).join("")+'</ul>':'';
}
var byId={};D.sections.forEach(function(s){s.items.forEach(function(i){byId[i.id]=i})});
var ov=document.getElementById("ov"),ovb=document.getElementById("ovb");
function open(html){ovb.innerHTML=html;ov.className="ov open";history.pushState({ov:1},"")}
function close(){ov.className="ov";ovb.innerHTML=""}
/* The item in full: picture, name, price, description. Nothing to press — the
   purchase is made at the sales counter, so there is no quantity or basket. */
function detail(it){
  var other=lang==="en"?it.name_zh:it.name_en;
  return '<div class="big" data-detail="'+it.id+'">'+(it.photo?'<img src="'+M+esc(it.photo)+'" alt="">':'')+'<div class="tx"><div class="nm">'+esc(t(it,"name"))+'</div>'+(other&&other!==t(it,"name")?'<div class="nm2">'+esc(other)+'</div>':'')+'<div class="pr">'+esc(price(it.price))+'</div>'+(t(it,"desc")?'<div class="ds">'+esc(t(it,"desc"))+'</div>':'')+'</div></div>';
}
document.addEventListener("click",function(e){
  var el=e.target.closest("button");if(!el)return;
  if(el.parentNode&&el.parentNode.className==="lang"){lang=el.getAttribute("data-l");render();if(ov.className.indexOf("open")>=0){var d=ovb.querySelector("[data-detail]");if(d)ovb.innerHTML=detail(byId[d.getAttribute("data-detail")])}return}
  if(el.hasAttribute("data-k")){tab=el.getAttribute("data-k");render();window.scrollTo(0,0);return}
  if(el.hasAttribute("data-id")){open(detail(byId[el.getAttribute("data-id")]));return}
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


def render_menu(data: dict, media_base: str, logo_html: str = "", pdf_url: str = "koseli-menu.pdf") -> str:
    """`data` = {settings, sections:[{..., items:[...]}], posters:[...]}."""
    title = (data.get("settings") or {}).get("title_en") or "Onboard Menu"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (PAGE.replace("__DATA__", payload)
                .replace("__MEDIA__", media_base.replace('"', ""))
                .replace("__PDF__", pdf_url.replace('"', ""))
                .replace("__TITLE__", title.replace("<", "&lt;"))
                .replace("__LOGO__", logo_html))
