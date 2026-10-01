import sys, numpy as np, pandas as pd
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weinstein_pure as W
cfg=dict(W.CFG)
ok=lambda s: print(f"  [OK] {s}")

def frame(p, vol=None):
    p=np.asarray(p,float); n=len(p)
    v=np.ones(n)*1e6 if vol is None else np.asarray(vol,float)
    idx=pd.date_range("2020-01-03",periods=n,freq="W-FRI")
    return pd.DataFrame({"Open":p*.99,"High":p*1.02,"Low":p*.98,"Close":p,
                         "Volume":v},index=idx)
def stg(df):
    ma=W.infra.moving_average(df["Close"],30,cfg["ma_type"])
    s,bo=W.classify(df,ma,cfg); return s,bo,ma

print("1. Four stages")
cases={
 "decline -> Stage 4": (list(np.linspace(200,90,60)),4),
 "base after decline -> Stage 1": (list(np.linspace(200,100,50))+[100+np.sin(i/3) for i in range(30)],1),
 "breakout -> Stage 2": (list(np.linspace(200,100,50))+[100+np.sin(i/3) for i in range(30)]+list(np.linspace(101,130,4)),2),
 "advance -> Stage 2": (list(np.linspace(100,120,40))+list(np.linspace(120,260,45)),2),
}
for name,(p,want) in cases.items():
    s,_,_=stg(frame(p)); got=int(s.iloc[-1])
    print(f"   {name:34} -> {got}")
    assert got==want,(name,got,want)
ok("Stage 1/2/4 transitions per Chapter 2")

print("\n2. Flat MA qualifies (p.14: 'no longer declining')")
p=(list(np.linspace(100,60,60))+[60+np.sin(i/3) for i in range(34)]+list(np.linspace(60,75,3)))
df=frame(p); s,bo,ma=stg(df)
prev=ma.shift(2); slope=float(((ma-prev)/prev.abs()).iloc[-1])*100
print(f"   MA slope {slope:+.2f}% -> stage {int(s.iloc[-1])}")
assert int(s.iloc[-1])==2
ok("breakout off a flat base is Stage 2")

print("\n3. Trading range + buy-stop price (p.115)")
base=[50+np.sin(i/2)*2 for i in range(40)]
df=frame(list(np.linspace(90,52,40))+base)
ma=W.infra.moving_average(df["Close"],30,cfg["ma_type"])
r=W.trading_range(df,ma,cfg)
print(f"   range {r['range_bottom']}-{r['range_top']} width {r['range_width_pct']}% "
      f"weeks {r['range_weeks']} -> is_range={r['is_range']}")
print(f"   buy-stop {r['buy_stop']}  ({r['pct_to_trigger']}% away)")
assert r["is_range"] and r["buy_stop"]>r["range_top"]*0.999
ok("range detected and a concrete trigger price produced")

print("\n4. Overhead resistance (p.115, p.119)")
def flat_high(p, spike_at, spike_high):
    df=frame(p); df["High"]=df["Close"]; df["Low"]=df["Close"]
    df.iloc[spike_at,df.columns.get_loc("High")]=spike_high
    return df
# a level 8% overhead that is ~3 years old: near but stale -> does not block
df=flat_high([50]*200,40,54)
res=W.overhead_resistance(df,cfg)
print(f"   old level: {res['resistance_pct']}% away, age {res['resistance_age_wks']}w "
      f"-> clear={res['resistance_clear']} ({res['resistance_note']})")
assert res["resistance_clear"] and res["resistance_note"]=="old"
# the same level, 10 weeks old, but only ONE week of trading up there: light
df=flat_high([50]*200,186,54)
res2=W.overhead_resistance(df,cfg)
print(f"   fresh single-week level: {res2['resistance_pct']}% away, age {res2['resistance_age_wks']}w "
      f"-> clear={res2['resistance_clear']} ({res2['resistance_note']})")
assert res2["resistance_clear"] and res2["resistance_note"].startswith("light")
# a trading AREA: 10 weeks spent 8% overhead -> heavy supply
p=[50]*150+[54]*10+[50]*40
df=frame(p); df["High"]=df["Close"]; df["Low"]=df["Close"]
res3=W.overhead_resistance(df,cfg)
print(f"   10 weeks of trading overhead: {res3['resistance_note']} -> clear={res3['resistance_clear']}")
assert not res3["resistance_clear"] and res3["resistance_note"].startswith("HEAVY")
# far away (40%) and fresh -> not "nearby"
df=flat_high([50]*200,186,70)
assert W.overhead_resistance(df,cfg)["resistance_clear"]
ok("heavy = a trading area overhead; a lone wick is light; stale or distant is clear")

print("\n5. Volume rules (p.105, p.150)")
def vmake(ratio):
    return [1e6]*4+[1e6*ratio]
df=frame([50]*30+list(np.linspace(50,70,5)), vol=[1e6]*30+vmake(3.0))
n=cfg["vol_base_weeks"]
base_v=float(df["Volume"].iloc[-(n+1):-1].mean()); got=df["Volume"].iloc[-1]/base_v
print(f"   breakout volume {got:.1f}x the prior {n}-week average")
assert got>=cfg["breakout_vol_mult"]
ok("2x-of-4-weeks threshold")

print("\n6. Verdicts -- and what is deliberately ABSENT")
def m(**kw):
    b=dict(stage=2,stage_weeks=1,price=100.,ma30=90.,ma_state="Rising",
           vol_ratio_4wk=2.5,rs=5.,rs_improving=True,resistance_clear=True,
           pct_above_breakout=2.,new_high=True,consol_near_ma=True,
           pct_above_consol=2.,is_range=False)
    b.update(kw); return b
strict=dict(cfg,resistance_mode="strict"); flagm=dict(cfg,resistance_mode="flag")
assert W.verdict(m(),cfg)=="BREAKOUT - BUY"
assert W.verdict(m(vol_ratio_4wk=1.2),cfg)=="SUSPECT - LOW VOLUME BREAKOUT"
assert W.verdict(m(stage_weeks=20),cfg)=="CONTINUATION - BUY"
assert W.verdict(m(stage_weeks=20,new_high=False,vol_ratio_4wk=0.5,pct_above_breakout=2.),cfg)=="PULLBACK - BUY"
assert W.verdict(m(stage=3),cfg)=="AVOID - STAGE 3 TOP"
assert W.verdict(m(price=80.,ma30=90.),cfg)=="AVOID - BELOW MA"
assert W.verdict(m(rs=-3.,rs_improving=False),cfg)=="AVOID - WEAK RS"
assert W.verdict(m(rs=-3.,rs_improving=True),cfg) in W.BUY_VERDICTS
assert W.verdict(m(stage=1,is_range=True),cfg)=="BUY-STOP WATCH"
ok("all buy/avoid paths behave")

print("\n7. Resistance modes")
assert W.verdict(m(resistance_clear=False),flagm)=="BREAKOUT - BUY"
assert W.verdict(m(resistance_clear=False),strict)=="WATCH - RESISTANCE OVERHEAD"
assert W.CFG["resistance_mode"]=="strict"
ok("strict (the default) discards the stock; flag keeps it")

print("\n8. Entry point, not height, decides 'too late' (p.46, p.62, p.71, p.139)")
# fresh breakout 18% past the breakout level -> wait for the pullback
assert W.verdict(m(pct_above_breakout=18.),cfg)==W.WAIT_VERDICT
# Swift Energy: 150% above the ORIGINAL breakout, but it consolidated back to
# the MA and is just clearing that new base -> still a buy
swift=W.verdict(m(stage_weeks=30,pct_above_breakout=150.,price=250.,ma30=235.,
                  consol_near_ma=True,pct_above_consol=3.),cfg)
print(f"   Swift-style: +150% overall, 3% over a base near the MA -> {swift}")
assert swift=="CONTINUATION - BUY"
# a new high that never came back to the MA is not a continuation buy
run=W.verdict(m(stage_weeks=30,price=250.,ma30=100.,consol_near_ma=False,
                vol_ratio_4wk=0.5,pct_above_breakout=150.),cfg)
print(f"   runaway, never tested the MA -> {run}")
assert run=="STAGE 2 - HOLD"
# continuation already 15% past its base top -> wait
assert W.verdict(m(stage_weeks=30,pct_above_consol=15.),cfg)==W.WAIT_VERDICT
# continuation with a flattening MA is refused ("This is important!" p.71)
assert W.verdict(m(stage_weeks=30,ma_state="Flat",vol_ratio_4wk=2.5),cfg)!="CONTINUATION - BUY"
ok("breakouts and continuations judged by distance from their own entry point")

print("\n9. Resistance measured from the right place")
# a base whose top is the only nearby high: measured from the buy-stop, clear
p=list(np.linspace(40,50,30))+[48+np.sin(i/2)*2 for i in range(40)]
df=frame(p); ma=W.infra.moving_average(df["Close"],30,cfg["ma_type"])
r=W.trading_range(df,ma,cfg)
naive=W.overhead_resistance(df,cfg)
right=W.overhead_resistance(df,cfg,ref_price=r["buy_stop"])
print(f"   from price: {naive['resistance_note']}   from buy-stop: {right['resistance_note']}")
assert right["resistance_clear"]
# a Stage 2 stock pulling back under its own recent peak is not 'blocked'
p=list(np.linspace(50,50,40))+list(np.linspace(50,80,15))+list(np.linspace(80,74,4))
df=frame(p)
naive=W.overhead_resistance(df,cfg)
right=W.overhead_resistance(df,cfg,floor=float(df["High"].iloc[:-19].max()))
print(f"   pullback under own 4-week-old peak: {naive['resistance_note']}   with floor: {right['resistance_note']}")
assert naive["resistance_level"] is not None and right["resistance_level"] is not None, "a fresh peak IS seen"
# highs at/below the breakout level were already cleared, so they do not count
p=[50]*40+[55]+[50]*3+list(np.linspace(50,54,8))   # ends BELOW the old 56.1 high
df=frame(p); lvl=float(df["High"].iloc[40])
r1=W.overhead_resistance(df,cfg); r2=W.overhead_resistance(df,cfg,floor=lvl)
print(f"   56.1 high overhead: no floor -> {r1['resistance_note']}; floor at it -> {r2['resistance_note']}")
assert r1["resistance_level"] is not None and r2["resistance_note"]=="clear"
ok("recent peaks count; already-cleared highs do not")

print("\n9b. A peak made in the last 3 weeks is seen")
p=[50]*50+list(np.linspace(50,70,6))+[66,64]
r=W.overhead_resistance(frame(p),cfg)
print(f"   {r['resistance_note']}")
assert r["resistance_level"] is not None and r["resistance_level"]>70
ok("truncated-right pivot detected")

print("\n9c. Pullback volume is judged against the breakout peak (p.105)")
assert W.verdict(m(stage_weeks=6,new_high=False,vol_ratio_4wk=0.6,vol_vs_peak=0.20,pct_above_breakout=-1.),cfg)=="PULLBACK - BUY"
assert W.verdict(m(stage_weeks=6,new_high=False,vol_ratio_4wk=0.6,vol_vs_peak=0.30,pct_above_breakout=-1.),cfg)=="STAGE 2 - HOLD"
assert W.verdict(m(stage_weeks=6,new_high=False,vol_ratio_4wk=0.6,vol_vs_peak=0.80,pct_above_breakout=-1.),cfg)=="STAGE 2 - HOLD"
ok("pullback needs volume down over 75% from the breakout peak (p.105)")

print("\n9d. Real base length and wide-stop flag")
p=list(np.linspace(90,50,30))+[48,52]*60
df=frame(p); ma=W.infra.moving_average(df["Close"],30,cfg["ma_type"])
r=W.trading_range(df,ma,cfg)
print(f"   150-week base reports {r['range_weeks']} weeks")
assert r["range_weeks"]>100
df=frame(list(np.linspace(50,150,100)))
r=W.analyse("X",df,None,frame(np.linspace(100,150,100)),dict(cfg,wide_stop_pct=1.0))
assert r["wide_stop"]==True
ok("base length uncapped; wide stop flagged")

print("\n9f. A fresh level blocks even when an ancient one sits closer (the SWKS case)")
df=flat_high([50]*200,40,51.5)                       # ancient high, +3%
df.iloc[190,df.columns.get_loc("High")]=53.0         # fresh high, +6%, 9 weeks old
r=W.overhead_resistance(df,cfg)
print(f"   ancient +3% and fresh +6% -> {r['resistance_note']}")
assert r["resistance_note"].startswith("light") and abs(r["resistance_pct"]-6.0)<0.1, r
df2=flat_high([50]*200,40,51.5)                      # ancient one only
assert W.overhead_resistance(df2,cfg)["resistance_clear"]
ok("an ancient nearest level does not hide a fresher one inside the band")

print("\n9o. Repeated tops are heavy supply even with little time spent (the PLUS case)")
def spikes(at, high, n=200, base=50):
    df=frame([base]*n); df["High"]=df["Close"]; df["Low"]=df["Close"]
    for k in at: df.iloc[k,df.columns.get_loc("High")]=high
    return df
# three separate rejections at ~54 (+8%), one week each
r3=W.overhead_resistance(spikes([120,150,185],54.0),cfg)
print(f"   3 tests of one level: {r3['resistance_note']} (tests={r3['resistance_tests']}, weeks={r3['resistance_weeks_over']})")
assert not r3["resistance_clear"] and r3["resistance_tests"]==3
# only two rejections -> light
r2=W.overhead_resistance(spikes([150,185],54.0),cfg)
assert r2["resistance_clear"] and r2["resistance_note"].startswith("light"), r2
# three highs at unrelated levels (well over 3% apart) -> not a repeated top
df=spikes([120,150,185],54.0); df.iloc[150,df.columns.get_loc("High")]=57.5; df.iloc[185,df.columns.get_loc("High")]=60.5
r4=W.overhead_resistance(df,cfg)
print(f"   3 highs at different levels: {r4['resistance_note']} (tests={r4['resistance_tests']})")
assert r4["resistance_clear"]
# tests older than the stale window do not count
r5=W.overhead_resistance(spikes([20,40,60],54.0),cfg)
assert r5["resistance_clear"]
ok("3+ tests of one level = heavy; two, scattered, or stale ones are not")

print("\n9e. Range ceiling older than the 30-week window (the WBD case)")
# peak of 30.1 about 45 weeks before a base that tops out at 29.2; price now 30.86
p=[20]*10+[30.1/1.02]+[26]*3+[27,28,27.5,26.5,27,28.5,27.2,26.4,27.8,28.6,27.4,26.8]*3+[28.6/1.0]*2+[30.86]
wk=frame(p)
dur=1
lvl=W.cleared_ceiling(wk,29.2,30.86,dur,cfg)
print(f"   30-week level 29.2 -> effective level {lvl:.2f}")
assert abs(lvl-30.1)<0.5, lvl
# a ceiling that price has NOT cleared is not a breakout level
assert W.cleared_ceiling(wk,29.2,29.9,dur,cfg)==29.2
ok("older range ceiling cleared -> breakout level moves up to it")

print("\n9g. Projected stop for a base")
p=list(np.linspace(90,50,40))+[48,52]*30
df=frame(p); wk=df.copy(); wk.iloc[-1,4]=1e6
rr=W.analyse("B",wk,None,frame(np.linspace(100,150,len(p))),cfg)
print(f"   buy-stop {rr['buy_stop']}  stop {rr['base_stop']}  risk {rr['base_risk_pct']}%  wide={rr['base_wide']}")
assert rr["base_stop"]<rr["range_bottom"] and rr["base_risk_pct"]<0
narrow=W.analyse("B",wk,None,frame(np.linspace(100,150,len(p))),dict(cfg,wide_stop_pct=50.0))
assert narrow["base_wide"]==False and rr["base_wide"]==(rr["base_risk_pct"]<-15)
ok("base stop computed under the floor, flagged against the 15% limit")

print("\n9i. A Stage 2 needs a base behind it (p.33, p.139)")
assert W.verdict(m(base_weeks_before=20),cfg)=="BREAKOUT - BUY"
assert W.verdict(m(base_weeks_before=2,new_high=False),cfg)==W.NO_BASE_VERDICT
assert W.verdict(m(stage_weeks=6,new_high=False,vol_ratio_4wk=0.6,vol_vs_peak=0.2,pct_above_breakout=-1.,base_weeks_before=2),cfg)==W.NO_BASE_VERDICT
assert W.verdict(m(stage_weeks=6,new_high=False,vol_ratio_4wk=0.6,vol_vs_peak=0.2,pct_above_breakout=-1.,base_weeks_before=20),cfg)=="PULLBACK - BUY"
# continuation has its own consolidation test, so it is not blocked here
assert W.verdict(m(stage_weeks=30,base_weeks_before=1),cfg)=="CONTINUATION - BUY"
# end to end: a V-bottom vs a real base
def run(p):
    w=frame(p); w.iloc[-1,4]=3e6
    return W.analyse("X",w,None,frame(np.linspace(100,150,len(p))),dict(cfg,resistance_mode="flag"))
vb=run(list(np.linspace(200,60,50))+list(np.linspace(60,130,14)))
rb=run(list(np.linspace(200,100,50))+[100+np.sin(k/3) for k in range(40)]+[112])
print(f"   V-bottom: stage {vb['stage']}, {vb['base_weeks_before']} base weeks -> {vb['verdict']}")
print(f"   real base: stage {rb['stage']}, {rb['base_weeks_before']} base weeks -> {rb['verdict']}")
print(f"   V-bottom consolidation width {vb['consol_width_pct']}%")
assert vb["verdict"] not in W.BUY_VERDICTS, vb["verdict"]
assert rb["base_weeks_before"]>=8 and rb["verdict"]=="BREAKOUT - BUY"
assert W.verdict(m(stage_weeks=30,consol_width_pct=55.),cfg)!="CONTINUATION - BUY"
assert W.verdict(m(stage_weeks=30,consol_width_pct=12.),cfg)=="CONTINUATION - BUY"
ok("V-shaped rallies are rejected; stocks with a base behind them pass")

print("\n9k. Stage 2 that restarted after a dip under the MA is a continuation, not a no-base V (the WBD case)")
wbd=m(stage_weeks=1,base_weeks_before=6,new_high=True,vol_ratio_4wk=5.8,consol_near_ma=True,
      consol_width_pct=15.,ma_state="Rising",pct_above_consol=6.5)
assert W.verdict(wbd,cfg)=="CONTINUATION - BUY"
# without the consolidation evidence it is still rejected
assert W.verdict(dict(wbd,consol_near_ma=False),cfg)==W.NO_BASE_VERDICT
assert W.verdict(dict(wbd,consol_width_pct=55.),cfg)==W.NO_BASE_VERDICT
assert W.verdict(dict(wbd,vol_ratio_4wk=1.2),cfg)==W.NO_BASE_VERDICT
# and a real initial breakout (with a base) is still a BREAKOUT
assert W.verdict(dict(wbd,base_weeks_before=20),cfg)=="BREAKOUT - BUY"
ok("continuation rescues WBD-type restarts; V-shaped rallies still rejected")

print("\n9l. No-base rule applies to stocks coming off a decline, not to restarted advances")
after=m(stage_weeks=6,base_weeks_before=3,after_advance=True,new_high=False,vol_ratio_4wk=0.6,
        vol_vs_peak=0.2,pct_above_breakout=-1.)
assert W.verdict(after,cfg)=="PULLBACK - BUY"
assert W.verdict(dict(after,after_advance=False),cfg)==W.NO_BASE_VERDICT
ok("MSFT-type restarts are evaluated normally; V-bottoms are still rejected")

print("\n9m. The price range, not stage flicker, decides whether there was a base")
p=[100]*5+[70,75,72,78,74,80,76,82,78,84,80,85,81,86,83,88,84]     # 17 weeks in a 70-88 range
wk=frame(p)
n,lo,hi=W.range_before(wk,len(wk),0.40)
print(f"   range before the breakout: {n} weeks, {lo:.1f}-{hi:.1f}")
assert n>=12 and lo<72
# a V-shaped rally never holds a 40% range for 8 weeks
v=frame(list(np.linspace(100,50,20))+list(np.linspace(50,120,10)))
nv,_,_=W.range_before(v,len(v),0.40)
print(f"   V-shaped rally: {nv} weeks")
assert nv<8
ok("sideways range found behind a breakout; none behind a V")

print("\n9n. Trailed stop (p.194)")
# breakout at index 0 (peak 100), 12% correction to 88, recovery to 99, then a drift
hi=[100,101,102,103,104,100,96,92,93,97,101,105]
lo=[ 98, 99,100,101,102, 92,88,89,90,94, 99,102]
cl=[ 99,100,101,102,103, 94,90,91,95,100,104,104]
wk=pd.DataFrame({"Open":cl,"High":hi,"Low":lo,"Close":cl,"Volume":1e6},
                index=pd.date_range("2024-01-05",periods=12,freq="W-FRI"))
low=W.trailed_correction_low(wk,0,0.08)
print(f"   completed correction low: {low}")
assert low==88
# a correction that has not recovered yet is not used
wk2=wk.iloc[:8]
assert W.trailed_correction_low(wk2,0,0.08) is None
# a shallow dip (<8%) is not a correction
hi2=[100,101,102,103,104,103,102,103]; lo2=[98,99,100,101,102,98,99,100]
w3=pd.DataFrame({"Open":hi2,"High":hi2,"Low":lo2,"Close":hi2,"Volume":1e6},index=pd.date_range("2024-01-05",periods=8,freq="W-FRI"))
assert W.trailed_correction_low(w3,0,0.08) is None
ok("stop trails to the last completed 8%+ correction, not an open or shallow one")

print("\n9j. Basing run tolerates failed breakouts inside the range")
S=np.array
# 10 base weeks, then a 2-week Stage 2 flicker, 6 more base weeks, then Stage 2 begins
stv=S([4]*5+[1]*10+[2]*2+[3]*6+[2]*1, float)
bs,bw,pr=W.basing_run(stv,1,8)
print(f"   flicker tolerated: {bw} basing weeks")
assert bw==18
# a long real advance before the base ends it
stv=S([1]*10+[2]*20+[3]*4+[2]*1, float)
bs,bw,pr=W.basing_run(stv,1,8)
assert pr==2
print(f"   real advance stops the count: {bw} basing weeks")
assert bw==4
# straight from a decline: no base
stv=S([4]*20+[2]*1, float)
assert W.basing_run(stv,1,8)[1]==0 and W.basing_run(stv,1,8)[2]==4
ok("false breakouts do not erase a base; real advances and declines end it")

print("\n9h. Stops placed the way the book places them (p.183)")
for floor,want,why in [(18.25,17.875,"18 1/4 floor -> 17 7/8"),
                       (18.75,18.375,"18 3/4 floor -> under the half, 18 3/8"),
                       (20.0,19.875,"MA 20 -> 19 7/8 (p.184 example)"),
                       (18.0,17.875,"floor on a round number -> 17 7/8"),
                       (25.0,24.875,"25 floor -> 24 7/8")]:
    got=W.book_stop(floor)
    print(f"   {why}: {got}")
    assert abs(got-want)<1e-9,(floor,got,want)
assert W.book_stop(57.2)==56.875
ok("one eighth under the floor, and under a round number or half when it lands just above one")

print("\n10. In-progress week is dropped")
d=pd.bdate_range("2026-08-03","2026-09-30")          # ends Wed 30 Sep 2026
daily=pd.DataFrame({"Open":1.,"High":1.,"Low":1.,"Close":1.,"Volume":100.},index=d)
wk=W.completed_weekly(daily,today="2026-09-30")
assert wk.index[-1]==pd.Timestamp("2026-09-25"), wk.index[-1]
assert W.completed_weekly(daily,include_partial=True).index[-1]==pd.Timestamp("2026-10-02")
d2=pd.bdate_range("2026-08-03","2026-10-02")         # full week ends Fri
assert W.completed_weekly(pd.DataFrame({"Open":1.,"High":1.,"Low":1.,"Close":1.,"Volume":100.},index=d2),today="2026-10-03").index[-1]==pd.Timestamp("2026-10-02")
d3=pd.bdate_range("2026-03-02","2026-04-02")         # Good Friday: ends Thu
assert W.completed_weekly(pd.DataFrame({"Open":1.,"High":1.,"Low":1.,"Close":1.,"Volume":100.},index=d3),today="2026-04-03").index[-1]==pd.Timestamp("2026-04-03")
ok("Mon-Wed run uses last Friday; complete and holiday weeks are kept")

print("\n11. Breakout volume: the book's two tests (p.104), judged on the breakout week")
cfgv=dict(W.CFG)
v=np.array([100,100,100,100,250,100,100],float)       # 2.5x spike at i=4
r=W.heavy_volume(v,4,cfgv); assert r["spike"] and r["heavy"], r
v=np.array([100,100,100,100,150,100],float)           # 1.5x: not heavy
assert not W.heavy_volume(v,4,cfgv)["heavy"]
# build-up: 4 quiet weeks at 100, then three weeks at 220, breakout week 230 (vs build-up mean 220)
v=np.array([100,100,100,100,220,220,220,230],float)
r=W.heavy_volume(v,7,cfgv); assert r["buildup"] and not r["spike"] and r["heavy"], r
# build-up but the breakout week is LOWER than the build-up: fails the 'some increase' clause
v=np.array([100,100,100,100,220,220,220,200],float)
assert not W.heavy_volume(v,7,cfgv)["buildup"]
ok("2x spike, or a 2x build-up with an increase on the breakout week")
# verdict uses the breakout week, not the latest one
base=dict(stage=2,stage_weeks=2,price=110.0,ma30=100.0,rs=5.0,rs_improving=True,
          vol_ratio_4wk=1.1,base_weeks_before=30,after_advance=False,new_high=False,
          consol_near_ma=False,consol_width_pct=10.0,ma_state="Rising",
          pct_above_breakout=3.0,vol_vs_peak=None,resistance_clear=True)
assert W.verdict(dict(base,bo_heavy=True,cur_heavy=False),W.CFG)=="BREAKOUT - BUY"
assert W.verdict(dict(base,bo_heavy=False,cur_heavy=False),W.CFG)=="SUSPECT - LOW VOLUME BREAKOUT"
ok("week-2 stock keeps its breakout-week volume verdict")

print("\n11b. RS lower than at the base's peak (p.113)")
weak=dict(base,rs=-4.0,rs_improving=True,rs_below_peak=True,bo_heavy=True)
assert W.verdict(weak,W.CFG)=="AVOID - WEAK RS"
assert W.verdict(dict(weak,rs_below_peak=False),W.CFG)=="BREAKOUT - BUY"
assert W.verdict(dict(weak,rs=4.0),W.CFG)=="BREAKOUT - BUY"      # positive RS unaffected
ok("negative RS below its earlier peak is rejected; positive RS is not")

print("\n11c. Ten-year long-range view (p.99)")
idx=pd.date_range("2016-01-01",periods=520,freq="W-FRI")
hi=pd.Series(np.linspace(20,40,520),index=idx)
w10=pd.DataFrame({"High":hi,"Low":hi*0.9,"Close":hi*0.95},index=idx)
lr=W.long_range(w10,price=100.0); assert lr["lr_virgin"] and lr["lr_near_years"]==0
lr=W.long_range(w10,price=35.0); assert not lr["lr_virgin"] and lr["lr_near_years"]>=1
ok("virgin territory and nearby yearly highs identified")

print("\n11d. Market breadth gauges (Ch.8)")
rng_=np.random.RandomState(1)
dates=pd.bdate_range("2024-01-01",periods=400)
up=pd.DataFrame(100+np.cumsum(rng_.normal(0.15,1,(400,60)),axis=0),index=dates)
g=W.breadth_gauges(up,up.mean(axis=1))
names={x["name"][:8]:x for x in g}
assert len(g)==3, g
assert names["Momentum"]["status"]=="pos", names["Momentum"]
dn=pd.DataFrame(100+np.cumsum(rng_.normal(-0.15,1,(400,60)),axis=0),index=dates)
g=W.breadth_gauges(dn,dn.mean(axis=1))
assert {x["name"][:8]:x for x in g}["Momentum"]["status"]=="neg"
ok("momentum index and new-high/low gauge read a rising and a falling market")

print("\n12. Ticker lookup explains each rule")
mm=dict(stage=2,stage_weeks=9,price=110.0,ma30=100.0,ma_state="Rising",group="Tech",group_stage=2,
        base_weeks_before=30,after_advance=False,bo_vol_ratio=3.0,bo_heavy=True,bo_buildup=False,
        rs=4.0,rs_improving=True,rs_below_peak=False,resistance_clear=True,resistance_note="clear",
        pct_above_breakout=4.0,breakout_level=105.8,vol_vs_peak=0.2,stop=96.0,stop_pct=-12.7,
        stop_basis="base floor",trail_stop=98.0,trail_stop_pct=-10.9,wide_stop=False,
        verdict="PULLBACK - BUY",triple_score=0)
ck=W.explain(mm,W.CFG,{"blocked":False}); st={c[0]:c[1] for c in ck}
assert st["Pullback volume"]=="pass" and st["Stop within 15%"]=="pass" and st["Sector"]=="pass", st
assert "dried up" in [c for c in ck if c[0]=="Pullback volume"][0][2]
bad=W.explain(dict(mm,vol_vs_peak=0.5,group_stage=4,resistance_clear=False,resistance_note="HEAVY +2% (9wk)",
                   rs=-3.0,rs_improving=False),W.CFG,{"blocked":True})
sb={c[0]:c[1] for c in bad}
assert sb["Market trend"]=="fail" and sb["Sector"]=="fail" and sb["Overhead resistance"]=="fail" \
   and sb["Relative strength"]=="fail" and sb["Pullback volume"]=="fail", sb
assert "Stage 4" in W.headline("SUSPENDED - MARKET",mm,bad) or "suspended" in W.headline("SUSPENDED - MARKET",mm,bad).lower()
far=W.explain(dict(mm,pct_above_breakout=25.0,verdict="STAGE 2 - HOLD",stop=50,stop_pct=-50),W.CFG,{})
assert {c[0]:c[1] for c in far}["Stop within 15%"]=="na"
ok("passes, fails, and not-yet-applicable checks are reported with reasons")

print("\n12b. Thinly traded stocks are flagged, not dropped")
thin=W.explain(dict(mm,liq="thin",adv_dollars=400000.0),W.CFG,{})
assert {c[0]:c[1] for c in thin}["Liquidity"]=="warn" and "half a point" in [c for c in thin if c[0]=="Liquidity"][0][2]
norm=W.explain(dict(mm,liq="normal",adv_dollars=5e7),W.CFG,{})
assert {c[0]:c[1] for c in norm}["Liquidity"]=="pass"
assert W.CFG["min_price"]==0 and W.CFG["min_dollar_volume"]==0
# a cheap, quiet stock is analysed and sized down
idx2=pd.date_range("2024-01-05",periods=140,freq="W-FRI")
px=pd.Series(np.concatenate([np.linspace(2.0,1.0,40),np.linspace(1.0,1.05,50),np.linspace(1.05,2.2,50)]),index=idx2)
wk=pd.DataFrame({"Open":px,"High":px*1.02,"Low":px*0.98,"Close":px,"Volume":2000.0},index=idx2)
dly=pd.DataFrame({"Open":px,"High":px,"Low":px,"Close":px,"Volume":2000.0},index=idx2)
r=W.analyse("CHEAP",wk,dly,wk.assign(Close=np.linspace(100,140,140)),W.CFG)
assert r is not None and r["liq"]=="very thin" and r["shares"]<=0.05*r["adv_shares"]+1, r and (r["liq"],r["shares"],r["adv_shares"])
ok("no price/volume floor; thin stocks flagged and position-capped")

print("\n12c. Extreme volume (split/relisting artifact) is flagged")
n3=150
idx3=pd.date_range("2023-01-06",periods=n3,freq="W-FRI")
p3=np.concatenate([np.linspace(30,5,50),5+0.3*np.sin(np.arange(60)/4),np.linspace(5.8,10,25),np.linspace(10,9.2,15)])
v3=np.full(n3,10_000.0); spike_i=110; v3[spike_i]=2_000_000.0   # one distorted week
v3[spike_i+1:]=60_000.0; v3[-1]=20_000.0
wk3=pd.DataFrame({"Open":p3,"High":p3*1.02,"Low":p3*0.98,"Close":p3,"Volume":v3},index=idx3)
dl3=pd.DataFrame({"Open":p3,"High":p3,"Low":p3,"Close":p3,"Volume":v3/5},index=idx3)
r3=W.analyse("ARTI",wk3,dl3,wk3.assign(Close=np.linspace(100,130,n3)),W.CFG)
assert r3 is not None
assert r3["stage"]==2 and r3["bo_vol_ratio"]>=20, (r3["stage"],r3["bo_vol_ratio"])
assert r3["vol_verify"] and r3["triple_vol"] is False, r3
assert abs(r3["vol_vs_peak"]-20000/60000)<0.02, r3["vol_vs_peak"]   # vs busiest ordinary week, not the spike
assert "Volume data check" in {c[0] for c in W.explain(r3,W.CFG,{})}
ok(f"{r3['bo_vol_ratio']:.0f}x breakout volume flagged; pullback measured vs ordinary peak; triple-volume credit withheld")
ex=W.explain(dict(mm,vol_verify=True,bo_vol_ratio=73.0),W.CFG,{})
assert {c[0]:c[1] for c in ex}["Volume data check"]=="warn"
nm=W.explain(dict(mm,vol_verify=False),W.CFG,{})
assert "Volume data check" not in {c[0] for c in nm}
ok("explain() adds the warning only when flagged")
# a quiet last four weeks lowers the liquidity read below the 50-day average
v4=np.full(n3,2_000_000.0); v4[-4:]=1_000.0
wk4=pd.DataFrame({"Open":p3,"High":p3*1.02,"Low":p3*0.98,"Close":p3,"Volume":v4},index=idx3)
dl4=pd.DataFrame({"Open":p3,"High":p3,"Low":p3,"Close":p3,"Volume":np.full(n3,400_000.0)},index=idx3)
r4=W.analyse("QUIET",wk4,dl4,wk4.assign(Close=np.linspace(100,130,n3)),W.CFG)
assert r4 is not None and r4["liq"] in ("thin","very thin"), r4 and r4["liq"]
ok("liquidity uses the lower of the 50-day and latest-four-week dollar volume")

print("\n12d. Profit taking (Chapter 6)")
# swing rule: peak 26, low 16 -> 10 points -> target 36 (p.202 chart 6-33)
pk=np.concatenate([np.linspace(15,25.5,40),np.linspace(25.5,16.5,25),16.5+0.4*np.sin(np.arange(50)/4),np.linspace(17,28,10)])
wsw=frame(pk); wsw["High"]=wsw["Close"]; wsw["Low"]=wsw["Close"]; wsw.iloc[39,wsw.columns.get_loc("High")]=26.0
wsw.iloc[64,wsw.columns.get_loc("Low")]=16.0
for c in ("High","Low"): wsw[c]=wsw[c].astype(float)
wsw["Low"]=np.minimum(wsw["Low"],wsw["Close"]); wsw["High"]=np.maximum(wsw["High"],wsw["Close"])
bo_i3=len(wsw)-10
sw=W.swing_target(wsw,bo_i3,28.0,W.CFG)
assert sw and abs(sw["swing_peak"]-26.0)<0.01 and abs(sw["swing_low"]-16.0)<0.01 and abs(sw["swing_target"]-36.0)<0.01, sw
assert W.swing_target(wsw,bo_i3,40.0,W.CFG) is None          # target already passed
assert W.swing_target(wsw,bo_i3,10.0,dict(W.CFG,swing_max_gain_pct=100))is None  # implausibly far
assert W.swing_target(frame(np.linspace(10,30,150)),100,25.0,W.CFG) is None  # no important decline
ok("swing rule: peak 26, low 16 projects 36; stale, absurd and no-decline cases return nothing")
# trader stop (p.194): no nearby reaction low -> about 5% under the breakout; a close one wins
wt=frame(np.concatenate([np.full(30,17.0),np.linspace(20.4,24,10)]))     # lows far below: no close reaction low
ts=W.trader_stop(wt,30,20.375,W.CFG)
assert 0.93*20.375<=ts<=0.96*20.375, ts
wt2=frame(np.concatenate([np.full(30,20.0),np.linspace(20.4,24,10)]))   # lows ~19.6: that is the reaction low
ts2=W.trader_stop(wt2,30,20.375,W.CFG)
assert 19.3<=ts2<19.6, ts2
ok("trader stop: reaction low if close, otherwise 4-6% under the breakout, under a round number")
# trailing stop: correction low raised only after the recovery; MA flat -> under the low even above the MA
up=np.concatenate([np.linspace(10,30,40),np.linspace(30,24,6),np.linspace(24,31,6),np.linspace(31,40,20)])
wup=frame(up); mup=W.infra.moving_average(wup["Close"],30,"SMA")
stp,steps=W.trail_stop(wup,mup,0,W.CFG)
assert stp is not None and 22<stp<=24, (stp,steps)
assert all(steps[i][1]<=steps[i+1][1] for i in range(len(steps)-1))
ok("trailing stop raised after a completed 8%+ correction and never lowered")
# overextension flag uses the pct above the 30-week average
assert W.CFG["overextended_pct"]==40.0
# positions
txt=("# comment\nticker,buy_date,buy_price,style,stop,sell_date,sell_price\nAAA,2025-01-10,25.5,investor,23\n"
     "BBB,2025-02-07,$10.00\nbad line\nCCC,2025-03-07,20,trader,,2025-05-09,24\n")
pp=W.parse_positions(txt)
assert [p["ticker"] for p in pp]==["AAA","BBB","CCC"] and pp[0]["stop"]=="23" and pp[1]["buy_price"]==10.0, pp
assert pp[2]["sell_price"]==24.0 and pp[2]["style"]=="trader" and "shares" not in pp[0], pp
assert W.parse_positions("AAA,2025-01-10,25.5")[0]["ticker"]=="AAA"          # no header needed
ok("positions CSV parsed; comments, malformed lines ignored; shares never read")
op,cl=W.compute_positions(pp,W.CFG,False,loader=lambda t: pd.DataFrame())
assert len(cl)==1 and cl[0]["gain_pct"]==20.0 and op==[], (op,cl)
ok("closed trades report their result; unreadable open positions are skipped, not fatal")
stg_up=np.full(len(wup),2)
ps=W.position_status({"ticker":"AAA","buy_date":wup.index[45].date(),"buy_price":float(wup["Close"].iloc[45]),"style":"investor","stop":"15"},
                     wup,mup,stg_up,W.CFG)
assert ps["status"] in ("RAISE STOP","TAKE PARTIAL - OVEREXTENDED","HOLD"), ps
assert ps["stop"]>15, ps
if ps["status"]=="HOLD": assert ps["notes"]
ok(f"open position reads {ps['status']}, book stop {ps['stop']}")
s3=np.full(len(wup),2); s3[-1]=3
inv=W.position_status({"ticker":"AAA","buy_date":wup.index[45].date(),"buy_price":25.0,"style":"investor"},wup,mup,s3,W.CFG)
trd=W.position_status({"ticker":"AAA","buy_date":wup.index[45].date(),"buy_price":25.0,"style":"trader"},wup,mup,s3,W.CFG)
assert inv["status"]=="SELL HALF - STAGE 3" and trd["status"]=="SELL - STAGE 3 TOP", (inv["status"],trd["status"])
s4=np.full(len(wup),2); s4[-1]=4
assert W.position_status({"ticker":"AAA","buy_date":wup.index[45].date(),"buy_price":25.0},wup,mup,s4,W.CFG)["status"]=="SELL - STAGE 4"
ok("Stage 3: investors sell half, traders sell all; Stage 4: sell")
# a stop that was breached since the purchase is reported
dn=np.concatenate([np.full(40,10.0),np.linspace(11,20,12),np.linspace(20,16,4),np.linspace(16,19,5),np.linspace(19,9,6)])
wdn=frame(dn); mdn=W.infra.moving_average(wdn["Close"],30,"SMA")
sdn=np.concatenate([np.full(40,1),np.full(len(dn)-40,2)])
ph=W.position_status({"ticker":"AAA","buy_date":wdn.index[41].date(),"buy_price":11.0,"style":"investor"},wdn,mdn,sdn,W.CFG)
assert ph["status"]=="SELL - STOP HIT" and ph["hit"], ph
ok("a breached stop is reported as a sell")

# strip docstrings and comments, then confirm none of the borrowed logic is
# actually executed anywhere in this module
import ast, io, tokenize
import os
path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"weinstein_pure.py")
tree=ast.parse(open(path).read())
for node in ast.walk(tree):
    if isinstance(node,(ast.Module,ast.FunctionDef,ast.ClassDef)) and ast.get_docstring(node):
        node.body=node.body[1:]
code=ast.unparse(tree).lower().replace("stage_vcp_screener","INFRA")
for banned in ["rsi","trend_template","vcp","atr","hard_ceiling","hist_peak","minervini"]:
    assert banned not in code, f"found {banned!r} in executable code"
ok("no RSI / Trend Template / VCP / ATR / extension logic in executable code")
print("\nPURE-WEINSTEIN TESTS PASSED")
