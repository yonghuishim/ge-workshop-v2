# Global Company Filings MCP (SEC EDGAR)

미국 SEC 전자공시(EDGAR)에서 **글로벌 상장사**(엔비디아·애플·마이크로소프트 등)의 정보를 실시간으로 가져오는 Custom MCP 서버. 핸즈온 3-1 *외부 회사 조회 에이전트* 가 사용합니다. (국내 상장사용 DART 버전은 `../mcp-server` 참고 — 구조 동일)

## 노출 도구 (4종)
| 도구 | 설명 | SEC 엔드포인트 |
|---|---|---|
| `search_company` | 회사 기본정보(산업·거래소·본사) | `submissions/CIK…json` |
| `get_recent_filings` | 최근 N일 공시 목록(form·날짜·SEC 링크) | `submissions/CIK…json` |
| `detect_risk_signals` | 공시 유형·8-K 항목 → 위험도 점수(0~10) | `submissions/CIK…json` |
| `get_financial_trend` | 연간 매출·순이익 + YoY | `api/xbrl/companyconcept/…` |

> 위험도 점수·YoY는 원본 공시에 없는 **파생 지표** — MCP 서버가 계산. 이게 데이터스토어(문서 검색)와 다른 지점.

## ⚠️ 필수: SEC User-Agent
SEC는 모든 요청에 **식별용 User-Agent(연락 이메일 포함)** 를 요구합니다. 미설정 시 403이 날 수 있어요.
```
SEC_USER_AGENT="LG CNS Gemini Workshop you@example.com"
```
API 키는 불필요합니다. (DART와 달리 무료·키리스)

## 로컬 실행
```bash
pip install -r requirements.txt
SEC_USER_AGENT="LG CNS Workshop you@example.com" python main.py
# → http://localhost:8080/sse
```

## Cloud Run 배포
```bash
gcloud run deploy mcp-company-sec \
  --source . --region asia-northeast3 --allow-unauthenticated \
  --set-env-vars SEC_USER_AGENT="LG CNS Gemini Workshop you@example.com"
```
배포 후 Gemini Enterprise 콘솔에 **Custom MCP**로 등록(엔드포인트 `…/sse`), 표시 이름은 `기업 공시 도구` 권장.

## 빠른 점검
```bash
# 엔비디아 기본정보가 나오면 정상 (CIK 1045810 / NASDAQ: NVDA)
SEC_USER_AGENT="test you@example.com" python -c "import asyncio,main; print(asyncio.run(main.search_company('엔비디아')))"
```

## 지원 회사
미국 SEC 등록(미국 상장) 기업. 한글 별칭 일부 내장(엔비디아·애플·마이크로소프트·테슬라·아마존·구글·메타 등), 그 외는 영문 회사명 또는 티커(예: `NVDA`)로 조회.
