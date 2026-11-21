#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创业板 / 中证红利 · 红利低波 监测数据更新脚本

数据源:
  1. 同花顺金融数据 API (fuyao.aicubes.cn) —— 指数日线、跟踪指数估值百分位(PE TTM 五年分位)、ETF 分红
  2. 中国货币网 (chinamoney.com.cn) —— 10 年期国债到期收益率(中债)

用法:
  FUYAO_API_KEY=xxxx python updater.py
  或把 key 写入本目录 fuyao_key.txt(仅供本地运行,不会进 git)

输出: docs/data.json
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://fuyao.aicubes.cn"
TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data.json")

# 目标基金(用于估值百分位): 跟踪指数分别为 创业板指 / 中证红利低波动指数
CYB_FUND = "159915.SZ"   # 易方达创业板ETF
HLB_FUND = "512890.SH"   # 华泰柏瑞红利低波动ETF
# 中证红利指数兜底: 若指数目录中没有"中证红利", 用中证红利ETF 日线替代
ZZHL_ETF_FALLBACK = "515080.SH"  # 招商中证红利ETF

YEARS = 5            # 指数日线最多取近 5 年(接口限制)
BOND_YEARS = 5       # 国债收益率取近 5 年
WARMUP_DAYS = 260    # 股息率(近12个月)需要约 252 个交易日的预热


def log(*a):
    print("[updater]", *a, flush=True)


def today_sh():
    return dt.datetime.now(TZ).date()


def d2ms(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp() * 1000)


def ms2d(ms) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000, TZ).date()


def http_get(url, headers=None, timeout=60, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "monitor/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            log(f"  GET 重试 {i + 1}/{retries}: {url} ({e})")
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET 失败: {url}: {last}")


def http_post(url, data, headers=None, timeout=60, retries=2):
    body = urllib.parse.urlencode(data).encode()
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers=headers or {
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/x-www-form-urlencoded",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            log(f"  POST 重试 {i + 1}/{retries}: {url} ({e})")
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"POST 失败: {url}: {last}")


def fuyao(path):
    env = http_get(BASE + path, headers={"X-api-key": API_KEY, "User-Agent": "monitor/1.0"})
    if env.get("code") != 0:
        raise RuntimeError(f"fuyao {path} -> code={env.get('code')} message={env.get('message')}")
    return env.get("data") or {}


# ---------------------------------------------------------------- 标的解析

def list_index_catalog():
    """拉全量指数目录, 返回 {名称: [thscode, ...]}"""
    all_items, offset = [], 0
    while True:
        data = fuyao(f"/api/meta/tickers/list?asset_type=a-share-index&limit=1000&offset={offset}")
        items = data.get("item") or []
        all_items.extend(items)
        offset += len(items)
        if not items or offset >= (data.get("total") or 0):
            break
    by_name = {}
    for it in all_items:
        nm = (it.get("name") or "").strip()
        if nm:
            by_name.setdefault(nm, []).append(it.get("thscode"))
    return by_name


def pick(by_name, names, prefer=None):
    for n in names:
        codes = by_name.get(n)
        if codes:
            if prefer:
                for c in codes:
                    if prefer in c:
                        return c
            return codes[0]
    return None


def resolve_indexes(by_name):
    cyb = pick(by_name, ["创业板指", "创业板指数"]) or "399006.SZ"
    zzhl = pick(by_name, ["中证红利", "中证红利指数", "中证红利价格指数"], prefer=".CSI")
    hlb_p = pick(by_name, ["红利低波", "中证红利低波动", "中证红利低波动指数", "红利低波(全)"])
    hlb_tr = pick(by_name, ["红利低波全收益", "中证红利低波动全收益", "中证红利低波动全收益指数", "红利低波全收益指数"])
    return {"cyb": cyb, "zzhl": zzhl, "hlb_price": hlb_p, "hlb_tr": hlb_tr}


# ---------------------------------------------------------------- 指数日线

def fetch_index_klines(codes):
    """codes: [thscode] -> {thscode: [(date, close), ...]}(升序)"""
    if not codes:
        return {}
    start = today_sh() - dt.timedelta(days=YEARS * 365)
    end = today_sh()
    data = fuyao("/api/a-share-index/prices/historical-batch?"
                 f"thscodes={urllib.parse.quote(','.join(codes))}&interval=1d"
                 f"&start={d2ms(start)}&end={d2ms(end)}")
    out = {}
    for grp in data.get("item") or []:
        code = grp.get("thscode")
        bars = grp.get("item") or []
        out[code] = [(ms2d(b["date_ms"]), b["close_price"]) for b in bars if b.get("close_price") is not None]
    return out


def fetch_fund_klines(code):
    """ETF 前复权日线(兜底用) -> [(date, close), ...]"""
    start = today_sh() - dt.timedelta(days=YEARS * 365)
    end = today_sh()
    data = fuyao(f"/api/fund/market/historical?thscode={code}&interval=1d"
                 f"&start={d2ms(start)}&end={d2ms(end)}")
    return [(ms2d(b["date_ms"]), b["close_price"]) for b in data.get("item") or []
            if b.get("close_price") is not None]


# ---------------------------------------------------------------- 估值百分位

def fetch_percentile(fund_code):
    """跟踪指数 PE TTM 五年分位 -> [(date, pct), ...]"""
    start = today_sh() - dt.timedelta(days=(YEARS + 1) * 365)
    end = today_sh()
    try:
        data = fuyao(f"/api/fund/performance/indicators-historical?thscode={fund_code}"
                     f"&start={d2ms(start)}&end={d2ms(end)}")
    except RuntimeError as e:
        if "2003" in str(e):
            log(f"  !! 基金指标接口无权限(2003), 估值百分位将缺失: {e}")
            return []
        raise
    out = []
    for i in data.get("item") or []:
        v = i.get("track_index_pe_ttm_five_year_percentile")
        if i.get("date_ms") and v is not None:
            out.append((ms2d(i["date_ms"]), float(v)))
    return out


# ---------------------------------------------------------------- 国债收益率

def fetch_bond10y():
    """中国货币网 10年期国债到期收益率 -> {date: yield%}
    接口单次查询范围上限约 1 年, 按 364 天分段抓取再合并。"""
    end = today_sh()
    out = {}
    chunk_start = end - dt.timedelta(days=BOND_YEARS * 365)
    while chunk_start <= end:
        chunk_end = min(chunk_start + dt.timedelta(days=364), end)
        page = 0
        while True:
            resp = http_post(
                "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/SddsIntrRateGovYldHis",
                {"startDate": chunk_start.isoformat(), "endDate": chunk_end.isoformat(),
                 "pageNum": str(page), "pageSize": "200",
                 "curveType": "YIELD_CURVE_TYPE1", "bondType": "BOND_TYPE7", "tenor": "10"})
            recs = resp.get("records") or []
            if not recs:
                break
            for r in recs:
                ds = r.get("dateString")
                v = r.get("tenRate")
                if ds and v is not None:
                    try:
                        out[dt.date.fromisoformat(ds)] = float(v)
                    except ValueError:
                        pass
            page_total = (resp.get("data") or {}).get("pageTotal") or 0
            page += 1
            if page_total and page >= page_total:
                break
            if page > 10:
                log("  !! 国债分页异常, 提前停止")
                break
        chunk_start = chunk_end + dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------- 股息率

def dividend_yield_tr(tr_series, p_series):
    """
    全收益指数/价格指数推演近12个月股息率:
    dy[t] = ( TR[t]/TR[t-252] ) / ( P[t]/P[t-252] ) - 1
    返回 [(date, dy%), ...]
    """
    tr = dict(tr_series)
    p = dict(p_series)
    dates = sorted(set(tr) & set(p))
    out = []
    for i, d in enumerate(dates):
        if i < 250:
            continue
        d0 = dates[i - 250]
        t0, t1, p0, p1 = tr[d0], tr[d], p[d0], p[d]
        if not (t0 and t1 and p0 and p1):
            continue
        dy = (t1 / t0) / (p1 / p0) - 1.0
        out.append((d, round(dy * 100.0, 4)))
    return out


def dividend_yield_fund(price_series):
    """ETF 分红推演: 近12个月每份现金分红 / 价格 -> [(date, dy%), ...]"""
    data = fuyao(f"/api/fund/corporate-actions/dividends?thscode={HLB_FUND}")
    events = []
    for it in data.get("item") or []:
        ex = it.get("ex_dividend_date_ms")
        cash = it.get("per_ten_cash_before_tax")
        if ex and cash is not None:
            events.append((ms2d(ex), float(cash) / 10.0))  # 每10份 -> 每份
    price = dict(price_series)
    dates = sorted(price)
    out = []
    for d in dates:
        window = [c for (ex, c) in events if d - dt.timedelta(days=365) < ex <= d]
        if not window or not price[d]:
            continue
        dy = sum(window) / price[d] * 100.0
        out.append((d, round(dy, 4)))
    return out


# ---------------------------------------------------------------- 主流程

def main():
    global API_KEY
    API_KEY = os.environ.get("FUYAO_API_KEY", "").strip()
    if not API_KEY:
        keyfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuyao_key.txt")
        if os.path.exists(keyfile):
            API_KEY = open(keyfile, encoding="utf-8").read().strip()
    if not API_KEY:
        print("错误: 缺少 API Key。用法: FUYAO_API_KEY=xxxx python updater.py", file=sys.stderr)
        sys.exit(2)

    today = today_sh()
    log(f"开始更新 {today} ...")

    # 1. 指数目录 + 目标代码解析
    by_name = list_index_catalog()
    idx = resolve_indexes(by_name)
    log(f"指数代码: 创业板={idx['cyb']} 中证红利={idx['zzhl']} "
        f"红利低波价格={idx['hlb_price']} 红利低波全收益={idx['hlb_tr']}")

    # 2. 指数日线(一次批量)
    codes = [c for c in (idx["cyb"], idx["zzhl"], idx["hlb_price"], idx["hlb_tr"]) if c]
    klines = fetch_index_klines(codes)
    log(f"指数日线: " + ", ".join(f"{k}={len(v)}" for k, v in klines.items()))

    cyb_px = klines.get(idx["cyb"]) or []
    zzhl_px = klines.get(idx["zzhl"]) or []

    # 中证红利兜底: ETF 前复权日线
    zzhl_via_etf = False
    if not zzhl_px:
        log(f"!! 指数目录中没有中证红利指数, 用 {ZZHL_ETF_FALLBACK} ETF 日线兜底")
        zzhl_px = fetch_fund_klines(ZZHL_ETF_FALLBACK)
        zzhl_via_etf = True
        idx["zzhl"] = f"{ZZHL_ETF_FALLBACK}(ETF兜底)"

    # 3. 比值序列
    ratio_map = {d: (c, None) for d, c in cyb_px}
    for d, c in zzhl_px:
        if d in ratio_map and c:
            cy, _ = ratio_map[d]
            ratio_map[d] = (cy, cy / c)
    ratio = [(d.isoformat(), round(v[1], 4)) for d, v in sorted(ratio_map.items()) if v[1]]

    # 4. 估值百分位
    cyb_pct = fetch_percentile(CYB_FUND)
    hlb_pct = fetch_percentile(HLB_FUND)
    log(f"估值百分位: 创业板={len(cyb_pct)} 红利低波={len(hlb_pct)}")

    # 5. 股息率: 优先全收益指数推演, 否则 ETF 分红推演
    dy_method = "tr_index"
    hlb_dy = []
    if idx["hlb_tr"] and klines.get(idx["hlb_tr"]) and klines.get(idx["hlb_price"]):
        hlb_dy = dividend_yield_tr(klines[idx["hlb_tr"]], klines[idx["hlb_price"]])
    if not hlb_dy:
        dy_method = "fund_dividends"
        price = klines.get(idx["hlb_price"]) or fetch_fund_klines(HLB_FUND)
        hlb_dy = dividend_yield_fund(price)
        log(f"!! 全收益指数不可用, 股息率改用 {HLB_FUND} 分红推演")
    log(f"股息率: {len(hlb_dy)} 点 (方法={dy_method})")

    # 6. 10年国债收益率 + 股债利差
    bond_map = {}
    try:
        bond_map = fetch_bond10y()
    except Exception as e:
        log(f"!! 国债收益率获取失败(股债利差将缺失): {e}")
    log(f"国债收益率: {len(bond_map)} 天")

    spread = []
    dy_map = dict(hlb_dy)
    for d, dy in hlb_dy:
        if d in bond_map:
            spread.append((d.isoformat(), round(dy - bond_map[d], 4)))

    # 7. 组装输出
    def pts(pairs):
        return [[d.isoformat(), v] for d, v in pairs]

    latest = {}
    if ratio:
        latest["date"] = ratio[-1][0]
        latest["ratio"] = ratio[-1][1]
        latest["ratio_prev"] = ratio[-2][1] if len(ratio) > 1 else None
        r = latest["ratio"]
        latest["signal"] = "cyb" if r < 0.40 else ("hlb" if r > 0.60 else "neutral")
    for key, pairs in (("cyb_pe_pct", cyb_pct), ("hlb_pe_pct", hlb_pct), ("hlb_dy", hlb_dy), ("spread", spread)):
        latest[key] = pairs[-1][1] if pairs else None
    latest["bond10y"] = sorted(bond_map.items())[-1][1] if bond_map else None

    doc = {
        "generated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "latest": latest,
        "series": {
            "ratio": {"name": "创业板指 / 中证红利", "points": ratio},
            "cyb_pe_pct": {"name": "创业板估值百分位(PE TTM 五年分位)", "points": pts(cyb_pct)},
            "hlb_pe_pct": {"name": "红利低波估值百分位(PE TTM 五年分位)", "points": pts(hlb_pct)},
            "hlb_dy": {"name": "红利低波股息率(近12个月)", "points": pts(hlb_dy)},
            "spread": {"name": "红利低波股债利差(股息率-10年国债)", "points": spread},
        },
        "meta": {
            "index_codes": idx,
            "percentile_funds": {"cyb": CYB_FUND, "hlb": HLB_FUND},
            "dy_method": dy_method,
            "zzhl_via_etf": zzhl_via_etf,
            "bond_source": "中国货币网·中债国债到期收益率(10年)",
            "signal_rules": "比值<0.4: 创业板机会; >0.6: 红利低波机会; 其余中性",
        },
    }

    # 数据未变化则跳过写入(避免无意义提交)
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8"))
            if old.get("series") == doc["series"]:
                log("数据无变化, 跳过写入")
                return
        except (OSError, ValueError):
            pass

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    log(f"已写入 {OUT} ({os.path.getsize(OUT) // 1024} KB)")
    log(f"最新({latest.get('date')}): 比值={latest.get('ratio')} 信号={latest.get('signal')} "
        f"创业板PE分位={latest.get('cyb_pe_pct')} 红利低波PE分位={latest.get('hlb_pe_pct')} "
        f"股息率={latest.get('hlb_dy')} 股债利差={latest.get('spread')}")


if __name__ == "__main__":
    main()
