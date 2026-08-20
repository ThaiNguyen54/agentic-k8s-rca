# eval/plot_ablation.py
"""Turn e2e_ablation_results.json into with/without line graphs (self-contained HTML).

Reads the JSON written by e2e_ablation.py, aggregates means per (scenario, mode),
and emits an HTML file with line charts across A/B/C for:
  - Localization accuracy (%)
  - Correct remediation (%)
  - Diagnosis latency (s)
  - Prompt context (tokens, log scale)

Run:  python eval/plot_ablation.py --in e2e_ablation_results.json --out ablation_charts.html
"""
import argparse
import json
from collections import defaultdict


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def aggregate(results):
    acc, rem, appr, lat, tok = (defaultdict(list) for _ in range(5))
    scenarios = []
    for r in results:
        if "error" in r:
            continue
        k = (r["scenario"], r["mode"])
        if r["scenario"] not in scenarios:
            scenarios.append(r["scenario"])
        acc[k].append(r.get("localization_acc"))
        rem[k].append(r.get("remediation_acc"))
        appr[k].append(1.0 if r.get("decision_correct") else 0.0 if "decision_correct" in r else None)
        lat[k].append(r.get("latency_s"))
        tok[k].append(r.get("prompt_tokens"))
    scenarios = sorted(set(scenarios))
    modes = ["raw", "distilled"]
    has_rem = any(v is not None for vs in rem.values() for v in vs)

    def series(d, scale=1.0):
        return {m: [((_mean(d[(s, m)]) or 0) * scale) for s in scenarios] for m in modes}

    return scenarios, {
        "loc": series(acc, 100),
        "rem": series(rem, 100),
        "appr": series(appr, 100),
        "lat": series(lat, 1),
        "tok": series(tok, 1),
    }, has_rem


def build_html(scenarios, S, has_rem=True):
    def maxof(*keys):
        vals = [v for k in keys for m in ("raw", "distilled") for v in S[k][m]]
        return max(vals) if vals else 1
    lat_max = max(10, (int(maxof("lat") / 10) + 1) * 10)
    charts = [
        {"title": "Localization accuracy", "unit": "% of true faults correctly named · higher is better",
         "scale": "lin", "min": 0, "max": 100, "ticks": [0, 25, 50, 75, 100], "fmt": "pct",
         "without": S["loc"]["raw"], "with": S["loc"]["distilled"]},
        {"title": "Diagnosis latency", "unit": "RCA agent seconds · lower is better",
         "scale": "lin", "min": 0, "max": lat_max, "ticks": [0, lat_max/4, lat_max/2, 3*lat_max/4, lat_max], "fmt": "sec",
         "without": S["lat"]["raw"], "with": S["lat"]["distilled"]},
        {"title": "Prompt context", "unit": "estimated tokens · log scale · lower is better",
         "scale": "log", "min": 100, "max": 1000000, "ticks": [100, 1000, 10000, 100000, 1000000],
         "tickLabels": ["100", "1k", "10k", "100k", "1M"], "fmt": "tok",
         "without": S["tok"]["raw"], "with": S["tok"]["distilled"]},
    ]
    if has_rem:
        charts.insert(1, {"title": "Correct remediation", "unit": "% faults with the right corrective action · higher is better",
                          "scale": "lin", "min": 0, "max": 100, "ticks": [0, 25, 50, 75, 100], "fmt": "pct",
                          "without": S["rem"]["raw"], "with": S["rem"]["distilled"]})
    payload = json.dumps({"scenarios": scenarios, "charts": charts})
    return _TEMPLATE.replace("__PAYLOAD__", payload)


_TEMPLATE = r"""<title>End-to-End Ablation — with vs without pipeline</title>
<style>
:root{--page:#f4f6f9;--card:#fff;--ink:#182430;--soft:#54646f;--faint:#8a99a4;--grid:#e2e8ee;--axis:#b6c2cb;--bd:#dde4ea;--without:#f0871e;--with:#2f7dc4;--mono:ui-monospace,Menlo,Consolas,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media(prefers-color-scheme:dark){:root{--page:#0e151b;--card:#161f27;--ink:#e8eef3;--soft:#a2b1bb;--faint:#6c7d88;--grid:#243039;--axis:#3a4750;--bd:#28343d;--without:#f6993f;--with:#4f9fe0}}
:root[data-theme=light]{--page:#f4f6f9;--card:#fff;--ink:#182430;--soft:#54646f;--faint:#8a99a4;--grid:#e2e8ee;--axis:#b6c2cb;--bd:#dde4ea;--without:#f0871e;--with:#2f7dc4}
:root[data-theme=dark]{--page:#0e151b;--card:#161f27;--ink:#e8eef3;--soft:#a2b1bb;--faint:#6c7d88;--grid:#243039;--axis:#3a4750;--bd:#28343d;--without:#f6993f;--with:#4f9fe0}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font-family:var(--sans)}
.wrap{max-width:1140px;margin:0 auto;padding:34px 22px 60px}h1{font-size:clamp(20px,3vw,27px);margin:0 0 6px}
.sub{color:var(--soft);font-size:14px;margin:0;max-width:90ch}
.legend{display:flex;gap:22px;margin:20px 0 6px;font-size:14px;flex-wrap:wrap}.legend span{display:inline-flex;align-items:center;gap:9px;font-weight:600}.legend i{width:26px;height:4px;border-radius:3px}.lo{background:var(--without)}.lw{background:var(--with)}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;margin-top:8px}@media(max-width:760px){.grid2{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px}
.card h3{margin:0 0 2px;font-size:16px}.card .u{margin:0 0 6px;font-size:12px;color:var(--faint);font-family:var(--mono)}
.card svg{width:100%;height:auto;overflow:visible}
.grid line{stroke:var(--grid);stroke-width:1}.axis line{stroke:var(--axis);stroke-width:1.2}
.tick,.xlab{fill:var(--faint);font-family:var(--mono);font-size:11px}.xlab{fill:var(--soft);font-size:11.5px}
.line{fill:none;stroke-width:2.6}.sw{stroke:var(--without)}.si{stroke:var(--with)}.mw{fill:var(--without)}.mi{fill:var(--with)}
.vl{font-family:var(--mono);font-size:11px;font-weight:600}.vlw{fill:var(--without)}.vli{fill:var(--with)}
footer{margin-top:24px;padding-top:14px;border-top:1px solid var(--bd);font-size:12.5px;color:var(--faint);font-family:var(--mono)}
</style>
<div class="wrap">
<h1>End-to-end ablation — with vs without the Evidence-First pipeline</h1>
<p class="sub">Same incidents through the real RCA + validator agents; only the evidence context differs. Orange = without pipeline (raw collect-all), blue = with pipeline (distilled).</p>
<div class="legend"><span><i class="lo"></i>Without pipeline</span><span><i class="lw"></i>With pipeline</span></div>
<div class="grid2" id="charts"></div>
<footer>Generated by eval/plot_ablation.py from e2e_ablation_results.json</footer>
</div>
<script>
const P=__PAYLOAD__;const NS="http://www.w3.org/2000/svg";
const PL=54,PR=18,PT=26,PB=250,W=380;const pw=W-PL-PR,ph=PB-PT;
const xs=P.scenarios.map((_,i)=>PL+pw*(P.scenarios.length===1?0.5:(0.12+0.76*i/(P.scenarios.length-1))));
function el(t,a,x){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(x!=null)e.textContent=x;return e}
function fmt(v,f){return f==="pct"?v.toFixed(0)+"%":f==="sec"?v.toFixed(1)+"s":f==="tok"?(v>=1000?(v/1000).toFixed(v>=1e4?0:1)+"k":""+Math.round(v)):v}
function ylin(v,mn,mx){return PB-(v-mn)/(mx-mn)*ph}
function ylog(v,mn,mx){v=Math.max(v,mn);return PB-(Math.log10(v)-Math.log10(mn))/(Math.log10(mx)-Math.log10(mn))*ph}
function draw(svg,c){
 const Y=c.scale==="log"?(v)=>ylog(v,c.min,c.max):(v)=>ylin(v,c.min,c.max);
 const tl=c.tickLabels||c.ticks.map(t=>fmt(t,c.fmt));
 c.ticks.forEach((t,i)=>{const y=Y(t);const g=el("g",{class:"grid"});g.appendChild(el("line",{x1:PL,y1:y,x2:W-PR,y2:y}));svg.appendChild(g);svg.appendChild(el("text",{class:"tick","text-anchor":"end",x:PL-8,y:y+3.5},tl[i]))});
 const ax=el("g",{class:"axis"});ax.appendChild(el("line",{x1:PL,y1:PT,x2:PL,y2:PB}));ax.appendChild(el("line",{x1:PL,y1:PB,x2:W-PR,y2:PB}));svg.appendChild(ax);
 P.scenarios.forEach((s,i)=>svg.appendChild(el("text",{class:"xlab","text-anchor":"middle",x:xs[i],y:PB+18},s)));
 [["without","sw","mw","vlw",false],["with","si","mi","vli",true]].forEach(([key,lc,mc,vc,above])=>{
  const vals=c[key];const pts=vals.map((v,i)=>[xs[i],Y(v)]);
  svg.appendChild(el("path",{class:"line "+lc,d:"M"+pts.map(p=>p[0]+","+p[1]).join(" L")}));
  pts.forEach((p,i)=>{svg.appendChild(el("circle",{class:mc,cx:p[0],cy:p[1],r:4}));svg.appendChild(el("text",{class:"vl "+vc,"text-anchor":"middle",x:p[0],y:p[1]+(above?-9:16)},fmt(vals[i],c.fmt)))});
 });
}
const root=document.getElementById("charts");
P.charts.forEach(c=>{const card=document.createElement("div");card.className="card";
 card.innerHTML='<h3>'+c.title+'</h3><p class="u">'+c.unit+'</p>';
 const svg=el("svg",{viewBox:"0 0 380 300",role:"img"});card.appendChild(svg);root.appendChild(card);draw(svg,c)});
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="e2e_ablation_results.json")
    ap.add_argument("--out", default="ablation_charts.html")
    args = ap.parse_args()
    with open(args.inp) as f:
        results = json.load(f)
    scenarios, S, has_rem = aggregate(results)
    with open(args.out, "w") as f:
        f.write(build_html(scenarios, S, has_rem))
    # console table
    print(f"{'Scenario':<14}{'Mode':<11}{'Localiz.':<10}{'Remed.':<9}{'Approve':<9}{'Latency':<10}{'Tokens'}")
    for i, s in enumerate(scenarios):
        for m in ("raw", "distilled"):
            print(f"{s:<14}{m:<11}{S['loc'][m][i]:>6.1f}%   {S['rem'][m][i]:>5.1f}%  {S['appr'][m][i]:>5.1f}%  {S['lat'][m][i]:>6.1f}s   {S['tok'][m][i]:>8,.0f}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
