#!/usr/bin/env python3
"""Turn weinstein_feed.json (written by weinstein_pure.py) into a single,
self-contained, shareable HTML dashboard.

    python3 build_dashboard.py [feed.json] [out.html]
"""
import html
import json
import sys
from datetime import datetime

FEED = sys.argv[1] if len(sys.argv) > 1 else "weinstein_feed.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "weinstein_dashboard.html"
e = html.escape


def _bad(v):
    return v is None or (isinstance(v, float) and v != v)


def money(v):
    return "–" if _bad(v) else f"${v:,.2f}"


def pct(v, sign=True):
    return "–" if _bad(v) else (f"{v:+.1f}%" if sign else f"{v:.1f}%")


def num(v, d=2):
    return "–" if _bad(v) else f"{v:.{d}f}"


def load():
    with open(FEED) as f:
        return json.load(f)


# ---------------------------------------------------------------- helpers
def badges(r):
    b = []
    sc = r.get("triple_score")
    sc = None if _bad(sc) else int(sc)
    if sc == 3:
        b.append("<span class='bdg gold'>TRIPLE</span>")
    elif sc:
        b.append(f"<span class='bdg dim'>{sc}/3</span>")
    if r.get("lr_virgin") is True:
        b.append("<span class='bdg gold'>A+ 10-YR HIGH</span>")
    return "".join(b)


def long_range_line(r):
    if r.get("lr_virgin") is True:
        return "Virgin territory: a new 10-year high, no overhead supply (p.99)."
    n = r.get("lr_near_years")
    if _bad(n):
        return ""
    n = int(n)
    if n == 0:
        return "No yearly high within 20% overhead on the 10-year view."
    return (f"{n} of the last {r.get('lr_years', 10)} yearly highs sit within 20% overhead "
            f"(nearest {money(r.get('lr_near_level'))}).")


def triple_line(r):
    sc = r.get("triple_score")
    if _bad(sc):
        return ""
    sc = int(sc)
    parts = [("volume", r.get("triple_vol")), ("RS turning positive", r.get("triple_rs")),
             ("40%+ run before breakout", r.get("triple_adv"))]
    met = ", ".join(n for n, v in parts if v) or "none"
    txt = f"Triple-confirmation check: {sc}/3 ({met})."
    if sc == 3:
        txt += " The book says to invest much more heavily in these (p.157)."
    if r.get("stage_weeks") == 1 and r.get("triple_vol"):
        txt += " Volume follow-through is still pending."
    return txt


def ticket(r, rules):
    """The book's order: buy-stop just above the breakout, with a limit, GTC (p.66).
    The book's example is a $12 stock: stop 1/8 over, limit 1/4 over (1/2 if thin).
    Scaled here to the same percentages so it works at any price."""
    lv = r["entry_low"]
    if lv is None:
        return ""
    stop_px, lim_px, thin_px = lv * 1.0104, lv * 1.0208, lv * 1.0417
    return (f"Order: BUY-STOP {money(stop_px)}, LIMIT {money(lim_px)} "
            f"({money(thin_px)} if thinly traded), GTC.")


# ---------------------------------------------------------------- cards
def active_card(r, rules):
    lo, hi = r["entry_low"], r["entry_high"]
    if r["kind"] == "pullback":
        how = (f"Buy on the pullback, inside <b>{money(lo)} – {money(hi)}</b>. "
               f"Price now {money(r['price'])}. Volume has dried up, the book's cue (p.105). "
               f"If you bought half on the breakout, this is the other half.")
        label = "PULLBACK BUY"
    elif r["kind"] == "base":
        how = (f"Buy-stop at <b>{money(lo)}</b>; skip it above {money(hi)}. " + ticket(r, rules))
        label = "BREAKOUT BUY"
    else:
        cont = r["verdict"].startswith("CONT")
        label = "CONTINUATION BUY" if cont else "BREAKOUT BUY"
        how = (f"Buy on strength above <b>{money(lo)}</b>. "
               f"Do not chase above <b>{money(hi)}</b> (+{rules['max_chase_pct']:.0f}%). "
               + ticket(r, rules) + " "
               + ("Continuation buys suit traders and late bull markets; buy the whole position "
                  "at the breakout (p.61-63)." if cont else
                  "Investors buy half at the breakout and half on the pullback if volume "
                  "contracts (p.59); traders buy it all (p.59)."))
    per = rules["account_size"] / rules["positions"]
    return f"""
    <div class="abuy">
      <div class="abuy-top"><span class="tk">{e(r['ticker'])}</span>
        <span class="tag">{label}</span></div>
      <div class="bdgs">{badges(r)}</div>
      <div class="abuy-sub">{e(r.get('group') or '')} · Stage 2 week {int(r['stage_weeks'])} ·
        RS {num(r['rs'],1)} · breakout volume {num(r.get('bo_vol_ratio') or r['vol_ratio_4wk'],1)}x{' (3-4 wk build-up)' if r.get('bo_buildup') else ''}</div>
      <div class="levels">
        <div class="lv entry"><small>ENTRY</small><b>{money(r['price']) if r['kind'] == 'pullback' else money(lo)}</b>
          <em>{('zone ' + money(lo) + ' – ' + money(hi)) if r['kind'] == 'pullback' else 'up to ' + money(hi)}</em></div>
        <div class="lv stop"><small>STOP LOSS</small><b>{money(r['stop'])}</b>
          <em>{pct(r['risk_pct'])} from price</em></div>
      </div>
      <p class="how">{how}</p>
      <p class="how">Stop goes in as a sell-stop the day you buy ({e(r.get('stop_basis') or 'base floor')},
        one-eighth under a round number). Raise it only as the stock builds new higher lows.</p>
      <p class="how sm">{e(long_range_line(r))} {e(triple_line(r))}</p>
      <p class="size">Example size: {int(r['shares'])} sh ≈ ${per:,.0f}
        (1/{rules['positions']} of ${rules['account_size']:,.0f}) · resistance: {e(r['resistance_note'])}</p>
    </div>"""


def active_panel(d):
    rules = d["rules"]
    a = sorted(d["active"], key=lambda r: -(r["risk_pct"] if r["risk_pct"] is not None else -99))
    if d["market_blocked"]:
        body = ("<div class='none stop4'><h3>Buying suspended</h3>"
                "<p>The S&amp;P 500 is in Stage 4. The book says don't buy into a bearish "
                "market (p.139). Candidates are hidden until the market improves.</p></div>")
    elif not a:
        body = ("<div class='none'><h3>No active buys this week</h3>"
                "<p>Nothing meets every rule. Patience is a position. "
                "Check the near misses below.</p></div>")
    else:
        body = f"<div class='abuys'>{''.join(active_card(r, rules) for r in a)}</div>"
    return f"""
  <section class="hero">
    <div class="hero-head"><span class="dot"></span>ACTIVE BUYS
      <span class="count">{0 if d['market_blocked'] else len(a)}</span></div>
    {body}
    <div class="hero-foot">Stop within {rules['wide_stop_pct']:.0f}% of entry ·
      breakout volume ≥ {rules['breakout_vol_mult']:.0f}x · no heavy resistance within
      {rules['resistance_near_pct']:.0f}% · group and market not in decline</div>
  </section>"""


# ---------------------------------------------------------------- tables
def section(cls, title, sub, rows, cols, note=""):
    if not rows:
        body = "<div class='empty'>None this week</div>"
    else:
        th = "".join(f"<th>{c[0]}</th>" for c in cols)
        trs = ""
        for r in rows:
            trs += "<tr>" + "".join(f"<td class='{c[2] if len(c) > 2 else ''}'>{c[1](r)}</td>"
                                    for c in cols) + "</tr>"
        body = f"<div class='scroll'><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>"
    return f"""
  <section class="panel {cls}">
    <div class="ph"><h2>{title}<span class="count">{len(rows)}</span></h2><p>{sub}</p></div>
    {body}{f'<div class="note">{note}</div>' if note else ''}
  </section>"""


def tk(r):
    return (f"<b class='t'>{e(r['ticker'])}</b><span class='g'>{e(r.get('group') or '')}</span>"
            f"{badges(r)}")


def res_cell(r):
    n = r.get("resistance_note") or ""
    c = "heavy" if n.startswith("HEAVY") else "light" if n.startswith("light") else "ok"
    return f"<span class='chip {c}'>{e(n)}</span>"


def build(d):
    near_cols = [
        ("Ticker", tk), ("Price", lambda r: money(r["price"])),
        ("Stage 2 wks", lambda r: int(r["stage_weeks"])),
        ("Volume vs peak", lambda r: f"{num(r['vol_vs_peak'])} <small>(needs ≤ {d['rules']['pullback_vol_peak_max']:.2f})</small>"),
        ("Pullback zone", lambda r: f"{money(r['entry_low'])} – {money(r['entry_high'])}"),
        ("Stop", lambda r: money(r["stop"])), ("Risk", lambda r: pct(r["risk_pct"])),
        ("RS", lambda r: num(r["rs"], 1)), ("Resistance", res_cell)]
    watch_cols = [
        ("Ticker", tk), ("Price", lambda r: money(r["price"])),
        ("Buy-stop", lambda r: f"<b>{money(r['entry_low'])}</b>"),
        ("To go", lambda r: pct(r["pct_to_trigger"])),
        ("Stop", lambda r: money(r["stop"])), ("Risk", lambda r: pct(r["risk_pct"])),
        ("Base", lambda r: f"{int(r['range_weeks'])}w / {num(r['range_width_pct'],0)}% wide"),
        ("RS", lambda r: num(r["rs"], 1)), ("Resistance", res_cell)]
    skip_cols = [
        ("Ticker", tk), ("Setup", lambda r: e(r["verdict"].title())),
        ("Price", lambda r: money(r["price"])),
        ("Entry", lambda r: money(r["entry_low"])),
        ("Stop", lambda r: money(r["stop"])), ("Risk", lambda r: f"<b class='bad'>{pct(r['risk_pct'])}</b>"),
        ("RS", lambda r: num(r["rs"], 1)), ("Resistance", res_cell)]
    wait_cols = [
        ("Ticker", tk), ("Price", lambda r: money(r["price"])),
        ("Entry point", lambda r: money(r["entry_low"])),
        ("Wait for", lambda r: f"a pullback toward {money(r['entry_low'])}–{money(r['entry_high'])}"),
        ("Stage 2 wks", lambda r: int(r["stage_weeks"])), ("RS", lambda r: num(r["rs"], 1)),
        ("Resistance", res_cell)]
    disc_cols = [
        ("Ticker", tk), ("Would have been", lambda r: e(str(r["would_be"]).title())),
        ("Price", lambda r: money(r["price"])),
        ("Resistance", lambda r: f"{money(r['resistance_level'])} <small>({pct(r['resistance_pct'])})</small>"),
        ("Age", lambda r: f"{int(r['resistance_age_wks'])}w"),
        ("Weeks over", lambda r: r["resistance_weeks_over"]),
        ("Risk", lambda r: pct(r["risk_pct"]))]
    susp_cols = [
        ("Ticker", tk), ("Price", lambda r: money(r["price"])),
        ("Stage 2 wks", lambda r: int(r["stage_weeks"])),
        ("Volume", lambda r: f"{num(r['vol_ratio_4wk'],1)}x <small>(needs ≥ {d['rules']['breakout_vol_mult']:.0f}x)</small>"),
        ("RS", lambda r: num(r["rs"], 1))]

    blocked = d["market_blocked"]
    ms = d["market_stage"]
    mcls = {1: "m1", 2: "m2", 3: "m3", 4: "m4"}.get(ms, "m3")
    mtxt = {1: "Stage 1 – basing", 2: "Stage 2 – advancing", 3: "Stage 3 – topping",
            4: "Stage 4 – declining"}.get(ms, f"Stage {ms}")
    grs = d.get("groups_rs") or {}
    groups = "".join(
        f"<span class='gc {'on' if s in (1, 2) else 'off'}'>{e(g)} <i>S{s if s else '?'}"
        f"{'' if _bad(grs.get(g)) else f' · RS {grs[g]:+.0f}'}</i></span>"
        for g, s in sorted(d["groups"].items()))
    mk = d.get("market") or {}
    gl = "".join(
        f"<li class='{g['status']}'><b>{e(g['name'])}</b><span>{e(g['detail'])}</span></li>"
        for g in mk.get("gauges", []))
    caution = ""
    if mk.get("caution") and not blocked:
        caution = ("<div class='caution'><b>Caution:</b> more market gauges are negative than positive. "
                   "The book says to do very little buying and accept only A+ setups when the market "
                   "trend is against you (p.75, p.154).</div>")
    gauge_html = (f"<details class='gauges' {'open' if mk.get('caution') or blocked else ''}><summary>"
                  f"Market weight of the evidence: {mk.get('pos', 0)} positive, {mk.get('neg', 0)} negative"
                  f" ({len(mk.get('gauges', []))} gauges)</summary><ul>{gl}</ul>"
                  "<p class='gnote'>Breadth gauges use the S&amp;P 1500 as a stand-in for the NYSE. "
                  "Not tracked: price/dividend ratio, contrary opinion, weekly NYSE common-stock new highs.</p>"
                  f"</details>{caution}") if gl else ""
    tiles = [("a", "Active buys", 0 if blocked else len(d["active"])),
             ("n", "Near misses", len(d["near"])),
             ("w", "Buy-stop watch", len(d["watch"])),
             ("p", "Wait for pullback", len(d["waits"])),
             ("s", "Skip: stop too wide", len(d["skip_stop"]) + len(d["watch_skip"])),
             ("d", "Discarded: resistance", len(d["disc"]))]
    tile_html = "".join(f"<div class='tile t{c}'><b>{n}</b><span>{t}</span></div>" for c, t, n in tiles)
    gen = datetime.fromisoformat(d["generated"]).strftime("%b %d, %Y %I:%M %p")

    hid = "<div class='note'>Hidden: the market is in Stage 4.</div>" if blocked else ""
    sections = [
        section("near", "Near Misses",
                "In the pullback zone with a stop inside the limit, but volume has not contracted enough. Watch for volume to dry up.",
                [] if blocked else d["near"], near_cols,
                "Book example: pullback volume contracted over 75% from peak (p.105). The threshold is tunable."),
        section("watch", "Buy-Stop Watchlist",
                "Coiled in a base with an acceptable stop. Place a buy-stop above the top; it is not a buy until it triggers on volume.",
                [] if blocked else d["watch"], watch_cols),
        section("wait", "Wait for Pullback",
                f"Good breakout, more than {d['rules']['max_chase_pct']:.0f}% above entry. Don't chase (p.139).",
                [] if blocked else d["waits"], wait_cols),
        section("skip", "Skip – Stop Too Wide",
                f"Good setup, but the stop is more than {d['rules']['wide_stop_pct']:.0f}% away (p.184). Occasional exceptions only for outstanding charts.",
                [] if blocked else d["skip_stop"] + d["watch_skip"], skip_cols),
        section("disc", "Discarded – Overhead Resistance",
                f"Would otherwise qualify, but supply sits within {d['rules']['resistance_near_pct']:.0f}% overhead (p.115, p.139). Re-check if price clears the level.",
                [] if blocked else d["disc"], disc_cols),
        section("susp", "Suspect Breakouts",
                "New Stage 2 without the required volume surge. If owned, sell on the first rally (p.116).",
                d["suspects"], susp_cols),
    ]

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weinstein Stage 2 Dashboard</title><style>{CSS}</style></head><body>
<header>
  <div><h1>Stage 2 Buy Dashboard</h1>
    <p>Stan Weinstein method · weekly charts · {d['screened']} stocks screened in favorable groups</p></div>
  <div class="meta"><div>Week ending <b>{d['last_bar']}</b></div><div>Updated {gen}</div></div>
</header>
<div class="mkt {mcls}"><b>S&amp;P 500: {mtxt}</b>
  <span>{'Buying suspended: the book says do not buy into a bearish market.' if blocked else 'Market trend permits buying.'}</span></div>
<div class="groups"><small>SECTORS</small>{groups}</div>
{gauge_html}
<div class="tiles">{tile_html}</div>
{active_panel(d)}
{hid}
<main>{''.join(sections)}</main>
<footer>
  <h3>How to read this</h3>
  <p><b>Active buy</b> = Stage 2 breakout on ≥2x volume (or continuation/pullback buy), market and sector not in decline,
  no heavy overhead resistance, stop within {d['rules']['wide_stop_pct']:.0f}% of entry. <b>Entry</b> is the breakout level; do not chase more than
  {d['rules']['max_chase_pct']:.0f}% above. <b>Stop</b> sits one-eighth below the base floor (or below the pullback low),
  placed as a sell-stop. Size positions equally, about 1/{d['rules']['positions']} of the account each.</p>
  <p class="disc">For education only. This is not investment advice and not a recommendation to buy or sell any security.
  Signals are mechanical and can be wrong; verify on the chart and size risk yourself. Data: Yahoo Finance, Friday weekly closes.
  Rules follow <i>Secrets for Profiting in Bull and Bear Markets</i>; thresholds the book does not specify are the author's choices.</p>
</footer></body></html>"""


CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#f3f5f9;color:#1c2333;font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:0 0 40px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;padding:26px 32px 18px;background:linear-gradient(135deg,#0f2a4a,#1d4f7a);color:#fff}
header h1{margin:0;font-size:26px;letter-spacing:.3px}header p{margin:4px 0 0;opacity:.8}
.meta{text-align:right;font-size:13px;opacity:.9}
.mkt{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 32px;font-size:13px;color:#fff}
.m1{background:#6b7a90}.m2{background:#1f9d55}.m3{background:#d9822b}.m4{background:#c0392b}
.mkt span{opacity:.92}
.groups{padding:12px 32px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.groups small{color:#6b7689;font-weight:600;letter-spacing:.8px;margin-right:4px}
.gc{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.gc i{font-style:normal;opacity:.7;margin-left:3px}
.gc.on{background:#dff3e6;color:#17683a}.gc.off{background:#e8eaef;color:#7a8396;text-decoration:line-through}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:16px 32px 6px}
.tile{background:#fff;border-radius:10px;padding:12px 14px;border-left:5px solid #999;box-shadow:0 1px 3px rgba(20,30,60,.08)}
.tile b{display:block;font-size:26px;line-height:1.1}.tile span{font-size:12px;color:#5d6778}
.ta{border-color:#1f9d55}.ta b{color:#1f9d55}.tn{border-color:#e6a100}.tn b{color:#b97f00}
.tw{border-color:#2f7fd6}.tw b{color:#2f7fd6}.tp{border-color:#8a5bd0}.tp b{color:#8a5bd0}
.ts{border-color:#d9582b}.ts b{color:#d9582b}.td{border-color:#8b94a6}.td b{color:#6b7689}
/* hero */
.hero{max-width:1040px;margin:22px auto 10px;background:#fff;border-radius:18px;border:3px solid #1f9d55;
 box-shadow:0 0 0 6px rgba(31,157,85,.12),0 12px 34px rgba(31,157,85,.18);overflow:hidden}
.hero-head{background:linear-gradient(90deg,#157a41,#23b068);color:#fff;text-align:center;font-size:22px;font-weight:800;
 letter-spacing:3px;padding:14px;display:flex;justify-content:center;align-items:center;gap:12px}
.hero-head .dot{width:12px;height:12px;border-radius:50%;background:#b8ffd3;box-shadow:0 0 0 4px rgba(255,255,255,.25)}
.count{display:inline-block;margin-left:10px;background:rgba(0,0,0,.14);border-radius:999px;padding:1px 12px;font-size:15px;letter-spacing:0;vertical-align:middle}
.abuys{display:flex;flex-wrap:wrap;gap:16px;justify-content:center;padding:22px}
.abuy{flex:1 1 300px;max-width:460px;border:2px solid #bfe6cd;background:#f4fbf7;border-radius:14px;padding:16px}
.abuy-top{display:flex;justify-content:space-between;align-items:center}
.tk{font-size:30px;font-weight:800;color:#0f5e32}.tag{background:#1f9d55;color:#fff;border-radius:6px;padding:3px 9px;font-size:11px;font-weight:700;letter-spacing:.8px}
.abuy-sub{color:#5d6778;font-size:12px;margin:2px 0 12px}
.levels{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.lv{border-radius:10px;padding:10px 12px;text-align:center}
.lv small{display:block;font-weight:700;letter-spacing:1px;font-size:11px}.lv b{font-size:26px;display:block;line-height:1.15}.lv em{font-style:normal;font-size:12px}
.lv.entry{background:#dff3e6;color:#0f5e32}.lv.stop{background:#fde4e1;color:#a52a1d}
.how{margin:10px 0 0;font-size:13px}.size{margin:10px 0 0;font-size:12px;color:#5d6778;border-top:1px dashed #bfe6cd;padding-top:8px}
.none{text-align:center;padding:34px 24px}.none h3{margin:0 0 6px;font-size:20px;color:#157a41}.none p{margin:0;color:#5d6778}
.none.stop4 h3{color:#c0392b}
.hero-foot{text-align:center;font-size:12px;color:#5d6778;background:#eef8f2;padding:10px 16px;border-top:1px solid #d3eadc}
/* panels */
main{max-width:1240px;margin:18px auto;padding:0 20px;display:grid;gap:18px}
.panel{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(20,30,60,.08);overflow:hidden;border-top:5px solid #999}
.ph{padding:14px 18px 8px}.ph h2{margin:0;font-size:17px}.ph p{margin:3px 0 0;color:#5d6778;font-size:13px}
.ph .count{background:#e9ecf2;color:#444;font-size:13px}
.near{border-color:#e6a100}.watch{border-color:#2f7fd6}.wait{border-color:#8a5bd0}.skip{border-color:#d9582b}.disc{border-color:#8b94a6}.susp{border-color:#5b6b85}
.near .ph h2{color:#a87100}.watch .ph h2{color:#2469b4}.wait .ph h2{color:#7547b8}.skip .ph h2{color:#c2471f}.disc .ph h2{color:#5f6878}.susp .ph h2{color:#46546b}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%}th{text-align:left;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:#6b7689;padding:8px 14px;background:#f7f8fb;white-space:nowrap}
td{padding:9px 14px;border-top:1px solid #edf0f5;white-space:nowrap}tr:hover td{background:#fafbfd}
.t{font-size:15px;margin-right:8px}.g{font-size:11px;color:#8a93a5}
small{color:#8a93a5}.bad{color:#c0392b}
.chip{padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.chip.light{background:#fff3d6;color:#8a6100}.chip.heavy{background:#fde4e1;color:#a52a1d}.chip.ok{background:#dff3e6;color:#17683a}
.empty{padding:16px 18px;color:#8a93a5}.note{padding:8px 18px 12px;font-size:12px;color:#7a8396}
footer{max-width:1040px;margin:26px auto 0;padding:0 24px;color:#5d6778;font-size:12.5px}
footer h3{margin:0 0 6px;font-size:14px;color:#1c2333}.disc{font-size:11.5px;color:#8a93a5}
.bdgs{margin:4px 0 0}.bdg{display:inline-block;border-radius:5px;padding:1px 7px;margin:0 4px 2px 0;font-size:10px;font-weight:800;letter-spacing:.6px}
.bdg.gold{background:#ffe9a8;color:#7a5300}.bdg.dim{background:#e8eaef;color:#6b7689}
td .bdg{margin-left:6px;vertical-align:middle}
.how.sm{font-size:12px;color:#5d6778}
.gauges{margin:12px 32px 0;background:#fff;border-radius:10px;padding:8px 14px;box-shadow:0 1px 3px rgba(20,30,60,.08);font-size:13px}
.gauges summary{cursor:pointer;font-weight:600}.gauges ul{list-style:none;margin:8px 0 4px;padding:0}
.gauges li{display:flex;justify-content:space-between;gap:12px;padding:5px 0 5px 12px;border-left:5px solid #b8bfcc;margin:3px 0}
.gauges li.pos{border-color:#1f9d55}.gauges li.neg{border-color:#c0392b}.gauges li span{color:#5d6778;text-align:right}
.gnote{margin:6px 0 2px;font-size:11.5px;color:#8a93a5}
.caution{margin:10px 32px 0;background:#fff3d6;border-left:5px solid #e6a100;border-radius:8px;padding:10px 14px;font-size:13px;color:#7a5300}
@media(max-width:640px){header,.mkt,.groups,.tiles{padding-left:16px;padding-right:16px}.levels{grid-template-columns:1fr}}
"""

if __name__ == "__main__":
    data = load()
    with open(OUT, "w") as f:
        f.write(build(data))
    print(f"wrote {OUT}  ({len(data['active'])} active, {len(data['near'])} near misses)")
