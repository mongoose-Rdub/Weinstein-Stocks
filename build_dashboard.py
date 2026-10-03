#!/usr/bin/env python3
"""Turn weinstein_feed.json (written by weinstein_pure.py) into a single,
self-contained, shareable HTML dashboard.

    python3 build_dashboard.py [feed.json] [out.html]
"""
import html
import json
import os
import sys
from datetime import datetime

EMBED_LIMIT = 1_500_000
REPO = os.environ.get("GITHUB_REPOSITORY") or "mongoose-Rdub/Weinstein-Stocks"
BRANCH = os.environ.get("POSITIONS_BRANCH") or "main"
BOOK_URL = (os.environ.get("BOOK_PDF_URL") or "").strip()      # optional link to a PDF you may open
BOOK_OFFSET = int(os.environ.get("BOOK_PDF_OFFSET") or 10)    # PDF page = printed page + this
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
def _badge(cls, label, title, lines):
    """A badge that opens a popup (click, tap, hover or Enter) explaining it for THIS stock.
    The popup text lives in data-tip: first line is the heading, the rest are paragraphs."""
    tip = "\n".join([title] + [x for x in lines if x])
    return (f"<span class='bdg {cls}' tabindex='0' role='button' "
            f"data-tip=\"{e(tip, quote=True)}\">{e(label)}</span>")


def _mk(ok):
    return "✓" if ok else "✗"


def triple_tip(r, sc):
    """Explain the triple-confirmation score with this stock's own numbers (p.150-152)."""
    ln = []
    # 1. volume
    bv, fol = r.get("bo_vol_ratio"), r.get("triple_follow")
    tv = bool(r.get("triple_vol"))
    t = f"{_mk(tv)} Volume: "
    t += (f"the breakout week traded {num(bv, 1)}x the prior four weeks (the book wants about 2x or more)."
          if not _bad(bv) else "breakout volume could not be measured.")
    if not _bad(fol):
        t += (f" Since the breakout, volume has averaged {num(fol, 1)}x the pre-breakout level "
              f"(we want 1.5x or more; that follow-through number is ours).")
    if r.get("vol_verify") is True:
        t += " Not counted: the volume reading is extreme and may be a data artifact."
    ln.append(t)
    # 2. relative strength
    rb, rn = r.get("triple_rs_before"), r.get("rs")
    trs = bool(r.get("triple_rs"))
    t = f"{_mk(trs)} Relative strength: "
    if trs:
        t += (f"it was {num(rb, 1)} just before the breakout (negative or hugging zero, as the book describes) "
              f"and is now {num(rn, 1)}, moving decisively positive.")
    elif _bad(rb) or _bad(rn):
        t += "not enough data to judge."
    elif rb > 3:
        t += (f"it was already {num(rb, 1)} before the breakout. The book wants a stock whose RS was "
              f"negative or near zero and then turned up (we accept up to +3).")
    elif rn <= 0:
        t += f"it was {num(rb, 1)} before the breakout and is {num(rn, 1)} now, so it has not turned positive yet."
    else:
        t += f"it was {num(rb, 1)} before the breakout and is {num(rn, 1)} now, not clearly rising."
    ln.append(t)
    # 3. prior advance
    ap = r.get("triple_adv_pct")
    tad = bool(r.get("triple_adv"))
    t = f"{_mk(tad)} Prior advance: "
    t += (f"price rose {num(ap, 0)}% from the base floor to the breakout level "
          f"(the book looks for some 40 to 50 percent or more)." if not _bad(ap)
          else "the size of the run could not be measured.")
    ln.append(t)
    ln.append("From the book's triple-confirmation pattern (p.150-152). It is a bonus signal, not a buy rule: "
              "it does not decide whether a stock qualifies.")
    if sc == 3:
        ln.append("With all three, the book says to invest much more heavily (p.157).")
    return f"Triple confirmation: {sc} of 3", ln


def badges(r):
    b = []
    sc = r.get("triple_score")
    sc = None if _bad(sc) else int(sc)
    if sc:
        title, lines = triple_tip(r, sc)
        b.append(_badge("gold" if sc == 3 else "dim", "TRIPLE" if sc == 3 else f"{sc}/3", title, lines))
    if r.get("lr_virgin") is True:
        yrs = r.get("lr_years")
        b.append(_badge("gold", "A+ 10-YR HIGH", "A+ 10-year high",
                        [f"{r.get('ticker', 'This stock')} is at a new 10-year high"
                         + (f" (checked over {int(yrs)} yearly highs)" if not _bad(yrs) else "") + ".",
                         "With no price history above it, there is no overhead supply: nobody is sitting on "
                         "losses waiting to sell at a higher price (p.99)."]))
    if r.get("vol_verify") is True:
        bv = r.get("bo_vol_ratio")
        b.append(_badge("thin", "VERIFY VOLUME", "Verify the volume",
                        [(f"Breakout volume was {num(bv, 0)}x normal, far outside the ordinary "
                          f"(we flag anything over 20x)." if not _bad(bv) else
                          "Breakout volume is extreme (we flag anything over 20x)."),
                         "That is often a split, a listing event or bad data rather than real buying. "
                         "Look at the weekly chart before trusting this stock's volume.",
                         "While flagged, the volume tests are not counted toward the triple check."]))
    lq = r.get("liq")
    if lq in ("thin", "very thin"):
        adv = r.get("avg_dollar_vol_m")
        very = lq == "very thin"
        b.append(_badge("thin", "VERY THIN" if very else "THIN",
                        "Very thinly traded" if very else "Thinly traded",
                        [(f"About ${num(adv, 2)}M traded per day on average." if not _bad(adv) else ""),
                         "Orders can move the price and a stop can fill well below its level, so keep the "
                         "position small. The example size is capped at about 5% of average daily volume "
                         "when that is smaller than 1/15 of the account.",
                         "For buys, the book uses a wider limit on thinly traded stocks (p.66)."]))
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
    """The book's orders. Buy: a buy-stop just above the breakout with a limit,
    GTC (p.66). The example is a $12 stock: stop 1/8 over, limit 1/4 over, or
    1/2 over "unless it trades very thinly". Sell: a GTC sell-stop the moment
    you buy; a straight stop on the NYSE, a stop-limit with a wide spread where
    only that is allowed (p.180). Scaled to the same percentages so it works
    at any price."""
    lv = r["entry_low"]
    if lv is None:
        return ""
    thin = r.get("liq") in ("thin", "very thin")
    stop_px, lim_px, thin_px = lv * 1.0104, lv * 1.0208, lv * 1.0417
    if thin:
        buy = (f"Order: BUY-STOP {money(stop_px)}, LIMIT {money(thin_px)} (the wider limit the book "
               f"uses for thinly traded stocks, p.66), GTC.")
    else:
        buy = (f"Order: BUY-STOP {money(stop_px)}, LIMIT {money(lim_px)} "
               f"({money(thin_px)} if it trades thinly), GTC.")
    return buy


def exit_line(r):
    sp = r.get("stop")
    if sp is None or _bad(sp):
        return ""
    wide = sp * 0.97
    txt = (f"Exit: enter a GTC SELL-STOP at {money(sp)} the day you buy. A straight stop on the NYSE; "
           f"if only a stop-limit is allowed, use a wide spread, e.g. stop {money(sp)}, limit {money(wide)} (p.180).")
    if r.get("liq") in ("thin", "very thin"):
        txt += " Thin stock: the stop can fill well below its price, so keep the position small."
    return txt


def profit_plan(r):
    """How the book says to take profits (Chapter 6). Investors get no price
    target: the trailing stop is the exit. Targets exist only for traders."""
    inv = ("<b>Investor (the book's default):</b> no price target. Raise the stop after the first 8-10% "
           "correction, but only once the stock rallies back near its prior high: put it under the "
           "correction low, or under the 30-week average if that is lower and still rising. When the "
           "average flattens, tighten it under the latest correction low. Sell <b>half</b> at the first "
           "sign of a Stage 3 top; the stop takes out the rest (p.36-37, p.184-186). "
           "Don't sell just because it feels high (p.165).")
    if r.get("overextended") is True:
        inv += (f" <b>It is already {num(r.get('pct_above_ma'), 0)}% above its 30-week average:</b> the book "
                f"says to lock in a quarter to a half of the position when a stock gets far above it (p.193).")
    tr = []
    ts = r.get("trader_stop")
    sp_ = r.get("stop")
    # a trader stop is only shown when it is actually closer than the investor stop
    if not _bad(ts) and (_bad(sp_) or ts > sp_):
        tr.append(f"stop {money(ts)} ({pct(r.get('trader_stop_pct'))}), a closer stop than the investor's: "
                  f"strong breakouts rarely fall more than 4-6% below the breakout (p.194-195)")
    if not _bad(r.get("swing_target")):
        tr.append(f"swing-rule target <b>{money(r['swing_target'])}</b> ({pct(r.get('swing_gain_pct'))}): "
                  f"old peak {money(r.get('swing_peak'))} minus the low {money(r.get('swing_low'))}, "
                  f"added to the peak. Sell part near it, the rest on the stop (p.202-205)")
    tr.append("sell half if a rising trendline touched 3+ times breaks (p.199); out if it closes under the "
              "30-week average even slightly (p.196)")
    trd = "<b>Trader only:</b> " + "; ".join(tr) + "."
    return (f"<details class='exitplan'><summary>Taking profits (book guidance)</summary>"
            f"<p>{inv}</p><p>{trd}</p></details>")


# ------------------------------------------------- green / yellow assessment
# Every number here is OURS, not the book's. They only decide how much caution to show
# on a stock that has ALREADY passed the book's buy rules. Green is never a recommendation.
SIG = {
    "overhead_pct": 10.0,        # a swing high this close overhead is worth a look even when rated light
    "yearly_highs": 3,           # this many of the last ~11 yearly highs sitting within 20% overhead
    "jump_pct": 10.0,            # a one-week move at least this big...
    "jump_vol_x": 8.0,           # ...on volume at least this many times the prior 4 weeks: check the news
    "wide_stop_pct": 12.0,       # stop this far away (the hard limit is 15%)
    "heavy_weeks": 8,            # weeks inside the overhead band that make supply "heavy" (cfg base_min_weeks)
}


def assess(r, d):
    """Return {'level': 'green'|'yellow', 'flags': [(title, text)]} for one active buy."""
    f = []
    rp, lvl = r.get("resistance_pct"), r.get("resistance_level")
    near = r.get("lr_near_years")
    if (not _bad(rp) and not _bad(lvl) and rp <= SIG["overhead_pct"]) or \
            (not _bad(near) and near >= SIG["yearly_highs"]):
        bits = []
        if not _bad(rp) and not _bad(lvl) and rp <= SIG["overhead_pct"]:
            wo = r.get("resistance_weeks_over")
            bits.append(f"a swing high at {money(lvl)} is only {num(rp, 1)}% above price"
                        + (f" and the stock has traded {int(wo)} weeks inside that band"
                           f" ({SIG['heavy_weeks']} or more would count as heavy and remove it from the list)"
                           if not _bad(wo) and wo else ""))
        if not _bad(near) and near >= SIG["yearly_highs"]:
            bits.append(f"{int(near)} of the last {int(r.get('lr_years') or 10)} yearly highs sit within 20% overhead"
                        f" (nearest {money(r.get('lr_near_level'))})")
        risk = r.get("risk_pct")
        txt = "; ".join(bits).capitalize() + "."
        if not _bad(rp) and not _bad(risk) and rp <= SIG["overhead_pct"]:
            txt += (f" The room up to that level ({num(rp, 1)}%) is about the size of the risk to the stop "
                    f"({num(abs(risk), 1)}%). The book wants room to run (p.115).")
        f.append(("Overhead supply", txt))
    lq = r.get("liq")
    if lq in ("thin", "very thin"):
        f.append(("Thin trading",
                  f"About ${num(r.get('avg_dollar_vol_m'), 2)}M traded per day. Orders can move the price and a stop "
                  f"can fill well below its level, so keep the position small."))
    wk, vx = r.get("wk_chg_pct"), r.get("vol_ratio_4wk")
    if r.get("vol_verify") is True:
        f.append(("Extreme volume", "Breakout volume is far outside the ordinary and may be a split, listing event "
                                    "or data artifact. Check the chart and the news."))
    elif not _bad(wk) and not _bad(vx) and wk >= SIG["jump_pct"] and vx >= SIG["jump_vol_x"]:
        f.append(("Unusual week",
                  f"Last week the stock moved {num(wk, 1)}% on about {num(vx, 0)}x its normal volume. "
                  f"A jump that size can mean news (earnings, a takeover offer). Check the news before acting."))
    if r.get("kind") != "pullback" and not _bad(r.get("entry_low")) and not _bad(r.get("price")):
        limit = r["entry_low"] * (1.0417 if lq in ("thin", "very thin") else 1.0208)
        if r["price"] > limit:
            f.append(("Order already behind price",
                      f"Price ({money(r['price'])}) is above the buy limit on the card ({money(limit)}), so that "
                      f"order would not fill as written. The do-not-chase ceiling is {money(r.get('entry_high'))}."))
    risk = r.get("risk_pct")
    if not _bad(risk) and abs(risk) >= SIG["wide_stop_pct"]:
        f.append(("Wide stop", f"The stop is {num(abs(risk), 1)}% below price. That passes the 15% limit, but it is "
                               f"a lot of room to give up."))
    gs = r.get("group_stage")
    if not _bad(gs) and int(gs) == 1:
        grs = (d.get("groups_rs") or {}).get(r.get("group"))
        f.append(("Sector still basing",
                  f"{r.get('group') or 'The sector'} is in Stage 1 (basing), not yet advancing"
                  + (f", with relative strength {num(grs, 1)}" if not _bad(grs) else "")
                  + ". The book prefers stocks in groups that are already in Stage 2 (p.78-80)."))
    rs = r.get("rs")
    if not _bad(rs) and rs <= 0:
        f.append(("Weak relative strength", f"Relative strength is {num(rs, 1)}, not positive."))
    if r.get("overextended") is True:
        f.append(("Extended", f"The stock is {num(r.get('pct_above_ma'), 0)}% above its 30-week average, "
                              f"where the book says to lock in profits rather than add (p.193)."))
    if (d.get("market") or {}).get("caution"):
        f.append(("Market evidence is mixed", "More of the market gauges are negative than positive right now."))
    level = "yellow" if f else "green"
    ids = book_ideals(r, d)
    n = sum(1 for x in ids if x["ok"])
    return {"level": level, "flags": f, "ideals": ids, "n_ideals": n, "star": n >= STAR_MIN}


STAR_MIN = 3        # the gold star: at least this many of the five book ideals (ours; historically 3 shows on ~2 in 5 cards)


def book_ideals(r, d):
    """The five marks of quality the book describes, each met or not for THIS stock, with its own numbers.
    This is guidance only: it never changes whether a stock is an active buy."""
    fresh = (d.get("rules") or {}).get("fresh_weeks", 2)
    sw, ts, gs = r.get("stage_weeks"), r.get("triple_score"), r.get("group_stage")
    near, rsv = r.get("lr_near_years"), r.get("rs")
    note = str(r.get("resistance_note") or "")
    out = []
    fb = r.get("verdict") == "BREAKOUT - BUY" and not _bad(sw) and sw <= fresh
    out.append({"label": "Fresh breakout", "ok": fb, "basis": "p.14, p.35, p.59",
                "detail": (f"Breaking out of its Stage 1 base in Stage 2 week {int(sw)}: the investor's ideal entry."
                           if fb else
                           f"Not a fresh breakout ({(r.get('verdict') or '').title().replace(' - ', ' ')}, Stage 2 week "
                           f"{int(sw) if not _bad(sw) else '?'}). Later entries are real buys, just not the textbook one.")})
    t3 = not _bad(ts) and int(ts) == 3
    out.append({"label": "Triple confirmation 3/3", "ok": t3, "basis": "p.150-152, p.157",
                "detail": (f"All three signs together (heavy volume, RS turning positive, a 40%+ swing before the breakout): "
                           f"the book's pattern for big winners, strictly for aggressive investors."
                           if t3 else
                           f"Scores {int(ts) if not _bad(ts) else 'n/a'} of 3. The 3/3 pattern is rare (about 2% of buys).")})
    s2 = not _bad(gs) and int(gs) == 2
    out.append({"label": "Sector in Stage 2", "ok": s2, "basis": "p.80, p.91",
                "detail": (f"{r.get('group') or 'The sector'} is already advancing: an A+ stock in an A+ group."
                           if s2 else
                           f"{r.get('group') or 'The sector'} is in Stage {int(gs) if not _bad(gs) else '?'}"
                           " (acceptable, not Stage 3 or 4, but not yet advancing).")})
    clr = note.startswith("clear") and not _bad(near) and near == 0
    out.append({"label": "Clear overhead", "ok": clr, "basis": "p.98-100",
                "detail": ("No meaningful resistance within 20% overhead and no yearly highs just above: room to run."
                           if clr else
                           f"Overhead supply: {note or 'resistance not rated'}"
                           + (f"; {int(near)} yearly high(s) within 20% above" if not _bad(near) and near else
                              ("" if not _bad(near) else "; 10-year history unavailable"))
                           + ".")})
    pos = not _bad(rsv) and rsv > 0
    out.append({"label": "Positive relative strength", "ok": pos, "basis": "p.110-113, p.115",
                "detail": (f"Relative strength is {num(rsv, 1)}, ahead of the market."
                           if pos else
                           f"Relative strength is {num(rsv, 1) if not _bad(rsv) else 'n/a'}: not yet positive "
                           "(allowed if improving, but not ideal).")})
    return out


def ideals_chip(a):
    n = a["n_ideals"]
    lines = [f"{n} of the book's 5 marks of quality are met for this stock. These describe how close it is to the "
             f"textbook best; every stock listed has already passed all the buy rules."]
    for x in a["ideals"]:
        lines.append(f"{'✓' if x['ok'] else '✗'} {x['label']} ({x['basis']}): {x['detail']}")
    lines.append("Historically, signals meeting 3 of 5 did slightly better than those meeting 2 (a small gap), and 5 of 5 "
                 "almost never occurs. This is a guide, not a prediction or a recommendation.")
    return _badge("ideals", f"IDEALS {n}/5", f"Book ideals: {n} of 5", lines)


def star_badge(a):
    """The big gold star: at least STAR_MIN of the five ideals."""
    n = a["n_ideals"]
    lines = [f"{n} of the book's 5 marks of quality are met (the star needs {STAR_MIN} or more):"]
    lines += [f"{'✓' if x['ok'] else '✗'} {x['label']} ({x['basis']})" for x in a["ideals"]]
    lines += ["The star is a strong-setup marker among stocks that already passed every buy rule. The threshold of 3 is ours. "
              "It is not a recommendation: confirm on the weekly chart and check the news."]
    return _badge("starbig", "★", f"STRONG SETUP: {n} of 5 book ideals", lines)


def signal_chip(a):
    """The green / yellow chip. Its popup explains the color for this stock."""
    n = len(a["flags"])
    if a["level"] == "green":
        title = "Clean setup: no flags"
        lines = ["Every buy rule passed and none of our caution checks fired for this stock."]
        label = "CLEAN SETUP"
    else:
        title = f"Check first: {n} flag{'s' if n != 1 else ''}"
        lines = [f"⚠ {t}: {x}" for t, x in a["flags"]]
        label = f"CHECK FIRST · {n}"
    lines += [
        "Passed the book's buy rules: Stage 2 above a rising 30-week average, breakout volume, no heavy overhead "
        "resistance within 20%, stop within 15%, and sector and market not in decline.",
        "Before acting, confirm on the weekly chart (is the base orderly? did the breakout bar close near its "
        "high? any gap?) and check the news. Neither color is a recommendation to buy.",
        "The flag thresholds (such as 10% overhead or a 12% stop) are our additions, not the book's.",
    ]
    return _badge("sig " + a["level"], label, title, lines)


def analysis_prompt(r, d, a):
    """A ready-to-paste prompt: this stock's screen results plus the rules, for any AI the reader uses."""
    rules = d["rules"]
    mk = d.get("market") or {}
    gl = "; ".join(f"{g['name']}: {g['detail']}" for g in mk.get("gauges", [])[:8])
    grs = (d.get("groups_rs") or {}).get(r.get("group"))
    t = r.get("ticker")
    L = [
        f"I'm checking {t} against Stan Weinstein's method ('Secrets for Profiting in Bull and Bear Markets'). "
        f"A screener flagged it as an active buy. I'm attaching a WEEKLY chart screenshot with a 30-week simple "
        f"moving average and Mansfield relative strength.",
        "",
        f"SCREEN RESULTS FOR {t} (data through the {d.get('last_bar')} weekly close):",
        f"- Setup: {r.get('verdict')}; sector {r.get('group')} is in Stage {r.get('group_stage')}"
        + (f" with relative strength {num(grs, 1)}" if not _bad(grs) else ""),
        f"- Price {money(r.get('price'))}; Stage 2 for {int(r['stage_weeks'])} weeks; stock RS {num(r.get('rs'), 1)}",
        f"- Entry {money(r.get('entry_low'))} to {money(r.get('entry_high'))}; stop {money(r.get('stop'))} "
        f"({pct(r.get('risk_pct'))} from price)",
        f"- Breakout volume {num(r.get('bo_vol_ratio'), 1)}x the prior 4 weeks; this week's volume "
        f"{num(r.get('vol_ratio_4wk'), 1)}x; last week's price change {pct(r.get('wk_chg_pct'))}",
        f"- Overhead resistance: {r.get('resistance_note')}; {long_range_line(r)}",
        f"- Average daily dollar volume about ${num(r.get('avg_dollar_vol_m'), 2)}M (liquidity: {r.get('liq')})",
        f"- Triple-confirmation: {num(r.get('triple_score'), 0)} of 3 (volume {r.get('triple_vol')}, "
        f"RS turn {r.get('triple_rs')}, prior 40%+ run {r.get('triple_adv')})",
        f"- Market gauges: {gl or 'n/a'}",
        f"- Screener caution flags: " + ("; ".join(f"{x}: {y}" for x, y in a["flags"]) or "none"),
        "",
        "THE RULES THE SCREEN USED: Stage 2 means price above a rising 30-week average; breakout volume at least "
        f"{rules['breakout_vol_mult']:.0f}x the prior 4 weeks; no heavy overhead resistance within "
        f"{rules['resistance_near_pct']:.0f}%; stop within {rules['wide_stop_pct']:.0f}% of entry; don't chase more "
        f"than {rules['max_chase_pct']:.0f}% above the breakout; pullback buys need volume down more than 75% from "
        "the breakout peak; sector and market not in Stage 3 or 4.",
        "",
        "PLEASE: (1) say what the chart shows and whether it confirms or contradicts each screen result above; "
        "(2) describe the base (tight or sloppy), how the breakout bar closed, any gaps, and the volume pattern; "
        "(3) point out anything the screen could not know and I should check, such as news; (4) list what would "
        "make this a 'wait' instead of a buy, and where the stop and the first overhead obstacle sit; (5) give a "
        "plain, balanced analysis against Weinstein's rules. Do not invent data you cannot see in the screenshot. "
        "This is for my own research, not personalized financial advice.",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------- cards
def active_card(r, rules, d=None):
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
    asm = assess(r, d or {"rules": rules}) if d is not None else \
        {"level": "green", "flags": [], "ideals": [], "n_ideals": 0, "star": False}
    star = bool(asm.get("star"))
    sig = signal_chip(asm) if d is not None else ""
    cp = (f"<button type='button' class='cpbtn' data-prompt=\"{e(analysis_prompt(r, d, asm), quote=True)}\">"
          f"Copy analysis prompt</button>") if d is not None else ""
    return f"""
    <div class="abuy sg-{asm['level']}{' star' if star else ''}">
      {star_badge(asm) if star else ''}
      <div class="abuy-top"><span class="tk">{e(r['ticker'])}</span>
        <span class="tag">{label}</span></div>
      <div class="bdgs">{sig}{ideals_chip(asm) if asm.get("ideals") else ''}{badges(r)}</div>
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
      <p class="how sm">{e(exit_line(r))}</p>
      {profit_plan(r)}
      <p class="how sm">{e(long_range_line(r))} {e(triple_line(r))}</p>
      <p class="size">Example size: {int(r['shares'])} sh ≈ ${r['shares'] * r['price']:,.0f}
        (1/{rules['positions']} of ${rules['account_size']:,.0f}{', capped at ~5% of average daily volume' if r.get('liq') in ('thin', 'very thin') else ''})
        · avg volume ${(r.get('adv_dollars') or 0) / 1e6:,.2f}M/day · resistance: {e(r['resistance_note'])}</p>
      <p class="cprow">{cp}<span class="cphint">Paste it with your own weekly chart screenshot into any AI to get a write-up.</span></p>
    </div>"""


def active_panel(d):
    rules = d["rules"]
    a = sorted(d["active"], key=lambda r: -(r["risk_pct"] if r["risk_pct"] is not None else -99))
    if d["market_blocked"]:
        body = ("<div class='none stop4'><h3>Buying suspended</h3>"
                "<p>The S&amp;P 500 is in Stage 4. The book says don't buy into a bearish "
                "market (p.129). Candidates are hidden until the market improves.</p></div>")
    elif not a:
        body = ("<div class='none'><h3>No active buys this week</h3>"
                "<p>Nothing meets every rule. Patience is a position. "
                "Check the near misses below.</p></div>")
    else:
        body = f"<div class='abuys'>{''.join(active_card(r, rules, d) for r in a)}</div>"
    return f"""
  <section class="hero" id="active">
    <div class="hero-head"><span class="dot"></span>ACTIVE BUYS
      <span class="count">{0 if d['market_blocked'] else len(a)}</span></div>
    {body}
    <div class="hero-foot">Stop within {rules['wide_stop_pct']:.0f}% of entry ·
      breakout volume ≥ {rules['breakout_vol_mult']:.0f}x · no heavy resistance within
      {rules['resistance_near_pct']:.0f}% · group and market not in decline</div>
  </section>"""


# ---------------------------------------------------------------- market playbook
REGIME_ORDER = ["bull", "high_risk", "defensive", "bear", "low_risk"]
REGIME_RULE = {
    "bull": "S&P 500 in Stage 2, and more long-term gauges positive than negative.",
    "high_risk": "S&P 500 in Stage 3, or in Stage 2 with more gauges negative than positive.",
    "defensive": "S&P 500 or the Dow in Stage 4, without the rest of the evidence being clearly bearish.",
    "bear": "S&P 500 in Stage 4, the Dow also in Stage 4, and more gauges negative than positive.",
    "low_risk": "S&P 500 in Stage 1 (basing).",
}


try:
    from weinstein_pure import REGIMES as REGIMES_TXT
except Exception:
    REGIMES_TXT = {}


def playbook_panel(d):
    rg = d.get("regime")
    if not rg:
        return ""
    blocked = d.get("market_blocked")
    nact = 0 if blocked else len(d.get("active", []))
    nsh = len(d.get("shorts", []))
    hid = (d.get("short_hidden") or {}).get("candidates", 0) + (d.get("short_hidden") or {}).get("watch", 0)
    ev = (f"S&P 500 Stage {rg.get('sp_stage') or '?'} · Dow Stage {rg.get('dow_stage') or '?'} · "
          f"long-term gauges {rg.get('gauges_pos', 0)} positive, {rg.get('gauges_neg', 0)} negative")
    rows = [("Buying", rg["buys"]), ("Short selling", rg["shorts"]), ("What you own", rg["held"])]
    if rg.get("cash"):
        rows.append(("Cash", rg["cash"]))
    grid = "".join(f"<div class='pbr'><small>{e(k)}</small><span>{e(v)}</span></div>" for k, v in rows)
    here = (f"On this page: {nact} active buy{'s' if nact != 1 else ''}"
            + (" (buying suspended)" if blocked else "")
            + f"; {nsh} short candidate{'s' if nsh != 1 else ''}"
            + (f" ({hid} more hidden because short selling is the exception in this market)" if hid and rg["key"] != "bear" else "")
            + ".")
    allr = ""
    for k in REGIME_ORDER:
        r = REGIMES_TXT.get(k)
        if not r:
            continue
        cur = " cur" if k == rg["key"] else ""
        allr += (f"<li class='{k}{cur}'><b>{e(r['name'])}</b>{' <em>(now)</em>' if cur else ''}"
                 f"<span>How it is recognized: {e(REGIME_RULE[k])}</span>"
                 f"<span>Buying: {e(r['buys'])}</span><span>Short selling: {e(r['shorts'])}</span>"
                 f"<span>Stocks you own: {e(r['held'])}</span></li>")
    return f"""
<section class="playbook pb-{rg['key']}" id="playbook">
  <div class="pbh"><small>MARKET PLAYBOOK</small><b>{e(rg['name'])}</b></div>
  <p class="pbs">{e(rg['summary'])} <span class="pbe">{e(ev)}</span></p>
  <div class="pbg">{grid}</div>
  <p class="pbf">{e(here)} <span>{e(rg['pages'])}</span></p>
  <details class="pball"><summary>All the market conditions and what the book does in each</summary><ul>{allr}</ul>
    <p class="gnote">The regime is read from the S&amp;P 500 and Dow stages and the balance of the long-term gauges
    (the book's "weight of the evidence", p.268-270). The cutoffs between regimes follow the book's wording; where the book
    gives no exact number the rule is ours. Not a recommendation.</p></details>
</section>"""


# ---------------------------------------------------------------- short candidates
def short_marks_chip(r):
    mk = r.get("sh_marks") or {}
    n = int(r.get("sh_marks_n") or 0)
    lines = [f"{n} of 5 'A+ short' marks. Every candidate here already passed the book's hard rules; these marks "
             "separate the A+ shorts from the merely OK ones (p.234-239)."]
    for k, v in mk.items():
        lines.append(f"{'✓' if v else '✗'} {k}")
    for lab, state, det, pg in (r.get("sh_checks") or []):
        lines.append(f"{'✓' if state == 'pass' else '⚠' if state == 'warn' else '✗'} {lab} ({pg}): {det}")
    lines.append("Volume is not needed on the breakdown: a stock can fall of its own weight (p.236-237).")
    lines.append("Not checked: short interest. The book warns against 'sucker shorts' whose short interest is 5 times "
                 "the average daily volume or more (p.222); look that up before acting.")
    return _badge("ideals", f"A+ MARKS {n}/5", f"A+ short marks: {n} of 5", lines)


def short_card(r, rules, d):
    kind = r.get("sh_kind")
    label = {"breakdown": "SHORT ON BREAKDOWN", "pullback": "SHORT ON PULLBACK",
             "continuation": "CONTINUATION SHORT", "watch": "WATCH: TOP FORMING"}.get(kind, "SHORT")
    sup, es, el = r.get("sh_support"), r.get("sh_entry_stop"), r.get("sh_entry_limit")
    if kind == "breakdown":
        entry_b = money(es)
        entry_s = f"sell short stop {money(es)}, limit {money(el)}"
        how = (f"Support at <b>{money(sup)}</b> has broken. Enter an order to sell short with a stop at <b>{money(es)}</b> and a "
               f"limit of <b>{money(el)}</b>: at least a half point of room, more for thin stocks (p.239-240). Traders sell the whole "
               "position on the breakdown; investors sell half now and half on a pullback toward the breakdown level (p.229-230).")
    elif kind == "pullback":
        entry_b = money(r.get("price"))
        entry_s = f"zone {money(r.get('sh_zone_lo'))} – {money(r.get('sh_zone_hi'))}"
        how = (f"The stock has rallied back toward its breakdown level at <b>{money(sup)}</b>. This is the second half of an "
               f"investor's short, or a later entry inside {money(r.get('sh_zone_lo'))} – {money(r.get('sh_zone_hi'))} (p.230).")
    elif kind == "continuation":
        entry_b = money(es)
        entry_s = f"sell short stop {money(es)}"
        how = (f"After consolidating under its declining average, the stock is breaking to a new low below <b>{money(sup)}</b>. "
               f"Sell short with a stop at <b>{money(es)}</b>. This is for aggressive traders (p.240).")
    else:
        entry_b = money(es)
        entry_s = f"only if it breaks {money(sup)}"
        how = (f"A Stage 3 top is forming with a flat or falling average. On the shopping list (p.229-230): sell short only if it "
               f"breaks below support at <b>{money(sup)}</b> (stop at <b>{money(es)}</b>). Not a short yet.")
    per = rules["account_size"] / rules["positions"]
    ent = r.get("sh_entry") or r.get("price")
    shares = int(per / ent) if ent else 0
    tgt = (f"<p class='how sm'>Downside swing-rule target {money(r.get('sh_target'))} ({pct(r.get('sh_target_pct'))}). "
           "When it nears the target, cover half and trail the rest with the buy-stop (p.247-249).</p>") if r.get("sh_target") else ""
    return f"""
    <div class="abuy shortc">
      <div class="abuy-top"><span class="tk">{e(r['ticker'])}</span><span class="tag">{label}</span></div>
      <div class="bdgs">{short_marks_chip(r)}</div>
      <div class="abuy-sub">{e(r.get('group') or '')} · Stage {int(r['stage'])} week {int(r['stage_weeks'])} ·
        RS {num(r.get('rs'), 1)} · run-up before the top {num(r.get('sh_runup_pct'), 0)}%</div>
      <div class="levels">
        <div class="lv entry"><small>SELL SHORT</small><b>{entry_b}</b><em>{e(entry_s)}</em></div>
        <div class="lv stop"><small>PROTECTIVE BUY-STOP</small><b>{money(r.get('sh_buy_stop'))}</b>
          <em>{pct(r.get('sh_buy_stop_pct'))} above entry</em></div>
      </div>
      <p class="how">{how}</p>
      <p class="how">Place the buy-stop as a good-til-canceled order <b>before</b> you short, above the prior rally peak of {money(r.get('sh_rally_high'))}
        and above the round number (p.250-251). Never short without it (p.226, p.250). Traders use {money(r.get('sh_trader_stop'))}
        ({pct(r.get('sh_trader_stop_pct'))}) and cover on any move above the 30-week average (p.254-255).</p>
      <p class="how sm">Lower the buy-stop after each rally of at least 8% fails and the stock drops back to its prior low (7% for traders) (p.251-256).
        Cover on a close above a declining 30-week average that holds (p.251-252).</p>
      {tgt}
      <p class="size">Example size: {shares} sh ≈ ${shares * (ent or 0):,.0f} (1/{rules['positions']} of ${rules['account_size']:,.0f}).
        Needs a margin account and shares available to borrow. Short interest not checked (p.222).</p>
    </div>"""


def short_panel(d):
    rg = d.get("regime") or {}
    shorts, watch = d.get("shorts") or [], d.get("short_watch") or []
    hid = d.get("short_hidden") or {}
    nhid = (hid.get("candidates", 0) + hid.get("watch", 0))
    rules = d["rules"]
    if rg.get("key") == "bear":
        policy = ("Bearish market: the book says to be aggressive on the short side, always with a protective buy-stop (p.215-217, p.231).")
    else:
        policy = ("Short selling is the exception in this market, not the rule (p.230-231). Only candidates that show all five "
                  f"A+ marks are listed{f'; {nhid} other candidates are hidden' if nhid else ''}.")
    if shorts:
        body = f"<div class='abuys'>{''.join(short_card(r, rules, d) for r in shorts)}</div>"
    else:
        body = ("<div class='none'><h3>No short candidates this week</h3><p>Nothing in Stage 3 or 4 meets the book's hard rules "
                "for a short sale under this market's policy.</p></div>")
    wl = ""
    if watch:
        trs = "".join(
            f"<tr><td>{tk(dict(r))}</td><td>{money(r.get('price'))}</td><td>{money(r.get('sh_support'))}</td>"
            f"<td>{money(r.get('sh_entry_stop'))}</td><td>{money(r.get('sh_buy_stop'))} ({pct(r.get('sh_buy_stop_pct'))})</td>"
            f"<td>{int(r.get('sh_marks_n') or 0)}/5</td></tr>" for r in watch)
        wl = ("<details class='panel shortwatch' open><summary><span>Short shopping list: tops forming</span>"
              f"<span class='cnt'>{len(watch)}</span></summary><div class='scroll'><table><thead><tr><th>Ticker</th><th>Price</th>"
              "<th>Support</th><th>Short below</th><th>Buy-stop</th><th>A+ marks</th></tr></thead>"
              f"<tbody>{trs}</tbody></table></div></details>")
    return f"""
  <section class="hero shorthero" id="shorts">
    <div class="hero-head"><span class="dot"></span>SHORT CANDIDATES
      <span class="count">{len(shorts)}</span></div>
    <p class="shortpol">{e(policy)}</p>
    {body}
    {wl}
    <div class="hero-foot">Never above a rising 30-week average · never a Stage 2 stock · not too thin (over 15,000 shares a week) ·
      relative strength not still rising · not in a strong group · protective buy-stop within {rules.get('short_max_stop_pct', 15):.0f}%.
      Higher risk than buying: for education only, not a recommendation.</div>
  </section>"""


# ---------------------------------------------------------------- tables
OPEN_BY_DEFAULT = {"near", "watch"}      # the lists most likely to turn into buys soon


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
    opn = " open" if cls in OPEN_BY_DEFAULT else ""
    return f"""
  <details class="panel {cls}" id="sec-{cls}" data-k="sec-{cls}"{opn}>
    <summary class="ph"><span class="sh">{title}<span class="count">{len(rows)}</span></span><span class="ssub">{sub}</span></summary>
    {body}{f'<div class="note">{note}</div>' if note else ''}
  </details>"""


def tk(r):
    return (f"<b class='t'>{e(r['ticker'])}</b><span class='g'>{e(r.get('group') or '')}</span>"
            f"{badges(r)}")


def res_cell(r):
    n = r.get("resistance_note") or ""
    c = "heavy" if n.startswith("HEAVY") else "light" if n.startswith("light") else "ok"
    return f"<span class='chip {c}'>{e(n)}</span>"


STAGE_COL = {1: "#8fb4d9", 2: "#1f9d55", 3: "#e6a100", 4: "#c0392b", 0: "#d5d9e2"}
STAGE_TXT = {1: "basing", 2: "advancing", 3: "topping", 4: "declining"}


def _arrow(v, unit=""):
    if _bad(v):
        return "<span class='fl'>\u2013</span>"
    if abs(v) < 0.05:
        return "<span class='fl'>\u25ac 0.0%s</span>" % unit
    glyph = "\u25b2" if v > 0 else "\u25bc"
    cls = "up" if v > 0 else "dn"
    return "<span class='%s'>%s %+.1f%s</span>" % (cls, glyph, v, unit)


def sector_panel(d):
    """Sector history: stage strip, how long and what came before, relative
    strength direction, and the share of the sector's stocks in Stage 2."""
    gd = d.get("group_detail") or {}
    if not gd:
        return ""
    order = sorted(gd, key=lambda g: (-(gd[g]["stage"] in (1, 2)), g))
    rows = ""
    for g in order:
        x = gd[g]
        st = int(x["stage"])
        hist = x.get("hist") or []
        n = len(hist)
        cells = "".join(
            f"<i style='background:{STAGE_COL.get(int(v), '#d5d9e2')}' "
            f"title='{n - k - 1} wk ago: Stage {int(v) if v else '?'}'></i>"
            for k, v in enumerate(hist))
        w = int(x.get("weeks") or 0)
        prev = x.get("prev_stage")
        if x.get("move") == "warming":
            tag = (f"<span class='mv warm'>Heating up</span> entered Stage {st} {w} wk ago"
                   f"{f' (was {prev})' if prev else ''}")
        elif x.get("move") == "cooling":
            tag = (f"<span class='mv cool'>Cooling off</span> entered Stage {st} {w} wk ago"
                   f"{f' (was {prev})' if prev else ''}")
        else:
            tag = f"In Stage {st} for {w} wk" + ("+" if w >= n and n else "")
            if prev and w < n:
                tag += f" (was {prev})"
        b = x.get("breadth") or {}

        def pc(v):
            return "\u2013" if _bad(v) else f"{v:.0f}%"
        bd = ""
        if not _bad(b.get("now")):
            delta = None if _bad(b.get("w4")) else b["now"] - b["w4"]
            bd = (f"<b>{pc(b.get('now'))}</b> <small>(4 wk ago {pc(b.get('w4'))}, "
                  f"13 wk ago {pc(b.get('w13'))})</small> {_arrow(delta, ' pts')}")
        fav = st in (1, 2)
        rs = "\u2013" if _bad(x.get("rs")) else f"{x['rs']:+.1f}"
        rows += (f"<tr class='{'fav' if fav else 'unfav'}'><td class='sn'>{e(g)}</td>"
                 f"<td><span class='sb' style='background:{STAGE_COL.get(st, '#999')}'>{st}</span></td>"
                 f"<td><div class='strip'>{cells}</div></td><td>{tag}</td>"
                 f"<td>{rs} <small>4w</small> {_arrow(x.get('rs_d4'))} <small>13w</small> {_arrow(x.get('rs_d13'))}</td>"
                 f"<td>{bd}</td></tr>")
    nweeks = len(next(iter(gd.values())).get("hist") or [])
    return f"""
<details class="sectors" open><summary>Sector history: heating up or cooling off</summary>
<div class="scroll"><table class="stab"><thead><tr><th>Sector</th><th>Stage</th>
<th>Last {nweeks} weeks (oldest to newest)</th><th>Where it stands</th><th>Relative strength vs S&amp;P (change)</th>
<th>Stocks in Stage 2</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="gnote"><i class="lg" style="background:{STAGE_COL[1]}"></i>1 basing
<i class="lg" style="background:{STAGE_COL[2]}"></i>2 advancing
<i class="lg" style="background:{STAGE_COL[3]}"></i>3 topping
<i class="lg" style="background:{STAGE_COL[4]}"></i>4 declining. Sectors are judged by the stage and relative strength of a
sector fund (p.78); several stocks in one group turning bullish together is a group signal (p.80). "Heating up" and "cooling off"
mean a stage change in the last 8 weeks toward or away from Stage 2; that window is not from the book. Stock counts use the
sector labels each stock carries, which are less reliable outside the S&amp;P 1500.</p></details>"""


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
        f"<li class='{g['status']}'><b>{e(g['name'])}{' <em class=ours>our addition</em>' if g.get('src') == 'ours' else ''}</b><span>{e(g['detail'])}</span></li>"
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
                  "Gauges tagged \"our addition\" are not from the book. Not tracked: price/dividend ratio, contrary opinion, weekly NYSE common-stock new highs.</p>"
                  f"</details>") if gl else ""
    tiles = [("a", "Active buys", 0 if blocked else len(d["active"])),
             ("n", "Near misses", len(d["near"])),
             ("w", "Buy-stop watch", len(d["watch"])),
             ("p", "Wait for pullback", len(d["waits"])),
             ("s", "Skip: stop too wide", len(d["skip_stop"]) + len(d["watch_skip"])),
             ("d", "Discarded: resistance", len(d["disc"]))]
    if d.get("regime"):
        tiles.insert(1, ("h", "Short candidates", len(d.get("shorts") or [])))
    jump = {"h": "shorts", "a": "active", "n": "sec-near", "w": "sec-watch", "p": "sec-wait", "s": "sec-skip", "d": "sec-disc"}
    tile_html = "".join(f"<a class='tile t{c}' href='#{jump[c]}' data-jump='{jump[c]}'><b>{n}</b><span>{t}</span></a>"
                        for c, t, n in tiles)
    cv = d.get("coverage") or {}
    cov_txt = ""
    if cv.get("universe"):
        cov_txt = f" · {cv.get('analysed', 0):,} of {cv['universe']:,} US stocks analysed"
        if cv.get("ended_early"):
            cov_txt += " (price download ended early; partial coverage)"
    _g = datetime.fromisoformat(d["generated"])
    if _g.tzinfo is None:                      # older feeds were written in UTC on the runner
        from datetime import timezone
        _g = _g.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        _g = _g.astimezone(ZoneInfo("America/Chicago"))
    except Exception:
        pass
    gen = _g.strftime("%b %d, %Y %I:%M %p ") + (_g.tzname() or "")

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
                f"Good breakout, more than {d['rules']['max_chase_pct']:.0f}% above entry. Don't chase (p.129).",
                [] if blocked else d["waits"], wait_cols),
        section("skip", "Skip – Stop Too Wide",
                f"Good setup, but the stop is more than {d['rules']['wide_stop_pct']:.0f}% away (p.184). Occasional exceptions only for outstanding charts.",
                [] if blocked else d["skip_stop"] + d["watch_skip"], skip_cols),
        section("disc", "Discarded – Overhead Resistance",
                f"Would otherwise qualify, but supply sits within {d['rules']['resistance_near_pct']:.0f}% overhead (p.115, p.129). Re-check if price clears the level.",
                [] if blocked else d["disc"], disc_cols),
        section("susp", "Suspect Breakouts",
                "New Stage 2 without the required volume surge. If owned, sell on the first rally (p.116).",
                d["suspects"], susp_cols),
    ]

    lookup_html = ""
    lk = d.get("_lookup")
    if lk:
        raw = json.dumps(lk, separators=(",", ":"))
        embed = len(raw) <= EMBED_LIMIT
        safe = raw.replace("</", "<\\/")
        data_tag = ('<script id="lkdata" type="application/json">' + safe + '</script>') if embed else ""
        n = len(lk.get("u", []))
        lookup_html = f"""
<section class="lookup">
  <h2>Check a ticker</h2>
  <p>Type any symbol from the {n:,} US stocks screened to see how it scores on each rule, as of the last Friday close.</p>
  <form id="lkform" autocomplete="off"><input id="lkin" placeholder="e.g. AAPL" maxlength="8" aria-label="Ticker symbol"><button type="submit">Check</button></form>
  <div id="lkout"></div>
</section>
{data_tag}
<script>{LOOKUP_JS}</script>"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weinstein Stage 2 Dashboard</title><style>{CSS}</style></head><body>
<header>
  <div><h1>Stage 2 Buy Dashboard</h1>
    <p>Stan Weinstein method · weekly charts · {d['screened']:,} stocks in favorable sectors screened{cov_txt}</p></div>
  <div class="meta"><div>Week ending <b>{d['last_bar']}</b></div><div>Updated {gen}</div></div>
</header>
<div class="mkt {mcls}"><b>S&amp;P 500: {mtxt}</b>
  <span>{'Buying suspended: the book says do not buy into a bearish market.' if blocked else 'Market trend permits buying.'}</span></div>
{caution}
{playbook_panel(d)}
<div class="tiles">{tile_html}</div>
{active_panel(d)}
{short_panel(d)}
{POS_SECTION}
{lookup_html}
<div class="groups"><small>SECTORS</small>{groups}</div>
{sector_panel(d)}
{gauge_html}
{hid}
<div class="listbar"><b>Stock lists</b><span><button type="button" id="expall">Expand all</button><button type="button" id="colall">Collapse all</button></span></div>
<main>{''.join(sections)}</main>
<footer>
  <h3>How to read this</h3>
  <p><b>Active buy</b> = Stage 2 breakout on ≥2x volume (or continuation/pullback buy), market and sector not in decline,
  no heavy overhead resistance, stop within {d['rules']['wide_stop_pct']:.0f}% of entry. <b>Entry</b> is the breakout level; do not chase more than
  {d['rules']['max_chase_pct']:.0f}% above. <b>Stop</b> sits one-eighth below the base floor (or below the pullback low),
  placed as a sell-stop. Size positions equally, about 1/{d['rules']['positions']} of the account each.</p>
  <p><b>Card colors.</b> Green = every buy rule passed and none of our caution checks fired. Yellow = it is an active buy, but
  something deserves a look first (click the chip for the reasons). <b>IDEALS n/5</b> counts the book's marks of quality: a fresh
  breakout, triple confirmation 3/3, the sector in Stage 2, clear overhead, and positive relative strength. <b>★ Gold star</b> = at least
  3 of the 5 (the 3 is our threshold, not the book's). Neither color, the count, nor the star is a recommendation.</p>
  <p class="disc">For education only. This is not investment advice and not a recommendation to buy or sell any security.
  Signals are mechanical and can be wrong; verify on the chart and size risk yourself. Data: Yahoo Finance, Friday weekly closes.
  Rules follow <i>Secrets for Profiting in Bull and Bear Markets</i>; thresholds the book does not specify are the author's choices.</p>
</footer>
<div id="pgpop" hidden></div><script>{LIST_JS}</script><script>{PAGE_JS}</script></body></html>"""


POS_SECTION = """
<section class="pos-wrap" id="positions">
  <h2 class="pos-h">My Positions <span class="pos-sub">tracked against the book's selling rules</span></h2>
  <div id="posbox"><div class="empty">Loading positions...</div></div>
  <div id="posclosed"></div>
  <details class="poslog"><summary>Owner: log a trade</summary>
    <p>This only prepares a line. It is saved by pasting it into <code>positions.csv</code> on GitHub, which only the
    repository owner can edit, so visitors cannot add or change positions. No share counts or dollar values are recorded.</p>
    <div class="pf">
      <label><input type="radio" name="pmode" value="buy" checked> I bought</label>
      <label><input type="radio" name="pmode" value="sell"> I sold</label>
    </div>
    <form id="posform" autocomplete="off">
      <div class="pfrow" id="pf-buy">
        <input id="pf-tk" placeholder="Ticker" maxlength="8" aria-label="Ticker">
        <input id="pf-dt" type="date" aria-label="Buy date">
        <input id="pf-px" type="number" step="0.01" min="0" placeholder="Buy price" aria-label="Buy price">
        <select id="pf-st" aria-label="Style"><option value="investor">Investor</option><option value="trader">Trader</option></select>
        <input id="pf-sp" type="number" step="0.01" min="0" placeholder="Your stop (optional)" aria-label="Your stop">
      </div>
      <div class="pfrow" id="pf-sell" style="display:none">
        <select id="pf-which" aria-label="Which position"></select>
        <input id="pf-sd" type="date" aria-label="Sell date">
        <input id="pf-spx" type="number" step="0.01" min="0" placeholder="Sell price" aria-label="Sell price">
      </div>
      <button type="submit">Copy line and open positions.csv</button>
    </form>
    <div id="pf-out"></div>
  </details>
</section>
<script>
(function(){
var REPO="__REPO__",BR="__BR__",URL="https://github.com/"+REPO+"/edit/"+BR+"/positions.csv";
var P=null;
function el(t,c,x){var n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n;}
function $(v){return v==null?"\u2013":"$"+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});}
function pc(v){return v==null?"\u2013":(v>=0?"+":"")+Number(v).toFixed(1)+"%";}
function cell(g,l,v){var d=el('div');d.appendChild(el('small','',l));d.appendChild(el('b','',v));g.appendChild(d);}
function cls(s){return s.indexOf('SELL')==0?'sell':s.indexOf('RAISE')==0?'raise':s.indexOf('TAKE')==0?'take':'hold';}
function render(D){
  P=D;var box=document.getElementById('posbox');box.textContent="";
  var ps=D.positions||[];
  if(!ps.length){box.appendChild(el('div','empty',"No open positions logged."));}
  ps.forEach(function(p){
    var c=el('div','pcard '+cls(p.status));
    var top=el('div','ptop');top.appendChild(el('span','ptk',p.ticker));top.appendChild(el('span','pchip',p.status));
    top.appendChild(el('small','',p.style+" \u00b7 bought "+p.buy_date+" at "+$(p.buy_price)));c.appendChild(top);
    var g=el('div','pgrid');
    cell(g,'Price',$(p.price));cell(g,'Gain',pc(p.gain_pct));cell(g,'Stage',String(p.stage));
    cell(g,'Book stop',$(p.stop)+(p.your_stop!=null?" (yours "+$(p.your_stop)+")":""));
    cell(g,'To stop',pc(p.stop_pct));cell(g,'Above 30-wk avg',pc(p.pct_above_ma));
    if(p.swing_target!=null)cell(g,'Swing target',$(p.swing_target));
    c.appendChild(g);
    var ul=el('ul','pnotes');(p.notes||[]).forEach(function(n){ul.appendChild(el('li','',n));});c.appendChild(ul);
    box.appendChild(c);});
  var cl=document.getElementById('posclosed');cl.textContent="";
  if((D.closed||[]).length){
    cl.appendChild(el('h3','pos-h2',"Closed trades"));
    var t=el('table','ptab');var h=el('tr');["Ticker","Style","Bought","Sold","Result"].forEach(function(x){h.appendChild(el('th','',x));});
    t.appendChild(h);
    D.closed.forEach(function(r){var tr=el('tr');
      [r.ticker,r.style,r.buy_date+" at "+$(r.buy_price),r.sell_date+" at "+$(r.sell_price),pc(r.gain_pct)].forEach(function(x,i){
        var td=el('td','',x);if(i==4)td.className=r.gain_pct>=0?'gain':'loss';tr.appendChild(td);});t.appendChild(tr);});
    cl.appendChild(t);}
  var sel=document.getElementById('pf-which');sel.textContent="";
  ps.forEach(function(p,i){var o=el('option','',p.ticker+" bought "+p.buy_date+" at "+$(p.buy_price));o.value=String(i);sel.appendChild(o);});
}
fetch('positions.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(render)
 .catch(function(){document.getElementById('posbox').textContent="No positions logged yet.";});
var today=new Date().toISOString().slice(0,10);
document.getElementById('pf-dt').value=today;document.getElementById('pf-sd').value=today;
Array.prototype.forEach.call(document.getElementsByName('pmode'),function(r){r.addEventListener('change',function(){
  var sell=document.querySelector('input[name=pmode]:checked').value=='sell';
  document.getElementById('pf-buy').style.display=sell?'none':'';document.getElementById('pf-sell').style.display=sell?'':'none';});});
document.getElementById('posform').addEventListener('submit',function(ev){
  ev.preventDefault();var out=document.getElementById('pf-out');out.textContent="";var line="",how="";
  var sell=document.querySelector('input[name=pmode]:checked').value=='sell';
  if(!sell){
    var tk=document.getElementById('pf-tk').value.trim().toUpperCase().replace(/[^A-Z0-9.\-]/g,"");
    var dt=document.getElementById('pf-dt').value,px=document.getElementById('pf-px').value;
    if(!tk||!dt||!px){out.textContent="Enter a ticker, date and price.";return;}
    line=[tk,dt,px,document.getElementById('pf-st').value,document.getElementById('pf-sp').value].join(",");
    how="Paste this as a new last line in positions.csv, then commit. The positions update runs in about a minute.";
  }else{
    var p=P&&P.positions&&P.positions[document.getElementById('pf-which').value];
    var sd=document.getElementById('pf-sd').value,sp=document.getElementById('pf-spx').value;
    if(!p||!sd||!sp){out.textContent="Pick the position and enter the sell date and price.";return;}
    line=[p.ticker,p.buy_date,p.buy_price,p.style,p.your_stop==null?"":p.your_stop,sd,sp].join(",");
    how="Replace that position's existing line in positions.csv with this one, then commit.";
  }
  out.appendChild(el('code','',line));out.appendChild(el('p','',how));
  try{navigator.clipboard.writeText(line);out.appendChild(el('p','',"The line is copied to your clipboard."));}catch(e){}
  window.open(URL,'_blank','noopener');
});
})();
</script>"""
POS_SECTION = POS_SECTION.replace("__REPO__", REPO).replace("__BR__", BRANCH)


RULE_NOTES = {
 "13": [
  "Resistance",
  "A price zone where a rally tends to stall and turn back. The more often and the longer a zone was tested, the more meaningful it is when price finally clears it."
 ],
 "14": [
  "Below the average",
  "A stock trading under its 30-week average should not be considered for purchase, especially when that average is falling."
 ],
 "25": [
  "Simple versus weighted",
  "Charting services often plot a weighted average. The book's own method is a plain 30-week average, which is what this dashboard uses."
 ],
 "31": [
  "Why weekly stages",
  "With the stage method you can flip through any chart book and quickly rule out most stocks."
 ],
 "33": [
  "Bases take time",
  "A base forms over months, sometimes years. A stage 2 advance should follow a real sideways base, not a sudden spike."
 ],
 "34": [
  "Second chance",
  "After a breakout, price often pulls back toward the breakout level. That dip is a lower-risk second chance to buy."
 ],
 "35": [
  "Overextended",
  "Late in stage 2 the stock sits far above its support and average. It is still a hold, but no longer a buy."
 ],
 "36": [
  "Stage 3 top",
  "A stalling, choppy advance with a flattening average is a stage 3 top. Traders get out; investors sell half."
 ],
 "36-37": [
  "Stage 3 top",
  "Once a stage 3 top forms, traders sell. Investors sell half and protect the rest with a stop under the new support."
 ],
 "38": [
  "Stage 3",
  "Never buy a stock in stage 3: the reward-to-risk is stacked against you."
 ],
 "39": [
  "Stage 4",
  "Never buy or hold a stage 4 stock. Declines tend to be fast and deep."
 ],
 "59": [
  "Half and half",
  "Investors buy half at the breakout and half on the pullback. Traders buy the whole position at the breakout."
 ],
 "61": [
  "Continuation buy",
  "A stock that already advanced pulls back near its average, consolidates, then breaks out again. That second breakout is a continuation buy."
 ],
 "61-62": [
  "Continuation buy",
  "A stock that already advanced pulls back near its average, consolidates, then breaks out again above that area."
 ],
 "61-63": [
  "Continuation buys",
  "Breakouts out of a fresh consolidation after a first advance. They suit traders and later stages of a bull market."
 ],
 "62": [
  "Pullbacks are common",
  "Roughly 80% of initial breakouts are followed by a pullback to near the breakout, so waiting is usually rewarded. A few strong ones never look back."
 ],
 "63": [
  "Mix of buys",
  "Rule of thumb: investors do about 75-80% of their buying early in stage 2 and the rest from continuation moves."
 ],
 "64": [
  "Buy-stop",
  "Place a buy-stop just above the top of the base so you are filled only if it really breaks out, even when you are not watching."
 ],
 "66": [
  "Order type",
  "Use a good-till-canceled buy-stop with a limit a little above it. Allow a wider limit for thinly traded stocks."
 ],
 "75": [
  "Forest to trees",
  "Work from the whole market down to the sector, then to the individual stock."
 ],
 "78": [
  "The sector",
  "Concentrate on the groups with the best technical picture. A strong group helps; a weak one hurts."
 ],
 "80": [
  "Group signal",
  "When several stocks in one group suddenly turn bullish (or bearish) on the charts, that is a clear signal about the group itself."
 ],
 "215": [
  "Selling short: the less traveled road",
  "About a third of the time the market is going down, and stocks fall faster than they rise. The book's answer is to sell short the weakest stocks in a bear market, with the same discipline as buying."
 ],
 "219": [
  "A buy-stop caps a short's loss",
  "A protective buy-stop keeps a short's worst case to roughly 10 to 15 percent, the same as a sell-stop on a long. With it you never carry a short to infinity."
 ],
 "222": [
  "Sucker shorts",
  "A stock that has soared and has a short interest five or more times its average daily volume tends to squeeze the shorts before it ever falls. Avoid the too-obvious short."
 ],
 "224": [
  "Never short above a rising average",
  "Never sell short a stock above its rising 30-week average, however overvalued it looks. It mirrors never buying a stock below its average."
 ],
 "226": [
  "Thin stocks and the buy-stop",
  "Don't short a thin stock (average weekly volume under 15,000 shares): covering would push the price up. And never short without a protective buy-stop."
 ],
 "227": [
  "Short-selling don'ts",
  "Not because the P/E is high, not because the stock has run up, not a crowded sucker short, not a thin stock, not a Stage 2 stock, not a strong group, and never without a buy-stop."
 ],
 "228": [
  "How to do it right",
  "Start with a stock that has had a big advance and is now in Stage 3 with a flat or falling average, has moved sideways for weeks, and has a clear support level to break."
 ],
 "229": [
  "The ideal short setup",
  "A substantial advance, then a Stage 3 top: the average flattens or declines, the stock trades sideways, and a clear support level at or below the average will start Stage 4 if it breaks."
 ],
 "230": [
  "When to sell short",
  "Traders short the whole position on the breakdown. Conservative investors short half on the breakdown and half on the pullback. In a bull market shorting is the exception."
 ],
 "231": [
  "Start with the market",
  "Aggressive shorting begins when all the market averages are in Stage 4 and most of the long-term gauges are negative."
 ],
 "232": [
  "The group",
  "The sector should have broken below its 30-week average with its relative strength trending lower, and several of its charts should look weak."
 ],
 "234": [
  "A+ shorts",
  "The best shorts had a big run-up before the top and have no significant support just below the breakdown point."
 ],
 "235": [
  "Relative strength for shorts",
  "Never short a stock whose relative strength is strong and rising. The best shorts have RS that has topped, turned down, and ideally fallen below zero."
 ],
 "236": [
  "Volume matters less on shorts",
  "A stock can fall of its own weight, so volume is not required on a breakdown. It is a bonus if volume rises on the breakdown and dries up on the pullback."
 ],
 "237": [
  "Support below",
  "A steep Stage 2 advance with little congestion on the way up falls fast. A stock with a big trading zone just below the breakdown resists decline."
 ],
 "239": [
  "Placing the short order",
  "Sell short with a stop at the breakdown price and a limit that leaves at least a half point of room, good-til-canceled."
 ],
 "240": [
  "Shorting after a big decline",
  "Possible, but only after a consolidation under the declining average and a new breakdown, like a continuation buy. For aggressive traders."
 ],
 "247": [
  "Downside swing rule",
  "Project a target below a broken low by repeating the size of the prior swing. Near the target, take profit on half."
 ],
 "249": [
  "Locking in short profits",
  "Near the downside target, cover half the position and protect the rest with a buy-stop, the same way profits are protected on a long."
 ],
 "250": [
  "The protective buy-stop",
  "Place it above the prior rally high and above the round number, before you short. If it would have to sit 30 to 40 percent away, choose another stock."
 ],
 "251": [
  "Trailing the buy-stop",
  "After the first selloff and a failed rally of at least 8 percent, lower the buy-stop above the declining 30-week average and the latest rally peak."
 ],
 "254": [
  "Traders' buy-stops",
  "If no prior peak is close, set it 4 to 6 percent above the breakdown, trail it tighter than an investor would, and never stay short a stock that moves above its 30-week average."
 ],
 "255": [
  "Round numbers for buy-stops",
  "Under $20 every half point counts as a round number. Place buy-stops above round numbers, and ignore rallies of less than 7 percent if you are a trader."
 ],
 "268": [
  "Weight of the evidence",
  "The book follows many long-, intermediate- and short-term gauges and goes with the majority, because any single indicator will eventually give a false signal."
 ],
 "269": [
  "High-risk and low-risk zones",
  "In a high-risk zone, build cash and be very selective. In a low-risk zone at the end of a bear market, lock in short-sale profits and get ready for selective buying."
 ],
 "91": [
  "A+ stock in an A+ group",
  "Don't hunt for the one winner in a sick group. Find a strong stock in a strong group, then let the position work."
 ],
 "98": [
  "Overhead supply",
  "Look at the next resistance above a breakout. Stocks with little supply overhead are the A+ candidates."
 ],
 "99": [
  "10-year view",
  "If a stock has not traded at higher prices in ten years, there is no overhead supply at all: an A+ situation."
 ],
 "100": [
  "Heavy supply",
  "Heavy supply is a price area where the stock spent a lot of time or was turned back again and again."
 ],
 "104": [
  "Breakout volume",
  "Volume on the breakout should be at least about double the recent weekly average, or show a 3-4 week build-up of double plus an increase on the breakout week."
 ],
 "105": [
  "Pullback volume",
  "On the pullback, volume should shrink sharply (the book's example fell over 75% from its peak) before you buy the second half."
 ],
 "110": [
  "Relative strength",
  "Relative strength compares the stock with the market. Above the zero line is a long-term positive, below is a negative. Never buy a stock lagging its own price action."
 ],
 "110-113": [
  "Relative strength",
  "Positive and improving relative strength confirms a breakout. Weak or falling relative strength is a reason to pass."
 ],
 "111": [
  "RS before the breakout",
  "Relative strength rising for about 90 days before the breakout, then crossing above zero, is a strong confirmation."
 ],
 "113": [
  "Weak RS",
  "A relative-strength line below zero, or below where it stood at the base's peak, is a red flag. It needs to show real strength."
 ],
 "115": [
  "Buying checklist",
  "Check the market, find the best groups, list stocks in bases, discard nearby resistance, check relative strength, place buy-stops for half, buy the rest on a quiet pullback."
 ],
 "116": [
  "Weak breakout",
  "If breakout volume is not strong enough, sell on the first rally. If the stock falls back under the breakout point, get out."
 ],
 "119": [
  "Old resistance",
  "Resistance that is close to two years old is much less potent."
 ],
 "120": [
  "Old resistance",
  "Resistance that is close to two years old is much less potent."
 ],
 "129": [
  "The don'ts",
  "Don't buy in a bearish market, in a negative group, below a falling 30-week average, too late in an advance, or on poor volume or relative strength."
 ],
 "134": [
  "Clear sailing",
  "With no resistance overhead, nothing slows the advance."
 ],
 "138": [
  "Diversify",
  "Spread money across stocks and groups in roughly equal amounts: a handful for small accounts, up to 10-20 for larger ones."
 ],
 "150": [
  "Triple confirmation",
  "The biggest winners show breakout volume well over double normal and staying heavy, plus relative strength moving to clearly positive."
 ],
 "150-152": [
  "Triple confirmation",
  "Heavy volume with follow-through, relative strength turning decisively positive, and a large prior advance mark the exceptional winners."
 ],
 "154": [
  "Three-way winner",
  "Winners often show volume near three times normal that stays heavy, with relative strength turning strongly positive."
 ],
 "154-157": [
  "Exceptional winners",
  "When volume, relative strength and the size of the move all line up, the stock deserves a bigger position."
 ],
 "157": [
  "Invest heavily",
  "When all three confirmations line up, invest more heavily than usual."
 ],
 "164": [
  "Selling",
  "The aim is to stop giving back gains and to stop selling winners too soon."
 ],
 "164-213": [
  "Selling chapter",
  "How to protect profits and cut losses: trailing stops for investors, tighter stops, trendlines and the swing rule for traders."
 ],
 "165": [
  "Don't sell on feel",
  "Don't sell just because a stock feels high. Selling too early is as costly as holding too long."
 ],
 "176": [
  "Sell at once",
  "When a stock shows trouble, sell. Don't wait for a rally to recover a point or two."
 ],
 "180": [
  "Sell-stop orders",
  "Use a straight sell-stop on NYSE stocks. Where only stop-limit orders are allowed, use a wide spread between the two prices."
 ],
 "183": [
  "Initial stop",
  "Put the first stop just under the significant support floor, below a round number or half, and plan it before you buy."
 ],
 "184": [
  "Stop and trailing",
  "Limit buys to setups whose initial stop is within about 15% of the price. After the first 8-10% correction, raise the stop once the stock recovers."
 ],
 "184-186": [
  "Trailing stop",
  "Raise the stop under each correction low, or under the 30-week average if lower and rising. Once the average flattens, tighten it."
 ],
 "185-186": [
  "Tighter when topping",
  "When the average stops rising, put the stop right under the latest correction low, even if it is above the average."
 ],
 "186": [
  "Let the stop work",
  "Make selling mechanical: the stop decides, not a weekly debate."
 ],
 "187-188": [
  "Give it room",
  "While the average is rising at a steep angle, give the stock plenty of room: keep the stop below the average so normal swings do not shake you out."
 ],
 "193": [
  "Overextended",
  "If a stock rockets far above its 30-week average, consider locking in a quarter to a half of the position and trailing the rest."
 ],
 "194": [
  "Trader's stop",
  "A trader wants a faster, smaller move, so use a closer stop: under the nearest prior low, or 4-6% below the breakout."
 ],
 "194-195": [
  "Trader's stop",
  "Under the nearest prior low, or about 4-6% below the breakout and under a round number."
 ],
 "195": [
  "Trader's corrections",
  "Traders ignore corrections under about 7% and raise the stop under each meaningful correction low."
 ],
 "195-196": [
  "Trader's trailing",
  "Raise the stop under each correction low of 7% or more; never use the average as the stop for a trade."
 ],
 "196": [
  "Trader's exit",
  "A trader should not hold a stock that closes below its 30-week average, even slightly."
 ],
 "198-201": [
  "Trendline sale",
  "When a rising trendline with three or more touches breaks, sell about half. The rest goes on the stop."
 ],
 "199": [
  "Trendline",
  "A valid trendline connects at least three points. Its break signals fading momentum; sell part."
 ],
 "200": [
  "Trendline",
  "Trendline stops for half the position, the last correction low for the other half."
 ],
 "202-205": [
  "Swing rule",
  "Subtract the low after an important decline from the peak before it, and add the difference to that peak. That gives a near-term target; sell part near it."
 ],
 "205": [
  "Swing rule",
  "Sell at least part of a trading position near the projected level and let the stop take the rest."
 ],
 "208": [
  "Failed breakout",
  "Real winners rarely fall back below the breakout point. If one does, a trader should get out."
 ],
 "270": [
  "Dow stage",
  "The Dow's stage is the one market indicator you can't skip. Be aggressive only when the major trend is clearly bullish."
 ],
 "275": [
  "Advance-decline line",
  "The advance-decline line should confirm new highs in the averages. A lagging line is a warning."
 ],
 "283": [
  "Momentum index",
  "A 200-day average of daily net advances measures the market's underlying strength."
 ],
 "287": [
  "New highs and lows",
  "New highs minus new lows is a long-term gauge of market health."
 ],
 "294": [
  "World markets",
  "The stage of world averages helps spot major market turns."
 ],
 "297": [
  "General Motors",
  "A heavily owned bellwether like General Motors is watched for the market's major trend."
 ],
 "313": [
  "Simple average",
  "The 30-week average is simple: add up 30 weeks, divide by 30, then roll forward one week at a time."
 ]
}

PAGE_JS = r"""
(function(){
var N=__NOTES__,BOOK="__BOOK__",OFF=__OFF__;
var pop=document.getElementById('pgpop');
var RX=/\bp\.(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?/g;
function el(t,c,x){var n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n;}
function entry(k,a){return N[k]||N[a]||null;}
function link(root){
  var w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode:function(n){
    var p=n.parentNode;if(!p||p.closest('script,style,textarea,input,.pg,#pgpop'))return NodeFilter.FILTER_REJECT;
    RX.lastIndex=0;return RX.test(n.nodeValue)?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT;}});
  var list=[],n;while((n=w.nextNode()))list.push(n);
  list.forEach(function(t){
    var s=t.nodeValue,f=document.createDocumentFragment(),i=0,m;RX.lastIndex=0;
    while((m=RX.exec(s))){
      if(m.index>i)f.appendChild(document.createTextNode(s.slice(i,m.index)));
      var b=el('span','pg',m[0]);b.tabIndex=0;b.setAttribute('role','button');
      b.dataset.a=m[1];b.dataset.k=m[2]?m[1]+'-'+m[2]:m[1];f.appendChild(b);i=m.index+m[0].length;}
    if(i<s.length)f.appendChild(document.createTextNode(s.slice(i)));
    t.parentNode.replaceChild(f,t);});
}
function show(b){
  var k=b.dataset.k,a=b.dataset.a,e=entry(k,a);
  pop.textContent="";
  pop.appendChild(el('b','pgh',(e?e[0]+"  ·  ":"")+"p."+k.replace('-','–')));
  pop.appendChild(el('p','pgt',e?e[1]:"See page "+a+" of the book."));
  if(BOOK){var u=BOOK+"#page="+(parseInt(a,10)+OFF);
    var l=el('a','pgl',"Open this page in the book ↗");l.href=u;l.target="_blank";l.rel="noopener noreferrer";pop.appendChild(l);}
  pop.hidden=false;
  var r=b.getBoundingClientRect(),pw=pop.offsetWidth,x=Math.min(Math.max(8,r.left+window.scrollX),window.scrollX+document.documentElement.clientWidth-pw-8);
  pop.style.left=x+"px";pop.style.top=(r.bottom+window.scrollY+6)+"px";pop.dataset.for=k;
}
function hide(){pop.hidden=true;}
var tipN=0;
function showTip(b){
  if(!b.dataset.tid)b.dataset.tid='t'+(++tipN);
  var L=b.dataset.tip.split("\n");
  pop.textContent="";
  pop.appendChild(el('b','pgh',L[0]));
  for(var i=1;i<L.length;i++)pop.appendChild(el('p','pgt pgp',L[i]));
  pop.hidden=false;
  var r=b.getBoundingClientRect(),pw=pop.offsetWidth,x=Math.min(Math.max(8,r.left+window.scrollX),window.scrollX+document.documentElement.clientWidth-pw-8);
  pop.style.left=x+"px";pop.style.top=(r.bottom+window.scrollY+6)+"px";pop.dataset.for=b.dataset.tid;
}
function copyText(txt,btn){
  var done=function(){var o=btn.dataset.o||btn.textContent;btn.dataset.o=o;btn.textContent="Copied ✓";
    setTimeout(function(){btn.textContent=o;},2000);};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(done,function(){fallback();});}
  else fallback();
  function fallback(){var ta=document.createElement('textarea');ta.value=txt;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done();}catch(x){btn.textContent="Press Ctrl/Cmd+C";}
    document.body.removeChild(ta);}
}
document.addEventListener('click',function(ev){
  var cb=ev.target.closest&&ev.target.closest('.cpbtn');
  if(cb){ev.preventDefault();copyText(cb.dataset.prompt,cb);return;}
  var b=ev.target.closest&&ev.target.closest('.pg');
  if(b){ev.preventDefault();if(!pop.hidden&&pop.dataset.for===b.dataset.k)hide();else show(b);return;}
  var t=ev.target.closest&&ev.target.closest('.bdg[data-tip]');
  if(t){ev.preventDefault();if(!pop.hidden&&pop.dataset.for===t.dataset.tid)hide();else showTip(t);return;}
  if(!ev.target.closest('#pgpop'))hide();});
document.addEventListener('keydown',function(ev){
  if(ev.key==='Escape')hide();
  if((ev.key==='Enter'||ev.key===' ')&&ev.target.classList){
    if(ev.target.classList.contains('pg')){ev.preventDefault();show(ev.target);}
    else if(ev.target.classList.contains('bdg')&&ev.target.dataset.tip){ev.preventDefault();showTip(ev.target);}}});
document.addEventListener('mouseover',function(ev){
  if(!window.matchMedia('(hover:hover)').matches)return;
  var b=ev.target.closest&&ev.target.closest('.pg');if(b){show(b);return;}
  var t=ev.target.closest&&ev.target.closest('.bdg[data-tip]');if(t)showTip(t);});
var timer=null;
function run(){timer=null;link(document.body);}
new MutationObserver(function(){if(!timer)timer=setTimeout(run,60);}).observe(document.body,{childList:true,subtree:true});
run();
})();
"""
PAGE_JS = (PAGE_JS.replace("__NOTES__", json.dumps(RULE_NOTES, ensure_ascii=False).replace("</", "<\\/"))
                  .replace("__BOOK__", BOOK_URL.replace('"', "")).replace("__OFF__", str(BOOK_OFFSET)))


LIST_JS = r"""
(function(){
var ds=Array.prototype.slice.call(document.querySelectorAll('details.panel[data-k]'));
function key(d){return 'wd-'+d.dataset.k;}
ds.forEach(function(d){
  try{var v=localStorage.getItem(key(d));if(v==='1')d.open=true;else if(v==='0')d.open=false;}catch(e){}
  d.addEventListener('toggle',function(){try{localStorage.setItem(key(d),d.open?'1':'0');}catch(e){}});});
function openFor(id){var t=document.getElementById(id);if(t&&t.tagName==='DETAILS')t.open=true;}
Array.prototype.forEach.call(document.querySelectorAll('a[data-jump]'),function(a){
  a.addEventListener('click',function(){openFor(a.dataset.jump);});});
if(location.hash)openFor(location.hash.slice(1));
var ex=document.getElementById('expall'),co=document.getElementById('colall');
if(ex)ex.addEventListener('click',function(){ds.forEach(function(d){d.open=true;});});
if(co)co.addEventListener('click',function(){ds.forEach(function(d){d.open=false;});});
})();
"""

LOOKUP_JS = r"""
(function(){
var L=null,U=null,loading=false,pending=null;
function ready(D){L=D.l;U=new Set(D.u);if(pending!==null){var p=pending;pending=null;show(p);}}
var tag=document.getElementById('lkdata');
if(tag){ready(JSON.parse(tag.textContent));}
else{loading=true;fetch('lookup.json',{cache:'no-cache'}).then(function(r){return r.json();}).then(ready)
  .catch(function(){var o=document.getElementById('lkout');o.textContent="The lookup data could not be loaded.";});}
var COL={"ACTIVE BUY":"#1f9d55","NEAR MISS":"#e6a100","BUY-STOP WATCH":"#2f7fd6","WAIT FOR PULLBACK":"#8a5bd0",
"SKIP - STOP TOO WIDE":"#d9582b","DISCARDED - RESISTANCE":"#8b94a6","SUSPECT BREAKOUT":"#5b6b85",
"BLOCKED - SECTOR":"#c0392b","SUSPENDED - MARKET":"#c0392b","NOT A CANDIDATE":"#6b7689"};
var ICON={pass:"\u2713",fail:"\u2717",warn:"!",na:"\u2013"};
function el(t,c,x){var n=document.createElement(t);if(c)n.className=c;if(x!==undefined)n.textContent=x;return n;}
function $(v){return v==null?"\u2013":"$"+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});}
function show(t){
  var out=document.getElementById('lkout');out.textContent="";
  if(L===null){pending=t;out.textContent="Loading the data...";return;}
  t=(t||"").trim().toUpperCase().replace(/[^A-Z0-9.\-]/g,"");
  if(!t)return;
  var r=L[t];
  if(!r){
    var m=el('div','lkmsg');
    m.textContent=U.has(t)?t+" is in the universe but was not screened: it has under a year of price history, or its price data was not up to date."
      :t+" is not in the list of US stocks this dashboard screens. It can be checked from the command line with the screener's --tickers option.";
    out.appendChild(m);return;}
  var card=el('div','lkcard');card.style.borderColor=COL[r.b]||"#999";
  var top=el('div','lktop');
  top.appendChild(el('b','lktk',t));
  top.appendChild(el('span','lkpx',$(r.p)+(r.g?"  \u00b7  "+r.g:"")));
  var chip=el('span','lkchip',r.b);chip.style.background=COL[r.b]||"#999";top.appendChild(chip);
  card.appendChild(top);
  card.appendChild(el('p','lkhead',r.h));
  if(r.e&&r.s!=null){
    var lv=el('div','lklv');
    lv.appendChild(el('span','','Entry zone '+$(r.e[0])+' \u2013 '+$(r.e[1])));
    lv.appendChild(el('span','','Stop '+$(r.s)+(r.r!=null?' ('+r.r.toFixed(1)+'%)':'')));
    card.appendChild(lv);}
  var ul=el('ul','lkcks');
  r.c.forEach(function(c){
    var li=el('li','ck '+c[1]);
    li.appendChild(el('i','',ICON[c[1]]||""));
    var b=el('b','',c[0]);li.appendChild(b);
    li.appendChild(el('span','',c[2]));
    ul.appendChild(li);});
  card.appendChild(ul);
  card.appendChild(el('p','lkfoot','Mechanical screen as of the last Friday close, not advice. Verify on the chart.'));
  out.appendChild(card);
}
document.getElementById('lkform').addEventListener('submit',function(ev){ev.preventDefault();show(document.getElementById('lkin').value);});
document.getElementById('lkin').addEventListener('change',function(){show(this.value);});
})();
"""

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
.tile{display:block;text-decoration:none;color:inherit}a.tile:hover{box-shadow:0 2px 8px rgba(20,30,60,.16)}
.panel summary.ph{cursor:pointer;list-style:none;display:block;padding:14px 18px 10px;position:relative}.panel summary.ph::-webkit-details-marker{display:none}
.panel summary.ph::after{content:"▸";position:absolute;right:18px;top:14px;color:#8a93a5;font-size:16px;transition:transform .15s}.panel[open] summary.ph::after{transform:rotate(90deg)}
.sh{display:block;font-size:17px;font-weight:700}.ssub{display:block;margin-top:3px;color:#5d6778;font-size:13px;padding-right:28px}
.near .sh{color:#a87100}.watch .sh{color:#2469b4}.wait .sh{color:#7547b8}.skip .sh{color:#c2471f}.disc .sh{color:#5f6878}.susp .sh{color:#46546b}
.listbar{max-width:1240px;margin:22px auto 0;padding:0 20px;display:flex;justify-content:space-between;align-items:center;font-size:14px}
.listbar button{margin-left:8px;padding:5px 12px;border:1px solid #cfd6e3;border-radius:8px;background:#fff;color:#1d4f7a;font-weight:600;cursor:pointer;font-size:12px}
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
.bdg.gold{background:#ffe9a8;color:#7a5300}.bdg.dim{background:#e8eaef;color:#6b7689}.bdg.thin{background:#fde4e1;color:#a52a1d}
td .bdg{margin-left:6px;vertical-align:middle}
.bdg[data-tip]{cursor:help}.bdg[data-tip]:hover,.bdg[data-tip]:focus{outline:2px solid #1d4f7a;outline-offset:1px}
.pgp{margin:6px 0 0}#pgpop{max-width:360px}
.bdg.sig{font-size:11px;padding:2px 9px;letter-spacing:.5px}
.bdg.sig.green{background:#d6f3e0;color:#0e6b35;border:1px solid #1f9d55}
.bdg.sig.yellow{background:#fff0bd;color:#7a5300;border:1px solid #e0a800}
.abuy{position:relative}
.abuy.sg-green{background:#e8f7ee;border-color:#1f9d55;border-top:6px solid #1f9d55}
.abuy.sg-yellow{background:#fff6d6;border-color:#e0a800;border-top:6px solid #e0a800}
.abuy.star{border-color:#c99700;box-shadow:0 0 0 3px #ffe27a,0 6px 18px rgba(201,151,0,.35)}

.playbook{margin:14px 32px 0;border-radius:12px;padding:14px 18px;border-left:7px solid #888;background:#f2f4f8}
.playbook .pbh small{display:block;font-size:11px;letter-spacing:1px;color:#5d6778}.playbook .pbh b{font-size:19px}
.playbook .pbs{margin:6px 0 10px;font-size:13px;color:#33405a}.playbook .pbe{display:block;margin-top:3px;font-size:12px;color:#5d6778}
.pbg{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.pbr{background:rgba(255,255,255,.7);border-radius:8px;padding:9px 11px}.pbr small{display:block;font-size:11px;letter-spacing:.6px;color:#5d6778;margin-bottom:2px}.pbr span{font-size:13px}
.pbf{margin:10px 0 0;font-size:12.5px;color:#33405a}.pbf span{color:#7a8499;margin-left:6px}
.pball{margin-top:8px}.pball summary{cursor:pointer;font-size:12.5px;color:#1d4f7a;font-weight:600}
.pball ul{list-style:none;padding:0;margin:8px 0 0;display:grid;gap:8px}.pball li{background:rgba(255,255,255,.75);border-radius:8px;padding:9px 11px;border-left:5px solid #aaa}
.pball li b{display:block;margin-bottom:2px}.pball li span{display:block;font-size:12.5px;color:#33405a}.pball li em{font-weight:400;color:#7a8499}
.pball li.cur{outline:2px solid #1d4f7a}
.pb-bull,.pball li.bull{border-color:#1f9d55}.pb-bull{background:#e8f7ee}
.pb-high_risk,.pball li.high_risk{border-color:#e0a800}.pb-high_risk{background:#fff6d6}
.pb-defensive,.pball li.defensive{border-color:#e07b00}.pb-defensive{background:#ffeedd}
.pb-bear,.pball li.bear{border-color:#c0392b}.pb-bear{background:#fbe6e3}
.pb-low_risk,.pball li.low_risk{border-color:#2f7fd6}.pb-low_risk{background:#e6f0fb}
.pb-unclear{background:#eef0f4}
.shorthero .hero-head{background:#7b1f1f}.shortpol{margin:10px 22px 0;font-size:13px;color:#7b1f1f}
.abuy.shortc{background:#fbeceb;border-color:#c0392b;border-top:6px solid #c0392b}
.shorthero .none{color:#5d6778}.th{border-color:#c0392b}.th b{color:#c0392b}
.shortwatch{margin:14px 22px}
.bdg.ideals{background:#e7eefb;color:#1d4f7a;border:1px solid #1d4f7a;font-size:11px;padding:2px 9px;letter-spacing:.5px}
.bdg.starbig{position:absolute;top:-20px;right:-12px;width:48px;height:48px;line-height:46px;text-align:center;font-size:32px;padding:0;border-radius:50%;background:#ffd23f;color:#7a5300;border:2px solid #c99700;box-shadow:0 2px 8px rgba(0,0,0,.3);cursor:help}
.cprow{margin:10px 0 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.cpbtn{border:1px solid #1d4f7a;background:#fff;color:#1d4f7a;border-radius:7px;padding:5px 10px;font-weight:700;font-size:12px;cursor:pointer}
.cpbtn:hover{background:#1d4f7a;color:#fff}.cphint{font-size:11px;color:#6b7689}
.how.sm{font-size:12px;color:#5d6778}
.gauges{margin:12px 32px 0;background:#fff;border-radius:10px;padding:8px 14px;box-shadow:0 1px 3px rgba(20,30,60,.08);font-size:13px}
.gauges summary{cursor:pointer;font-weight:600}.gauges ul{list-style:none;margin:8px 0 4px;padding:0}
.gauges li{display:flex;justify-content:space-between;gap:12px;padding:5px 0 5px 12px;border-left:5px solid #b8bfcc;margin:3px 0}
.gauges li.pos{border-color:#1f9d55}.gauges li.neg{border-color:#c0392b}.gauges li span{color:#5d6778;text-align:right}
.gauges em.ours{font-style:normal;font-size:10px;font-weight:700;background:#e8eaef;color:#5d6778;border-radius:4px;padding:0 6px;margin-left:6px}
.gnote{margin:6px 0 2px;font-size:11.5px;color:#8a93a5}
.caution{margin:10px 32px 0;background:#fff3d6;border-left:5px solid #e6a100;border-radius:8px;padding:10px 14px;font-size:13px;color:#7a5300}
.lookup{max-width:1040px;margin:18px auto 0;background:#fff;border-radius:14px;padding:16px 22px;box-shadow:0 1px 3px rgba(20,30,60,.08)}
.lookup h2{margin:0 0 2px;font-size:17px}.lookup p{margin:0 0 10px;color:#5d6778;font-size:13px}
#lkform{display:flex;gap:8px}#lkin{flex:1;max-width:260px;padding:9px 12px;border:2px solid #cfd6e3;border-radius:8px;font-size:16px;text-transform:uppercase}
#lkin:focus{outline:none;border-color:#2f7fd6}
#lkform button{padding:9px 18px;border:0;border-radius:8px;background:#1d4f7a;color:#fff;font-weight:700;cursor:pointer}
.lkmsg{margin-top:12px;padding:10px 14px;background:#f3f5f9;border-radius:8px;color:#5d6778;font-size:13px}
.lkcard{margin-top:14px;border:3px solid #999;border-radius:12px;padding:14px 16px}
.lktop{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.lktk{font-size:26px}.lkpx{color:#5d6778}
.lkchip{margin-left:auto;color:#fff;font-weight:800;font-size:12px;letter-spacing:.8px;padding:4px 10px;border-radius:6px}
.lkhead{margin:8px 0;font-size:14px}.lklv{display:flex;gap:18px;flex-wrap:wrap;font-weight:700;margin:6px 0 10px}
.lkcks{list-style:none;margin:6px 0 0;padding:0}.ck{display:flex;gap:10px;align-items:baseline;padding:6px 0;border-top:1px solid #edf0f5;font-size:13px}
.ck i{font-style:normal;font-weight:800;width:18px;text-align:center;flex:none}.ck b{width:150px;flex:none}.ck span{color:#3b4456}
.ck.pass i{color:#1f9d55}.ck.fail i{color:#c0392b}.ck.warn i{color:#d19200}.ck.na i{color:#9aa3b4}
.ck.fail b{color:#a52a1d}.lkfoot{margin:10px 0 0;font-size:11.5px;color:#8a93a5}
.pg{color:#1d4f7a;border-bottom:1px dotted #1d4f7a;cursor:pointer;white-space:nowrap}.pg:hover,.pg:focus{background:#e8f0f8;outline:none}
#pgpop{position:absolute;z-index:50;max-width:320px;background:#fff;border:1px solid #cfd6e3;border-radius:10px;padding:10px 12px;box-shadow:0 8px 24px rgba(20,30,60,.22);font-size:12.5px;line-height:1.4;color:#1c2333}
#pgpop[hidden]{display:none}.pgh{display:block;color:#0f2a4a;margin-bottom:3px}.pgt{margin:0}.pgl{display:inline-block;margin-top:8px;font-weight:700;color:#1d4f7a}
.sectors{margin:12px 32px 0;background:#fff;border-radius:10px;padding:8px 14px;box-shadow:0 1px 3px rgba(20,30,60,.08);font-size:13px}.sectors summary{cursor:pointer;font-weight:600}
.stab{border-collapse:collapse;width:100%;margin-top:8px}.stab th{font-size:10.5px;padding:6px 10px;background:#f7f8fb;white-space:nowrap}.stab td{padding:6px 10px;border-top:1px solid #edf0f5;white-space:nowrap;vertical-align:middle}
.stab tr.unfav td{opacity:.75}.stab .sn{font-weight:700}.sb{display:inline-block;min-width:22px;text-align:center;color:#fff;border-radius:5px;font-weight:800;padding:1px 6px}
.strip{display:flex;gap:1px}.strip i{display:block;width:7px;height:16px;border-radius:1px}.lg{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
.mv{font-weight:800;font-size:11px;border-radius:5px;padding:1px 6px;margin-right:4px}.mv.warm{background:#dff3e6;color:#17683a}.mv.cool{background:#fde4e1;color:#a52a1d}
.up{color:#17883f;font-weight:700}.dn{color:#c0392b;font-weight:700}.fl{color:#8a93a5}
.exitplan{margin:8px 0 0;font-size:12.5px;background:#fff;border:1px solid #cfe7d8;border-radius:8px;padding:6px 10px}
.exitplan summary{cursor:pointer;font-weight:700;color:#157a41}.exitplan p{margin:6px 0;color:#3b4456}
.pos-wrap{max-width:1040px;margin:18px auto;padding:0 20px}.pos-h{margin:0 0 10px;font-size:20px}.pos-sub{font-size:12px;font-weight:400;color:#6b7689;margin-left:8px}
.pos-h2{margin:14px 0 6px;font-size:15px}.ptab{width:100%;background:#fff;border-radius:10px;overflow:hidden}.ptab td.gain{color:#157a41;font-weight:700}.ptab td.loss{color:#c0392b;font-weight:700}
.poslog{margin-top:14px;background:#fff;border-radius:10px;padding:8px 14px;font-size:13px;box-shadow:0 1px 3px rgba(20,30,60,.08)}.poslog summary{cursor:pointer;font-weight:600;color:#1d4f7a}
.pfrow{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.pfrow input,.pfrow select{padding:8px 10px;border:2px solid #cfd6e3;border-radius:8px;font-size:14px}.pf{display:flex;gap:16px;margin:8px 0}
#posform button{padding:9px 16px;border:0;border-radius:8px;background:#1d4f7a;color:#fff;font-weight:700;cursor:pointer}#pf-out code{display:block;margin:10px 0 4px;padding:8px 10px;background:#f3f5f9;border-radius:6px;word-break:break-all}
.pcard{background:#fff;border-radius:12px;border-left:8px solid #999;box-shadow:0 1px 3px rgba(20,30,60,.08);padding:14px 18px;margin:0 0 14px}
.pcard.sell{border-color:#c0392b}.pcard.raise{border-color:#e6a100}.pcard.take{border-color:#2f7fd6}.pcard.hold{border-color:#1f9d55}
.ptop{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.ptk{font-size:24px;font-weight:800}
.pchip{color:#fff;font-weight:800;font-size:12px;letter-spacing:.8px;padding:4px 10px;border-radius:6px}
.pcard.sell .pchip{background:#c0392b}.pcard.raise .pchip{background:#d19200}.pcard.take .pchip{background:#2f7fd6}.pcard.hold .pchip{background:#1f9d55}
.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:10px 0 4px}
.pgrid div small{display:block;font-size:10.5px;letter-spacing:.7px;text-transform:uppercase}.pgrid div b{font-size:16px}
.pnotes{margin:6px 0 0;padding-left:18px;font-size:13px;color:#3b4456}
@media(max-width:640px){.ck{flex-wrap:wrap}.ck b{width:auto}}
@media(max-width:640px){header,.mkt,.groups,.tiles{padding-left:16px;padding-right:16px}.levels{grid-template-columns:1fr}}
"""

if __name__ == "__main__":
    data = load()
    lkp = FEED.replace(".json", "") + "_lookup.json"
    if os.path.exists(lkp):
        with open(lkp) as f:
            data["_lookup"] = json.load(f)
        raw = json.dumps(data["_lookup"], separators=(",", ":"))
        if len(raw) > EMBED_LIMIT:                  # too big to embed: ship it beside the page
            with open(os.path.join(os.path.dirname(os.path.abspath(OUT)), "lookup.json"), "w") as f:
                f.write(raw)
    with open(OUT, "w") as f:
        f.write(build(data))
    print(f"wrote {OUT}  ({len(data['active'])} active, {len(data['near'])} near misses)")
