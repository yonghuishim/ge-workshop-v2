"""
Global Company Filings MCP Server (SEC EDGAR)
=============================================

미국 SEC 전자공시(EDGAR)에서 글로벌 상장사 정보를 실시간으로 가져오는 Custom MCP 서버.
DART(국내) 버전과 동일한 구조 — 국내 상장사 대신 미국 SEC 등록 글로벌 기업(엔비디아·애플 등)을 조회.
Gemini Enterprise No-Code 에이전트에서 도구로 호출 가능.

노출 도구 (4종):
  1. search_company        — 회사 기본 정보 (산업·거래소·본사 소재지 등)
  2. get_recent_filings    — 최근 N일 공시(filing) 목록
  3. detect_risk_signals   — 공시 유형·8-K 항목에서 위험 신호 자동 탐지 + 위험도 점수
  4. get_financial_trend   — 연간 매출·순이익 + YoY 증감률

DATA SOURCE
  - SEC EDGAR (https://www.sec.gov) — 실시간 호출, API 키 불필요
      · company_tickers.json           회사명/티커 → CIK 매핑
      · data.sec.gov/submissions/...   회사 개요 + 최근 공시
      · data.sec.gov/api/xbrl/...       재무(XBRL)
  - 모든 응답은 원본 그대로가 아니라 MCP 서버에서 가공·집계·점수화한 결과
    → 이게 데이터스토어(문서 인덱싱)와 결정적으로 다른 지점

요구사항
  - SEC는 모든 요청에 식별용 User-Agent(연락 이메일 포함)를 요구합니다.
    환경변수 SEC_USER_AGENT 에 "회사명 contact@example.com" 형식으로 반드시 설정하세요.
    (미설정 시 SEC가 403을 반환할 수 있습니다.)

RUN
  로컬:        SEC_USER_AGENT="LG CNS Workshop you@example.com" python main.py   → http://localhost:8080/sse
  Cloud Run:   gcloud run deploy mcp-company-sec --source . --set-env-vars SEC_USER_AGENT="..."
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Annotated, Any

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ─────────────────────────────────────────────────────────────────────────────
# 환경 / 상수
# ─────────────────────────────────────────────────────────────────────────────

# SEC는 식별용 User-Agent(연락처 포함)를 요구. 반드시 실제 값으로 교체.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "GE Workshop Custom MCP admin@example.com"
).strip()

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"

_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

# 공시 유형/8-K 항목 → 위험 카테고리 매핑
# 8-K 항목 코드(submissions의 items)와 form type을 함께 본다.
RISK_8K_ITEMS: dict[str, str] = {
    "4.01": "신뢰성",   # 회계법인(감사인) 변경
    "4.02": "신뢰성",   # 기존 재무제표 신뢰 불가(Non-Reliance)
    "1.02": "사업변동",  # 중요 계약 해지
    "1.01": "사업변동",  # 중요 계약 체결
    "2.01": "사업변동",  # 자산 인수/처분 완료
    "2.03": "재무위험",  # 직접 금융채무 발생
    "2.04": "재무위험",  # 채무 조기상환 의무 발생
    "3.01": "법률위험",  # 상장 규정 위반/상장폐지 통지
    "5.02": "지배구조",  # 이사·임원 선임/퇴임
}

# 위험 신호가 되는 form type
RISK_FORMS: dict[str, str] = {
    "SC 13D": "지배구조",     # 5% 이상 대량 취득(경영참여)
    "DFAN14A": "지배구조",    # 위임장 대결(액티비스트)
    "NT 10-K": "신뢰성",      # 정기보고서 기한 내 미제출
    "NT 10-Q": "신뢰성",
}

RISK_WEIGHTS: dict[str, float] = {
    "재무위험": 2.5,
    "지배구조": 2.0,
    "법률위험": 2.0,
    "신뢰성": 3.0,
    "사업변동": 1.5,
}

# 매출/순이익 XBRL 태그 후보 (회사마다 사용 태그가 달라 순서대로 시도)
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
NET_INCOME_TAGS = ["NetIncomeLoss", "ProfitLoss"]

# ─────────────────────────────────────────────────────────────────────────────
# 회사명/티커 → CIK 매핑 (company_tickers.json 캐시)
# ─────────────────────────────────────────────────────────────────────────────

_ticker_cache: dict[str, dict[str, Any]] = {}  # key=upper(ticker or title) → {cik, ticker, title}
_ticker_loaded = False

# 한글 별칭 → 검색어(영문) 보정: 워크샵에서 자주 입력하는 회사
_ALIAS: dict[str, str] = {
    "엔비디아": "NVDA",
    "애플": "AAPL",
    "마이크로소프트": "MSFT",
    "테슬라": "TSLA",
    "아마존": "AMZN",
    "구글": "GOOGL",
    "알파벳": "GOOGL",
    "메타": "META",
    "넷플릭스": "NFLX",
    "인텔": "INTC",
    "AMD": "AMD",
}


async def _ensure_tickers() -> None:
    """SEC company_tickers.json 한 번 다운로드해 메모리 캐시."""
    global _ticker_loaded
    if _ticker_loaded:
        return
    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
        r = await client.get(TICKERS_URL)
        r.raise_for_status()
        data = r.json()
    for row in data.values():
        entry = {
            "cik": int(row["cik_str"]),
            "ticker": row["ticker"].upper(),
            "title": row["title"],
        }
        _ticker_cache[entry["ticker"]] = entry
        _ticker_cache.setdefault(entry["title"].upper(), entry)
    _ticker_loaded = True


async def _resolve_cik(company_name: str) -> dict[str, Any] | None:
    """회사명/티커/한글 별칭 → {cik, ticker, title}. 정확일치 → 부분일치."""
    await _ensure_tickers()
    q = company_name.strip()
    q = _ALIAS.get(q, q).upper()
    if q in _ticker_cache:
        return _ticker_cache[q]
    # 부분일치(타이틀 포함) — 가장 짧은 회사명 우선
    candidates = [v for k, v in _ticker_cache.items() if q in k]
    if candidates:
        return min(candidates, key=lambda v: len(v["title"]))
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MCP Server
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="company-sec-mcp",
    instructions=(
        "글로벌 상장사(미국 SEC 전자공시 EDGAR)의 정보를 실시간으로 가져오는 도구 모음. "
        "회사 기본정보·최근 공시·위험 신호·재무 추세를 제공합니다. "
        "엔비디아·애플·마이크로소프트 등 미국 SEC 등록 기업을 회사명 또는 티커로 조회하세요. "
        "위험도 점수·YoY 증감률 같은 파생 지표는 MCP 서버 내 알고리즘으로 계산됩니다."
    ),
)


def _edgar_filing_url(cik: int, accession: str, primary_doc: str) -> str:
    acc_nodash = accession.replace("-", "")
    if primary_doc:
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary_doc}"
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession}-index.htm"


async def _fetch_submissions(cik: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
        r = await client.get(SUBMISSIONS_URL.format(cik=cik))
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def search_company(
    company_name: Annotated[str, Field(description="조회할 회사명 또는 티커 (예: '엔비디아', 'NVDA', 'Apple')")],
) -> dict[str, Any]:
    """
    회사명/티커로 SEC EDGAR 기본 정보 조회 — 실시간.

    Returns:
        회사명·티커·CIK·산업(SIC)·상장 거래소·본사 소재지 등
    """
    hit = await _resolve_cik(company_name)
    if not hit:
        return {"error": f"SEC EDGAR에 등록된 '{company_name}'을(를) 찾을 수 없습니다. (미국 상장사만 지원)"}

    sub = await _fetch_submissions(hit["cik"])
    addr = (sub.get("addresses") or {}).get("business") or {}
    location = ", ".join(
        x for x in [addr.get("city"), addr.get("stateOrCountry")] if x
    )
    return {
        "company": sub.get("name", hit["title"]),
        "ticker": hit["ticker"],
        "cik": hit["cik"],
        "exchanges": sub.get("exchanges"),
        "industry_sic": sub.get("sicDescription"),
        "sic_code": sub.get("sic"),
        "hq_location": location or None,
        "homepage": sub.get("website") or None,
        "fiscal_year_end": sub.get("fiscalYearEnd"),
        "_data_source": "SEC EDGAR — 실시간 조회",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def get_recent_filings(
    company_name: Annotated[str, Field(description="조회할 회사명 또는 티커")],
    days: Annotated[int, Field(description="최근 N일 (1~365)", ge=1, le=365)] = 90,
) -> dict[str, Any]:
    """
    회사의 최근 N일 공시(filing) 목록 — 실시간 SEC EDGAR.

    각 항목은 접수일·공시 유형(form)·설명·SEC 문서 URL을 포함.
    """
    hit = await _resolve_cik(company_name)
    if not hit:
        return {"error": f"SEC EDGAR에 등록된 '{company_name}'을(를) 찾을 수 없습니다."}

    sub = await _fetch_submissions(hit["cik"])
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    descs = recent.get("primaryDocDescription", [])
    items = recent.get("items", [])

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    filings = []
    for i in range(len(forms)):
        if dates[i] < cutoff:
            continue
        filings.append(
            {
                "filing_date": dates[i],
                "form": forms[i],
                "description": (descs[i] if i < len(descs) else "") or None,
                "items": (items[i] if i < len(items) else "") or None,
                "url": _edgar_filing_url(hit["cik"], accs[i], docs[i] if i < len(docs) else ""),
            }
        )

    return {
        "company": sub.get("name", hit["title"]),
        "ticker": hit["ticker"],
        "period": f"최근 {days}일",
        "total_filings": len(filings),
        "filings": filings,
        "_data_source": "SEC EDGAR — 실시간",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def detect_risk_signals(
    company_name: Annotated[str, Field(description="조회할 회사명 또는 티커")],
    days: Annotated[int, Field(description="검토할 최근 N일", ge=1, le=365)] = 90,
) -> dict[str, Any]:
    """
    회사 최근 공시에서 위험 신호를 자동 탐지 — 실시간.

    공시 유형(form)과 8-K 항목 코드를 위험 카테고리(재무·지배구조·법률·신뢰성·사업변동)에
    매핑하고, 가중치 합산으로 위험도 점수(0~10)를 산출.

    NOTE: 위험도 점수는 원본 공시에 없는 파생 지표 — MCP 서버 내 알고리즘 결과.
    """
    resp = await get_recent_filings(company_name, days)
    if "error" in resp:
        return resp

    detected: dict[str, list[dict]] = {cat: [] for cat in RISK_WEIGHTS}
    for f in resp["filings"]:
        form = (f.get("form") or "").strip()
        # form type 기반
        if form in RISK_FORMS:
            cat = RISK_FORMS[form]
            detected[cat].append({"date": f["filing_date"], "form": form, "url": f["url"]})
        # 8-K 항목 기반
        if form == "8-K" and f.get("items"):
            for code in [c.strip() for c in f["items"].split(",")]:
                if code in RISK_8K_ITEMS:
                    cat = RISK_8K_ITEMS[code]
                    detected[cat].append(
                        {"date": f["filing_date"], "form": f"8-K Item {code}", "url": f["url"]}
                    )

    raw = sum(RISK_WEIGHTS[c] * len(v) for c, v in detected.items())
    score = min(round(raw, 1), 10.0)
    if score >= 7.0:
        grade = "🔴 HIGH"
    elif score >= 4.0:
        grade = "🟠 MEDIUM"
    elif score > 0:
        grade = "🟡 LOW"
    else:
        grade = "🟢 NONE"

    return {
        "company": resp["company"],
        "검토기간": f"최근 {days}일",
        "총공시건수": resp["total_filings"],
        "위험도점수": score,
        "위험등급": grade,
        "위험신호_카테고리별": {c: v for c, v in detected.items() if v},
        "_data_source": "SEC EDGAR 실시간 + 위험 매핑 알고리즘 (MCP 서버 내)",
        "_note": (
            "위험도 점수·등급은 원본 공시에 없는 파생 지표. "
            "MCP 서버가 공시 유형·8-K 항목 코드를 카테고리와 매핑하고 가중치 합산해 계산."
        ),
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def get_financial_trend(
    company_name: Annotated[str, Field(description="조회할 회사명 또는 티커")],
) -> dict[str, Any]:
    """
    회사의 연간 매출·순이익 추세 — 실시간 SEC XBRL(연차 10-K 기준).

    최근 2개 회계연도 매출·순이익을 조회하고 YoY 증감률을 계산.

    NOTE: YoY 증감률은 원본에 없는 파생 지표 (MCP가 계산).
    """
    hit = await _resolve_cik(company_name)
    if not hit:
        return {"error": f"SEC EDGAR에 등록된 '{company_name}'을(를) 찾을 수 없습니다."}

    async def _annual(tags: list[str]) -> dict[int, int]:
        """연도별(FY) 값 {fy: value} — 10-K(연차) 우선."""
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            for tag in tags:
                r = await client.get(CONCEPT_URL.format(cik=hit["cik"], tag=tag))
                if r.status_code != 200:
                    continue
                units = (r.json().get("units") or {}).get("USD") or []
                out: dict[int, int] = {}
                for u in units:
                    if u.get("form") == "10-K" and u.get("fp") == "FY" and u.get("fy"):
                        # 같은 FY 중복 시 가장 최근 접수본으로 덮어씀
                        out[int(u["fy"])] = int(u["val"])
                if out:
                    return out
        return {}

    rev = await _annual(REVENUE_TAGS)
    net = await _annual(NET_INCOME_TAGS)
    years = sorted(rev.keys())[-2:] if rev else []

    def _yoy(now: int | None, prev: int | None) -> float | None:
        if not now or not prev or prev <= 0:
            return None
        return round((now / prev - 1) * 100, 1)

    cur = years[-1] if years else None
    prv = years[-2] if len(years) >= 2 else None
    return {
        "company": hit["title"],
        "ticker": hit["ticker"],
        "currency": "USD",
        "current_year": {"fy": cur, "revenue": rev.get(cur), "net_income": net.get(cur)},
        "prev_year": {"fy": prv, "revenue": rev.get(prv), "net_income": net.get(prv)},
        "yoy_growth": {
            "revenue_pct": _yoy(rev.get(cur), rev.get(prv)),
            "net_income_pct": _yoy(net.get(cur), net.get(prv)),
        },
        "_data_source": "SEC EDGAR XBRL — 연차보고서(10-K)",
        "_note": "YoY 증감률은 원본 재무제표에 없는 파생 지표 (MCP가 계산).",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cloud Run entrypoint (SSE transport over HTTP)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    mcp.run(transport="sse", host="0.0.0.0", port=port)
