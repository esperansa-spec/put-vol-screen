"""
Adds the US / Japan vol-curve panel to the put vol screen page.

Works on either the generated index.html or the generator's template source, since it only
keys off markup that appears in both. Idempotent (skips if already applied).
    python3 apply_curves_patch.py index.html            # or: your_generator.py
The panel stays hidden until D.curves is present (see vol_curves.build_curves).
"""
import sys

CSS = """.curves{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;margin-bottom:14px}
.cv{background:var(--surface);border:1px solid var(--rule);border-radius:8px;padding:10px 14px}
.cv h3{font-size:13px;margin:0;font-weight:600}.cv .m{color:var(--ink2);font-size:12px;margin:2px 0 4px}
.cv .m b{color:var(--ink);font-weight:600}
"""

HTML = '<div class="curves" id="curves"></div>\n'

JS = r"""
/* ---- vol curves (US / JP index term structure) ---- */
function curves(){const host=$('#curves');const cs=D.curves||{};const ks=['US','JP'].filter(k=>cs[k]&&cs[k].pts&&cs[k].pts.length);
 if(!ks.length){host.style.display='none';return}
 const C={rule:'#e4e3df',ink3:'#8a8984',ink2:'#52514e',ink:'#0b0b0b',s1:'#2a78d6',s2:'#eb6834',surface:'#fcfcfb'};
 const TK=[[9,'9d'],[30,'1M'],[90,'3M'],[180,'6M'],[365,'1Y']];
 const at=(pts,t)=>{const p=pts.find(q=>q.t==t);return p?p.v:null};
 const near=(pts,t)=>pts.reduce((a,b)=>Math.abs(b.t-t)<Math.abs(a.t-t)?b:a);
 host.innerHTML=ks.map(k=>`<div class="cv" id="cv${k}"></div>`).join('');
 ks.forEach(k=>{const c=cs[k],el=$('#cv'+k);
  const W=Math.max(280,(el.clientWidth||360)-28),H=170,P={l:30,r:12,t:10,b:22};
  const series=[['m1',c.m1,C.ink3,'2 3',1],['w1',c.w1,C.s2,'4 3',1.5],['now',{asof:c.asof,pts:c.pts},C.s1,'',2]].filter(s=>s[1]&&s[1].pts&&s[1].pts.length);
  const all=series.flatMap(s=>s[1].pts.map(p=>p.v)).concat((c.raw||[]).map(p=>p.v));
  const tmin=Math.min(...c.pts.map(p=>p.t),30),tmax=Math.max(...c.pts.map(p=>p.t),180);
  let lo=Math.min(...all),hi=Math.max(...all);const pad=Math.max(.01,(hi-lo)*.15);lo=Math.max(0,lo-pad);hi=hi+pad;
  const sx=t=>Math.sqrt(t),x=t=>P.l+(sx(t)-sx(tmin))/(sx(tmax)-sx(tmin)||1)*(W-P.l-P.r),y=v=>P.t+(H-P.t-P.b)*(1-(v-lo)/(hi-lo||1));
  const ln=pts=>pts.map((p,i)=>(i?'L':'M')+x(p.t).toFixed(1)+' '+y(p.v).toFixed(1)).join('');
  const yt=[0,.5,1].map(f=>lo+f*(hi-lo));
  /* slope readout: 3M minus 1M (VIX3M-VIX for US). Positive = upward sloping (normal); negative = inverted, near-term stress */
  const v1=at(c.pts,30),v3=at(c.pts,93)??at(c.pts,90),sl=(v1!=null&&v3!=null)?(v3-v1)*100:null;
  const slTxt=sl==null?'':` · 3M−1M <b style="color:${sl<0?'var(--crit)':'var(--ink)'}">${sl>0?'+':''}${sl.toFixed(1)}</b> ${sl<0?'inverted':'upward'}`;
  const chg=s=>{if(!s)return'';const p=near(s.pts,30),q=at(c.pts,p.t);return q==null?'':` · 1M vs ${s===c.w1?'1w':'1m'} ago ${(q-p.v)*100>0?'+':''}${((q-p.v)*100).toFixed(1)}`};
  let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${k} implied vol term structure">`+
   yt.map(t=>`<line x1="${P.l}" x2="${W-P.r}" y1="${y(t)}" y2="${y(t)}" stroke="${C.rule}"/><text x="${P.l-5}" y="${y(t)+4}" text-anchor="end" fill="${C.ink3}" font-size="11">${(t*100).toFixed(0)}</text>`).join('')+
   TK.filter(([t])=>t>=tmin&&t<=tmax).map(([t,l])=>`<text x="${x(t)}" y="${H-6}" text-anchor="middle" fill="${C.ink3}" font-size="11">${l}</text>`).join('')+
   series.map(([id,s,col,da,w])=>`<path d="${ln(s.pts)}" fill="none" stroke="${col}" stroke-width="${w}" ${da?`stroke-dasharray="${da}"`:''}/>`).join('')+
   (c.raw||[]).filter(p=>p.t>=tmin&&p.t<=tmax).map(p=>`<circle cx="${x(p.t)}" cy="${y(p.v)}" r="2.5" fill="none" stroke="${C.s1}"/>`).join('')+
   c.pts.map(p=>`<circle cx="${x(p.t)}" cy="${y(p.v)}" r="3" fill="${C.s1}" stroke="${C.surface}" stroke-width="1.5"/>`).join('')+
   `<line class="xh" y1="${P.t}" y2="${H-P.b}" stroke="${C.ink3}" stroke-dasharray="3 3" style="display:none"/><rect class="hit" x="${P.l}" y="0" width="${W-P.l-P.r}" height="${H}" fill="transparent"/></svg>`;
  const leg=series.map(([id,s,col,da])=>`<span><span class="sw" style="background:${col}${da?';opacity:.7':''}"></span>${id=='now'?'Today':id=='w1'?'~1w ago':'~1m ago'} <span class="mut">${s.asof}</span></span>`).join('')+(c.raw&&c.raw.length?'<span class="mut">○ listed expiries</span>':'');
  el.innerHTML=`<h3>${k=='US'?'US':'Japan'} · ${c.name}${c.stale?' <span class="q">stale</span>':''}</h3><div class="m">1M <b>${v1==null?'–':(v1*100).toFixed(1)}</b>${slTxt}${chg(c.w1)}</div><div class="leg">${leg}</div>${svg}`;
  const tip=$('#tip'),xh=el.querySelector('.xh'),hit=el.querySelector('.hit');
  const move=e=>{const pt=(e.touches&&e.touches[0])||e,b=hit.getBoundingClientRect();
   const tx=sx(tmin)+(pt.clientX-b.left)/(b.width||1)*(sx(tmax)-sx(tmin)),p=near(c.pts,tx*tx);
   xh.setAttribute('x1',x(p.t));xh.setAttribute('x2',x(p.t));xh.style.display='';tip.style.display='block';tip.style.left=(pt.clientX+12)+'px';tip.style.top=(pt.clientY-10)+'px';
   tip.innerHTML=`<b>${p.t}d${p.src?' · '+p.src:''}${p.x?' (flat-extrap.)':''}</b><br>Today ${(p.v*100).toFixed(1)}`+[[c.w1,'1w ago'],[c.m1,'1m ago']].filter(s=>s[0]).map(([s,l])=>{const q=at(s.pts,p.t);return q==null?'':`<br>${l} ${(q*100).toFixed(1)}`}).join('')};
  const off=()=>{tip.style.display='none';xh.style.display='none'};
  hit.onmousemove=move;hit.ontouchmove=e=>{e.preventDefault();move(e)};hit.onmouseleave=off;hit.ontouchend=off;});}
curves();let _cvT;window.addEventListener('resize',()=>{clearTimeout(_cvT);_cvT=setTimeout(curves,150)});
"""

NOTE = (" Vol curves: US = CBOE VIX9D/VIX/VIX3M/VIX6M/VIX1Y; Japan = Nikkei 225 index-option ATM vol "
        "from JPX settlement per listed expiry, resampled to 1M/2M/3M/6M/1Y by total variance; "
        "x-axis is square-root of days.")


def patch(s: str) -> str:
    if 'id="curves"' in s:
        print("already applied");return s
    steps = [
        ('.note{', CSS + '.note{'),
        ('<div class="gauges" id="gauges"></div>', '<div class="gauges" id="gauges"></div>\n' + HTML),
        ('Indicative only; get a tradeable quote.', 'Indicative only; get a tradeable quote.' + NOTE),
        ('render();\n</script>', 'render();\n' + JS + '</script>'),
    ]
    for old, new in steps:
        if s.count(old) != 1:
            sys.exit(f"marker not found exactly once: {old[:40]!r}")
        s = s.replace(old, new)
    return s


if __name__ == "__main__":
    p = sys.argv[1]
    src = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(patch(src))
    print("patched", p)
