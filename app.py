# -*- coding: utf-8 -*-
"""
止跌企稳信号追踪器 - Web版
本地启动后浏览器访问 http://localhost:8080
"""
import json, datetime, os, sys, math, threading, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import requests as http_requests

# ============================================================
# 配置
# ============================================================
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
SIGNAL_THRESHOLD = 3

DEFAULT_STOCKS = {
    "300916": {"name": "朗特智能", "secid": "0.300916", "exchange": "SZ", "support": 0, "cost_hint": None, "group": "默认"},
    "300413": {"name": "芒果超媒", "secid": "0.300413", "exchange": "SZ", "support": 0, "cost_hint": None, "group": "默认"},
    "00700":  {"name": "腾讯控股", "secid": "116.00700", "exchange": "HK", "support": 0, "cost_hint": None, "group": "默认"},
    "601888": {"name": "中国中免", "secid": "1.601888", "exchange": "SH", "support": 0, "cost_hint": None, "group": "默认"},
    "002129": {"name": "TCL中环", "secid": "0.002129", "exchange": "SZ", "support": 0, "cost_hint": None, "group": "默认"},
    "000062": {"name": "深圳华强", "secid": "0.000062", "exchange": "SZ", "support": 0, "cost_hint": None, "group": "默认"},
    "301286": {"name": "侨源股份", "secid": "0.301286", "exchange": "SZ", "support": 0, "cost_hint": None, "group": "默认"},
}

def detect_exchange(code):
    if len(code) == 5 and code.isdigit():
        return "HK", f"116.{code}"
    elif code.startswith("6"):
        return "SH", f"1.{code}"
    elif code.startswith(("0", "3")):
        return "SZ", f"0.{code}"
    return "SZ", f"0.{code}"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            stocks = data.get("stocks", DEFAULT_STOCKS)
            # 兼容旧配置：补 group 字段
            for code, cfg in stocks.items():
                if "group" not in cfg:
                    cfg["group"] = "默认"
            return stocks
        except: pass
    return DEFAULT_STOCKS.copy()

def save_config(stocks):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"stocks": stocks}, f, ensure_ascii=False, indent=2)

def get_groups(stocks):
    """获取所有分组及其股票数"""
    groups = {}
    for code, cfg in stocks.items():
        g = cfg.get("group", "默认")
        if g not in groups:
            groups[g] = []
        groups[g].append(code)
    return groups

# ============================================================
# 技术指标
# ============================================================
def calc_ma(data, period):
    if len(data) < period: return None
    return sum(data[-period:]) / period

def calc_ema(data, period):
    ema = [data[0]]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        ema.append(data[i] * k + ema[-1] * (1 - k))
    return ema

def calc_macd(closes):
    if len(closes) < 26: return None, None, None
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(closes))]
    dea = calc_ema(dif, 9)
    macd = [(dif[i] - dea[i]) * 2 for i in range(len(closes))]
    return dif, dea, macd

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    rsi_series = [None] * period
    for i in range(period, len(closes)):
        gains, losses = [], []
        for j in range(i - period + 1, i + 1):
            change = closes[j] - closes[j-1]
            if change > 0: gains.append(change)
            else: losses.append(abs(change))
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0.001
        rs = avg_gain / avg_loss
        rsi_series.append(100 - 100 / (1 + rs))
    return rsi_series

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period: return None
    return sum(trs[-period:]) / period

# ============================================================
# 数据获取
# ============================================================
def fetch_kline_a(secid, days=150):
    parts = secid.split(".")
    symbol = f"sz{parts[1]}" if parts[0] == "0" else f"sh{parts[1]}"
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=5&datalen={days}"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = http_requests.get(url, headers=headers, timeout=15)
        return [{"date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
                 "high": float(d["high"]), "low": float(d["low"]), "volume": int(d["volume"])} for d in resp.json()]
    except Exception as e:
        print(f"  [错误] A股K线失败: {e}")
        return []

def fetch_kline_hk(code, days=150):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk{code},day,,,{days},qfq"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = http_requests.get(url, headers=headers, timeout=15)
        data = resp.json()
        stock_data = data.get("data", {}).get(f"hk{code}", {})
        klines = stock_data.get("qfqday", stock_data.get("day", []))
        return [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
                 "high": float(k[3]), "low": float(k[4]), "volume": int(float(k[5]))} for k in klines]
    except Exception as e:
        print(f"  [错误] 港股K线失败: {e}")
        return []

def fetch_kline(secid, days=150):
    parts = secid.split(".")
    if parts[0] == "116": return fetch_kline_hk(parts[1], days)
    return fetch_kline_a(secid, days)

# ============================================================
# 分析逻辑
# ============================================================
def analyze_stock(code, config):
    name = config["name"]
    secid = config["secid"]
    support = config["support"]

    klines = fetch_kline(secid)
    if not klines or len(klines) < 30:
        return None

    closes = [k["close"] for k in klines]
    volumes = [k["volume"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    latest = klines[-1]
    price = latest["close"]
    date = latest["date"]

    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    dif, dea, macd = calc_macd(closes)
    rsi_series = calc_rsi(closes, 14)
    rsi = rsi_series[-1] if rsi_series else None
    rsi_prev = rsi_series[-2] if rsi_series and len(rsi_series) > 1 else None
    atr = calc_atr(highs, lows, closes, 14)
    vol_ma5 = calc_ma(volumes, 5)

    # ---- 自动计算支撑/压力 ----
    low_20 = min(lows[-20:])
    low_60 = min(lows[-60:]) if len(lows) >= 60 else low_20
    high_20 = max(highs[-20:])
    support_auto = low_20  # 近20日低点作为近期支撑
    support_key = ma60 if ma60 and ma60 < price else low_60  # MA60或60日低点作为关键支撑

    r = {
        "code": code, "name": name, "date": date, "price": price,
        "support_auto": support_auto, "support_key": support_key,
        "cost_hint": config.get("cost_hint"),
        "ma": {"5": ma5, "10": ma10, "20": ma20, "60": ma60},
        "rsi": rsi, "rsi_prev": rsi_prev, "atr": atr,
        "volume_ratio": volumes[-1] / vol_ma5 if vol_ma5 else 0,
        "conditions": {}, "scores": {}, "distances": {}, "estimates": {},
        "operation": "", "composite_score": 0,
    }

    if dif and dea:
        r["dif"] = dif[-1]; r["dea"] = dea[-1]; r["macd_hist"] = macd[-1]

    # 统一规则：评分 >= 60 = 满足，< 60 = 未满足
    # 条件1: MACD金叉（DIF在DEA上方且DIF上升）
    if dif and dea and len(dif) >= 3:
        dif_val, dea_val = dif[-1], dea[-1]
        gap = dif_val - dea_val
        gap_prev = dif[-2] - dea[-2]
        dif_rising = dif_val > dif[-2]

        if gap > 0 and gap_prev <= 0: score1 = 95  # 刚金叉
        elif gap > 0 and dif_rising: score1 = min(100, 70 + gap / abs(dea_val) * 30) if dea_val != 0 else 80
        elif gap > 0: score1 = 55  # DIF>DEA但DIF在降，临界
        elif dif_rising: score1 = 35  # DIF<DEA但在上升
        else:
            convergence_rate = gap_prev - gap
            score1 = max(0, (1 - abs(gap) / abs(dea_val)) * 35) if dea_val != 0 else 0
            if convergence_rate > 0 and abs(gap) > 0:
                est = abs(gap) / convergence_rate
                if est < 200: r["estimates"]["macd_cross"] = f"约{int(est)}天"
            elif convergence_rate < 0:
                r["estimates"]["macd_cross"] = "趋势发散中"

        r["scores"]["macd_golden_cross"] = score1
        r["distances"]["macd_golden_cross"] = f"DIF-DEA={gap:.4f}"
    else:
        r["scores"]["macd_golden_cross"] = 0
        r["distances"]["macd_golden_cross"] = "数据不足"

    # 条件2: RSI回升（RSI>30且在上升通道）
    if rsi is not None:
        rsi_rising = rsi_prev is not None and rsi > rsi_prev
        if rsi > 50 and rsi_rising: score2 = min(100, 70 + (rsi - 50))
        elif rsi > 40 and rsi_rising: score2 = 60 + (rsi - 40)
        elif rsi > 30 and rsi_rising: score2 = 50 + (rsi - 30)
        elif rsi > 50: score2 = 55  # 高位但下行
        elif rsi > 40: score2 = 40
        elif rsi > 30: score2 = 30
        elif rsi > 20: score2 = 15 + (rsi - 20) * 1.5
        else: score2 = rsi * 0.75

        if rsi_prev and rsi_rising and rsi < 30:
            rate = rsi - rsi_prev
            if rate > 0:
                d = (30 - rsi) / rate
                if d < 100: r["estimates"]["rsi_recover"] = f"约{int(d)}天回到30上方"
        elif rsi_prev and not rsi_rising:
            r["estimates"]["rsi_recover"] = "RSI仍在下行"

        r["scores"]["rsi_recovering"] = score2
        r["distances"]["rsi_recovering"] = f"RSI={rsi:.1f} ({'上行' if rsi_rising else '下行'})"
    else:
        r["scores"]["rsi_recovering"] = 0
        r["distances"]["rsi_recovering"] = "数据不足"

    # 条件3: 站上MA20
    if ma20:
        bias = (price - ma20) / ma20 * 100
        if bias > 0: score3 = min(100, 60 + bias * 5)
        elif bias > -3: score3 = 40 + (bias + 3) * 6.67
        else: score3 = max(0, 20 + (bias + 10) * 2.86)
        r["scores"]["above_ma20"] = score3
        r["distances"]["above_ma20"] = f"乖离率{bias:+.1f}%"
    else:
        r["scores"]["above_ma20"] = 0
        r["distances"]["above_ma20"] = "数据不足"

    # 条件4: 底部放量（量比>1.5）
    if vol_ma5:
        vol_ratio = volumes[-1] / vol_ma5
        if vol_ratio > 2.0: score4 = 90
        elif vol_ratio > 1.5: score4 = 70 + (vol_ratio - 1.5) * 40
        elif vol_ratio > 1.0: score4 = 40 + (vol_ratio - 1.0) * 40
        elif vol_ratio > 0.7: score4 = 25 + (vol_ratio - 0.7) * 50
        else: score4 = 15
        r["scores"]["volume_surge"] = score4
        r["distances"]["volume_surge"] = f"量比={vol_ratio:.2f}"
    else:
        r["scores"]["volume_surge"] = 0
        r["distances"]["volume_surge"] = "数据不足"

    # 条件5: 均线拐头（MA5上穿MA10 或 MA20+MA5同时拐头）
    if ma5 and ma10 and ma20 and len(closes) >= 22:
        ma5_prev = calc_ma(closes[:-1], 5)
        ma10_prev = calc_ma(closes[:-1], 10)
        ma20_prev = calc_ma(closes[:-1], 20)
        ma5_cross = ma5 > ma10 and (ma5_prev and ma10_prev and ma5_prev <= ma10_prev)
        ma20_turning = ma20_prev is not None and ma20 > ma20_prev
        ma5_turning = ma5_prev is not None and ma5 > ma5_prev

        if ma5_cross: score5 = 90
        elif ma20_turning and ma5_turning: score5 = 75
        elif ma5_turning: score5 = 45
        elif ma20_turning: score5 = 35
        else: score5 = 15

        desc_parts = []
        if ma5_cross: desc_parts.append("MA5上穿MA10")
        if ma20_turning: desc_parts.append("MA20拐头")
        if ma5_turning: desc_parts.append("MA5拐头")
        if not desc_parts: desc_parts.append("均线持续下行")

        r["scores"]["ma_turning"] = score5
        r["distances"]["ma_turning"] = ", ".join(desc_parts)
    else:
        r["scores"]["ma_turning"] = 0
        r["distances"]["ma_turning"] = "数据不足"

    # 统一判定：评分 >= 60 = 满足
    for k in ["macd_golden_cross", "rsi_recovering", "above_ma20", "volume_surge", "ma_turning"]:
        r["conditions"][k] = r["scores"][k] >= 60

    # 综合评分
    weights = {"macd_golden_cross": 0.25, "rsi_recovering": 0.20,
               "above_ma20": 0.25, "volume_surge": 0.15, "ma_turning": 0.15}
    r["composite_score"] = sum(r["scores"][k] * weights[k] for k in weights)
    r["signal_count"] = sum(1 for v in r["conditions"].values() if v)
    r["triggered"] = r["signal_count"] >= SIGNAL_THRESHOLD

    # 操作建议（基于数据推导的支撑/压力，不依赖人工输入）
    ops = []
    if r["triggered"]: ops.append("多条件共振，可考虑分批建仓")
    elif r["composite_score"] >= 60: ops.append("信号接近触发，密切关注")
    elif r["composite_score"] >= 40: ops.append("信号尚远，耐心等待")
    else: ops.append("空头格局明确，不宜介入")

    # ATR止损/目标
    if atr:
        ops.append(f"ATR(14)={atr:.2f}，止损参考{price-2*atr:.2f}，短期目标{price+1.5*atr:.2f}")

    # 自动推导的支撑/压力
    ops.append(f"近期支撑: {support_auto:.2f} (20日低点)")
    if support_key != support_auto:
        ops.append(f"关键支撑: {support_key:.2f}" + (" (MA60)" if ma60 and ma60 < price else " (60日低点)"))
    if ma20:
        ops.append(f"MA20压力: {ma20:.2f}")
    if ma60 and price < ma60:
        ops.append(f"MA60压力: {ma60:.2f}")

    # 持仓成本盈亏
    cost = config.get("cost_hint")
    if cost:
        pnl = (price - cost) / cost * 100
        ops.append(f"持仓成本{cost:.2f}，当前{'盈利' if pnl > 0 else '亏损'}{abs(pnl):.1f}%")

    r["operation"] = ops

    # ---- 短期预测 ----
    pred = {}
    # 趋势方向
    bull_signals = 0
    bear_signals = 0
    if dif and dea:
        if dif[-1] > dea[-1]: bull_signals += 1
        else: bear_signals += 1
        if len(dif) >= 2 and dif[-1] > dif[-2]: bull_signals += 1
        else: bear_signals += 1
    if rsi is not None:
        if rsi > 50: bull_signals += 1
        elif rsi < 30: bear_signals += 1
        elif rsi > 40: bull_signals += 0.5
        else: bear_signals += 0.5
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20: bull_signals += 2
        elif ma5 < ma10 < ma20: bear_signals += 2
        elif ma5 > ma10: bull_signals += 1
        elif ma5 < ma10: bear_signals += 1
    if price > (ma20 or 0): bull_signals += 1
    else: bear_signals += 1

    if bull_signals >= bear_signals + 3:
        pred["trend"] = "偏多"
        pred["trend_desc"] = "多项技术指标共振偏多，短期上行概率较大"
    elif bear_signals >= bull_signals + 3:
        pred["trend"] = "偏空"
        pred["trend_desc"] = "空头信号占优，短期仍有下行压力"
    elif bull_signals > bear_signals:
        pred["trend"] = "弱多"
        pred["trend_desc"] = "部分指标转好但未形成合力，震荡偏强"
    elif bear_signals > bull_signals:
        pred["trend"] = "弱空"
        pred["trend_desc"] = "偏弱运行，反弹力度有限"
    else:
        pred["trend"] = "震荡"
        pred["trend_desc"] = "多空交织，短期维持区间震荡"

    # 价格区间预测（基于ATR）
    if atr:
        pred["range_low"] = round(price - 1.5 * atr, 2)
        pred["range_high"] = round(price + 1.5 * atr, 2)
        pred["range_days"] = "3-5日"

    # 关键价位
    pred["support"] = round(support_auto, 2)
    if support_key != support_auto:
        pred["support_key"] = round(support_key, 2)
    resistance = []
    if ma20 and ma20 > price: resistance.append(round(ma20, 2))
    if ma60 and ma60 > price: resistance.append(round(ma60, 2))
    if high_20 > price: resistance.append(round(high_20, 2))
    if resistance:
        pred["resistance"] = sorted(set(resistance))[:3]

    r["prediction"] = pred
    return r

# ============================================================
# HTML页面
# ============================================================
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>止跌企稳信号追踪器</title>
<style>
:root {
  --bg: #f5f5f7; --card: #fff; --text: #1d1d1f; --text2: #6e6e73; --text3: #aeaeb2;
  --border: #d2d2d7; --border-light: #e8e8ed; --accent: #0071e3; --accent-hover: #0077ed;
  --green: #34c759; --red: #ff3b30; --orange: #ff9500; --track: #e8e8ed;
  --input-bg: #fff; --input-border: #d2d2d7; --shadow: 0 1px 3px rgba(0,0,0,0.06);
  --tag-bg: #f0f0f5; --tag-border: #d2d2d7; --ops-bg: #f9f9fb;
}
@media(prefers-color-scheme:dark){:root{
  --bg: #000; --card: #1c1c1e; --text: #f5f5f7; --text2: #98989d; --text3: #636366;
  --border: #38383a; --border-light: #2c2c2e; --accent: #0a84ff; --accent-hover: #409cff;
  --green: #30d158; --red: #ff453a; --orange: #ff9f0a; --track: #38383a;
  --input-bg: #2c2c2e; --input-border: #38383a; --shadow: 0 1px 3px rgba(0,0,0,0.3);
  --tag-bg: #2c2c2e; --tag-border: #38383a; --ops-bg: #252528;
}}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"SF Pro Display","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;-webkit-font-smoothing:antialiased}
.container{max-width:960px;margin:0 auto;padding:24px 20px}
h1{text-align:center;font-size:1.5em;font-weight:700;padding:20px 0 4px;letter-spacing:0.5px}
.subtitle{text-align:center;color:var(--text3);font-size:0.82em;margin-bottom:28px}
.action-bar{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:12px}
.action-bar input,.action-bar select{background:var(--input-bg);border:1px solid var(--input-border);color:var(--text);padding:8px 12px;border-radius:8px;font-size:13px;width:128px;outline:none;transition:border-color 0.15s}
.action-bar input::placeholder{color:var(--text3)}
.action-bar input:focus,.action-bar select:focus{border-color:var(--accent)}
.btn{padding:8px 18px;border:none;border-radius:8px;font-size:13px;cursor:pointer;font-weight:500;transition:all 0.15s}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover{background:var(--accent-hover)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text2)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm{padding:5px 12px;font-size:12px;border-radius:6px}
.group-tabs{display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin-bottom:20px}
.group-tab{background:var(--tag-bg);border:1px solid var(--tag-border);padding:5px 14px;border-radius:14px;font-size:12.5px;cursor:pointer;color:var(--text2);transition:all 0.15s;user-select:none}
.group-tab:hover{border-color:var(--accent);color:var(--accent)}
.group-tab.active{background:var(--accent);border-color:var(--accent);color:#fff}
.group-tab .count{font-size:10.5px;opacity:0.6;margin-left:3px}
.group-mgr{display:flex;gap:8px;justify-content:center;align-items:center;margin-bottom:20px}
.group-mgr input{background:var(--input-bg);border:1px solid var(--input-border);color:var(--text);padding:5px 10px;border-radius:6px;font-size:12.5px;width:110px;outline:none}
.group-mgr input:focus{border-color:var(--accent)}
.cards{display:grid;grid-template-columns:1fr;gap:14px}
.card{background:var(--card);border:1px solid var(--border-light);border-radius:12px;padding:20px;position:relative;box-shadow:var(--shadow);transition:border-color 0.2s}
.card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;gap:8px}
.card-header-left{flex:1;min-width:0}
.card-title{font-size:1.05em;font-weight:600}
.card-meta{display:flex;align-items:center;gap:8px;margin-top:4px}
.card-date{font-size:0.78em;color:var(--text3)}
.card-group{font-size:0.7em;background:var(--tag-bg);border:1px solid var(--tag-border);padding:2px 8px;border-radius:8px;color:var(--accent);white-space:nowrap}
.card-actions{display:flex;gap:4px;align-items:center;flex-shrink:0}
.card-actions select{background:var(--input-bg);border:1px solid var(--input-border);color:var(--text2);padding:3px 6px;border-radius:6px;font-size:10.5px;outline:none}
.card-actions .del{background:none;border:none;color:var(--text3);cursor:pointer;font-size:13px;padding:2px 6px;border-radius:4px;line-height:1}
.card-actions .del:hover{color:var(--red);background:rgba(255,59,48,0.08)}
.score-bar{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.score-track{flex:1;height:6px;background:var(--track);border-radius:3px;overflow:hidden}
.score-fill{height:100%;border-radius:3px;transition:width 0.5s ease}
.score-val{font-size:1.3em;font-weight:700;min-width:50px;text-align:right;font-variant-numeric:tabular-nums}
.score-label{font-size:0.82em;color:var(--text2);min-width:60px}
.signal-info{font-size:0.78em;color:var(--text3);margin-bottom:14px}
.cond-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:16px}
.cond-item{background:var(--ops-bg);border:1px solid var(--border-light);border-radius:8px;padding:10px;text-align:center;transition:border-color 0.15s}
.cond-item.met{border-color:var(--green);background:rgba(52,199,89,0.05)}
.cond-item .cond-name{font-size:0.75em;color:var(--text3);margin-bottom:4px}
.cond-item .cond-status{font-size:0.8em;font-weight:600;margin-bottom:2px}
.cond-item.met .cond-status{color:var(--green)}
.cond-item:not(.met) .cond-status{color:var(--text3)}
.cond-item .cond-score{font-size:0.9em;font-weight:700;font-variant-numeric:tabular-nums}
.cond-item .cond-detail{font-size:0.7em;color:var(--text3);margin-top:3px;min-height:1em}
.ops{background:var(--ops-bg);border:1px solid var(--border-light);border-radius:8px;padding:12px 16px;margin-bottom:12px}
.ops-title{font-size:0.78em;color:var(--text3);margin-bottom:6px;font-weight:500}
.ops li{list-style:none;padding:3px 0;font-size:0.82em;color:var(--text2)}
.ops li::before{content:"› ";color:var(--accent);font-weight:600}
.metrics{display:flex;gap:16px;flex-wrap:wrap}
.metric{font-size:0.78em;color:var(--text3)}
.metric span{color:var(--text2);font-weight:500}
.pred{background:var(--ops-bg);border:1px solid var(--border-light);border-radius:8px;padding:12px 16px;margin-bottom:12px}
.pred-title{font-size:0.78em;color:var(--text3);margin-bottom:8px;font-weight:500}
.pred-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:0.82em}
.pred-label{color:var(--text3);min-width:56px}
.pred-trend{font-weight:700;font-size:0.95em;padding:1px 8px;border-radius:4px}
.pred-trend.bull{color:var(--green);background:rgba(52,199,89,0.08)}
.pred-trend.bear{color:var(--red);background:rgba(255,59,48,0.08)}
.pred-trend.neutral{color:var(--orange);background:rgba(255,149,0,0.08)}
.pred-desc{font-size:0.78em;color:var(--text2);margin-top:4px;line-height:1.5}
.pred-levels{display:flex;gap:12px;flex-wrap:wrap;margin-top:6px;font-size:0.78em}
.pred-levels .lv{color:var(--text3)}
.pred-levels .lv span{color:var(--text2);font-weight:500}
.summary-card{margin-top:16px}
.summary-card .card-title{margin-bottom:14px}
.summary-table{width:100%;border-collapse:collapse;font-size:0.85em}
.summary-table th{text-align:left;color:var(--text3);font-weight:500;padding:8px 10px;border-bottom:1px solid var(--border-light);font-size:0.8em}
.summary-table td{padding:9px 10px;border-bottom:1px solid var(--border-light)}
.summary-table tr:last-child td{border-bottom:none}
.loading{text-align:center;padding:80px 20px;color:var(--text3)}
.spinner{display:inline-block;width:22px;height:22px;border:2px solid var(--border);border-top:2px solid var(--accent);border-radius:50%;animation:spin 0.7s linear infinite;margin-bottom:10px}
@keyframes spin{to{transform:rotate(360deg)}}
.triggered{border-color:var(--green)!important;box-shadow:0 0 0 1px var(--green),0 2px 12px rgba(52,199,89,0.1)}
.triggered .card-title::after{content:" 信号触发";color:var(--green);font-size:0.72em;font-weight:500;margin-left:6px}
.logic-section{margin-top:32px;border:1px solid var(--border-light);border-radius:10px;padding:0;overflow:hidden}
.logic-section summary{padding:14px 18px;font-size:0.85em;color:var(--text2);cursor:pointer;user-select:none;font-weight:500}
.logic-section summary:hover{color:var(--accent)}
.logic-section[open] summary{border-bottom:1px solid var(--border-light)}
.logic-body{padding:16px 18px;font-size:0.8em;color:var(--text2);line-height:1.7}
.logic-body b{color:var(--text)}
.logic-table{width:100%;border-collapse:collapse;margin:12px 0;font-size:0.9em}
.logic-table th{text-align:left;color:var(--text3);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--border-light);font-size:0.85em}
.logic-table td{padding:7px 8px;border-bottom:1px solid var(--border-light)}
.logic-table tr:last-child td{border-bottom:none}
.logic-note{margin-top:10px;color:var(--text3);font-size:0.85em}
@media(max-width:700px){
  .cond-grid{grid-template-columns:repeat(2,1fr)}
  .action-bar{flex-direction:column;align-items:stretch}
  .action-bar input,.action-bar select{width:100%}
  .card-header{flex-direction:column}
  .card-meta{align-self:flex-start}
  .logic-table{font-size:0.8em}
  .logic-table th,.logic-table td{padding:5px 4px}
}
@media(max-width:400px){.cond-grid{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="container">
  <h1>止跌企稳信号追踪器</h1>
  <p class="subtitle">A股 / 港股 · 多维度技术信号 · 综合评分 · 操作建议</p>
  <div class="action-bar">
    <input id="codeInput" placeholder="代码 如 600519" />
    <input id="nameInput" placeholder="名称 如 贵州茅台" />
    <input id="costInput" placeholder="成本（可选）" type="number" step="0.01" />
    <select id="groupSelect"></select>
    <button class="btn btn-primary" onclick="addStock()">添加</button>
    <button class="btn btn-ghost" onclick="runAnalysis()">刷新</button>
  </div>
  <div class="group-mgr">
    <input id="newGroupInput" placeholder="新分组名" />
    <button class="btn btn-ghost btn-sm" onclick="addGroup()">新建分组</button>
  </div>
  <div class="group-tabs" id="groupTabs"></div>
  <div id="content"><div class="loading"><div class="spinner"></div><br>正在加载...</div></div>
  <details class="logic-section">
    <summary>评分逻辑说明</summary>
    <div class="logic-body">
      <p><b>统一规则：单项评分 ≥ 60 = 满足，< 60 = 未满足</b>，不存在评分与状态矛盾的情况。</p>
      <table class="logic-table">
        <tr><th>条件</th><th>评分逻辑</th><th>权重</th></tr>
        <tr><td>MACD金叉</td><td>DIF在DEA上方且DIF上升 → 70-95分；DIF>DEA但下降 → 55分；DIF<DEA → 0-35分</td><td>25%</td></tr>
        <tr><td>RSI回升</td><td>RSI>40且上升 → 60-100分；RSI>30且上升 → 50-60分；高位下行 → 55分；低位 → 0-40分</td><td>20%</td></tr>
        <tr><td>站上MA20</td><td>价格在MA20上方 → 60-100分（按乖离率）；下方 → 0-55分</td><td>25%</td></tr>
        <tr><td>底部放量</td><td>量比>1.5 → 70-90分；量比1.0-1.5 → 40-70分；缩量 → 0-35分</td><td>15%</td></tr>
        <tr><td>均线拐头</td><td>MA5上穿MA10 → 90分；MA20+MA5同时拐头 → 75分；仅MA5拐头 → 45分；下行 → 15分</td><td>15%</td></tr>
      </table>
      <p class="logic-note">综合评分 = 各项评分 × 权重之和。满足 3 个及以上条件触发"信号触发"。预测基于多指标趋势推导，仅供参考，不构成投资建议。</p>
    </div>
  </details>
</div>
<script>
let stocks={};let allResults=[];let currentGroup='全部';
const SIGNAL_THRESH=3;
async function api(p,q){return(await fetch('/api/'+p+'?'+new URLSearchParams(q))).json()}
function getGroups(){const g={};for(const[c,x]of Object.entries(stocks)){const k=x.group||'默认';if(!g[k])g[k]=[];g[k].push(c)}return g}
async function loadStocks(){stocks=await api('stocks');renderGroupTabs();renderGroupSelect()}
function renderGroupTabs(){const g=getGroups(),ns=Object.keys(g),t=Object.keys(stocks).length;let h=`<div class="group-tab${currentGroup==='全部'?' active':''}" onclick="switchGroup('全部')">全部<span class="count">${t}</span></div>`;for(const n of ns)h+=`<div class="group-tab${currentGroup===n?' active':''}" onclick="switchGroup('${n}')">${n}<span class="count">${g[n].length}</span></div>`;document.getElementById('groupTabs').innerHTML=h}
function renderGroupSelect(){const g=getGroups(),s=document.getElementById('groupSelect');s.innerHTML=Object.keys(g).map(x=>`<option value="${x}">${x}</option>`).join('')}
function switchGroup(n){currentGroup=n;renderGroupTabs();renderResults()}
async function addGroup(){const n=document.getElementById('newGroupInput').value.trim();if(!n)return;const s=document.getElementById('groupSelect');if(![...s.options].some(o=>o.value===n)){const o=document.createElement('option');o.value=n;o.text=n;s.add(o)}document.getElementById('newGroupInput').value='';renderGroupTabs()}
async function addStock(){const c=document.getElementById('codeInput').value.trim(),n=document.getElementById('nameInput').value.trim(),$=document.getElementById('costInput').value.trim(),g=document.getElementById('groupSelect').value||'默认';if(!c||!n){alert('请填写股票代码和名称');return}await api('add',{code:c,name:n,cost:$,group:g});document.getElementById('codeInput').value='';document.getElementById('nameInput').value='';document.getElementById('costInput').value='';await loadStocks();runAnalysis()}
async function removeStock(c){if(!confirm(`确认删除 ${stocks[c]?.name||c}？`))return;await api('remove',{code:c});await loadStocks();runAnalysis()}
async function moveStock(c,g){await api('move',{code:c,group:g});await loadStocks();runAnalysis()}
async function runAnalysis(){const el=document.getElementById('content');el.innerHTML='<div class="loading"><div class="spinner"></div><br>正在分析全部自选股...</div>';allResults=await api('analyze_all');renderResults()}
function renderResults(){const el=document.getElementById('content');let rs=allResults.filter(Boolean);if(currentGroup!=='全部')rs=rs.filter(r=>r.group===currentGroup);rs.sort((a,b)=>b.composite_score-a.composite_score);let h='<div class="cards">';rs.forEach(r=>{h+=renderCard(r)});h+='</div>'+renderSummary(rs);el.innerHTML=h}
function scColor(s){return s>=70?'var(--green)':s>=55?'var(--accent)':s>=40?'var(--orange)':'var(--red)'}
function scLabel(s){return s>=70?'强烈关注':s>=55?'接近信号':s>=40?'继续等待':'回避观望'}
function renderCard(r){
  const c=scColor(r.composite_score),l=scLabel(r.composite_score);
  const cl={macd_golden_cross:'MACD金叉',rsi_recovering:'RSI回升',above_ma20:'站上MA20',volume_surge:'底部放量',ma_turning:'均线拐头'};
  let ci='';
  for(const[k,lb]of Object.entries(cl)){
    const met=r.conditions[k],s=r.scores[k],d=r.distances[k]||'',e=r.estimates[k]||'';
    ci+=`<div class="cond-item${met?' met':''}"><div class="cond-name">${lb}</div><div class="cond-status">${met?'已满足':'未满足'}</div><div class="cond-score" style="color:${scColor(s)}">${s?.toFixed(0)||0}</div><div class="cond-detail">${d}${d&&e?' · ':''}${e}</div></div>`;
  }
  let ops=r.operation.map(o=>`<li>${o}</li>`).join('');
  let m='';
  if(r.atr)m+=`<div class="metric">ATR(14): <span>${r.atr.toFixed(2)}</span></div>`;
  if(r.ma){const ma=r.ma;let st='交织';if(ma['5']&&ma['10']&&ma['20']){if(ma['5']<ma['10']&&ma['10']<ma['20'])st='空头排列';else if(ma['5']>ma['10']&&ma['10']>ma['20'])st='多头排列'}m+=`<div class="metric">均线: <span>${st}</span></div>`}
  m+=`<div class="metric">量比: <span>${r.volume_ratio?.toFixed(2)||'—'}</span></div>`;
  // 预测
  let pred='';
  if(r.prediction){
    const p=r.prediction;
    let tc='neutral';if(p.trend.includes('多'))tc='bull';else if(p.trend.includes('空'))tc='bear';
    pred+=`<div class="pred"><div class="pred-title">短期预测</div>`;
    pred+=`<div class="pred-row"><div class="pred-label">趋势</div><span class="pred-trend ${tc}">${p.trend}</span></div>`;
    if(p.trend_desc)pred+=`<div class="pred-desc">${p.trend_desc}</div>`;
    if(p.range_low!==undefined)pred+=`<div class="pred-row"><div class="pred-label">${p.range_days||'3-5日'}</div><span>${p.range_low} ~ ${p.range_high}</span></div>`;
    let levels='';
    levels+=`<div class="lv">支撑 <span>${p.support}</span></div>`;
    if(p.support_key)levels+=`<div class="lv">关键支撑 <span>${p.support_key}</span></div>`;
    if(p.resistance&&p.resistance.length)levels+=`<div class="lv">压力 <span>${p.resistance.join(' / ')}</span></div>`;
    if(levels)pred+=`<div class="pred-levels">${levels}</div>`;
    pred+=`</div>`;
  }
  const gs=Object.keys(getGroups());let go=gs.map(g=>`<option value="${g}"${g===r.group?' selected':''}>${g}</option>`).join('');
  return`<div class="card${r.triggered?' triggered':''}"><div class="card-header"><div class="card-header-left"><div class="card-title">${r.name}（${r.code}）</div><div class="card-meta"><span class="card-group">${r.group||'默认'}</span><span class="card-date">${r.date} · ${r.price.toFixed(2)}</span></div></div><div class="card-actions"><select onchange="moveStock('${r.code}',this.value)">${go}</select><button class="del" onclick="removeStock('${r.code}')" title="删除">✕</button></div></div><div class="score-bar"><div class="score-label">${l}</div><div class="score-track"><div class="score-fill" style="width:${r.composite_score}%;background:${c}"></div></div><div class="score-val" style="color:${c}">${r.composite_score.toFixed(0)}</div></div><div class="signal-info">信号满足: ${r.signal_count}/5 · 触发阈值: ${SIGNAL_THRESH}</div><div class="cond-grid">${ci}</div>${pred}<div class="ops"><div class="ops-title">操作建议</div><ul>${ops}</ul></div><div class="metrics">${m}</div></div>`;
}
function renderSummary(rs){
  if(!rs.length)return'';
  let rows=rs.map(r=>{let s='回避';if(r.triggered)s='触发!';else if(r.composite_score>=55)s='接近';else if(r.composite_score>=40)s='等待';return`<tr><td>${r.name}</td><td>${r.group||'默认'}</td><td>${r.price.toFixed(2)}</td><td style="color:${scColor(r.composite_score)};font-weight:600">${r.composite_score.toFixed(0)}</td><td>${r.signal_count}/5</td><td style="color:${scColor(r.composite_score)}">${s}</td></tr>`}).join('');
  return`<div class="card summary-card"><div class="card-title">信号汇总</div><table class="summary-table"><thead><tr><th>股票</th><th>分组</th><th>价格</th><th>评分</th><th>信号</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
loadStocks().then(()=>runAnalysis());
</script>
</body>
</html>'''

# ============================================================
# HTTP API
# ============================================================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass  # 静默日志

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/':
            self._html(HTML_PAGE)

        elif path == '/api/stocks':
            self._json(load_config())

        elif path == '/api/add':
            stocks = load_config()
            code = params.get('code', [''])[0]
            name = params.get('name', [''])[0]
            cost = params.get('cost', [''])[0]
            group = params.get('group', ['默认'])[0]
            exchange, secid = detect_exchange(code)
            stocks[code] = {"name": name, "secid": secid, "exchange": exchange,
                           "support": 0, "cost_hint": float(cost) if cost else None, "group": group}
            save_config(stocks)
            self._json({"ok": True, "name": name})

        elif path == '/api/remove':
            stocks = load_config()
            code = params.get('code', [''])[0]
            if code in stocks:
                del stocks[code]
                save_config(stocks)
            self._json({"ok": True})

        elif path == '/api/move':
            stocks = load_config()
            code = params.get('code', [''])[0]
            group = params.get('group', ['默认'])[0]
            if code in stocks:
                stocks[code]["group"] = group
                save_config(stocks)
            self._json({"ok": True})

        elif path == '/api/rename_group':
            stocks = load_config()
            old_name = params.get('old', [''])[0]
            new_name = params.get('new', [''])[0]
            for cfg in stocks.values():
                if cfg.get("group") == old_name:
                    cfg["group"] = new_name
            save_config(stocks)
            self._json({"ok": True})

        elif path == '/api/groups':
            stocks = load_config()
            self._json(get_groups(stocks))

        elif path == '/api/analyze':
            code = params.get('code', [''])[0]
            stocks = load_config()
            if code in stocks:
                r = analyze_stock(code, stocks[code])
                r["group"] = stocks[code].get("group", "默认")
                self._json(r if r else {"error": f"无法获取 {code} 数据"})
            else:
                self._json({"error": f"股票 {code} 不在自选列表中"})

        elif path == '/api/analyze_all':
            stocks = load_config()
            results = []
            for code, cfg in stocks.items():
                try:
                    r = analyze_stock(code, cfg)
                    if r:
                        r["group"] = cfg.get("group", "默认")
                    results.append(r)
                except Exception as e:
                    print(f"  [错误] {code}: {e}")
                    results.append(None)
            self._json(results)

        else:
            self.send_response(404)
            self.end_headers()

# ============================================================
# 启动
# ============================================================
def main():
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"
    server = HTTPServer((host, port), Handler)
    print(f"止跌企稳信号追踪器 | http://{host}:{port}")

    # 本地运行时自动打开浏览器
    if host == "127.0.0.1" or os.environ.get("RENDER") is None:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()

if __name__ == "__main__":
    main()
