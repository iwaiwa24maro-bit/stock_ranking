#!/usr/bin/env python3
"""
US株 売買代金ランキング スクレイパー
データソース: Yahoo Finance US (yfinance)
  - most_actives 250件（株数出来高上位）
  - 時価総額上位100件（高株価・大型株をカバー）
  2ソースを統合して売買代金（株価×出来高）降順でソート
実行するたびに ranking_data.json を更新し ranking.html を生成する
"""

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
import yfinance.screener as yfs
from yfinance.data import YfData

# ── 設定 ──────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_FILE = BASE_DIR / "ranking_data.json"
HTML_FILE = BASE_DIR / "ranking.html"
TARGET    = "NVDA"
TOP_N     = 10

_SCREENER_URL_ = "https://query1.finance.yahoo.com/v1/finance/screener"
_SCREENER_PARAMS_ = {"corsDomain": "finance.yahoo.com", "formatted": "false", "lang": "en-US", "region": "US"}


def _fetch_by_marketcap(n: int = 100) -> list[dict]:
    """時価総額上位n件を取得（高株価銘柄をカバー）"""
    body = {
        "offset": 0, "size": n,
        "sortField": "intradaymarketcap", "sortType": "DESC",
        "quoteType": "EQUITY", "userId": "", "userIdType": "guid",
        "query": {"operator": "AND", "operands": [{"operator": "eq", "operands": ["region", "us"]}]},
    }
    resp = YfData().post(_SCREENER_URL_, data=json.dumps(body, separators=(",", ":")), params=_SCREENER_PARAMS_)
    return resp.json()["finance"]["result"][0].get("quotes", []) if resp.ok else []


# ── データ取得 ─────────────────────────────────────────────────────
def fetch_rankings() -> list[dict]:
    # ソース1: 株数出来高トップ250（低価格高出来高銘柄をカバー）
    r1 = yfs.screen("most_actives", count=250)
    q1 = r1.get("quotes", [])

    # ソース2: 時価総額トップ100（高株価銘柄をカバー：SNDK等）
    q2 = _fetch_by_marketcap(100)

    if not q1 and not q2:
        raise RuntimeError("データが取得できませんでした")

    # 統合・重複排除（dollar_vol が大きい方を優先）
    merged: dict[str, dict] = {}
    for q in q1 + q2:
        sym = q.get("symbol", "")
        if not sym:
            continue
        price  = q.get("regularMarketPrice", 0) or 0
        volume = q.get("regularMarketVolume", 0) or 0
        dv     = price * volume
        if sym not in merged or dv > merged[sym]["dollar_vol"]:
            merged[sym] = {
                "symbol":     sym,
                "name":       q.get("shortName") or q.get("longName", ""),
                "price":      price,
                "change":     q.get("regularMarketChange", 0) or 0,
                "change_pct": q.get("regularMarketChangePercent", 0) or 0,
                "volume":     volume,
                "dollar_vol": dv,
            }

    stocks = sorted(merged.values(), key=lambda x: x["dollar_vol"], reverse=True)
    return stocks[:TOP_N]


def fmt_price(v: float) -> str:
    return f"${v:,.2f}"


def fmt_change(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}"


def fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def fmt_vol(v: float) -> str:
    """売買代金を億ドル表示"""
    b = v / 1_000_000_000
    if b >= 1:
        return f"${b:.1f}B"
    return f"${v/1_000_000:.0f}M"


# ── JSON 蓄積保存 ──────────────────────────────────────────────────
def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


def save_data(data: dict) -> None:
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def to_record(rank: int, s: dict) -> dict:
    """JSON保存用レコード"""
    return {
        "rank":       rank,
        "ticker":     s["symbol"],
        "name":       s["name"],
        "price":      s["price"],
        "change":     round(s["change"], 4),
        "change_pct": round(s["change_pct"], 4),
        "volume":     s["volume"],
        "dollar_vol": round(s["dollar_vol"], 0),
    }


# ── TARGET 推移 ────────────────────────────────────────────────────
def get_history(all_data: dict) -> tuple[list[str], list]:
    dates = sorted(all_data.keys())
    ranks = []
    for date in dates:
        entry = next(
            (e for e in all_data[date] if e.get("ticker", "").upper() == TARGET),
            None,
        )
        ranks.append(entry["rank"] if entry else None)
    return dates, ranks


# ── 1位回数集計 ────────────────────────────────────────────────────
def get_no1_history(all_data: dict) -> list[dict]:
    """各銘柄が1位になった回数・日付一覧を返す（回数降順）"""
    counts: dict[str, dict] = {}
    for date in sorted(all_data.keys()):
        entries = all_data[date]
        top = next((e for e in entries if e.get("rank") == 1), None)
        if not top:
            continue
        ticker = top.get("ticker", "")
        if ticker not in counts:
            counts[ticker] = {"ticker": ticker, "name": top.get("name", ""), "count": 0, "dates": []}
        counts[ticker]["count"] += 1
        counts[ticker]["dates"].append(date)

    return sorted(counts.values(), key=lambda x: x["count"], reverse=True)


# ── HTML 生成 ──────────────────────────────────────────────────────
def build_no1_rows(no1: list[dict]) -> str:
    rows = []
    max_count = no1[0]["count"] if no1 else 1
    for i, item in enumerate(no1, 1):
        last_date = item["dates"][-1] if item["dates"] else "-"
        all_dates = ", ".join(item["dates"])
        bar_pct   = int(item["count"] / max_count * 100)
        medal_cls = {1: "gold", 2: "silver", 3: "bronze"}.get(i, "")
        rows.append(
            f'<tr title="{all_dates}">'
            f'<td><span class="rank-pill {medal_cls}">{i}</span></td>'
            f'<td class="tk">{item["ticker"]}</td>'
            f'<td class="nm">{item["name"]}</td>'
            f'<td><div class="bar-cell">'
            f'<div class="bar-track"><div class="bar-fill" style="width:{bar_pct}%"></div></div>'
            f'<span class="bar-val">{item["count"]}日</span>'
            f'</div></td>'
            f'<td class="dim mono">{last_date}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def build_rows(rankings: list[dict]) -> str:
    max_dv = max(r.get("dollar_vol", 1) for r in rankings) or 1
    rows   = []
    for r in rankings:
        pct    = r.get("change_pct", 0)
        arrow  = "▲" if pct >= 0 else "▼"
        cls    = "neg" if pct < 0 else "pos"
        is_tgt = r.get("ticker", "").upper() == TARGET
        hl     = ' class="hl"' if is_tgt else ""
        bar_w  = int(r.get("dollar_vol", 0) / max_dv * 100)
        rows.append(
            f'<tr{hl}>'
            f'<td><span class="rank-num">{r["rank"]}</span></td>'
            f'<td class="tk">{r["ticker"]}</td>'
            f'<td class="nm">{r.get("name","")}</td>'
            f'<td class="mono right">{fmt_price(r.get("price",0))}</td>'
            f'<td class="mono right {cls}">{arrow} {fmt_pct(pct)}</td>'
            f'<td><div class="bar-cell">'
            f'<div class="bar-track"><div class="bar-fill" style="width:{bar_w}%"></div></div>'
            f'<span class="bar-val mono">{fmt_vol(r.get("dollar_vol",0))}</span>'
            f'</div></td>'
            f'</tr>'
        )
    return "\n".join(rows)


def generate_html(today: str, rankings: list[dict], all_data: dict) -> str:
    dates, ranks = get_history(all_data)
    no1_list     = get_no1_history(all_data)

    chart_labels = json.dumps(dates, ensure_ascii=False)
    chart_data   = "[" + ", ".join("null" if r is None else str(r) for r in ranks) + "]"

    no1_tickers = json.dumps([x["ticker"] for x in no1_list])
    no1_counts  = json.dumps([x["count"]  for x in no1_list])
    bar_colors  = json.dumps(
        ["#f5a623" if i == 0 else "#9ca3af" if i == 1 else "#b45309" if i == 2
         else "#1e6bb8" for i in range(len(no1_list))]
    )

    timeline_rows = ""
    for date in sorted(all_data.keys(), reverse=True):
        top = next((e for e in all_data[date] if e.get("rank") == 1), None)
        if top:
            timeline_rows += (
                f'<tr>'
                f'<td class="dim mono">{date}</td>'
                f'<td class="tk">{top["ticker"]}</td>'
                f'<td class="nm">{top.get("name","")}</td>'
                f'</tr>\n'
            )

    nvda_entry  = next((e for e in rankings if e.get("ticker","").upper() == TARGET), None)
    nvda_rank   = f"#{nvda_entry['rank']}" if nvda_entry else "OUT"
    nvda_pct    = nvda_entry.get("change_pct", 0) if nvda_entry else 0
    nvda_cls    = "kpi-neg" if nvda_pct < 0 else "kpi-pos"

    top1_today  = rankings[0]["ticker"] if rankings else "-"
    top1_name   = rankings[0].get("name","") if rankings else ""
    most_no1    = no1_list[0]["ticker"] if no1_list else "-"
    most_count  = no1_list[0]["count"]  if no1_list else 0
    total_days  = len(all_data)

    table_rows = build_rows(rankings)
    no1_rows   = build_no1_rows(no1_list)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>米国株 売買代金ダッシュボード — {today}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{
  --bg:        #eef2f7;
  --surface:   #ffffff;
  --surface2:  #f9fafb;
  --border:    #e4e9f0;
  --border-md: #d1d9e6;
  --accent:    #3b6ef0;
  --accent-dim:rgba(59,110,240,.07);
  --green:     #0f9954;
  --red:       #e53232;
  --gold:      #c47c0a;
  --gold-dim:  rgba(196,124,10,.08);
  --text:      #3d4f63;
  --bright:    #111c2d;
  --dim:       #8fa3bb;
  --shadow-sm: 0 1px 3px rgba(15,30,60,.07),0 1px 2px rgba(15,30,60,.04);
  --shadow-md: 0 4px 12px rgba(15,30,60,.08),0 1px 4px rgba(15,30,60,.05);
  --mono:      "SF Mono","Fira Code","Consolas",monospace;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{font-size:14px;-webkit-font-smoothing:antialiased}}
body{{
  background:var(--bg);
  color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;
  line-height:1.5;
  min-height:100vh;
}}

/* ── ヘッダー ─────────────────────── */
.topbar{{
  display:flex;align-items:center;justify-content:space-between;
  padding:.85rem 2rem;
  background:var(--surface);
  border-bottom:1px solid var(--border);
  box-shadow:var(--shadow-sm);
  position:sticky;top:0;z-index:20;
}}
.topbar-left{{display:flex;align-items:center;gap:.9rem}}
.logo{{
  font-family:var(--mono);font-size:.88rem;font-weight:700;
  color:var(--accent);letter-spacing:.06em;
}}
.sep{{width:1px;height:16px;background:var(--border-md)}}
.title{{font-size:.82rem;color:var(--dim);font-weight:500;letter-spacing:.01em}}
.topbar-right{{display:flex;align-items:center;gap:1rem}}
.live-badge{{
  display:flex;align-items:center;gap:.4rem;
  background:rgba(15,153,84,.1);border:1px solid rgba(15,153,84,.2);
  border-radius:20px;padding:.2rem .7rem;
}}
.live-dot{{
  width:6px;height:6px;border-radius:50%;
  background:var(--green);
  animation:pulse 2.4s ease-in-out infinite;
}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.5;transform:scale(.85)}}}}
.live-text{{font-size:.68rem;font-weight:600;color:var(--green);letter-spacing:.04em}}
.date-tag{{
  font-family:var(--mono);font-size:.72rem;
  color:var(--dim);background:var(--surface2);
  border:1px solid var(--border);border-radius:4px;
  padding:.2rem .6rem;
}}
.src-tag{{font-size:.68rem;color:var(--dim)}}

/* ── メイン ───────────────────────── */
.main{{padding:1.6rem 2rem;display:flex;flex-direction:column;gap:1.4rem;max-width:1600px;margin:0 auto}}

/* ── KPI カード ───────────────────── */
.kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}}
@media(max-width:900px){{.kpi-row{{grid-template-columns:repeat(2,1fr)}}}}
.kpi{{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:10px;
  padding:1.1rem 1.3rem 1rem;
  box-shadow:var(--shadow-sm);
  position:relative;overflow:hidden;
  transition:box-shadow .2s;
}}
.kpi:hover{{box-shadow:var(--shadow-md)}}
.kpi-stripe{{
  position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--accent),rgba(59,110,240,.3));
  border-radius:10px 10px 0 0;
}}
.kpi-label{{
  font-size:.66rem;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;color:var(--dim);margin-bottom:.55rem;
}}
.kpi-value{{
  font-family:var(--mono);font-size:1.6rem;font-weight:700;
  color:var(--bright);line-height:1;letter-spacing:-.01em;
}}
.kpi-value.kpi-pos{{color:var(--green)}}
.kpi-value.kpi-neg{{color:var(--red)}}
.kpi-value.kpi-gold{{color:var(--gold)}}
.kpi-sub{{
  font-size:.7rem;color:var(--dim);margin-top:.45rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}

/* ── グリッド ─────────────────────── */
.grid-2{{display:grid;grid-template-columns:3fr 2fr;gap:1.2rem}}
.grid-3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1.2rem}}
@media(max-width:1100px){{.grid-2{{grid-template-columns:1fr}}}}
@media(max-width:860px){{.grid-3{{grid-template-columns:1fr}}}}

/* ── カード ───────────────────────── */
.card{{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:10px;
  box-shadow:var(--shadow-sm);
  overflow:hidden;
  transition:box-shadow .2s;
}}
.card:hover{{box-shadow:var(--shadow-md)}}
.card-header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:.7rem 1.2rem;
  border-bottom:1px solid var(--border);
  background:var(--surface2);
}}
.card-title{{
  font-size:.67rem;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;color:var(--dim);
}}
.card-sub{{font-size:.65rem;color:var(--dim)}}

/* ── テーブル ─────────────────────── */
table{{width:100%;border-collapse:collapse;font-size:.8rem}}
th{{
  padding:.55rem 1rem;
  font-size:.63rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--dim);
  border-bottom:1px solid var(--border);
  background:var(--surface2);white-space:nowrap;
}}
td{{
  padding:.7rem 1rem;
  color:var(--text);
  border-bottom:1px solid var(--border);
  vertical-align:middle;
}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr{{transition:background .15s}}
tbody tr:hover td{{background:#f4f7fd}}

/* ランクバッジ */
.rank-num{{
  display:inline-flex;align-items:center;justify-content:center;
  width:22px;height:22px;border-radius:5px;
  font-family:var(--mono);font-size:.7rem;font-weight:700;
  background:var(--surface2);color:var(--dim);
  border:1px solid var(--border);
}}
.rank-pill{{
  display:inline-flex;align-items:center;justify-content:center;
  width:24px;height:24px;border-radius:50%;
  font-family:var(--mono);font-size:.7rem;font-weight:700;
  background:var(--surface2);color:var(--dim);
  border:1px solid var(--border);
}}
.rank-pill.gold  {{background:#fef3c7;color:#92400e;border-color:#fcd34d}}
.rank-pill.silver{{background:#f3f4f6;color:#6b7280;border-color:#d1d5db}}
.rank-pill.bronze{{background:#fef2e0;color:#92400e;border-color:#fbbf24}}

/* セル */
.tk{{font-family:var(--mono);font-size:.82rem;font-weight:700;color:var(--accent);letter-spacing:.03em}}
.nm{{color:var(--text);font-size:.78rem;max-width:175px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mono{{font-family:var(--mono)}}
.right{{text-align:right}}
.dim{{color:var(--dim)}}
.pos{{color:var(--green);font-weight:600}}
.neg{{color:var(--red);font-weight:600}}

/* NVDA ハイライト */
tr.hl td{{background:var(--gold-dim) !important}}
tr.hl td:first-child{{box-shadow:inset 3px 0 0 var(--gold)}}
tr.hl .tk{{color:var(--gold)}}
tr.hl:hover td{{background:rgba(196,124,10,.12) !important}}

/* インラインバー */
.bar-cell{{display:flex;align-items:center;gap:.65rem}}
.bar-track{{
  flex:1;height:4px;border-radius:99px;
  background:var(--border);min-width:50px;max-width:110px;
}}
.bar-fill{{
  height:100%;border-radius:99px;
  background:linear-gradient(90deg,var(--accent),rgba(59,110,240,.35));
}}
.bar-val{{font-family:var(--mono);font-size:.73rem;color:var(--text);white-space:nowrap}}

/* チャートエリア */
.chart-wrap{{position:relative;padding:.9rem 1.1rem 1.1rem}}

/* スクロールエリア */
.scroll{{max-height:330px;overflow-y:auto}}
.scroll::-webkit-scrollbar{{width:4px}}
.scroll::-webkit-scrollbar-track{{background:transparent}}
.scroll::-webkit-scrollbar-thumb{{background:var(--border-md);border-radius:4px}}
.scroll::-webkit-scrollbar-thumb:hover{{background:var(--dim)}}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-left">
    <span class="logo">◈ 米国株式</span>
    <div class="sep"></div>
    <span class="title">売買代金ランキング　ダッシュボード</span>
  </div>
  <div class="topbar-right">
    <span class="src-tag">出典: Yahoo Finance</span>
    <span class="date-tag">{today}</span>
    <div class="live-badge">
      <span class="live-dot"></span>
      <span class="live-text">更新済み</span>
    </div>
  </div>
</div>

<div class="main">

  <!-- KPI カード -->
  <div class="kpi-row">
    <div class="kpi">
      <div class="kpi-stripe"></div>
      <div class="kpi-label">累計追跡日数</div>
      <div class="kpi-value">{total_days}<span style="font-size:.85rem;font-weight:400;color:var(--dim)"> 日</span></div>
      <div class="kpi-sub">計測開始以来</div>
    </div>
    <div class="kpi">
      <div class="kpi-stripe" style="background:linear-gradient(90deg,var(--gold),rgba(196,124,10,.2))"></div>
      <div class="kpi-label">本日の 1位</div>
      <div class="kpi-value kpi-gold">{top1_today}</div>
      <div class="kpi-sub">{top1_name}</div>
    </div>
    <div class="kpi">
      <div class="kpi-stripe" style="background:linear-gradient(90deg,{('var(--green)' if nvda_cls=='kpi-pos' else 'var(--red)')},transparent)"></div>
      <div class="kpi-label">NVDA 本日順位</div>
      <div class="kpi-value {nvda_cls}">{nvda_rank}</div>
      <div class="kpi-sub">前日比 {fmt_pct(nvda_pct)}</div>
    </div>
    <div class="kpi">
      <div class="kpi-stripe" style="background:linear-gradient(90deg,var(--gold),rgba(196,124,10,.2))"></div>
      <div class="kpi-label">最多 1位獲得銘柄</div>
      <div class="kpi-value kpi-gold">{most_no1}</div>
      <div class="kpi-sub">{most_count}日 1位獲得</div>
    </div>
  </div>

  <!-- トップ10 ＋ NVDA推移チャート -->
  <div class="grid-2">
    <div class="card">
      <div class="card-header">
        <span class="card-title">本日のトップ 10（売買代金順）</span>
        <span class="card-sub">売買代金 ＝ 株価 × 出来高</span>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width:36px">順位</th>
            <th>銘柄コード</th>
            <th>銘柄名</th>
            <th style="text-align:right">株価（USD）</th>
            <th style="text-align:right">騰落率</th>
            <th>売買代金</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-title">NVDA — 順位推移グラフ</span>
        <span class="card-sub">数値が小さいほど上位</span>
      </div>
      <div class="chart-wrap" style="height:282px">
        <canvas id="nvdaChart"></canvas>
      </div>
    </div>
  </div>

  <!-- 1位履歴 3カラム -->
  <div class="grid-3">
    <div class="card">
      <div class="card-header">
        <span class="card-title">歴代 1位獲得回数ランキング</span>
        <span class="card-sub">全期間の集計</span>
      </div>
      <div class="scroll">
        <table>
          <thead>
            <tr>
              <th style="width:36px">順位</th>
              <th>銘柄コード</th>
              <th>銘柄名</th>
              <th>獲得日数</th>
              <th style="text-align:right">最終 1位日</th>
            </tr>
          </thead>
          <tbody>{no1_rows}</tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-title">1位獲得回数グラフ</span>
      </div>
      <div class="chart-wrap" style="height:282px">
        <canvas id="no1Chart"></canvas>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-title">1位 日別履歴</span>
        <span class="card-sub">新しい順</span>
      </div>
      <div class="scroll">
        <table>
          <thead>
            <tr><th>日付</th><th>銘柄コード</th><th>銘柄名</th></tr>
          </thead>
          <tbody>{timeline_rows}</tbody>
        </table>
      </div>
    </div>
  </div>

</div>

<script>
Chart.defaults.color = '#8fa3bb';
Chart.defaults.borderColor = '#e4e9f0';

// NVDA 順位推移
(function(){{
  const ctx = document.getElementById('nvdaChart').getContext('2d');
  const grad = ctx.createLinearGradient(0,0,0,260);
  grad.addColorStop(0,'rgba(196,124,10,.18)');
  grad.addColorStop(1,'rgba(196,124,10,.0)');
  new Chart(ctx,{{
    type:'line',
    data:{{
      labels:{chart_labels},
      datasets:[{{
        label:'NVDA 順位',
        data:{chart_data},
        borderColor:'#c47c0a',backgroundColor:grad,
        borderWidth:2.5,
        pointBackgroundColor:'#c47c0a',pointBorderColor:'#ffffff',pointBorderWidth:2,
        pointRadius:5,pointHoverRadius:7,
        tension:.35,spanGaps:true,fill:true,
      }}]
    }},
    options:{{
      responsive:true,maintainAspectRatio:false,
      scales:{{
        y:{{
          reverse:true,min:1,max:10,
          ticks:{{stepSize:1,color:'#8fa3bb',callback:v=>'第'+v+'位',font:{{size:10}}}},
          grid:{{color:'#e4e9f0'}},
        }},
        x:{{
          ticks:{{color:'#8fa3bb',maxRotation:40,font:{{size:10}}}},
          grid:{{color:'#e4e9f0'}},
        }},
      }},
      plugins:{{
        legend:{{display:false}},
        tooltip:{{
          backgroundColor:'#ffffff',borderColor:'#e2e8f0',borderWidth:1,
          titleColor:'#0f172a',bodyColor:'#475569',
          callbacks:{{label:c=>c.parsed.y!=null?`  順位: 第${{c.parsed.y}}位`:'  圏外'}},
        }},
      }},
    }},
  }});
}})();

// 1位獲得回数グラフ
(function(){{
  const ctx2 = document.getElementById('no1Chart').getContext('2d');
  new Chart(ctx2,{{
    type:'bar',
    data:{{
      labels:{no1_tickers},
      datasets:[{{
        label:'1位獲得日数',
        data:{no1_counts},
        backgroundColor:{bar_colors},
        borderRadius:3,borderSkipped:false,
      }}]
    }},
    options:{{
      indexAxis:'y',
      responsive:true,maintainAspectRatio:false,
      scales:{{
        x:{{ticks:{{stepSize:1,color:'#8fa3bb',font:{{size:10}}}},grid:{{color:'#e4e9f0'}}}},
        y:{{
          ticks:{{color:'#334155',font:{{family:'"SF Mono","Fira Code",monospace',weight:'bold',size:11}}}},
          grid:{{display:false}},
        }},
      }},
      plugins:{{
        legend:{{display:false}},
        tooltip:{{
          backgroundColor:'#ffffff',borderColor:'#e2e8f0',borderWidth:1,
          titleColor:'#0f172a',bodyColor:'#475569',
          callbacks:{{label:c=>`  ${{c.parsed.x}}日 1位獲得`}},
        }},
      }},
    }},
  }});
}})();
</script>
</body>
</html>
"""


# ── メイン ────────────────────────────────────────────────────────
def main():
    today = datetime.now().strftime("%Y-%m-%d")

    print("=== US株 売買代金ランキング ===")
    print("Yahoo Finance US API からデータ取得中...")

    try:
        stocks = fetch_rankings()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nTop {len(stocks)} (売買代金順):")
    records = []
    for i, s in enumerate(stocks, 1):
        mark = " ← ★" if s["symbol"] == TARGET else ""
        print(f"  #{i:2d} {s['symbol']:<6s} {s['name']:<30s} {fmt_vol(s['dollar_vol'])}{mark}")
        records.append(to_record(i, s))

    # JSON に蓄積保存
    all_data = load_data()
    all_data[today] = records
    save_data(all_data)
    print(f"\n💾 保存: {DATA_FILE}")

    # HTML 生成
    html = generate_html(today, records, all_data)
    HTML_FILE.write_text(html, encoding="utf-8")
    print(f"🌐 生成: {HTML_FILE}")
    print(f"\n→ ブラウザで開く:  open '{HTML_FILE}'")


if __name__ == "__main__":
    main()
