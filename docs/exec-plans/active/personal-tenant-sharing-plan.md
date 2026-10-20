# 개인 테넌트·회사 키·공유 공백 실행 계획

Status: approved 2026-10-21 (Q1–Q8 추천안 채택) — 구현 중
Branch: `feature/personal-tenant` (단계별 커밋, 한 브랜치; 5절 PR 구분은 커밋 묶음으로 유지)
Baseline: `24c29186` (PR #316 머지 직후 main)
Related: [조직·권한·공유 실행 기록](org-authz-progress.md), [ADR-023](../../design-docs/adr-023-org-authz.md)

## 1. 목표와 배경

- 모르는 사람끼리 가입해 쓰는 설치에서도 사람마다 회사(테넌트) 경계로 분리한다(개인 테넌트).
- 회사가 LLM 키와 도구·MCP 인증을 한 번에 제공하고 구성원은 그대로 쓰게 한다(회사 키).
- legacy 권한 제거(I-03) 전에 개인 사용 경로와 공유 공백을 닫는다.

판단 근거(2026-10-21 대화): 테넌트는 RLS 보안 경계(`i06_company_rls`)다. 개인 사용자 전원을
공용 테넌트 하나에 넣으면 `organization#member` 공유와 조직 `governor` 권한이 모르는 사람에게까지
닿으므로, 가입마다 개인 테넌트를 하나씩 만든다.

## 2. 이미 내린 결정 (PR-A에 구현됨, 검토 필요)

| # | 결정 | 이유 |
|---|---|---|
| A1 | 새 값 `REGISTRATION_POLICY=personal_tenant`. 기본값은 지금처럼 `default_org` | 사내 설치 동작을 바꾸지 않음 |
| A2 | `personal_tenant`는 `DEPLOYMENT_MODE=multi_tenant`에서만 허용(시작 시 검증) | `single_tenant`는 회사 간 파일 저장을 공유하고 회사 관리자가 자기 한도를 바꿀 수 있음 |
| A3 | 첫 가입자(`ALLOW_FIRST_USER_AS_ADMIN`)는 그대로 기본 회사 운영자 | 설치 운영자 부트스트랩 유지 |
| A4 | 초대 가입은 초대한 조직으로 들어감(개인 테넌트 없음) | 초대 의미 유지 |
| A5 | 기존 사용자는 이전하지 않음(기본 회사에 그대로) | 리소스·권한 데이터 이전 위험 |
| A6 | 개인 테넌트 표시는 `tenants.settings.kind = "personal"`(마이그레이션 없음) | 열 추가 없이 구분 |
| A7 | 가입자는 개인 회사·조직의 관리자 | 나중에 사람을 초대해 팀으로 전환 |
| A8 | 한도는 `TENANT_DEFAULT_LIMITS`에서 지정된 키만 저장, 없는 키는 무제한 | 기존 설정 재사용 |
| A9 | 플랫폼 카탈로그 연결(fanout)은 가입 커밋 후 플랫폼 세션에서 수행(실패 시 로그, `cli catalog-fanout --org`로 복구) | 가입 세션은 identity 범위라 RLS에서 회사 grant를 못 씀 |
| A10 | **범위 추가**: RLS를 켜면 모든 가입이 500이던 기존 버그 수정(`i08_outbox_request_enqueue`, outbox INSERT만 요청 범위 허용, RETURNING 제거) | 새 RLS 테스트가 발견. 개인 테넌트가 RLS에서 동작하려면 필수 |

목록에서 바뀐 점: 원래 1-3 "운영자와 테넌트 관리자 분리"는 별도 작업이 필요 없었다. `multi_tenant`에서는
이미 플랫폼 운영(super_user)과 회사 관리(TenantRoleGrant)가 분리돼 있고, A3만 정리했다.

## 3. 작업 항목

### P1-6 마켓플레이스 회사 경계 (PR-A에 추가)

- 현재: legacy·shadow 목록·상세·버전·설치가 쓰는 `catalog_view_candidate_predicate`
  (`backend/app/authz/catalog_scope.py:45-81`)가 `published` + `public/unlisted` +
  `tenant_publication in (none, approved)` 항목을 **모든 회사**에 보여준다. enforce는 이미 같은 회사로
  제한(`marketplace_access.py:97-103`). 기본 모드가 legacy라 개인 테넌트에서 지금 바로 노출된다.
- 변경: 공개 분기에 `tenant_id == context.tenant_id OR tenant_id IS NULL` 추가. 같은 회사 안의
  다른 조직에 보이는 기존 legacy 범위(`test_e09_publication_legacy.py:82-96`)는 유지.
- 테스트: 두 회사 legacy 테스트(목록·상세·버전·설치 모두 404/비노출), 같은 회사 다른 조직은 계속 노출,
  뮤테이션(조건 제거 시 실패) 확인.
- 위험: 낮음. 회사 간 공개 마켓플레이스를 의도한 설치는 없다(enforce와 동작 일치).

### P1-5 개인 테넌트 한도 검증 (PR-A에 추가)

- 발견된 결함(PR-A 코드): `TENANT_DEFAULT_LIMITS`에 `request_rate_limit`(실제로 집행되는 키,
  `limit_capacity.py:101-103`)을 넣으면 `TenantLimitSummary(extra="forbid")`가 거부해 **모든 개인 가입이
  실패**한다. 시작 시 검증도 없다.
- 변경: 허용 키를 집행되는 한도 전체(`member_limit`, `monthly_spend_limit`, `concurrent_run_limit`,
  `request_rate_limit`, `allow_system_models`)로 맞춘 전용 스키마로 검증하고, `personal_tenant`일 때
  설정 로드 시점에 잘못된 JSON·키를 거부.
- 테스트: 각 키 저장, 알 수 없는 키·음수는 시작 거부.

### P1-5b 버려진 개인 테넌트 정리 (보류 권장)

- 현재: 계정 삭제 라우트가 아직 없다(`user_service.delete_user`, "No route calls this yet").
  삭제 경로가 생기면 마지막 구성원이 사라진 개인 테넌트가 `active`로 남는다. 테넌트 삭제 예약은
  운영자 전용(`platform/tenant_deletion.py`), 정리 잡 `tenant_purge`는 기본 꺼짐.
- 권장: 계정 삭제 기능을 만들 때 "개인 테넌트의 마지막 구성원 삭제 → 그 테넌트 삭제 예약(시스템 행위자,
  30일 유예)"을 같이 넣는다. 비활성 기간 기반 자동 삭제는 하지 않는다(알림 체계 없음).

### P1-4 혼자인 회사 화면 (PR-B, 프론트 중심)

- 현재: 설정 사이드바가 회사·조직 메뉴를 항상 보여준다(`settings-sidebar.tsx:103-113`,
  `company-nav-items.ts`, `organization-nav-items.ts`). 조직 전환기는 조직이 1개여도 드롭다운을 그린다
  (`organization-switcher.tsx:30`). 테넌트 종류를 프론트에 주는 API가 없다.
- 변경:
  1. 백엔드: `OrganizationSummary`에 `tenant_kind` 추가(`org_service.list_user_organizations`가 이미
     Tenant를 읽음, 추가 쿼리 없음).
  2. 프론트: `solo = tenant_kind === 'personal' && active_member_count === 1`이면 회사·조직 하위 메뉴를
     접고 "사람 초대해서 팀으로 쓰기" 항목 하나만 표시 → `/settings/company/members?invite=1`
     (쿼리로 초대 대화상자 자동 열기).
  3. 조직이 1개면 전환기는 이름만 표시.
- 화면 숨김일 뿐 권한은 그대로다(관리 라우트는 계속 존재).
- 테스트: 사이드바·전환기 vitest, organizations 응답 백엔드 테스트, i18n ko/en.

### P0-4 커버리지 측정 명령 갱신 (PR-C)

- 현재 기록(`backend/quality/coverage-baseline.json:4`)은 직렬 명령이지만 CI는 `--workers 4`로 측정.
- 변경: JSON, `scripts/check_coverage_baseline.py` 상수, `tests/test_coverage_governance.py`를 함께
  갱신. 정책상 `reviewed_runs` 2회 측정 기록 필요.

### P0-5 안 쓰는 코드 정리 (PR-C)

- `CredentialDefault`(`credential_defaults` 테이블): 읽는 코드 없음. 새 마이그레이션으로 테이블 삭제,
  RLS 분류(`rls_manifest.py:44`)에서 제거, 관련 테스트 정리. 동결된 m81 계약 파일은 손대지 않음.
- `Credential.is_shared`: 권한 로직은 안 읽지만 API·UI 라벨로 노출되고, 삭제가 이미 m90 초안
  (`schema_drafts/org_authz/m90_drop_legacy_authz.py`, I-04 게이트)에 있다. **I-04에 맡기고 지금은
  손대지 않음**을 권장.

### P2-1·2-2 회사 LLM 키를 에이전트 채팅에 사용 (PR-D, 보안 경계 변경)

- 현재(`agent_runtime/credential_resolution.py`): T1 에이전트 키(본인 또는 enforce 위임) → T2 모델
  기본 키(본인 소유만, `get_for_user`) → T3 같은 provider 개인 키 → 없으면 422. 회사·플랫폼 키는 어디에도
  없음(ADR-016 §4.2). 채팅·Agent API·트리거·하위 에이전트가 모두 이 함수를 거친다.
- 회사 키 지정은 `tenants.settings["credential_orgs"]`(legacy 판정 `company_tier.credential_reaches`),
  enforce는 `system_fanout` grant → `credential#can_use`.
- 변경(권장 순서):
  - T2: 모델 기본 키가 **회사 키이고 실행 조직에 지정**돼 있으면 사용.
  - T4(신규, 마지막): 같은 provider의 **실행 조직에 지정된 활성 회사 키**.
  - 판정 주체는 **사용자**(에이전트 아님 — `agent:A`는 조직 member가 아니라 고정 신원·트리거 런이 실패함,
    `run_resolution._usable`과 같은 방식). legacy는 지정 JSON, shadow/enforce는 `can_use`.
  - 플랫폼 키는 계속 제외. 플랫폼 모델 `default_credential_id`는 SUPER 생성·수정 시 플랫폼 키 또는
    NULL만 허용하도록 검증 추가(지금은 아무 id나 저장됨).
- 비용: 지금처럼 요청자(트리거·API는 소유자) 기준 집계·한도 적용. `token_usages`에 `credential_scope`
  (personal/tenant) 열을 추가해 회사 키 사용량을 구분.
- 문서: ADR-016 §4.2 개정(회사 키 허용, 플랫폼 키 제외 유지).
- 테스트: 지정/미지정, 다른 회사, 비활성 키, 고정·사용자별 신원, legacy/enforce, 트리거·Agent API 경로,
  시크릿 마스킹(`conversation_history_secrets.py:73-81`)에 회사 키 포함.
- 위험: 다른 조직에 공유된 에이전트가 미지정 조직에서 회사 키를 쓰는 경우 → 실행 조직 기준 재검증으로 차단.

### P2-3·2-4 도구·MCP 회사 키 + 인증 모드 화면 (PR-E)

- 현재: `shared` 모드는 리소스 소유자의 개인 키만, `per_user`는 본인 키만(`run_resolution.py:118-191`).
  키 선택 검증(`credentials/validation.py`)이 회사 키를 모든 모드에서 거부. MCP 탐색(probe)은 플랫폼 서버에
  플랫폼 키를 허용하지만 실행은 거부하는 **불일치** 있음. 모드 전환 API는 있으나 화면이 없고, 도구·MCP 응답에
  `credential_mode` 필드도 없음.
- 변경:
  - `shared` 모드에서만 회사 키 허용(같은 회사, 실행 조직 지정 재검증, 사용자 주체). `per_user`는 개인 키 유지.
  - 키 선택 게이트, `_shared`, MCP probe에 회사 키 분기. `_require_reshare`가 회사 키를 남의 키로 보는 문제 수정.
  - probe/실행의 플랫폼 키 불일치는 실행 기준(거부)으로 통일.
  - 응답에 `credential_mode`, `credential_definition_key` 추가 → 도구 상세·MCP 카드에 모드 전환(편집 권한자만).
- 테스트: `test_f05_credential_resolution.py`, `test_runtime_credential_scope.py`, `test_f04_credential_modes.py`, 프론트 대화상자.

### P2-5 스킬 공유 화면 (PR-F, 작음)

- 백엔드는 이미 지원(`share_targets.py:46`, `resource_shares.py:138-139`). 프론트 스킬 행에 공유 버튼만 없음.
- 주의: `SkillResponse`에 `user_id`가 없어 legacy에서 버튼 노출 판정이 항상 참이 됨 → `user_id`를 추가하거나
  `useResourceAccess('skill')` 권한을 전달.
- 테스트: `skill-studio-permissions.test.tsx`, `test_e01_share_rules.py`.

### P2-6 마켓플레이스 공개 범위를 그룹·조직 단위로 (보류 권장)

- 현재 ACL 테이블은 사용자만, 전체 교체 방식. legacy 판정도 사용자 ACL만. `marketplace_item_acl`은 I-04에서
  grant로 대체될 예정.
- 권장: legacy 테이블을 확장하지 말고 I-04 이후 grant 기반으로 구현(지금 하면 버릴 코드).

## 4. 사용자 결정이 필요한 질문 (권장안 포함)

| # | 질문 | 권장 |
|---|---|---|
| Q1 | 회사 키 우선순위: 개인 키보다 앞? 뒤? | 개인 키 뒤(T4). 단 모델에 회사 키가 기본으로 지정되면 T2에서 사용 |
| Q2 | 회사 키 사용 비용 집계 | 요청자 기준 유지 + `credential_scope` 열 추가 |
| Q3 | ADR-016 개정(회사 키 허용, 플랫폼 키 제외 유지) | 예 |
| Q4 | 도구·MCP 회사 키 허용 모드 | `shared`만 |
| Q5 | `is_shared` 삭제 시점 | I-04(m90)에 맡김 |
| Q6 | P2-6 마켓플레이스 그룹·조직 공개 | I-04 이후로 보류 |
| Q7 | 버려진 개인 테넌트 정리 | 계정 삭제 기능 구현 시 함께. 비활성 기반 자동 삭제는 안 함 |
| Q8 | 혼자 판정에 대기 중 초대 포함? | 포함 안 함(활성 구성원 1명이면 혼자) |

## 5. PR 순서와 검증 게이트

| PR | 내용 | 비고 |
|---|---|---|
| PR-A | P1-1(완료) + RLS 가입 버그(완료) + P1-6 + P1-5 한도 검증 | 백엔드 |
| PR-B | P1-4 혼자인 회사 화면 | 프론트 중심 |
| PR-C | P0-4, P0-5(`CredentialDefault`만) | 정리 |
| PR-D | P2-1·2-2 회사 LLM 키 + ADR-016 개정 | Q1~Q3 결정 후 |
| PR-E | P2-3·2-4 도구·MCP 회사 키 + 모드 화면 | Q4 결정 후 |
| PR-F | P2-5 스킬 공유 화면 | |

모든 PR 공통 게이트:

- 백엔드 단위 전체(`-n 4`/`-n 8`), 관련 PostgreSQL 통합(RLS 켠 상태 포함), 프론트 vitest 전체·tsc·eslint.
- 새 권한·경계 테스트는 해당 조건을 제거한 뮤테이션에서 실패하는지 확인.
- 공유/메인 DB에 마이그레이션 금지(일회용 DB만).
- legacy와 enforce 양쪽 동작을 테스트로 고정.

## 6. 진행 기록

- 2026-10-21: PR-A 1차 커밋 `664c0ce0`(P1-1 + RLS 가입 버그). 계획 문서 `5dd201bb`.
- 2026-10-21: 사용자가 Q1–Q8 추천안 승인, 전체 개발 진행 지시.
- P1-6 `bf0d5cbf`, P1-5 한도 검증 `7d4f06f4`, P1-4 `874bed9f`.
- **P0-4 보류(사용자 결정 필요)**: CI 명령(`-n 4`)으로 2회 측정한 결과 85.76%(66,650/77,715, 2회차 66,649)로,
  기존 기준(`ea253c19`, 80.21%)보다 높다. 측정 명령만 바꾸면 기록 수치와 명령이 맞지 않고, 제대로 갱신하면
  하한이 85.76%로 올라 여유가 0이 된다(측정마다 1줄 차이가 나서 CI가 흔들림). 커버리지 정책 변경이라 보류.
- **P0-5 보류 → I-04**: `credential_defaults`는 m81 DDL·m77 동결 계약·schema_drafts·마이그레이션 왕복 테스트에
  묶여 있어, `is_shared`와 함께 m90(I-04)에서 정리한다.
- P2-1·2-2 구현: `app/credentials/company_use.py`(회사 키 판정 공용), 해석 순서 T2 회사 기본 키 + T4 회사 키,
  기록 마스킹에 회사 키 포함, ADR-016 §9 개정.
- **편차(Q2)**: `token_usages.credential_scope` 열은 넣지 않았다. 사용량 행은 런의 귀속 정보(F-14)를 복사해
  만들어지므로, 열을 쓰려면 대화·트리거·Agent API 세 런 종류에 키 출처를 저장·전달해야 한다(범위가 큼).
  비용 집계는 지금처럼 요청자 기준이며, 열 추가는 후속 작업으로 남긴다.
- **편차(P2-2)**: 플랫폼 모델 `default_credential_id`를 "플랫폼 키 또는 NULL"로 제한하는 검증은 넣지 않았다.
  운영자 모델 추가 화면(`model-add-dialog.tsx`)이 운영자 본인의 개인 키를 기본 키로 저장하는 정상 흐름이라,
  제한하면 그 흐름이 깨진다. 실행 시 남의 개인 키는 계속 건너뛴다.
- P2-3 구현: `shared` 모드만 회사 키 허용(`require_credential_use(allow_company=True)`는 도구·MCP 연결에만,
  사용자별 연결은 그대로 개인 키), 실행 시 `RunCredentialResolver._company`가 실행 조직 지정을 다시 판정,
  MCP 탐색(`require_mcp_probe_scope`)·MCP Apps 인증·모드 전환 재공유 가드에 회사 키 분기.
- **편차(P2-3)**: MCP 탐색은 플랫폼 서버에 플랫폼 키를 허용하고 실행은 거부하는 불일치는 손대지 않았다.
  실행 기준으로 맞추면 시드된 플랫폼 MCP 서버의 탐색이 깨질 수 있어, 영향 조사가 먼저 필요하다(후속).
