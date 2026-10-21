# 조직·권한 운영 가이드 (org-authz)

회사(tenant)·조직·권한(OpenFGA)·공유 기능을 운영 환경에 올리고 단계적으로 켜는 절차입니다.
기준 문서는 [기획서](exec-plans/active/org-authz-plan.html) 15장(배포와 설정), 16.6(성능과 보안),
17장 I-05이고, 구현과 다르게 정해진 결정은 [진행 기록](exec-plans/active/org-authz-progress.md)을 따릅니다.

기본 운영 수칙(`APP_ENV=production` 부팅 검증, 첫 운영자 계정)은 [운영 환경 셋업](operator-setup.md)에
있습니다. 이 문서는 그 위에 조직·권한 기능에 필요한 것만 다룹니다.

> **상태**: 이 PR(PR 1)은 `AUTHZ_MODE=legacy`, `DB_RLS_ENABLED=false` 기본값으로 머지됩니다.
> 머지만으로는 권한 판정 동작이 바뀌지 않습니다. 아래 전환 절차는 배포 후 운영자가 설정을 바꿔
> 진행합니다. 부하·보안 점검(I-05 앞부분)은 아직 하지 않았습니다([9절](#9-부하보안-점검-i-05--미완료)).

---

## 1. 전환 단계 한눈에 보기

| 단계 | 설정 | 판정 주체 | 되돌리기 |
|------|------|-----------|----------|
| 0. legacy | `AUTHZ_MODE=legacy` (기본) | 기존 소유자 판정. OpenFGA를 부르지 않음 | — |
| 1. shadow | `AUTHZ_MODE=shadow` | 기존 판정으로 응답, OpenFGA 판정은 기록만 (`authz_shadow_diffs`) | `AUTHZ_MODE=legacy` |
| 2. 유형별 enforce | `AUTHZ_MODE=enforce` + `AUTHZ_ENFORCE_TYPES=<유형,...>` | 목록에 있는 유형만 OpenFGA 판정 | 해당 유형을 목록에서 빼거나 `AUTHZ_MODE=shadow` |
| 3. 전체 enforce | `AUTHZ_MODE=enforce` + `AUTHZ_ENFORCE_TYPES` 전 유형 나열 또는 빈 값 | 모든 유형 OpenFGA 판정 | 2단계와 같음 |
| 4. RLS | `DB_RLS_ENABLED=true` + `rls_activation enable` | DB 행 단위 회사 경계 추가 | `rls_activation disable` 후 `DB_RLS_ENABLED=false` |

**주의**: `AUTHZ_ENFORCE_TYPES`가 빈 값이면 enforce 모드에서 **모든 유형을 한 번에 강제**합니다
(`app/authz/service_core.py` `enforces`). 2단계에서는 반드시 유형을 명시합니다. 모르는 유형 이름은
설정 오류로 기동이 실패합니다(`parse_enforce_types`). 유형 이름은 `backend/app/authz/model.fga`의
`type` 이름(`marketplace_item`, `tool`, `mcp_server`, `skill`, `credential`, `agent` 등)입니다.

legacy 판정 코드는 정리 PR(PR 2, I-03) 전까지 남아 있으므로 1~3단계는 설정만으로 즉시 되돌릴 수 있습니다.

---

## 2. 업그레이드 절차 (기획서 15.3)

### 2.1 0단계 — OpenFGA 추가와 legacy 배포

1. 루트 `.env`(Compose 보간용)에 `OPENFGA_API_TOKEN`(preshared 키)과 `OPENFGA_DB_PASSWORD`를 넣습니다.
   `backend/.env`만으로는 Compose 보간이 되지 않습니다. 두 값이 없으면 기본 `docker-compose.yml`은
   개발용 기본값으로 뜨고([Docker 빠른 시작](docker-quickstart.md)), TLS 오버레이는 기동을 거부합니다.
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # 각각 따로 생성
   ```
2. 운영 DB 백업을 받습니다. m82(기본 회사·조직 이전)는 되돌리기에 이전 스냅숏이 필요하고,
   I-07 이후 일부 downgrade는 데이터 조건을 확인한 뒤 거부합니다([6절](#6-마이그레이션과-되돌리기)).
3. `AUTHZ_MODE=legacy` 그대로 배포합니다. Compose의 1회성 `migrate` 서비스가 backend보다 먼저
   `alembic upgrade head`를 실행하며
   m78~i06이 적용되고 기본 회사(`DEFAULT_TENANT_NAME`)와 기본 조직(`DEFAULT_ORG_NAME`)이 만들어집니다.
   기존 사용자는 기본 조직의 빌더 역할로 들어가 기존 자원을 그대로 봅니다(Q2).
4. OpenFGA 포트는 외부에 열지 않습니다. backend만 내부 네트워크로 접근합니다.

### 2.2 bootstrap과 대조

Compose로 설치하면 1회성 `authz-bootstrap` 서비스가 `migrate` 뒤, backend 앞에서 매 기동마다
bootstrap을 실행하고(기획서 15.1) 출력의 store ID와 model ID를 `moldy_runtime` 볼륨의 `openfga.env`에
고정합니다. backend는 `OPENFGA_STORE_ID`, `OPENFGA_MODEL_ID`가 비어 있을 때만 이 값을 씁니다.
모델이 바뀌지 않았으면 같은 ID를 다시 씁니다. 고정된 값은 `docker compose logs authz-bootstrap`으로
확인합니다.

Compose 밖에서 설치했다면 운영자가 한 번 직접 실행합니다.

```bash
cd backend
uv run python -m app.authz.cli bootstrap          # store·모델 생성, 기대 튜플 전체 기록, 건수 출력(JSON)
```

출력의 store ID와 model ID를 `OPENFGA_STORE_ID`, `OPENFGA_MODEL_ID`에 넣고 재시작합니다. 두 값이
비어 있으면 shadow/enforce에서 아웃박스·대조·만료 작업이 등록되지 않습니다(경고 로그
`authz jobs not scheduled`). 이어서 차이를 확인합니다.

```bash
uv run python -m app.authz.cli reconcile --dry-run --json      # 기본이 dry run. 차이가 있으면 종료 코드 1
uv run python -m app.authz.cli catalog-fanout --actor <운영자 user id>   # 카탈로그 정책 fanout
```

`catalog-fanout`은 shadow/enforce로 바꾸기 **직전에 다시** 실행합니다. legacy 모드에서 바꾼 카탈로그
정책은 저장만 되고 튜플로 투영되지 않기 때문입니다.

### 2.3 1단계 — shadow 14일 관찰 (I-01)

1. `AUTHZ_MODE=shadow`로 재시작합니다. 응답은 계속 기존 판정을 따릅니다.
2. 14일 동안 `authz_shadow_diffs` 0건을 목표로 관찰합니다([8절](#8-모니터링)). 불일치가 나오면
   원인을 기획서 9장 정책 또는 이전 규칙에서 찾아 고치고, 고친 뒤부터 다시 셉니다.
3. 섀도 판정은 `AUTHZ_SHADOW_MAX_RPS`(기본 50)를 넘으면 표본을 버립니다. 버린 건수는
   `authz_shadow_summary` 로그에 남으므로 0건 판단 전에 함께 확인합니다.

### 2.4 2·3단계 — 유형별 강제 (I-02)

기획서 순서대로 한 유형씩 추가하고, 유형마다 거부 오류·문의를 확인한 뒤 다음으로 넘어갑니다.

```bash
AUTHZ_MODE=enforce
AUTHZ_ENFORCE_TYPES=marketplace_item
AUTHZ_ENFORCE_TYPES=marketplace_item,tool
AUTHZ_ENFORCE_TYPES=marketplace_item,tool,mcp_server
AUTHZ_ENFORCE_TYPES=marketplace_item,tool,mcp_server,skill
AUTHZ_ENFORCE_TYPES=marketplace_item,tool,mcp_server,skill,credential
AUTHZ_ENFORCE_TYPES=marketplace_item,tool,mcp_server,skill,credential,agent
```

enforce에서는 OpenFGA 장애 시 판정이 차단(fail closed)됩니다. 사용자 전용 데이터 경로는 영향이 없습니다.
전 유형 강제 후 2주 안정화되면 정리 PR(PR 2)에서 legacy 판정 코드 제거(I-03)와 m90/m91 옛 열·테이블
삭제(I-04)를 진행합니다. **m90은 데이터 삭제이므로 운영 백업 후 적용합니다.**

### 2.5 4단계 — RLS

RLS 활성화, 앱·마이그레이션 DB 역할 분리, `REASSIGN OWNED` 처리는
[운영 환경 셋업 8절](operator-setup.md#8-회사-기준-rls와-db-역할-분리-i-06-m92)의 절차를 그대로 따릅니다.
순서만 요약하면 다음과 같습니다.

1. 앱 역할(`NOBYPASSRLS`, 테이블 소유자 아님)과 마이그레이션 역할을 나눕니다.
2. `MIGRATION_DATABASE_URL`(마이그레이션 역할), `DATABASE_URL`·`DATABASE_URL_SYNC`(앱 역할) 설정 후
   `alembic upgrade head`.
3. `DB_RLS_ENABLED=true`로 앱 재시작(세션이 회사 값을 설정하기 시작, 정책은 아직 꺼짐).
4. 마이그레이션 역할 환경에서 `python -m app.migrations.org_authz_v1.rls_activation enable`.
5. `python -m app.tenancy.checkpoint_audit --json`으로 체크포인트 회사 대조(불일치 시 종료 코드 1).

RLS는 네이티브 PostgreSQL 레인 검증을 아직 돌리지 않았으므로(아래 9절) **스테이징에서 먼저** 켭니다.
SaaS로 운영하려면 그 뒤 `DEPLOYMENT_MODE=multi_tenant`로 바꾸고 회사를 추가합니다. 외부 비밀 저장소를
쓰면 회사별 OpenBao 네임스페이스를 만들고 `tenants.secrets_namespace`에 기록합니다.

---

## 3. 새 설정

정의는 `backend/app/authz/config.py`(`AuthzSettings`)와 `backend/app/config.py`, 예시는
`backend/.env.example`에 있습니다.

### 3.1 배포 방식과 회사

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DEPLOYMENT_MODE` | `single_tenant` | `single_tenant`(설치형) / `multi_tenant`(SaaS). 플랫폼 운영 회사 관리 화면(S03/S04)과 `/api/admin/tenants/*`는 `multi_tenant` + super_user일 때만 열림(`platform_tenant_management_enabled`, 그 외 404) |
| `DEFAULT_TENANT_NAME` | `기본 회사` | m82 이전과 설치형 첫 회사 이름 |
| `DEFAULT_ORG_NAME` | `기본 조직` | m82 이전 시 기본 조직 이름 |
| `TENANT_DEFAULT_LIMITS` | `{}` | SaaS 새 회사의 기본 한도 JSON. `personal_tenant` 가입으로 만든 개인 회사에도 적용(지정한 키만 저장, 없는 키는 무제한). 키: `member_limit`, `monthly_spend_limit`, `concurrent_run_limit`, `request_rate_limit`, `allow_system_models`. `personal_tenant`이면 잘못된 값은 시작 시 거부 |
| `ORG_MULTI_ENABLED` | `true` | `false`면 회사당 조직 1개, 조직 만들기 숨김 |
| `REGISTRATION_POLICY` | `default_org` | `default_org` / `personal_tenant` / `invite_only` / `closed`. `personal_tenant`는 `DEPLOYMENT_MODE=multi_tenant`에서만 시작됨: 초대 없는 가입마다 개인 회사(`settings.kind=personal`)와 기본 조직을 만들고 가입자를 회사·조직 관리자로 둠. 첫 가입자(`ALLOW_FIRST_USER_AS_ADMIN`)는 그대로 기본 회사 운영자. 초대 가입은 초대한 조직으로 들어감 |
| `SSO_ENABLED` | `true` | 회사별 SSO 메뉴 노출 |
| `SSO_PUBLIC_BASE_URL` | 빈 값 | IdP Redirect URI에 쓰는 공개 URL(없으면 요청 URL) |

### 3.2 권한 판정과 OpenFGA

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUTHZ_MODE` | `legacy` | `legacy` / `shadow` / `enforce` |
| `AUTHZ_ENFORCE_TYPES` | 빈 값 | enforce에서 강제할 유형(콤마). **빈 값 = 전체** |
| `OPENFGA_API_URL` | `http://openfga:8080` | 운영 모드에서는 https 필수(7절) |
| `OPENFGA_API_TOKEN` | 빈 값 | preshared 키. shadow 이상에서 필수 |
| `OPENFGA_STORE_NAME` | `moldy` | bootstrap이 찾거나 만드는 store 이름 |
| `OPENFGA_STORE_ID`, `OPENFGA_MODEL_ID` | 빈 값 | bootstrap 결과로 고정. 비어 있으면 leader 작업 미등록 |
| `OPENFGA_TIMEOUT_MS` | `800` | 단건 판정 제한 시간 |
| `OPENFGA_CA_CERT` | 빈 값 | 사설 CA 경로 |
| `AUTHZ_OUTBOX_POLL_SECONDS` | `2` | 아웃박스 반영 주기 |
| `AUTHZ_RECONCILE_CRON` | `0 3 * * *` | 일일 대조 |
| `AUTHZ_DELEGATION_CRON` | `30 3 * * *` | 일일 위임 검증(F-03) |
| `AUTHZ_SHADOW_MAX_RPS` | `50` | 섀도 판정 상한 |
| `AUTHZ_TRUSTED_PROXY_CIDRS` | 빈 값 | user_ip 계산 시 신뢰할 프록시 |

### 3.3 DB·암호화·전송 구간

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DB_RLS_ENABLED` | `false` | m92 정책 적용 후 켬(2.5절) |
| `MIGRATION_DATABASE_URL` | 빈 값 | 마이그레이션 역할 접속. 설정하면 Alembic이 이 URL을 씀 |
| `DATA_KEY_PROVIDER` | `local` | `local`(ENCRYPTION_KEYS로 회사 데이터 키를 감쌈) / `transit`(OpenBao·Vault Transit) |
| `TENANT_DATA_KEYS_ENABLED` | `false` | 회사별 데이터 키와 암호문 v2(B-16) |
| `VAULT_CA_CERT` | 빈 값 | OpenBao·Vault 사설 CA |
| `INTERNAL_PLAINTEXT_HOSTS` | 빈 값 | 운영 기동 점검에서 평문을 허용할 내부 호스트(7절) |
| `CREDENTIAL_HTTP_ALLOWED_HOSTS` | 빈 값 | 자격 증명을 http로 보내도 되는 내부 호스트 |
| `OUTBOUND_PRIVATE_ALLOWED_HOSTS` | 빈 값 | SSRF 사설 IP 차단 예외 호스트 |
| `CREDENTIAL_EMPTY_HOSTS_POLICY` | `warn` | 허용 호스트가 빈 자격 증명 처리(`warn` / `enforce`, D18·Q14) |

### 3.4 실행·보존·회사 데이터 작업

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PREPARED_RUN_LEASE_ENABLED` | `false` | 실행을 준비된 lease(잠금 아래 구체화 후 고정 그래프 실행)로 돌림. 끄면 기존 `_prepare_agent` 경로 |
| `PREPARED_MCP_DISCOVERY_SERVER_TIMEOUT_SECONDS` / `..._TOTAL_TIMEOUT_SECONDS` | `3.0` / `5.0` | lease 구체화 중 MCP 탐색 제한 시간 |
| `MCP_OAUTH_CLIENT_METADATA_BASE_URL` | 빈 값 | 외부 인가 서버가 닿는 공개 URL. 설정 시 CIMD 제공(F-15). 폐쇄망은 비움 |
| `MCP_OAUTH_CLIENT_DEPLOYMENT` | `default` | CIMD 경로의 배포 이름 |
| `RETENTION_SWEEP_ENABLED` | `false` | 회사·조직 보존 정책 일일 삭제(E-08). 켜기 전에는 아무것도 지우지 않음 |
| `RETENTION_SWEEP_CRON` | `20 3 * * *` | 보존 삭제 시각 |
| `TENANT_DELETION_GRACE_DAYS` | `30` | 회사 삭제 예약 유예 기간 |
| `TENANT_EXPORT_TTL_DAYS` | `7` | 내보내기 파일 보관 기간 |
| `TENANT_PURGE_ENABLED` | `false` | 유예가 끝난 회사의 영구 삭제 leader 작업. 꺼져 있어도 예약·취소는 동작 |
| `TENANT_PURGE_CRON` | `40 3 * * *` | 영구 삭제 시각 |
| `TENANT_EXPORT_STALE_MINUTES` | `120` | 이 시간 넘게 멈춘 내보내기 작업을 실패 처리 |
| `TENANT_EXPORT_GC_CRON` | `55 * * * *` | 내보내기 정리 작업 시각 |

---

## 4. OpenFGA 운영

- 이미지: `openfga/openfga:v1.22.0`. 폐쇄망 설치 번들에 포함합니다. Moldy DB와 분리된 전용 Postgres
  (`openfga-postgres`)를 쓰고 `openfga-migrate`가 스키마를 만듭니다. playground는 꺼져 있습니다.
- OpenFGA 데이터는 Postgres에서 파생된 색인입니다. 원본은 항상 Moldy DB(`resource_grants`, 조직·그룹 테이블)입니다.
- CLI (`python -m app.authz.cli`, 종료 코드 0 성공·허용 / 1 거부 또는 차이 남음 / 2 사용 오류):

| 명령 | 용도 |
|------|------|
| `bootstrap` | 모델 배포 후 기대 튜플 전체 기록. 결과의 store/model ID를 설정에 고정 |
| `reconcile [--tenant\|--org] [--dry-run\|--apply] [--json]` | 차이 확인. `--apply`는 수리 작업을 아웃박스에 넣음 |
| `rebuild [--tenant\|--org] [--apply]` | Postgres에서 store를 직접 다시 씀. **OpenFGA 데이터 손상 시 복구 수단** |
| `check --user --relation --object` | 고정된 모델로 단건 판정 |
| `explain --relation --object` | 관계 트리 펼치기(판정 아님) |
| `catalog-fanout --actor <id>` | 카탈로그 정책 fanout 백필. shadow/enforce 전환 직전에 다시 실행 |
| `personal-account-shares [--json]` | 개인 계정을 빌려주는 공유 목록(읽기 전용, 위반 시 종료 코드 1) |

- 모델을 바꾸면 bootstrap을 다시 실행하고 새 model ID로 교체합니다. `authz_model_versions`에 이력이 남습니다.
- 장애 대응: OpenFGA를 재시작합니다. 그 사이 enforce 유형은 차단, shadow는 기록만 실패합니다. 오래 걸리면
  해당 유형을 `AUTHZ_ENFORCE_TYPES`에서 빼서 임시로 기존 판정으로 돌립니다.

---

## 5. 회사 내보내기·삭제·영구 삭제 (I-07)

`DEPLOYMENT_MODE=multi_tenant`에서 플랫폼 운영자(super_user)만 씁니다. 화면은
`/settings/admin/tenants/[tenantId]`(S04), API는 `/api/admin/tenants/{id}/export|exports|schedule-deletion|cancel-deletion`,
CLI는 같은 동작을 제공합니다.

```bash
cd backend
uv run python -m app.services.platform.tenant_data_cli export --tenant <id> --out <파일>
uv run python -m app.services.platform.tenant_data_cli schedule-deletion --tenant <id> --slug <회사 slug> [--reason ...]
uv run python -m app.services.platform.tenant_data_cli cancel-deletion --tenant <id> [--reason ...]
uv run python -m app.services.platform.tenant_data_cli purge [--tenant <id>]
```

- 내보내기: 회사당 동시에 1건. 비밀 값(암호화 열, 비밀 이름 키, URL 사용자 정보·쿼리, 사용자 비밀번호·로그인 IP)은
  제외됩니다. 파일은 `TENANT_EXPORT_TTL_DAYS` 뒤 정리됩니다. 내보내기는 요청 백그라운드 작업이라 서버가 재시작되면
  GC가 `export_interrupted`로 실패 처리하므로 다시 요청합니다.
- 삭제 예약: slug 확인이 맞아야 하며 `TENANT_DELETION_GRACE_DAYS` 뒤가 삭제 예정일입니다. 영구 삭제가 실행되기 전까지는
  예정일이 지나도 취소할 수 있습니다(회사는 `suspended`로 돌아감).
- 영구 삭제: `TENANT_PURGE_ENABLED=true`일 때 leader 작업이 실행하거나 CLI `purge`로 실행합니다. 회사 행 잠금 아래
  한 트랜잭션으로 처리하며, 회사 밖에서 끊을 수 없는 참조가 있으면 **아무것도 지우지 않고 중단**합니다(로그
  `outside_reference table.column=n`). 다른 회사가 설치한 마켓플레이스 항목은 사본을 남기고 `source_removed`로 표시합니다.
  큰 회사는 잠금이 길어지므로 한가한 시간에 실행합니다. 영수증(`tenant_purges`)은 회사가 사라진 뒤에도 남습니다.
- 아직 하지 않는 것: 완료 알림, 회사 관리자 다운로드, 외부 비밀 저장소 네임스페이스와 로컬이 아닌 아티팩트 저장소 삭제,
  스킬 빌더 초안 작업 공간 정리. 이 항목은 수동으로 확인합니다.

---

## 6. 마이그레이션과 되돌리기

기획서의 m78~m92 번호와 실제 Alembic revision ID는 다릅니다. 체인은 `m77_side_chat_link`(origin/main) 뒤에 붙고
head는 `i08_outbox_request_enqueue`입니다. 모두 downgrade가 있으며, 되돌리기 전 반드시 운영 백업을 받습니다.

| 순서 | revision | 기획 번호·작업 | 내용 | 되돌리기 주의 |
|------|----------|----------------|------|---------------|
| 1 | `m78_tenants_organizations` | m78, B-01 | 회사·조직·구성원·역할 | 테이블 삭제 |
| 2 | `m79_groups` | m79, B-01 | 그룹 | 테이블 삭제 |
| 3 | `m80_org_capabilities_invitations` | m80, B-01 | 조직 기능 권한·초대 | 테이블·열 삭제 |
| 4 | `m81_org_scope_columns` | m81, B-02 | 자원 테이블 범위 열 | 열 삭제 |
| 5 | `m82_default_org_backfill` | m82, B-02 | 기본 회사·조직 생성과 기존 데이터 이전 | 이전 스냅숏으로 복원. 스냅숏이 없거나 어긋나면 거부(재대조 필요) |
| 6 | `m83_org_scope_not_null` | m83, B-02 | 범위 열 NOT NULL·제약 | 기존 불변식 위반 데이터가 있으면 거부 |
| 7 | `b08_agent_lifecycle` | B-08 | 에이전트 보관·영구 삭제 이력 | 보관 이력에 의존하는 행이 있으면 거부(`_assert_downgrade_safe`) |
| 8 | `b11_company_administration` | B-11 | 회사 초대, 조직 보관 표식 | 열·테이블 삭제 |
| 9 | `c01_authz_core` | m84, C-01 | grant·아웃박스·회수 표식·모델 버전·섀도 차이 | 테이블 삭제 |
| 10 | `c01_marketplace_acl_grants` | m85, C-01 | 마켓플레이스 ACL → grant 복사 | 이 revision이 쓴 grant만 삭제(ACL 원본은 그대로) |
| 11 | `c04_outbox_sequence` | C-04 | 아웃박스 순번·튜플 키 | 색인·열 삭제 |
| 12 | `d02_agent_user_preferences` | m86 일부, D-02 | 공유 에이전트 즐겨찾기 | 테이블 삭제 |
| 13 | `f04_credential_modes` | m87, F-04 | 자격 증명 모드·사용자별 연결·허용 호스트 | 테이블·열 삭제 |
| 14 | `f01_connection_delegation` | m86, F-01 | 연결 위임 열과 백필 | 위임 grant·아웃박스 행 삭제. OpenFGA에 이미 쓴 튜플은 `authz rebuild`로 정리 |
| 15 | `f11_credential_definition_hosts` | m87, F-11 | 정의 기반 허용 호스트 채우기 | 데이터만, 되돌릴 것 없음 |
| 16 | `f09_agent_memo` | F-09 | 공유 운영 메모 제안 | `agent_memo` 제안 행 삭제 |
| 17 | `f14_run_attribution` | m89, F-14 | 실행·사용량 귀속 | 열·색인 삭제 |
| 18 | `f08_trigger_run_reason_code` | F-08 | 트리거 BLOCKED 사유 | 열 삭제 |
| 19 | `h01_access_requests` | m88, H-01 | 접근 요청 | 테이블 삭제 |
| 20 | `h03_audit_tenant_index` | m89, H-03 | 회사·조직 감사 색인 | 색인 복원 |
| 21 | `h02_notifications` | m88, H-02 | 알림 | 테이블 삭제 |
| 22 | `h08_sso_hardening` | H-08 | SSO 도메인·클레임·인증 방식 | 열·색인 삭제 |
| 23 | `h04_authz_reconcile_runs` | H-04 | 대조 실행 기록 | 테이블 삭제 |
| 24 | `i07_tenant_exports` | I-07 | 내보내기·삭제 영수증·`source_removed` | `source_removed` 설치가 있으면 거부(먼저 삭제) |
| 25 | `i06_company_rls` | m92, I-06 | 회사 RLS 정책·앱 역할 권한 | PostgreSQL만. RLS 끄고 정책 삭제. 그 전에 `rls_activation disable` + `DB_RLS_ENABLED=false` |
| 26 | `i08_outbox_request_enqueue` | I-06 후속 | 가입·회사 세션이 `authz_outbox`에 행을 추가할 수 있게 함(읽기·수정·삭제는 플랫폼 전용 유지) | PostgreSQL만. outbox 정책을 플랫폼 전용으로 되돌림 |

m90(옛 열·테이블 삭제)과 m91은 이 PR에 없습니다(I-04, PR 2). m90은 되돌릴 수 없으므로 백업·복원 시험 기록을 남긴 뒤 적용합니다.

권한 판정 문제는 마이그레이션 되돌리기보다 설정(1절)으로 먼저 되돌립니다. OpenFGA 데이터 손상은
`python -m app.authz.cli rebuild --apply`로 Postgres에서 다시 만듭니다.

---

## 7. 내부 구간 TLS (I-09)

인증서 준비, `docker-compose.tls.yml` 오버레이, 외부 Postgres·OpenBao, 기존 설치 업그레이드는
[운영 환경 셋업 7절](operator-setup.md#7-내부-구간-tls와-인증서)을 따릅니다.

```bash
bash scripts/internal-tls-certs.sh
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
bash scripts/run-internal-tls-lane.sh      # Docker 필요. 이 PR 작성 환경에서는 실행하지 못함
```

`APP_ENV=production`에서는 평문 내부 URL(`verify-full`이 아닌 DB URL, http OpenFGA·OpenBao)로 기동하지 않습니다.
같은 호스트 Compose 설치만 `INTERNAL_PLAINTEXT_HOSTS=openfga,postgres,openfga-postgres`로 예외를 둡니다.

---

## 8. 모니터링

### 8.1 운영 화면과 API

- 화면: `/settings/admin/authz`(S27, super_user). 모드, 동기화 활성 여부, OpenFGA 상태, 아웃박스 대기·재시도·실패,
  회수 표식 대기·정체, 최근 대조 실행, 섀도 불일치를 보여줍니다.
- API: `GET /api/admin/authz/status`, `GET /api/admin/authz/failures`,
  `POST /api/admin/authz/failures/{outbox_id}/retry`, `POST /api/admin/authz/reconcile`,
  `GET /api/admin/authz/reconcile/{job_id}`.

### 8.2 로그 지표 (구조화 로그 이름)

| 로그 | 의미 | 대응 |
|------|------|------|
| `authz_shadow_mismatch`, `authz_shadow_summary` | 섀도 불일치, 제출·상한 초과·버림 집계 | 0건 목표. 버림이 있으면 관찰 기간 판단에 반영 |
| `authz_backlog` | 아웃박스 대기·실패·지연, 가장 오래된 회수 표식 | 지연이 계속 늘면 OpenFGA 상태 확인 |
| `authz_revocation_stuck` (error) | 회수 표식이 오래 처리되지 않음 | OpenFGA 삭제 실패 확인. 접근은 거부 상태로 유지됨 |
| `authz_outbox_immediate_failed`, `authz_outbox_completion_failed` | 즉시 반영 실패 | 다음 주기에 재시도. 반복되면 failures 화면에서 재시도 |
| `authz_reconcile_diff`, `authz_reconcile_failed`, `authz_reconcile_skipped` | 일일 대조 결과·실패 | 차이가 반복되면 원인 조사 후 `reconcile --apply` |
| `authz_openfga_down`, `authz_check_latency_ms` | OpenFGA 연결 끊김, 판정 지연 | 4절 장애 대응 |
| `authz_job_failed`, `authz_grant_expiry_failed` | leader 작업 실패 | 로그의 작업 이름으로 원인 확인 |
| `checkpoint_tenant_mismatch` | 체크포인트 회사와 대화 회사 불일치 | `checkpoint_audit --json`으로 대상 확인 |

### 8.3 섀도 불일치 직접 조회

```sql
SELECT route, permission, legacy, fga, sum(count) AS hits, max(created_at) AS last_seen
FROM authz_shadow_diffs
GROUP BY route, permission, legacy, fga
ORDER BY hits DESC;
```

`legacy=true, fga=false`는 전환 시 접근이 막힐 경우, `legacy=false, fga=true`는 새로 열릴 경우입니다.
둘 다 0이 될 때까지 다음 단계로 가지 않습니다.

---

## 9. 부하·보안 점검 (I-05) — 미완료

I-05 중 **운영 문서(이 문서)와 점검 스크립트는 준비했고, 스테이징 실행은 아직입니다.** 부하·보안 점검은 스테이징에서
`AUTHZ_MODE=enforce`(I-09 TLS 구성)로 실행해야 합니다. 아래 목표(기획서 16.6, 8.10)를 모두 달성해야 I-05가 완료됩니다.

### 9.0 실행 방법 (스크립트)

두 스크립트는 HTTP(S)로 배포된 API(부하 시험은 OpenFGA HTTP API도)만 호출합니다. DB에 직접 붙지 않으며 새 패키지 없이
`backend/.venv`의 Python으로 실행합니다.

- **설정 파일.** `scripts/i05_config.example.json`을 복사해 스테이징 값으로 채웁니다. 회사 A 사용자(구성원·소유자·동료),
  회사 B 사용자, 부하용 에이전트, 회사 B의 자원 ID(`security.foreign`, 경로 변수 이름별), 자기 자원 ID(`security.own`,
  중첩 경로용)를 적습니다. **비밀번호·토큰은 설정 파일에 쓰지 않습니다.** 설정에는 `password_env`, `token_env`처럼 환경 변수
  이름만 적고 값은 셸에서 `export`합니다. 설정에 비밀 값이 그대로 있으면 두 스크립트 모두 실행을 거부합니다(종료 코드 2).
- **안전장치.** localhost가 아닌 주소는 `--i-know-this-is-staging`이 없으면 거부하고, https가 아니면 거부합니다
  (`--allow-http`로만 허용). DB·브로커 포트(5432, 6432, 6379 등)와 주소 안 자격 증명도 거부합니다. `--dry-run`은 로그인하지
  않고 설정 검사, 시나리오·프로브 계획, 비어 있는 환경 변수 이름만 출력합니다. 먼저 `--dry-run`으로 확인합니다.
- **부하 시험** — `scripts/i05_load_test.py`.

  ```bash
  export I05_A_MEMBER_PASSWORD=... I05_A_OWNER_PASSWORD=... I05_A_PEER_PASSWORD=... I05_FGA_TOKEN=...
  backend/.venv/bin/python scripts/i05_load_test.py --config i05.json --i-know-this-is-staging --dry-run
  backend/.venv/bin/python scripts/i05_load_test.py --config i05.json --i-know-this-is-staging \
      --concurrency 8 --iterations 500 --warmup 10 --ramp-seconds 5
  ```

  시나리오(`--list`로 확인, `--scenarios a,b`로 선택): `fga.check`(p95 20ms), `fga.check_concurrent_8`(동시 8, p95 20ms),
  `fga.batch_check_50`(p95 60ms), `fga.list_objects`(3초 한도, 1,000개 도달 시 오류), `api.resource_check`(`GET
  /api/agents/{id}` 단건 판정), `api.run_readiness`(실행 준비 BatchCheck), `api.list.*`(에이전트·스킬·자격 증명·MCP·도구·
  마켓플레이스·대화 목록, 후보 + 페이지 BatchCheck), `share.propagation`(grant 생성 → 받는 사람에게 보일 때까지,
  회수 → 404가 될 때까지의 아웃박스 반영 지연, 기본 p95 2초 = 8.6 즉시 반영 제한 시간), `api.run_start`(대화 생성 →
  `run.start` 명령 응답 시간 → 즉시 취소; LLM 호출이 생기므로 scripted 모델 에이전트로 `--scenarios`에 명시할 때만 실행).
  기획서에 수치가 없는 항목(목록 API, 실행 준비, 실행 시작)은 `measured`로 기록만 하며, 설정 `targets`로 기준을 줄 수 있습니다.
  결과는 `output/i05/load-<시각>/load-report.json`·`.md`(p50/p95/p99, 오류율, 기준 대비 판정)에 남고, 기준을 하나라도
  못 맞추면 종료 코드 1입니다. OpenFGA 기준선 재측정은 기존 `scripts/p0/fga_bench.py`를 스테이징 OpenFGA에 그대로 씁니다.
- **보안 점검** — `scripts/i05_security_check.py`. 배포한 커밋에서 라우트 목록을 먼저 뽑습니다(정책은 `app/authz`의
  `authz_policy` 표식).

  ```bash
  PYTHONPATH=backend backend/.venv/bin/python scripts/i05_route_inventory.py --out output/i05/routes.json
  backend/.venv/bin/python scripts/i05_security_check.py --config i05.json --i-know-this-is-staging --dry-run
  backend/.venv/bin/python scripts/i05_security_check.py --config i05.json --i-know-this-is-staging
  ```

  점검(`--checks`로 선택): `idor`(경로 변수가 있는 모든 RES·PRIVATE·SELF·CAP·GOVERNANCE 라우트에 회사 B ID를 넣어 404,
  그리고 어디에도 없는 ID와 상태·오류 코드가 같은지 = 404 동일성; 중첩 경로는 자기 대화 + 남의 아티팩트처럼 섞음),
  `csrf`(없는 ID로 변경 요청을 CSRF 헤더 없이·위조해 보내 403 `csrf_mismatch`), `org_cookie`(구성원이 아닌 조직을
  `moldy_active_org`에 넣으면 403), `rate_limit`(회사 A 사용자로 조직 검색 한도를 소진해도 회사 B는 429가 아님),
  `share`(자기 대화 공유 → 익명 열람 200, 공유 토큰 아래 남의 아티팩트 404, 임의 토큰 404, 정지 회사 토큰 404, 회수 후 404),
  `transport`(인증 쿠키 HttpOnly·Secure, `moldy_active_org` 속성이 인증 쿠키와 같음, http→https 리다이렉트 또는 거부,
  인증서 검증·TLS 1.2 이상, 권장 헤더는 경고), `rls_parity`(같은 사용자로 RLS 켠 배포와 끈 배포의 목록 ID가 같은지).
  변경 라우트 IDOR는 빈 본문으로만 보내며 `--include-mutations`(DELETE는 `--include-delete`도)를 줄 때만 실행합니다.
  빈 본문 때문에 남의 ID와 없는 ID가 똑같이 422로 답하면 판정 게이트에 닿지 못한 것이라 `warn`(존재 여부는 드러나지 않음)으로,
  비구성원 조직에 대한 403 `org_access_denied`가 양쪽에 같으면 통과로 기록합니다. 운영자 전용(SUPER) 라우트의 CSRF는
  `security.operator`(super user 페르소나)로 보내며, 없으면 `skipped`입니다. `--label "local rehearsal"`처럼 주면 보고서 제목에 붙습니다.
  계획 요약의 `unprobed`(고정 ID가 없는 라우트)와 `families_without_probe`를 보고 `security.foreign`을 채워 빈 곳을 없앱니다.
  결과는 `output/i05/security-<시각>/security-report.json`·`.md`, 실패가 하나라도 있으면 종료 코드 1입니다.
- **로컬 리허설(2026-10-18).** 임시 Postgres·메모리 OpenFGA·`AUTHZ_MODE=enforce` 로컬 스택에서 두 스크립트를 끝까지 돌려
  하네스가 동작함을 확인했습니다. 이 수치는 스테이징 결과를 대신하지 않습니다. 회사 B 시드 시 주의: 회사 초대로 가입한 첫 관리자는
  조직 구성원이 아니므로 `POST /api/orgs/{org_id}/members`로 자신을 넣어야 하고, 기본 조직 가입·구성원 추가는 OpenFGA 구성원 튜플을
  바로 쓰지 않으므로(C-04 미완) 시드 뒤 `authz reconcile --apply`를 실행합니다. 가입 한도(시간당 5회)는 시드 동안만 끄고 점검 전에 켭니다.
- 두 스크립트로 확인되지 않는 항목(9.2 RLS 코드 검사, 9.3의 래칫·기동 거부, 9.4의 도구·위임·마켓플레이스·어시스턴트 항목)은
  기존 테스트와 수동 점검으로 확인합니다.

### 9.1 부하 (조직 1, 사용자 2,000, 에이전트 5,000, grant 50,000)

- [ ] 목록 API p95 측정 (DB 후보 + 페이지 50 BatchCheck)
- [ ] 실행 준비 BatchCheck 측정
- [ ] 아웃박스 지연 측정
- [ ] 단건 Check p95 20ms 이하 (같은 네트워크)
- [ ] BatchCheck 50건 p95 60ms 이하
- [ ] ListObjects 기본 한도(1,000개, 3초) 안
- [ ] 동시 8 요청 Check p95 20ms 이하
- [ ] `scripts/p0/fga_bench.py`로 운영 네트워크에서 다시 잰 값을 8.10 기준선(A-12, 로컬 측정)과 비교

### 9.2 RLS (네이티브 PostgreSQL)

- [ ] 회사 컨텍스트 없이 조회 0행, 다른 회사 컨텍스트 조회 0행
- [ ] 플랫폼 범위 허용 목록 밖 사용 시 테스트 실패
- [ ] 연결 1개 풀에서 회사 A 세션 뒤 컨텍스트 없는 세션 0행(값 미잔류)
- [ ] 코드에 `set_config(..., false)`·`SET app.` 없음

### 9.3 인증·전송

- [ ] 토큰 클레임의 권한 값을 읽는 코드 0건(래칫)
- [ ] `moldy_active_org` 쿠키 속성이 인증 쿠키와 같음
- [ ] 다른 사용자의 조직 ID를 쿠키에 넣으면 403
- [ ] 마지막 회사에서 제거된 사용자의 리프레시 실패
- [ ] 자격 증명이 붙은 http 요청과 https→http 리다이렉트 차단
- [ ] `verify=False`와 HTTP 클라이언트 직접 생성 0건(래칫)
- [ ] 운영 모드에서 평문 내부 URL로 기동 거부

### 9.4 보안 점검 목록

- [ ] 공유 자격 증명이 연결된 HTTP 도구로 허용 호스트 밖 전송 시도(리다이렉트 포함)
- [ ] 위조한 재개 결정(허용 목록 밖 값)
- [ ] 쿠키 조직 위조(구성원이 아닌 조직 지정)
- [ ] 다른 조직 자원 ID 직접 호출
- [ ] 중첩 경로 IDOR(대화 아래 아티팩트 ID 교차)
- [ ] 공유 링크로 조직 정책 우회
- [ ] API 키 소유자 권한 상실 후 호출
- [ ] 회수 직후 실행 중 도구 호출
- [ ] 위임 자원 재공유
- [ ] 마켓플레이스 설치로 권한 상승
- [ ] 어시스턴트 도구로 남의 자원 수정
- [ ] 조건 컨텍스트 누락

결과는 [진행 기록](exec-plans/active/org-authz-progress.md) P8 I-05 항목에 수치와 함께 남깁니다.
