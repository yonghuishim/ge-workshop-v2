"""
Hanbit DART MCP Server
======================

한국 상장사의 DART(전자공시시스템) 정보를 실시간으로 가져오는 Custom MCP 서버.
Gemini Enterprise No-Code 에이전트에서 도구로 호출 가능.

노출 도구 (5종):
  1. search_company          — 회사 기본 정보 (CEO·종목코드·업종 등)
  2. get_recent_filings      — 최근 N일 공시 목록
  3. detect_risk_signals     — 공시에서 위험 신호 자동 탐지 + 위험도 점수
  4. get_financial_trend     — 연간 재무 + YoY 증감률
  5. get_executive_changes   — 임원 현황 (정기보고서 기준)

DATA SOURCE
  - DART OpenAPI (https://opendart.fss.or.kr) — 실시간 호출
  - 모든 응답은 원본 그대로가 아니라 MCP 서버에서 가공·집계·점수화한 결과
    → 이게 데이터스토어(문서 인덱싱)와 결정적으로 다른 지점

RUN
  로컬:        python main.py     → http://localhost:8080/sse
  Cloud Run:   ./deploy.sh
"""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timedelta
from typing import Annotated, Any
from xml.etree import ElementTree as ET

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ─────────────────────────────────────────────────────────────────────────────
# 환경 / 상수
# ─────────────────────────────────────────────────────────────────────────────

DART_API_KEY = os.environ.get("DART_API_KEY", "").strip()
DART_BASE = "https://opendart.fss.or.kr/api"

if not DART_API_KEY:
    raise RuntimeError(
        "DART_API_KEY 환경변수가 필요합니다. "
        "https://opendart.fss.or.kr/ 에서 무료 발급 후 설정하세요."
    )

# 공시 제목에서 매칭할 위험 키워드 (카테고리별)
RISK_KEYWORDS: dict[str, list[str]] = {
    "재무위험": ["감자", "유상증자", "전환사채", "교환사채", "신주인수권부사채",
                 "회사채 발행", "자기주식 처분", "단기차입금 증가"],
    "지배구조": ["대표이사 변경", "대표이사 선임", "최대주주 변경", "임원 선임",
                 "주식 등의 대량보유", "공개매수"],
    "법률위험": ["소송", "민사소송", "공정거래위원회", "행정처분", "고발", "압수수색"],
    "신뢰성": ["정정공시", "회계처리위반", "감사의견 거절", "감사의견 한정",
               "감사보고서 정정"],
    "사업변동": ["영업양수도", "타법인 주식 취득", "합병", "분할", "사업포기",
                 "자산양수도"],
}

RISK_WEIGHTS: dict[str, float] = {
    "재무위험": 2.5,
    "지배구조": 2.0,
    "법률위험": 2.0,
    "신뢰성": 3.0,
    "사업변동": 1.5,
}

# ─────────────────────────────────────────────────────────────────────────────
# 회사명 → corp_code 매핑 (DART corpCode.xml 캐시)
# ─────────────────────────────────────────────────────────────────────────────

_corp_code_cache: dict[str, str] = {}
_corp_code_loaded: bool = False


async def _ensure_corp_codes() -> None:
    """DART corpCode.xml 한 번 다운로드해 메모리 캐시 (상장사만 보관)."""
    global _corp_code_loaded
    if _corp_code_loaded:
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": DART_API_KEY},
        )
        r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open("CORPCODE.xml") as f:
            tree = ET.parse(f)
            for el in tree.findall(".//list"):
                name = (el.findtext("corp_name") or "").strip()
                code = (el.findtext("corp_code") or "").strip()
                stock = (el.findtext("stock_code") or "").strip()
                # 상장사만 (stock_code 존재)
                if name and code and stock:
                    _corp_code_cache[name] = code
    _corp_code_loaded = True


async def _resolve_corp_code(company_name: str) -> str | None:
    """회사명 → corp_code. 완전일치 → 부분일치 fallback."""
    await _ensure_corp_codes()
    if company_name in _corp_code_cache:
        return _corp_code_cache[company_name]
    candidates = [k for k in _corp_code_cache if company_name in k or k in company_name]
    if candidates:
        # 가장 짧은 이름 우선 (가장 정확할 가능성)
        return _corp_code_cache[min(candidates, key=len)]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MCP Server
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="hanbit-dart-mcp",
    instructions=(
        "한국 상장사의 DART(전자공시시스템) 정보를 실시간으로 가져오는 도구 모음. "
        "회사 기본정보·최근 공시·위험 신호·재무 추세·임원 현황을 제공합니다. "
        "모든 응답은 DART OpenAPI 실시간 호출 결과이며, 위험도 점수·YoY 증감률 같은 "
        "파생 지표는 MCP 서버 내 알고리즘으로 계산됩니다."
    ),
)


@mcp.tool()
async def search_company(
    company_name: Annotated[str, Field(description="조회할 회사명 (예: '삼성전자', 'D메디칼')")],
) -> dict[str, Any]:
    """
    회사명으로 DART 기본 정보 조회 — 실시간.

    Returns:
        회사명·종목코드·업종·CEO·설립일·주소·홈페이지 등
    """
    corp_code = await _resolve_corp_code(company_name)
    if not corp_code:
        return {"error": f"DART에 등록된 '{company_name}'을(를) 찾을 수 없습니다. (상장사만 지원)"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{DART_BASE}/company.json",
            params={"crtfc_key": DART_API_KEY, "corp_code": corp_code},
        )
        data = r.json()

    if data.get("status") != "000":
        return {"error": data.get("message", "DART 조회 실패")}

    return {
        "corp_name": data.get("corp_name"),
        "corp_name_eng": data.get("corp_name_eng"),
        "stock_code": data.get("stock_code"),
        "stock_market": data.get("corp_cls"),  # Y=유가, K=코스닥, N=코넥스, E=기타
        "ceo_name": data.get("ceo_nm"),
        "establish_date": data.get("est_dt"),
        "industry_code": data.get("induty_code"),
        "address": data.get("adres"),
        "homepage": data.get("hm_url"),
        "phone": data.get("phn_no"),
        "_data_source": "DART OpenAPI — 실시간 조회",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def get_recent_filings(
    company_name: Annotated[str, Field(description="조회할 회사명")],
    days: Annotated[int, Field(description="최근 N일 (1~365)", ge=1, le=365)] = 90,
) -> dict[str, Any]:
    """
    회사의 최근 N일 공시 목록 — 실시간 DART API.

    각 공시 항목은 날짜·제목·접수번호·DART URL을 포함.
    """
    corp_code = await _resolve_corp_code(company_name)
    if not corp_code:
        return {"error": f"DART에 등록된 '{company_name}'을(를) 찾을 수 없습니다."}

    bgn_de = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end_de = datetime.now().strftime("%Y%m%d")

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{DART_BASE}/list.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_count": 100,
            },
        )
        data = r.json()

    if data.get("status") not in ("000", "013"):  # 013 = 조회 결과 없음
        return {"error": data.get("message", "DART 조회 실패")}

    filings = [
        {
            "filing_date": item.get("rcept_dt"),
            "title": item.get("report_nm"),
            "filer": item.get("flr_nm"),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no')}",
        }
        for item in data.get("list", [])
    ]

    return {
        "company": company_name,
        "period": f"{bgn_de} ~ {end_de}",
        "total_filings": len(filings),
        "filings": filings,
        "_data_source": "DART OpenAPI — 실시간",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def detect_risk_signals(
    company_name: Annotated[str, Field(description="조회할 회사명")],
    days: Annotated[int, Field(description="검토할 최근 N일", ge=1, le=365)] = 90,
) -> dict[str, Any]:
    """
    회사 최근 공시에서 위험 신호를 자동 탐지 — 실시간.

    카테고리 (재무·지배구조·법률·신뢰성·사업변동) 별 위험 키워드로 매칭하고,
    가중치 합산으로 위험도 점수(1~10)를 산출.

    NOTE: 위험도 점수는 원본 공시에 없는 파생 지표 — MCP 서버 내 알고리즘 결과.
    """
    filings_resp = await get_recent_filings(company_name, days)
    if "error" in filings_resp:
        return filings_resp

    filings: list[dict] = filings_resp["filings"]
    detected: dict[str, list[dict]] = {cat: [] for cat in RISK_KEYWORDS}

    for f in filings:
        title = f.get("title", "")
        for cat, keywords in RISK_KEYWORDS.items():
            for kw in keywords:
                if kw in title:
                    detected[cat].append(
                        {
                            "date": f["filing_date"],
                            "title": title,
                            "matched_keyword": kw,
                            "url": f["url"],
                        }
                    )
                    break  # 한 공시에 한 카테고리당 1회만 카운트

    # 가중치 합산 → 0~10 cap
    raw_score = sum(RISK_WEIGHTS[cat] * len(items) for cat, items in detected.items())
    risk_score = min(round(raw_score, 1), 10.0)

    if risk_score >= 7.0:
        signal = "🔴 HIGH"
    elif risk_score >= 4.0:
        signal = "🟠 MEDIUM"
    elif risk_score > 0:
        signal = "🟡 LOW"
    else:
        signal = "🟢 NONE"

    return {
        "company": company_name,
        "검토기간": f"최근 {days}일",
        "총공시건수": len(filings),
        "위험도점수": risk_score,
        "위험등급": signal,
        "위험신호_카테고리별": {cat: items for cat, items in detected.items() if items},
        "_data_source": "DART OpenAPI 실시간 + 위험 키워드 매칭 알고리즘 (MCP 서버 내)",
        "_note": (
            "위험도 점수·등급은 원본 공시에 없는 파생 지표. "
            "MCP 서버가 공시 제목을 카테고리 키워드와 매칭하고 가중치 합산해 계산."
        ),
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def get_financial_trend(
    company_name: Annotated[str, Field(description="조회할 회사명")],
    year: Annotated[int, Field(description="기준 사업연도 (YYYY)", ge=2015, le=2026)] = 2025,
) -> dict[str, Any]:
    """
    회사의 연간 재무 추세 — 실시간 DART 사업보고서 기반.

    당해 + 전년 매출·영업이익을 조회하고 YoY 증감률을 계산.

    NOTE: YoY 증감률은 원본에 없는 파생 지표 (MCP가 계산).
    """
    corp_code = await _resolve_corp_code(company_name)
    if not corp_code:
        return {"error": f"DART에 등록된 '{company_name}'을(를) 찾을 수 없습니다."}

    async def _fetch(yr: int) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": DART_API_KEY,
                    "corp_code": corp_code,
                    "bsns_year": str(yr),
                    "reprt_code": "11011",  # 사업보고서
                    "fs_div": "CFS",  # 연결재무제표 (없으면 OFS로 폴백)
                },
            )
            data = r.json()
            if data.get("status") == "013":
                # 연결재무 없음 → 별도재무로 재시도
                r2 = await client.get(
                    f"{DART_BASE}/fnlttSinglAcntAll.json",
                    params={
                        "crtfc_key": DART_API_KEY,
                        "corp_code": corp_code,
                        "bsns_year": str(yr),
                        "reprt_code": "11011",
                        "fs_div": "OFS",
                    },
                )
                data = r2.json()
            return data

    data_current = await _fetch(year)
    data_prev = await _fetch(year - 1)

    def _extract(data: dict, account_id: str) -> int:
        for item in data.get("list", []):
            if item.get("account_id") == account_id:
                amt = (item.get("thstrm_amount") or "0").replace(",", "")
                try:
                    return int(amt)
                except ValueError:
                    return 0
        return 0

    revenue_now = _extract(data_current, "ifrs-full_Revenue")
    op_income_now = _extract(data_current, "dart_OperatingIncomeLoss")
    net_income_now = _extract(data_current, "ifrs-full_ProfitLoss")

    revenue_prev = _extract(data_prev, "ifrs-full_Revenue")
    op_income_prev = _extract(data_prev, "dart_OperatingIncomeLoss")

    def _yoy(now: int, prev: int) -> float | None:
        if prev <= 0:
            return None
        return round((now / prev - 1) * 100, 1)

    return {
        "company": company_name,
        "year": year,
        "current_year": {
            "revenue_krw": revenue_now,
            "operating_income_krw": op_income_now,
            "net_income_krw": net_income_now,
            "operating_margin_pct": (
                round(op_income_now / revenue_now * 100, 1) if revenue_now else None
            ),
        },
        "prev_year": {
            "revenue_krw": revenue_prev,
            "operating_income_krw": op_income_prev,
        },
        "yoy_growth": {
            "revenue_pct": _yoy(revenue_now, revenue_prev),
            "operating_income_pct": _yoy(op_income_now, op_income_prev),
        },
        "_data_source": "DART OpenAPI — 사업보고서 (연결재무 우선, 없으면 별도재무)",
        "_note": "YoY 증감률·영업이익률은 원본 재무제표에 없는 파생 지표 (MCP가 계산).",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


@mcp.tool()
async def get_executive_changes(
    company_name: Annotated[str, Field(description="조회할 회사명")],
    year: Annotated[int, Field(description="기준 사업연도", ge=2015, le=2026)] = 2025,
) -> dict[str, Any]:
    """
    회사 임원 현황 — 실시간 DART 사업보고서 기반.

    임원 명단·등기여부·주요경력·임기를 반환. 최대 20명.
    """
    corp_code = await _resolve_corp_code(company_name)
    if not corp_code:
        return {"error": f"DART에 등록된 '{company_name}'을(를) 찾을 수 없습니다."}

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{DART_BASE}/exctvSttus.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
            },
        )
        data = r.json()

    if data.get("status") not in ("000", "013"):
        return {"error": data.get("message", "DART 조회 실패")}

    execs = data.get("list", [])
    return {
        "company": company_name,
        "year": year,
        "total_executives": len(execs),
        "executives": [
            {
                "name": e.get("nm"),
                "gender": e.get("sexdstn"),
                "birth_year": e.get("birth_ym"),
                "position": e.get("ofcps"),
                "registered_officer": e.get("rgist_exctv_at"),
                "full_time": e.get("fte_at"),
                "duty": e.get("chrg_job"),
                "career": e.get("main_career"),
                "term_ends": e.get("ofcps_trtm_term_end_de"),
            }
            for e in execs[:20]
        ],
        "_data_source": "DART OpenAPI — 사업보고서 임원 현황",
        "_retrieved_at": datetime.now().isoformat(timespec="seconds"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cloud Run entrypoint (SSE transport over HTTP)
# ─────────────────────────────────────────────────────────────────────────────

# FastMCP SSE ASGI app — Gemini Enterprise Custom MCP가 호출하는 엔드포인트
app = mcp.sse_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
