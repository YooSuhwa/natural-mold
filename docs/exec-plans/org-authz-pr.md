# feat(authz): organizations, tenants, permissions and sharing

## 변경 의도

Moldy를 운영자 1명 + 사용자별 소유 모델에서 **회사(tenant) > 조직 > 구성원·그룹·역할** 구조로 바꾸고,
자원 공유와 실행 권한을 OpenFGA 기반 판정으로 옮깁니다(ADR-023,
[기획서](org-authz-plan.html), [진행 기록](org-authz-progress.md)).
설치형(single_tenant)과 SaaS(multi_tenant)를 같은 코드로 지원합니다.

이 PR은 기획서 17.4의 **PR 1(구현 완료)** 입니다. `AUTHZ_MODE=legacy`, `DB_RLS_ENABLED=false`가 기본값이라
**머지만으로는 권한 판정 동작이 바뀌지 않습니다.** 기존 설치는 업그레이드 시 기본 회사·기본 조직으로 이전되고
기존 사용자는 빌더 역할로 기존 자원을 그대로 씁니다. shadow 관찰, 유형별 강제, legacy 코드 제거는 머지·배포 후
진행합니다(아래 "머지 후 운영 전환").

- 범위: `origin/main` 9657ca60 기준 168 커밋.
- 운영 문서: [조직·권한 운영 가이드](../org-authz-operations.md), [운영 환경 셋업](../operator-setup.md) 7·8절.

## 단계별 요약과 커밋 범위

단계 단위로 보려면 `git log --oneline <범위>`를 씁니다. P6~P8 작업은 같은 시기에 섞여 커밋되어 하나의 범위로 묶고
작업별 커밋을 따로 적었습니다.

| 단계 | 범위 (커밋 수) | 작업 ID | 내용 |
|------|----------------|---------|------|
| P0 기반 | `9657ca60..f0c4d8a1` (17) | A-01~A-13 | 기획·ADR 등록, OpenFGA Compose·설정, SDK 어댑터, 권한 모델·CI(`fga model test`), bootstrap CLI, 가짜 판정·실연동 레인, 라우트 정책·소유자 판정·CSRF·단일 head 래칫, 미들웨어·interrupt·RLS 시제품, 판정 부하 측정, 스키마 초안. 단계 완료 커밋 `f0c4d8a1` |
| P1 조직 데이터 | `f0c4d8a1..90123d0b` (75) | B-01~B-16 | m78~m83(B-01 `941e9247`, B-02 `e6252b9e`), 조직 컨텍스트(B-03 `46519d75`), 회사·배포 방식(B-10 `74ba8620`), 조직 관리 API(B-04 `e7f9306b`), 초대·가입 정책(B-05 `14cff37f`, `1937287d`), 비공개 데이터 조직 필터(B-06 `6c7407a3..5b894c66`), 에이전트 보관 수명주기(B-08 `f26a332a..6d5323ee`), 회사 관리·플랫폼 회사 API와 준비된 실행 lease(B-11/B-12 `2f736445..d7d73941`), 회사 데이터 키(B-16 `56d3d58b`), 회사·플랫폼 세션(B-13 `8dbf5386`), 조직 전환·관리 화면(B-07 `ef456044`, `67c7d0ba`), 세션 회수(B-15 `451fb359`), 전환 시 화면 상태 초기화(B-09 `90123d0b`) |
| P2 권한 모듈 | `90123d0b..c8739d49` (9) | C-01~C-08 | 권한 핵심 테이블·마켓 grant 백필(C-01), AuthzService(C-03), 튜플 투영(C-02), 회수 표식·거부 오버레이(C-08), 섀도 기록(C-06), GrantService·아웃박스(C-04), bootstrap/rebuild/reconcile(C-07), leader 작업(C-05) |
| P3 엔드포인트 전환 | `c8739d49..97eb70ac` (12) | P3-0, D-01~D-10 | lifespan 연결, 공통 라우트 정책, 대화·스킬·도구/MCP/자격증명/모델·에이전트·인증/감사/빌더·어시스턴트·Agent API/트리거·마켓플레이스 라우트 전환, 기존 권한 공백 수정(D-10), 권한 매트릭스 테스트(D-09) |
| P4 공유 | `97eb70ac..64b28cc2` (10) | E-01~E-09 | 회사 게시 정책(E-09), 공유·접근 목록·소유권 이전 API(E-01), 카탈로그 fanout(E-03), 개인 계정 유형(E-05), 공유 화면(E-04), 구성원 제거 정산(E-02), 회사 설정 계층(E-07), 위임 계정 공개(E-06), 보존 정책(E-08) |
| P5 런타임 | `64b28cc2..6c9eab96` (14) | F-01~F-15 | 자격증명 모드·사용자별 연결(F-04), MCP 인가 명세(F-15), 위임 열·동기화(F-01/F-02), 실행 자격증명 해석·복호화 경계(F-05/F-10), 허용 호스트·SSRF(F-11), 위임 검증(F-03), 단일 실행 권한 경계(F-06), MCP 인증 오류(F-12), 공유 메모리(F-09), 귀속(F-14), 별도 실행 경로 재판정(F-13), BLOCKED 기록(F-08), ConnectionGuardMiddleware(F-07) |
| P6·P7·P8 | `6c9eab96..HEAD` (31, 채팅 배너 수정 27f53b6c와 이 문서 커밋 포함) | G-01~G-06, H-01~H-08, I-06~I-09, U-01~U-04 | 아래 표 |

| 작업 | 커밋 |
|------|------|
| G-01 준비 상태 서비스 | `69ef8fff` |
| G-02 막힌 에이전트 409 | `9e864e64` |
| G-03/G-06 auth_required 재개·서버 검증 | `012fd56a` |
| G-04 준비 배지·점검 패널·연결 대화상자 | `0266149e` |
| G-05 인증 카드 복원·OAuth 수신 공용화 | `217fbdf4` (+ F-06 후속 `17d75508`) |
| H-01 접근 요청 | `6ce83305` |
| H-02 알림 | `42c3c595` |
| H-03 조직·회사 감사·사용량 | `f046ac86`, `e592e549` |
| H-04 운영 동기화 화면·지표 | `7be6fee8` |
| H-05 화면 권한 | `bd63b9b8`, `a9750810` |
| H-06 사용 한도 4단계 | `9b4da3b8` |
| H-07 백그라운드 작업 회사 범위 | `bd39f4fe` (+ E-03 후속 `82f33aeb`) |
| H-08 회사 SSO(OIDC, LDAP·AD) | `aebe2589` |
| I-06 회사 RLS(m92)·역할 분리 | `e7c47fe7`, `1eba9024`, `4a54c7e6` |
| I-07 회사 내보내기·삭제·영구 삭제 | `0d10d7eb`, `58c171d0` |
| I-08 회사별 경로·속도 제한·공유 링크·추적 | `d0f75c6c` |
| I-09 내부 구간 TLS·운영 기동 점검 | `f4c0c680` |
| U-01 플랫폼 회사 목록·상세(S03/S04) | `b14ed9a2` |
| U-02 회사 설정(S05~S07) | `d2a79dd4` |
| U-03 조직 설정(S08~S14)·초대 수락 | `85d094c6` |
| U-04 구성원 제거·소유권 이전·보관(S15/S17/S19) | `8122c038` |
| (공통) If-Match 버전 비교 수정 | `cc6c2293` |
| I-05 운영 문서 | 이 PR의 문서 커밋 |

## 리뷰 순서

1. **계약 문서**: ADR-023, 기획서 6~9장, [이벤트 계약](org-authz-event-contract.md), [회사 관리 계약](org-authz-company-administration-contract.md).
2. **P0 모델과 래칫**: `backend/app/authz/model.fga`(+ `model.fga.yaml`), `tests/authz/test_route_policies.py`, `scripts/check_authz_predicates.py`, CSRF·단일 head 테스트.
3. **P1 마이그레이션**: m78~m83과 `app/migrations/org_authz_v1/`(m82 이전·되돌리기 스냅숏). 데이터 이전이 가장 위험한 부분입니다.
4. **P2 권한 모듈**: `app/authz/service*.py`, `tuples.py`, `outbox.py`, `revocations.py`, `reconcile*.py`, `shadow*.py`.
5. **P3 라우트 정책**: `app/authz/policies.py` 다음 라우터별 정책 선언, `tests/authz/test_matrix_*.py`.
6. **P4 공유, P5 런타임 경계**: `prepare_authorized_runtime`, `RuntimeAccessPlanner`, 자격증명 경계, 허용 호스트.
7. **P6~P7**: 준비 상태, auth_required, 요청·알림·감사·한도·SSO.
8. **P8**: RLS 정책과 manifest(`rls_manifest.py`), 회사 삭제, TLS 점검, 화면(U-01~U-04).

## 마이그레이션 목록과 되돌리기 (기획서 15.4)

체인: `m77_side_chat_link`(origin/main) → `m78_tenants_organizations` → `m79_groups` → `m80_org_capabilities_invitations`
→ `m81_org_scope_columns` → `m82_default_org_backfill` → `m83_org_scope_not_null` → `b08_agent_lifecycle`
→ `b11_company_administration` → `c01_authz_core`(m84) → `c01_marketplace_acl_grants`(m85) → `c04_outbox_sequence`
→ `d02_agent_user_preferences` → `f04_credential_modes`(m87) → `f01_connection_delegation`(m86)
→ `f11_credential_definition_hosts` → `f09_agent_memo` → `f14_run_attribution`(m89) → `f08_trigger_run_reason_code`
→ `h01_access_requests`(m88) → `h03_audit_tenant_index` → `h02_notifications`(m88) → `h08_sso_hardening`
→ `h04_authz_reconcile_runs` → `i07_tenant_exports` → `i06_company_rls`(m92, head).

- 모든 revision에 downgrade가 있습니다. 데이터 조건을 확인하고 거부하는 것: `m82`(이전 스냅숏 필요), `m83`, `b08`,
  `i07`(`source_removed` 설치가 있으면 거부). `f01` downgrade는 위임 grant를 지우며 OpenFGA에 남은 튜플은 `authz rebuild`로 정리합니다.
  `i06`은 PostgreSQL에서만 정책을 지우며, 먼저 `rls_activation disable` 후 `DB_RLS_ENABLED=false`로 되돌립니다.
- m90(옛 열·테이블 삭제, 되돌릴 수 없음)과 m91은 이 PR에 없습니다. PR 2(I-04)에서 운영 백업 후 적용합니다.
- 강제 모드 문제는 마이그레이션이 아니라 설정으로 되돌립니다: `AUTHZ_MODE=shadow` 또는 해당 유형을 `AUTHZ_ENFORCE_TYPES`에서 제외.
  legacy 판정 코드는 PR 2 전까지 남아 있습니다.
- OpenFGA 데이터 손상: `python -m app.authz.cli rebuild --apply`로 Postgres에서 재구축.
- revision별 설명은 [운영 가이드 6절](../org-authz-operations.md#6-마이그레이션과-되돌리기).

## 새 설정 (기획서 15.2)

전체 표와 기본값은 [운영 가이드 3절](../org-authz-operations.md#3-새-설정)에 있습니다. 모든 기본값은 기존 동작을 유지합니다.

- 배포·회사: `DEPLOYMENT_MODE`, `DEFAULT_TENANT_NAME`, `DEFAULT_ORG_NAME`, `TENANT_DEFAULT_LIMITS`, `ORG_MULTI_ENABLED`,
  `REGISTRATION_POLICY`, `SSO_ENABLED`, `SSO_PUBLIC_BASE_URL`. 플랫폼 회사 관리는 `multi_tenant` + super_user에서만 노출.
- 권한: `AUTHZ_MODE`(legacy), `AUTHZ_ENFORCE_TYPES`(빈 값 = enforce에서 전체), `OPENFGA_API_URL`, `OPENFGA_API_TOKEN`,
  `OPENFGA_STORE_NAME`/`_ID`, `OPENFGA_MODEL_ID`, `OPENFGA_TIMEOUT_MS`, `OPENFGA_CA_CERT`, `AUTHZ_OUTBOX_POLL_SECONDS`,
  `AUTHZ_RECONCILE_CRON`, `AUTHZ_DELEGATION_CRON`, `AUTHZ_SHADOW_MAX_RPS`, `AUTHZ_TRUSTED_PROXY_CIDRS`.
- DB·암호화·전송: `DB_RLS_ENABLED`(false), `MIGRATION_DATABASE_URL`, `DATA_KEY_PROVIDER`, `TENANT_DATA_KEYS_ENABLED`(false),
  `VAULT_CA_CERT`, `INTERNAL_PLAINTEXT_HOSTS`, `CREDENTIAL_HTTP_ALLOWED_HOSTS`, `OUTBOUND_PRIVATE_ALLOWED_HOSTS`,
  `CREDENTIAL_EMPTY_HOSTS_POLICY`(warn).
- 실행·보존·회사 데이터: `PREPARED_RUN_LEASE_ENABLED`(false), `PREPARED_MCP_DISCOVERY_*_TIMEOUT_SECONDS`,
  `MCP_OAUTH_CLIENT_METADATA_BASE_URL`, `MCP_OAUTH_CLIENT_DEPLOYMENT`, `RETENTION_SWEEP_ENABLED`(false), `RETENTION_SWEEP_CRON`,
  `TENANT_DELETION_GRACE_DAYS`, `TENANT_EXPORT_TTL_DAYS`, `TENANT_PURGE_ENABLED`(false), `TENANT_PURGE_CRON`,
  `TENANT_EXPORT_STALE_MINUTES`, `TENANT_EXPORT_GC_CRON`.
- Compose: 루트 `.env`에 `OPENFGA_API_TOKEN`, `OPENFGA_DB_PASSWORD` 필수. TLS 오버레이는 `docker-compose.tls.yml`.

**운영 영향**: I-09 이후 `APP_ENV=production`에서 내부 URL이 평문이면 기동을 거부합니다. 기존 운영 설치는 배포 전
TLS를 켜거나(같은 호스트 Compose만) `INTERNAL_PLAINTEXT_HOSTS`를 설정해야 합니다.

## 1.3 완료 기준 검증 결과

이 PR을 준비한 환경에는 Docker가 없어 **네이티브 PostgreSQL 레인, OpenFGA 실연동 레인, TLS 레인, Playwright E2E를 실행하지 못했습니다.**
해당 테스트는 작성·등록만 되어 있습니다. 진행 기록의 체크박스 상당수가 비어 있는 이유도 이 네이티브 검증이 남아 있기 때문입니다
(일부 항목의 "uncommitted" 문구는 작성 당시 상태이며 현재는 모두 커밋되어 있습니다).

| 기준 | 상태 | 근거 |
|------|------|------|
| 모든 운영 경로에 권한 정책, 미지정 0건 | 충족(SQLite) | `tests/authz/test_route_policies.py`, `legacy_routes.txt` 기준선 0 |
| 권한 모듈 밖 소유자 판정이 허용 목록만 | 미충족(예정) | 래칫 기준선 173 → 111(`scripts/authz-predicate-baseline.json`). 허용 목록만 남기는 것은 I-03(PR 2) |
| 섀도 14일 불일치 0 후 enforce | 머지 후 | I-01/I-02. 실제 트래픽 필요 |
| OpenFGA 모델 테스트·권한 매트릭스 CI 통과 | 일부 | `fga model test`는 CI `authz-model` 작업. 매트릭스(`test_matrix_*.py`)는 FakeAuthz/SQLite로 실행, OpenFGA 실연동 레인 미실행 |
| 업그레이드 후 기본 조직에서 기존 자원 사용(E2E 회귀) | 미검증 | m82 이전·스냅숏 테스트는 SQLite. 업그레이드 E2E 미실행 |
| 점검 패널·인증 카드 Playwright | 미검증 | `frontend/e2e/readiness-*.spec.ts`, `org-authz-auth-card-oauth.spec.ts` 작성, 미실행 |
| 실행 경로 13개가 단일 권한 경계 통과 | 충족(SQLite) | `tests/authz/test_f06_runtime_paths.py` |
| CSRF 유지, 다른 조직 자원 ID 404 | 충족(SQLite) | `test_csrf_invariant.py`, 매트릭스 테스트. 네이티브 레인 미실행 |
| 신규 경로 OpenAPI 스냅숏 | 충족(SQLite) | `tests/authz/openapi_new_routes.json` |
| 이벤트별 기대 튜플 = 실제 튜플 | 충족(SQLite) | `test_tuples_expected.py`, `test_event_source_contract.py` |
| 마이그레이션 upgrade/backfill/downgrade, m90~m92 백업·복원 기록 | 일부 | SQLite 체인·downgrade 테스트. PostgreSQL upgrade/downgrade와 백업·복원 시험 미실행. m90/m91은 PR 2 |
| 회사 격리 묶음(SaaS, RLS 0행 등) | 미검증 | `test_i06_rls_postgres_integration.py` 등 회사 레인에 등록, 미실행 |
| MCP 인증 명세(2026-07-28) 필수 항목 | 충족(단위) | `tests/authz/test_f15_*.py` |
| axe 심각·치명 0, U-05 과업 성공률 80% | 미충족 | U-05 사용자 시험 미실시. axe 검사 미실행 |

P8 미완료 작업:

- **I-01~I-04**: 머지·배포 후 운영 전환과 PR 2(설계상 PR 1 범위 밖).
- **I-05**: 운영 문서만 완료. 부하·보안 점검은 스테이징에서 `AUTHZ_MODE=enforce`로 실행해야 해서 하지 않았습니다.
  목표 수치(조직 1·사용자 2,000·에이전트 5,000·grant 50,000, Check p95 ≤20ms, BatchCheck 50 p95 ≤60ms 등)와 보안 점검 목록은
  [운영 가이드 9절](../org-authz-operations.md#9-부하보안-점검-i-05--미완료)에 체크리스트로 있습니다.
- **U-05**: 사내 사용자 3~5명 대상 사용자 시험 미실시. **U-01~U-04는 사용자 결정으로 U-05보다 먼저 만들었습니다.**
  U-05 결과에 따라 화면이 바뀔 수 있습니다.
- B-07·B-09 E2E, B-11~B-16 및 C·D·E·F·G·H·I 항목의 네이티브 PostgreSQL 검증.

## 머지 후 운영 전환 (기획서 15.3)

자세한 명령은 [운영 가이드 2절](../org-authz-operations.md#2-업그레이드-절차-기획서-153).

1. OpenFGA 추가, `OPENFGA_API_TOKEN` 설정, `AUTHZ_MODE=legacy` 그대로 배포(마이그레이션, 기본 조직 생성). 배포 전 운영 백업.
2. `python -m app.authz.cli bootstrap` 후 store/model ID 고정. Compose는 bootstrap을 자동 실행하지 않습니다.
3. `authz reconcile --dry-run`으로 차이 0 확인, `catalog-fanout` 재실행.
4. `AUTHZ_MODE=shadow`, 14일 관찰(`authz_shadow_diffs` 0건 목표).
5. `AUTHZ_MODE=enforce` + `AUTHZ_ENFORCE_TYPES`로 유형별 강제: 마켓플레이스 → 도구 → MCP → 스킬 → 자격 증명 → 에이전트.
6. 전 유형 강제 후 2주 안정화 → PR 2(I-03 legacy 코드 제거, I-04 m90/m91).
7. RLS: 역할 분리 → `DB_RLS_ENABLED=true` → `rls_activation enable`([운영 환경 셋업 8절](../operator-setup.md#8-회사-기준-rls와-db-역할-분리-i-06-m92)).
   SaaS 운영은 그 뒤 `DEPLOYMENT_MODE=multi_tenant`.

## 남은 결정

기획서 19장 미결·후속 항목(Q20은 기획서에 없습니다):

- **Q19 메일 발송**: 후속. 이번에는 초대 링크 복사와 앱 안 알림만 있습니다. 발송 수단 결정 필요.
- **Q21 회사별 SSO**: 이번 범위(OIDC, LDAP·AD, 도메인 확인, SSO 필수·비상 관리자, JIT·그룹→역할)는 구현. SAML 직접 구현과 SCIM은 후속.
- **Q22 에이전트 신원과 표준 위임**: subject/actor 구분과 위임 확인은 구현. 외부 IdP 에이전트 ID, OAuth 토큰 교환, CIBA는 후속.
- **Q23 감사 기록 내보내기와 보존**: 회사 정책 보존 기간과 조회·하루 단위 내보내기 범위. 실시간 외부 전송은 후속.

화면 구현 중 생긴 제품 질문:

- `permission_required` 오류 화면의 "권한 요청" 링크가 어디로 가야 하는지(접근 요청 S18 생성 화면 vs 자원 상세).
- 초대 대화상자에서 회사 구성원 한도에 닿았을 때의 처리(막기 vs 경고 후 진행).
- 회사 삭제 예약 후 유예 기간 없이 바로 영구 삭제하는 운영자 동작을 제공할지.
- S03 회사 목록에서 행마다 사용량을 보여줄지(조회 비용).
- 라우트 이름: 기획서는 `/settings/tenant/*`, 구현은 기존 `/settings/company` 접두어 유지. 확정 필요.
- 회사 설정의 `allow_system_models`(플랫폼 모델 허용) 토글을 회사 관리자에게 노출할지.

## PR 체크리스트 (CONTRIBUTING.md)

- [ ] 테스트 통과 (`uv run pytest`, `pnpm build`) — SQLite 백엔드 테스트와 프론트 vitest는 작업별로 실행(진행 기록). 네이티브 PostgreSQL·OpenFGA·TLS·E2E 레인은 CI/스테이징에서 확인 필요
- [ ] 린트 통과 (`uv run ruff check .`, `pnpm lint`) — PR CI에서 확인
- [x] DB 변경 시 alembic 마이그레이션 포함 — 위 마이그레이션 목록
- [x] PR 본문에 변경 의도 + 검증 결과 명시
- [ ] 관련 이슈 번호 연결 (있을 경우)

chester

https://claude.ai/code/session_017vnRdiuRM5342zQCXuW1JL
