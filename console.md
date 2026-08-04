# 콘솔 수동 단계 가이드 (A ~ E)

`setup.sh`가 끝낸 0~4단계(API·버킷·BigQuery·Cloud Run) **이후**, 콘솔에서 직접 진행하는 단계입니다.
아래 변수는 setup.sh에서 쓴 값과 동일하게 사용하세요.

```
PROJECT_ID = <본인 프로젝트>          예) playground-gemini-ent-20251027
REGION     = asia-northeast3
BUCKET     = <PROJECT_ID>-ge-data     예) playground-gemini-ent-20251027-ge-data
```

> UI 명칭은 테넌트/시점에 따라 다를 수 있습니다(예: "Agent Builder" ↔ "AI Applications"). 흐름은 동일합니다.

---

## A. Vertex AI Search 데이터 스토어 (인덱싱 15~30분 — 가장 먼저!)

사규·매뉴얼·FAQ를 시멘틱 검색 + 출처 인용이 되도록 인덱싱합니다. **setup.sh 실행과 동시에 시작**하세요.

1. Cloud Console → **Vertex AI → Agent Builder(AI Applications)** 이동 → (최초 진입 시 API 사용 동의).
2. 좌측 **Data Stores → `+ CREATE DATA STORE`**.
3. 소스 선택: **Cloud Storage**.
   - 폴더(FOLDER) 선택 → `gs://<BUCKET>/policies/`  (예: `gs://playground-gemini-ent-20251027-ge-data/policies/`)
   - 데이터 종류: **Unstructured documents**
4. 위치(Location): **global**  ← `DATA_STORE_LOCATION`과 동일해야 함
5. 데이터 스토어 이름/ID: **`company-knowledge`**  ← `DATA_STORE_ID`와 **반드시 동일** (mcp-knowledge가 이 ID로 호출)
6. (검색 앱) 안내가 나오면 **Search 앱**을 함께 생성 → 이름 예 `company-knowledge-search` (Preview·그라운딩 테스트에 필요).
7. 생성 후 **Activity/Documents 탭에서 상태가 Importing → Ready** 가 될 때까지 대기(평균 15~30분).
8. **검색 테스트** (Preview 탭):
   - `주택 구매 목적으로 퇴직금 중간정산이 가능한가요?` → `06_FAQ_복지제도` 인용 답변이 나오면 성공.
   - `출장비 정산 절차 알려줘` → `01_출장규정` 인용 확인.

✅ 완료 기준: Preview에서 사규 문서를 **출처로 인용**한 답변이 나온다.

> 참고: `mcp-knowledge`는 데이터 스토어의 `default_config` 서빙 구성을 호출합니다. 만약 검색이 비면 (1) 상태가 Ready인지, (2) `DATA_STORE_ID`/`DATA_STORE_LOCATION`이 일치하는지 확인하세요.

---

## B. OAuth 동의화면 + 인증 정보 (Workspace 커넥터용)

워크샵 도메인을 OAuth 클라이언트로 사전 등록하면, 참가자는 인증 단계 없이 바로 사용 가능합니다.

1. Cloud Console → **APIs & Services → OAuth consent screen**.
2. User Type: **Internal** (회사 도메인 한정 — 검수 불필요). *(외부 도메인이면 External + 검수 필요)*
3. App name: `LG CNS Workshop Gemini Enterprise`, 지원 이메일 입력.
4. **Scopes** 추가:
   - `gmail.modify`, `gmail.compose`
   - `drive.file`, `drive.readonly`
   - `calendar.events.readonly`
5. (External일 때만) **Test users**에 워크샵 참가자 이메일 등록.
6. **APIs & Services → Credentials → `+ CREATE CREDENTIALS` → OAuth client ID** → 유형 **Web application** → 생성.
7. 발급된 **Client ID / Client Secret 메모** (C단계에서 사용).

> ⚠️ Calendar는 `events.readonly`(읽기 전용)만 부여 → 두 에이전트는 일정 **조회**만 합니다. "일정 만들어줘" 같은 생성은 동작하지 않습니다(의도된 범위). 생성까지 필요하면 `calendar.events` scope로 확대.

---

## C. Gemini Enterprise 콘솔 등록 (데이터스토어 · MCP · 커넥터)

Gemini Enterprise 콘솔 → **데이터 스토어 → 만들기**로 아래를 차례로 등록한 뒤, **에이전트 커넥터**에 붙입니다(C-4).

> ⚠️ **실전 검증 메모 (2026-06 기준 UI):** no-code 에이전트의 **커넥터** 목록은 *데이터 스토어*에서 끌어옵니다. 그래서 Custom MCP도 **"데이터 스토어 → 서드 파티 소스 → Custom MCP Server"** 로 등록해야 에이전트에 붙일 수 있습니다. (별도의 "MCP 서버 추가" 도구 레지스트리는 no-code 에이전트 커넥터에 안 뜸)

### C-1. 지식 데이터 스토어 (사규·매뉴얼·FAQ)
콘솔 → **데이터 스토어 → 만들기 → Cloud Storage**
- 경로: **`gs://<BUCKET>/policies/*`** ← 정책 문서만!
- 종류: **Unstructured documents**, 위치: **global**
- ⚠️ **`.md` 금지** — Vertex AI Search는 `text/markdown` 미지원(확장자로 mime 추론). **`.txt`(text/plain)로 업로드** (setup.sh가 자동 처리).
- ⚠️ **CSV 금지** — 디렉토리·거래처 CSV는 여기 넣지 말 것. CSV는 **BigQuery**로 가서 디렉토리/거래처 MCP가 SQL로 조회.
- ⚠️ **데이터 스토어 ID는 자동 생성**됨 (예: `company-knowledge_1780485998844`). 데이터 스토어 상세에서 **실제 ID 확인** 후, mcp-knowledge env를 일치시켜야 함:
  ```bash
  gcloud run services update mcp-knowledge --region asia-northeast3 \
    --update-env-vars DATA_STORE_ID=<실제ID>,DATA_STORE_LOCATION=global
  ```
- 인덱싱 **Ready** + 문서 수 = 정책 개수 확인. Preview 검색으로 사규 인용 확인.

### C-2. Custom MCP Server 등록 (데이터 스토어, OAuth 필수)
디렉토리·거래처·외부회사 3종을 등록 (knowledge는 C-1 데이터스토어로 커버). 콘솔 → **데이터 스토어 → 만들기 → 서드 파티 소스 → Custom MCP Server (프리뷰)**.

**사전 준비 — OAuth 클라이언트 (4종 공용, 1회만)**
- APIs & Services → Credentials → **OAuth client ID → Web application**
- **승인된 리디렉션 URI**에 반드시 추가:
  ```
  https://vertexaisearch.cloud.google.com/oauth-redirect
  ```
- Client ID / Secret 메모

**① 인증 설정** (OAuth 2.0 — 칸이 전부 필수)
| 필드 | 값 |
|---|---|
| MCP Server URL | `https://mcp-directory-...run.app/mcp` (끝에 `/mcp`) |
| Authorization URL | `https://accounts.google.com/o/oauth2/v2/auth` |
| Token URL | `https://oauth2.googleapis.com/token` |
| Client ID / Secret | 위 OAuth 클라이언트 값 |
| **Scopes** | `openid email profile` ← **비우면 "Missing required parameter: scope" 에러** |
| Enable PKCE | 선택 |

→ **로그인** 클릭 → Google 동의 → 통과.
> Cloud Run이 `--allow-unauthenticated`라 서버는 토큰을 무시하고 응답 → 데모 동작 OK. (4종 모두 같은 OAuth 클라이언트 재사용 → redirect URI 재등록 불필요)

**② 고급 옵션** ("사용설명서 두 장 원칙" — Description + Agent Instructions)
- **디렉토리**: `업무 키워드·이름·부서로 담당자(이메일·내선)를 조회하는 MCP.` / `담당자 안내가 필요할 때 담당업무 키워드로 검색한다.`
- **거래/고객**: `거래처/고객 회사 정보와 거래 이력을 조회하는 MCP.` / `외부 미팅·고객 질의 시 회사명으로 거래 이력과 우리쪽 담당자를 확인한다.`
- **외부 회사**: `외부 회사 개요·산업·매출·최근 뉴스를 조회하는 MCP(데모는 샘플).` / `처음 만나는 회사 사전 조사 시 회사명으로 조회한다.`

**③ 구성**: 멀티 리전 **global**(변경 불가), 데이터 커넥터 이름(예: `임직원 디렉토리 MCP`) → **만들기**.

### C-2-1. 등록 시 자주 막히는 지점 (실전)
| 증상 | 원인 | 해결 |
|---|---|---|
| `Import tools` → 400 / Not Acceptable | streamable-HTTP는 단순 fetch 거부 | **Import tools 쓰지 말 것.** OAuth로 진행 |
| `Provided JSON ... does not contain any tools` | toolspec를 배열로 붙임 | `{"tools":[...]}` 로 감싸기 |
| `Missing required parameter: scope` | Scopes 칸 비움 | `openid email profile` 입력 |
| `redirect_uri_mismatch` | redirect URI 미등록 | OAuth 클라이언트에 `vertexaisearch.cloud.google.com/oauth-redirect` 추가 |
| MCP가 404 DataStore not found | mcp env ID ≠ 실제 데이터스토어 ID | env를 자동생성 ID로 업데이트 |
| 에이전트가 "문서 없음" | 데이터스토어 미인덱싱 / .md·CSV 혼입 | .txt만, policies만, Ready 확인 |

### C-3. Google Workspace 커넥터 (Gmail · Drive · Calendar)
각 커넥터마다: 데이터 소스 → 추가 → **Google Workspace** 카테고리 → Gmail/Drive/Calendar 선택 → **B단계의 Client ID/Secret 입력** → scopes 확인 → 저장 → **Test connection ✓** 확인.

✅ 진행자 계정으로 사전 점검:
```
"내일 일정 알려줘"
"받은 메일 중 D메디칼 관련 검색해줘"
"Drive에서 '미팅 브리프' 파일 찾아줘"
```

### C-4. 에이전트에 연결 (실제 사용)
C-1~C-3에서 등록한 것들은 모두 **데이터 스토어/커넥터**가 됩니다. no-code 에이전트에서 사용하려면:
1. **에이전트 → 편집 → 커넥터 → +** 열기
2. 목록에서 **C-1 지식 데이터스토어 + C-2 MCP들 + C-3 Workspace 커넥터**를 선택해 추가
3. **업데이트(저장)** → **미리보기**에서 테스트
> 채팅창의 `/`나 🗄️ 데이터소스 메뉴엔 커스텀 MCP가 안 뜹니다 — **반드시 에이전트 커넥터로** 붙여야 합니다.

### 동작 검증 (MCP 직접 호출)
등록 전/후 MCP가 데이터를 반환하는지 curl로 확인 가능 (초기화 → tools/call):
```bash
URL="https://mcp-knowledge-<번호>.asia-northeast3.run.app/mcp"
SID=$(curl -sS -D - -o /dev/null -X POST "$URL" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"c","version":"0"}}}' | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
curl -sS -X POST "$URL" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SID" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
curl -sS -X POST "$URL" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_company_knowledge","arguments":{"query":"출장비 정산 절차"}}}'
```
→ 문서 내용이 나오면 정상. `404 DataStore ... not found` 이면 C-1의 ID 불일치.

---

## D. 레퍼런스 에이전트 2종 사전 생성 (핸드아웃 부록 A)

참가자가 따라 만들 레퍼런스를 진행자 계정에 미리 만들어 둡니다. 콘솔 → **에이전트 → 새 에이전트** → 빨간 창(설정)에 프롬프트 붙여넣기 → 도구 추가 → 파란 창(테스트) → **생성**.

### D-1. 에이전트 #1 — 외부 미팅 사전 브리핑 (Prebuilt + Custom)
**빨간 창(설정):**
```
Calendar에서 내일 외부 미팅 일정을 찾고, 상대 회사의 개요·최근
뉴스를 검색하고, 우리 회사와의 거래 이력을 확인하며, 우리 쪽
담당자가 누군지 정리해 미팅 1쪽 브리프를 만든 뒤 Drive에 저장하는
에이전트.

출력 형식 (1쪽 브리프, Drive 저장 + 채팅창 표시):
- 미팅 일정 (날짜·시간·참석자)
- 회사 개요 (산업·규모·핵심 사업)
- 최근 뉴스 3건 (제목 + 요약)
- 우리 거래 이력 (있다면 마지막 거래·담당자)
- 우리 쪽 담당자 (부서·이름·연락처)
- 추천 미팅 안건 3개
```
**도구 추가:** Calendar(Prebuilt) · Drive(Prebuilt) · 외부 회사 검색 · 거래/고객 DB · 임직원 디렉토리
**파란 창(테스트):**
```
내일 일정에 외부 미팅 있어? 있으면 사전 브리프 만들어서 Drive에 저장해줘.
```
> 데모 데이터에 `D메디칼`이 거래처 DB·외부회사 양쪽에 있으므로, 진행자 Calendar에 내일자 "D메디칼 미팅" 일정을 하나 넣어두면 브리프가 풍부하게 나옵니다.

### D-2. 에이전트 #2 — 사내 정책·담당자 안내 (Custom + Prebuilt)
**빨간 창(설정):**
```
사내 정책·매뉴얼·FAQ를 검색해 즉답하고, 담당 부서·담당자를
안내하며, 사용자가 원하면 그 담당자에게 보낼 Gmail 초안까지
만드는 사내 도우미 에이전트.

응대 절차:
1. 사내 지식 검색에서 관련 정책·매뉴얼·FAQ 조회
2. 핵심 요약 + 절차 정리
3. 임직원 디렉토리에서 담당 부서·담당자 확인
4. 답변 + 담당자 연결 정보 제공
5. (사용자 요청 시) 담당자에게 보낼 Gmail 초안 생성
   (실제 발송은 사용자가 검토 후 보내기 클릭)

출력 형식:
- 한 줄 답변
- 핵심 정책·절차 (3~5 bullet)
- 인용 출처 (사규 문서명·조항)
- 추가 문의 담당자 (이름·부서·이메일·내선)
- (요청 시) Gmail Draft 링크
```
**도구 추가:** 사내 지식 검색 · 임직원 디렉토리 · Gmail(Prebuilt)
**파란 창(테스트):**
```
다음 주 해외 출장 가는데 출장비·비자 처리 알려주고,
담당자에게 보낼 Gmail 초안도 만들어줘.
```
추가 검증 질의: `연차 미사용분 이월 가능해? 며칠까지?` / `신규 입사자 PC·법인카드 신청은 어떻게 해?` / `재택근무 신청 절차랑 한도 알려줘.`

---

## E. 계정 발급 + 라이선스 + 권한

### E-1. 계정 발급 (Google Workspace 관리 콘솔)
- 100개 계정 생성 (70명 + 예비 30), 예: `workshop-01@회사도메인 ~ workshop-100@회사도메인`
- 또는 기존 도메인 계정 일괄 사용.

### E-2. Gemini Enterprise 라이선스 부여
- Workspace 관리 콘솔 → Gemini Enterprise 라이선스 **100석 부여** → 자동 활성화.

### E-3. 리소스 권한 부여
| 리소스 | 권한 |
|---|---|
| 데이터 스토어 (company-knowledge) | Read |
| Custom MCP 4종 | Use |
| 진행자가 만든 레퍼런스 에이전트 2종 | View (참가자가 동일하게 직접 생성) |

---

## 워크샵 당일 점검 체크리스트
- [ ] 데이터 스토어 인덱싱 **Ready** 상태
- [ ] `gcloud run services list --region asia-northeast3` → MCP 4종 정상
- [ ] **콜드 스타트 방지: 시작 30분 전 4종에 더미 호출로 워밍업**
- [ ] 레퍼런스 에이전트 2종 동작 확인 (D메디칼 미팅 브리프 / 출장비 안내)
- [ ] Workspace 커넥터(Gmail·Drive·Calendar) 진행자 계정 점검
- [ ] 70명 동시 로그인 부하 1회 사전 테스트
- [ ] 보조 진행자 2명 채팅 트러블슈팅 대기
