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
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "data.json")
ANCHOR = os.path.join(HERE, "docs", "anchor.json")  # ETF兜底时的比值锚定

# 目标基金(用于估值百分位): 跟踪指数分别为 创业板指 / 中证红利低波动指数
CYB_FUND = "159915.SZ"   # 易方达创业板ETF
HLB_FUND = "512890.SH"   # 华泰柏瑞红利低波动ETF
# 中证红利指数兜底: 若指数数据不可用, 用中证红利ETF 日线 + 锚定换算
ZZHL_ETF_FALLBACK = "515080.SH"  # 招商中证红利ETF
EM_ZZHL_SECID = "1.000922"       # 东财 中证红利指数 secid

YEARS = 5            # 指数日线最多取近 5 年
BOND_YEARS = 5       # 国债收益率取近 5 年
CSINDEX_PERF = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


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


def fetch_csindex_perf(code, years=YEARS):
    """中证指数官网 指数日线收盘 -> [(date, close), ...]"""
    start = today_sh() - dt.timedelta(days=years * 365)
    end = today_sh()
    resp = http_get(f"{CSINDEX_PERF}?indexCode={code}&startDate={start:%Y%m%d}&endDate={end:%Y%m%d}",
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.csindex.com.cn/"})
    out = []
    for row in resp.get("data") or []:
        ts = row.get("tradeDate")
        c = row.get("close")
        if ts and c is not None:
            try:
                out.append((dt.date(int(ts[:4]), int(ts[4:6]), int(ts[6:8])), float(c)))
            except (ValueError, TypeError):
                pass
    return out


def fetch_em_klines(secid, years=YEARS):
    """东方财富 指数日线收盘(兜底) -> [(date, close), ...]"""
    start = today_sh() - dt.timedelta(days=years * 365)
    end = today_sh()
    resp = http_get("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=" + secid +
                    "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                    f"&klt=101&fqt=0&beg={start:%Y%m%d}&end={end:%Y%m%d}&lmt=1000000",
                    headers={"User-Agent": "Mozilla/5.0"})
    out = []
    for line in ((resp.get("data") or {}).get("klines")) or []:
        parts = line.split(",")
        if len(parts) >= 3 and parts[0] and parts[2]:
            try:
                out.append((dt.date.fromisoformat(parts[0]), float(parts[2])))
            except (ValueError, IndexError):
                pass
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
    """跟踪指数 PE TTM 五年分位 -> [(date, pct), ...]
    接口区间上限 5 年; 返回可能同一天多条, 按日期去重保留末条。"""
    start = today_sh() - dt.timedelta(days=YEARS * 365)
    end = today_sh()
    try:
        data = fuyao(f"/api/fund/performance/indicators-historical?thscode={fund_code}"
                     f"&start={d2ms(start)}&end={d2ms(end)}")
    except RuntimeError as e:
        if "2003" in str(e):
            log(f"  !! 基金指标接口无权限(2003), 估值百分位将缺失: {e}")
            return []
        raise
    out = {}
    for i in data.get("item") or []:
        v = i.get("track_index_pe_ttm_five_year_percentile")
        if i.get("date_ms") and v is not None:
            out[ms2d(i["date_ms"])] = float(v)
    return sorted(out.items())


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

def load_anchor():
    """ETF兜底时的比值锚定: {"date": ..., "factor": index_ratio/etf_ratio}"""
    try:
        a = json.load(open(ANCHOR, encoding="utf-8"))
        if a.get("date") and a.get("factor"):
            return a
    except (OSError, ValueError):
        pass
    return None


def save_anchor(anchor):
    try:
        os.makedirs(os.path.dirname(ANCHOR), exist_ok=True)
        json.dump(anchor, open(ANCHOR, "w", encoding="utf-8"), ensure_ascii=False)
    except OSError as e:
        log(f"  !! 锚定写入失败: {e}")


def carry_forward(doc):
    """某数据源当日故障时, 沿用上一次成功的数据, 并在 meta 里打标"""
    if not os.path.exists(OUT):
        return
    try:
        old = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        return
    for k, s in doc["series"].items():
        if not s["points"]:
            old_pts = (old.get("series") or {}).get(k) or {}
            old_pts = old_pts.get("points") or []
            if old_pts:
                s["points"] = old_pts
                doc["meta"]["stale_" + k] = True
                log(f"  ~ {k} 本次无数据, 沿用上次 {len(old_pts)} 点")


def main():
    global API_KEY
    API_KEY = os.environ.get("FUYAO_API_KEY", "").strip()
    if not API_KEY:
        keyfile = os.path.join(HERE, "fuyao_key.txt")
        if os.path.exists(keyfile):
            API_KEY = open(keyfile, encoding="utf-8").read().strip()
    if not API_KEY:
        print("错误: 缺少 API Key。用法: FUYAO_API_KEY=xxxx python updater.py", file=sys.stderr)
        sys.exit(2)

    log(f"开始更新 {today_sh()} ...")
    srcs = {}  # 数据来源标记

    # 1. 创业板指: 同花顺指数日线
    by_name = list_index_catalog()
    cyb_code = pick(by_name, ["创业板指", "创业板指数"]) or "399006.SZ"
    klines = fetch_index_klines([cyb_code])
    cyb_px = klines.get(cyb_code) or []
    log(f"创业板指 {cyb_code}: {len(cyb_px)} 天")

    # 2. 中证红利 000922: 中证指数官网 -> 东财 -> ETF兜底(锚定换算)
    zzhl_px = []
    try:
        zzhl_px = fetch_csindex_perf("000922")
        if zzhl_px:
            srcs["zzhl"] = "中证指数官网·中证红利(000922)"
    except Exception as e:
        log(f"  !! 官网中证红利失败: {e}")
    if not zzhl_px:
        try:
            zzhl_px = fetch_em_klines(EM_ZZHL_SECID)
            if zzhl_px:
                srcs["zzhl"] = "东方财富·中证红利(000922)"
        except Exception as e:
            log(f"  !! 东财中证红利失败: {e}")
    zzhl_via_etf = not zzhl_px
    if zzhl_via_etf:
        log(f"  !! 中证红利指数不可用, 用 {ZZHL_ETF_FALLBACK} ETF 兜底")
        etf_px = fetch_fund_klines(ZZHL_ETF_FALLBACK)
        anchor = load_anchor()
        if etf_px and anchor:
            f = anchor["factor"]
            zzhl_px = [(d, c / f) for d, c in etf_px]  # 指数点位 = ETF价格 / factor
            srcs["zzhl"] = f"{ZZHL_ETF_FALLBACK} ETF 日线÷锚定系数"
            log(f"  已用锚定系数 {f} (锚定日 {anchor['date']})")
        else:
            log("  !! 无可用锚定, 比值序列将沿用上次数据")

    # 3. 比值序列
    ratio = []
    if cyb_px and zzhl_px:
        m = dict(cyb_px)
        ratio = [(d.isoformat(), round(m[d] / c, 4)) for d, c in zzhl_px if d in m and m[d]]
    log(f"比值: {len(ratio)} 点")

    # 更新锚定(有真实指数数据时): factor = 指数比值 / ETF比值
    if not zzhl_via_etf:
        try:
            etf_px = dict(fetch_fund_klines(ZZHL_ETF_FALLBACK))
            m = dict(cyb_px)
            common = [d for d, c in zzhl_px if d in m and d in etf_px and etf_px[d]]
            if common:
                d = common[-1]
                save_anchor({"date": d.isoformat(),
                             "factor": round((m[d] / dict(zzhl_px)[d]) / (m[d] / etf_px[d]), 8)})
        except Exception as e:
            log(f"  ~ 锚定更新失败(不影响主流程): {e}")

    # 4. 估值百分位(同花顺基金指标)
    cyb_pct = fetch_percentile(CYB_FUND)
    hlb_pct = fetch_percentile(HLB_FUND)
    log(f"估值百分位: 创业板={len(cyb_pct)} 红利低波={len(hlb_pct)}")

    # 5. 股息率: 红利低波全收益/价格指数(中证指数官网)
    hlb_dy = []
    dy_method = "tr_index"
    try:
        hlb_tr = fetch_csindex_perf("H20269", years=YEARS + 1)
        hlb_p = fetch_csindex_perf("H30269", years=YEARS + 1)
        hlb_dy = dividend_yield_tr(hlb_tr, hlb_p)
        if hlb_dy:
            srcs["hlb"] = "中证指数官网·红利低波(H30269/H20269)"
    except Exception as e:
        log(f"  !! 官网红利低波指数失败: {e}")
    if not hlb_dy:
        dy_method = "fund_dividends"
        price = fetch_fund_klines(HLB_FUND)
        hlb_dy = dividend_yield_fund(price)
        log(f"  !! 全收益指数不可用, 股息率改用 {HLB_FUND} 分红推演")
    log(f"股息率: {len(hlb_dy)} 点 (方法={dy_method})")

    # 6. 10年国债收益率 + 股债利差
    bond_map = {}
    try:
        bond_map = fetch_bond10y()
    except Exception as e:
        log(f"!! 国债收益率获取失败(股债利差将缺失): {e}")
    log(f"国债收益率: {len(bond_map)} 天")

    spread = []
    for d, dy in hlb_dy:
        if d in bond_map:
            spread.append((d.isoformat(), round(dy - bond_map[d], 4)))

    # 7. 组装输出
    def pts(pairs):
        return [[d.isoformat(), v] for d, v in pairs]

    doc = {
        "generated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "series": {
            "ratio": {"name": "创业板指 / 中证红利", "points": ratio},
            "cyb_pe_pct": {"name": "创业板估值百分位(PE TTM 五年分位)", "points": pts(cyb_pct)},
            "hlb_pe_pct": {"name": "红利低波估值百分位(PE TTM 五年分位)", "points": pts(hlb_pct)},
            "hlb_dy": {"name": "红利低波股息率(近12个月)", "points": pts(hlb_dy)},
            "spread": {"name": "红利低波股债利差(股息率-10年国债)", "points": spread},
        },
        "meta": {
            "sources": srcs,
            "percentile_funds": {"cyb": CYB_FUND, "hlb": HLB_FUND},
            "dy_method": dy_method,
            "zzhl_via_etf": zzhl_via_etf,
            "bond_source": "中国货币网·中债国债到期收益率(10年)",
            "signal_rules": "比值<0.4: 创业板机会; >0.6: 红利低波机会; 其余中性",
        },
    }

    # 8. 断源保底: 某系列本次无数据时沿用上次
    carry_forward(doc)

    # 9. 最新值 + 信号
    latest = {}
    ratio_p = doc["series"]["ratio"]["points"]
    if ratio_p:
        latest["date"] = ratio_p[-1][0]
        latest["ratio"] = ratio_p[-1][1]
        latest["ratio_prev"] = ratio_p[-2][1] if len(ratio_p) > 1 else None
        r = latest["ratio"]
        latest["signal"] = "cyb" if r < 0.40 else ("hlb" if r > 0.60 else "neutral")
    for key in ("cyb_pe_pct", "hlb_pe_pct", "hlb_dy", "spread"):
        p = doc["series"][key]["points"]
        latest[key] = p[-1][1] if p else None
    latest["bond10y"] = sorted(bond_map.items())[-1][1] if bond_map else None
    doc["latest"] = latest

    # 10. 数据未变化则跳过写入(避免无意义提交)
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
