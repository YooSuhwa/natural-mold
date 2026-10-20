# Operator Setup — 운영 환경 셋업 체크리스트

운영 배포 전 반드시 통과해야 하는 환경 변수 셋업입니다. `APP_ENV=production`
으로 부팅하면 서버가 직접 검증하여 문제가 하나라도 있으면 **부팅을 거부**합니다
(`app/security/production_check.py` 참조).

ADR-016 §8.4 / HANDOFF #2.

---

## 1. 필수 환경 변수

| 변수 | 운영 값 | 생성 명령 |
|------|---------|-----------|
| `APP_ENV` | `production` | — |
| `JWT_SECRET` | 32+ 자 랜덤 문자열 | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `COOKIE_SECURE` | `true` | — (HTTPS 필수) |
| `ALLOW_FIRST_USER_AS_ADMIN` | `false` | 운영자 계정 생성 후 즉시 |
| `CORS_ALLOWED_ORIGINS` | 실제 프론트 origin 콤마 구분 | 예: `https://moldy.example.com,https://staging.moldy.example.com` |
| `ENCRYPTION_KEYS` | 64자 hex 키 (콤마 구분) | `python -c "import secrets; print(secrets.token_hex(32))"` |

내부 구간(Postgres, OpenFGA, OpenBao) TLS 설정은 [7. 내부 구간 TLS와 인증서](#7-내부-구간-tls와-인증서)를
따릅니다. `APP_ENV=production`에서는 이 설정도 부팅 검증 대상입니다.

선택:
- `COOKIE_DOMAIN` — 서브도메인 공유 시 (`.moldy.example.com`)
- `COOKIE_SAMESITE` — cross-site fetch 필요 시 `none` (단 `COOKIE_SECURE=true` 강제)

---

## 2. 운영 검증

배포 직전에 동일 검증을 수동 실행하려면:

```bash
APP_ENV=production python -c "
from app.config import settings
from app.security.production_check import enforce_production_safety
enforce_production_safety(settings)
print('OK — production settings clean')
"
```

문제가 있으면 어떤 변수가 왜 잘못됐는지 액션 가능한 메시지로 출력합니다.

---

## 3. 부팅 거부 예시

`APP_ENV=production` 인데 `COOKIE_SECURE=false`로 부팅 시도:

```
RuntimeError: Refusing to start with insecure production settings:
  - COOKIE_SECURE=false. Set true so browsers refuse to send auth
    cookies over plain HTTP.
Fix the above and restart, or set APP_ENV=dev to bypass
(local development only).
```

---

## 4. 첫 운영자 계정 부트스트랩

`ALLOW_FIRST_USER_AS_ADMIN=true`가 켜진 상태로만 자동 승격이 동작합니다.
운영자 계정 생성 절차:

1. 일시적으로 `ALLOW_FIRST_USER_AS_ADMIN=true` + `APP_ENV=dev`로 부팅
2. `/api/auth/register`로 운영자 계정 1건 생성 — 자동으로 `is_super_user=true`
3. 즉시 `ALLOW_FIRST_USER_AS_ADMIN=false` + `APP_ENV=production`으로 재부팅
4. 이후 등록되는 모든 user는 평민. super_user 부여는 운영자가 DB / 어드민 API로 수동

---

## 5. 마이그레이션

```bash
cd backend
uv run alembic upgrade head
```

(스키마 변경이 있는 PR을 머지한 직후 항상 실행)

---

## 6. 관련 문서

- [조직·권한 운영 가이드](org-authz-operations.md) — legacy → shadow → 유형별 enforce → RLS 전환,
  새 설정, 마이그레이션 되돌리기, OpenFGA·회사 데이터 운영, 모니터링
- `docs/exec-plans/active/org-authz-plan.html` 15.1·15.2.1 — Compose 구성과 전송 구간 보호 기준
- `docs/design-docs/adr-016-multiuser-auth.md` — 인증/세션 전체 설계
- `backend/.env.example` — 전체 변수 목록 + 인라인 주석
- `backend/app/security/production_check.py` — 검증 로직 (단일 소스)
- `backend/app/security/internal_tls_check.py` — 내부 구간 TLS 검증 (7절)
- `docker-compose.tls.yml` — 내부 구간 TLS Compose 오버레이 (7절)

---

## 7. 내부 구간 TLS와 인증서

`APP_ENV=production`으로 부팅하면 내부 연결도 검증합니다. 아래 중 하나라도 걸리면
부팅을 거부합니다.

| 설정 | 요구 사항 |
|------|-----------|
| `DATABASE_URL`, `MIGRATION_DATABASE_URL` (asyncpg) | `?ssl=verify-full` |
| `DATABASE_URL_SYNC` (psycopg, 체크포인터·스케줄러) | `?sslmode=verify-full&sslrootcert=<CA 경로>` |
| `OPENFGA_API_URL` | `https://...` |
| `VAULT_URL` (설정한 경우) | `https://...` |
| `OPENFGA_CA_CERT`, `VAULT_CA_CERT` (설정한 경우) | 파일이 실제로 있어야 함 |

`require`, `verify-ca`, `prefer`, `disable`처럼 서버 인증서와 호스트 이름을 함께 확인하지
않는 모드도 거부합니다. 오류 메시지에는 설정 이름과 `호스트:포트`만 나오고 계정·비밀번호는
나오지 않습니다.

예외는 `INTERNAL_PLAINTEXT_HOSTS`(콤마 구분, 기본값 빈 값)에 적은 호스트뿐입니다. 한 대의
호스트에서 기본 `docker-compose.yml`만으로 돌리는 설치는 다음처럼 서비스 이름을 적고,
쿠버네티스처럼 노드 사이를 오가는 배포는 비워 둡니다.

```bash
INTERNAL_PLAINTEXT_HOSTS=openfga,postgres,openfga-postgres
```

### 7.1 인증서 준비

회사 CA에서 서버 인증서를 발급받는 것이 원칙입니다. 준비할 파일은 세 개입니다.

| 파일 | 내용 |
|------|------|
| `ca.crt` | 서버 인증서를 발급한 CA 인증서 (PEM). Moldy와 OpenFGA가 신뢰 저장소로 사용 |
| `server.crt` | 서버 인증서 (PEM). SAN에 `postgres`, `openfga-postgres`, `openfga` 포함. 외부 DB를 쓰면 그 호스트 이름도 포함 |
| `server.key` | 서버 개인 키 (PEM, 암호 없음). 권한 `0640`, 그룹은 `MOLDY_TLS_KEY_GID`로 지정 |

평가 환경이나 회사 CA가 없을 때는 스크립트로 사설 CA와 인증서를 만듭니다.

```bash
bash scripts/internal-tls-certs.sh                 # certs/internal 에 생성
MOLDY_TLS_EXTRA_SANS=DNS:db.corp.example bash scripts/internal-tls-certs.sh   # SAN 추가
```

스크립트는 컨테이너에 마운트되는 `certs/internal`(인증서·서버 키, 디렉터리 `0750`)과
마운트되지 않는 `certs/internal-ca`(CA 개인 키 `ca.key`, `0600`)를 나눠 만듭니다. 두 번째
인자로 CA 키 위치를 바꿀 수 있습니다. `ca.key`는 인증서를 다시 발급할 때만 필요하므로
설치 호스트 밖으로 옮겨 보관하고, 인증서 디렉터리에는 절대 넣지 않습니다. `certs/`는 git과
Docker 빌드 컨텍스트에서 제외되어 있습니다.

### 7.2 Compose 오버레이로 기동

```bash
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
```

`docker-compose.tls.yml`이 바꾸는 내용:

- `postgres`, `openfga-postgres`: `ssl=on`, 평문 TCP 접속은 `pg_hba`에서 거부
- `openfga`: `OPENFGA_HTTP_TLS_ENABLED=true`, 전용 Postgres 접속은 `sslmode=verify-full`
- `backend`(1회성 `migrate`, `authz-bootstrap` 포함): 위 표대로 세 DB URL과 `OPENFGA_API_URL=https://openfga:8080`,
  `OPENFGA_CA_CERT=/certs/ca.crt`, `PGSSLROOTCERT=/certs/ca.crt`, `INTERNAL_PLAINTEXT_HOSTS` 빈 값
- 기본 파일의 개발용 OpenFGA 기본값을 쓰지 않습니다. 루트 `.env`에 `OPENFGA_API_TOKEN`과
  `OPENFGA_DB_PASSWORD`가 없으면 Compose가 기동을 거부합니다.

인증서 디렉터리는 `MOLDY_TLS_CERT_DIR`(기본 `./certs/internal`)이며 모든 서비스에
`/certs`로 읽기 전용 마운트됩니다. OpenFGA 이미지는 root가 아닌 사용자로 돌기 때문에
`server.key`(`0640`)를 파일 그룹으로 읽습니다. 루트 `.env`에 `server.key`의 그룹 ID를
`MOLDY_TLS_KEY_GID`로 적으면 오버레이가 OpenFGA 컨테이너에 그 그룹을 추가합니다(값이 없으면
Compose가 기동을 거부합니다). 스크립트는 마지막 줄에 넣을 값을 출력합니다. 회사 인증서도
같은 권한(`server.key` `0640`, 디렉터리 `0750`)으로 둡니다.

### 7.3 외부 Postgres·OpenBao를 쓸 때

- 유닉스 소켓 접속(`?host=/run/postgresql`처럼 네트워크 호스트 없이 절대 경로만 지정)은
  호스트 밖으로 나가지 않으므로 TLS 없이 허용합니다. 반대로 URL에 `host`·`hostaddr` 쿼리가
  있으면 실제 접속 대상이 바뀔 수 있어 `INTERNAL_PLAINTEXT_HOSTS` 예외를 적용하지 않습니다.
- `postgres://` 등 sqlite가 아닌 모든 DB URL이 검증 대상입니다.
- asyncpg는 SQLAlchemy URL의 `sslrootcert`를 받지 않습니다. `DATABASE_URL`에는
  `ssl=verify-full`만 넣고 CA 경로는 환경 변수 `PGSSLROOTCERT`로 지정합니다.
- `DATABASE_URL_SYNC`는 libpq 형식이라 URL에 `sslrootcert=`를 직접 넣습니다.
- OpenBao·Vault는 `VAULT_URL=https://...`, 사설 CA면 `VAULT_CA_CERT=<경로>`를 지정합니다.
- 바꾼 뒤 [2. 운영 검증](#2-운영-검증) 명령으로 부팅 전에 확인합니다.

### 7.4 TLS 통합 레인

TLS 구성이 실제로 접속되는지는 Docker가 있는 환경에서 아래 명령으로 확인합니다. 임시
Compose 프로젝트를 새 인증서로 띄워 asyncpg·psycopg·OpenFGA 접속과 평문 접속 거부를
확인하고, 끝나면 프로젝트와 볼륨을 지웁니다.

```bash
bash scripts/run-internal-tls-lane.sh
```

### 7.5 기존 운영 설치 업그레이드

이 검증이 들어간 버전으로 올리면, 내부 URL이 평문인 기존 운영 설치는 부팅이 거부됩니다.
배포 전에 7.2처럼 TLS를 켜거나, 같은 호스트 Compose 설치라면 `INTERNAL_PLAINTEXT_HOSTS`를
설정합니다.

## 8. 회사 기준 RLS와 DB 역할 분리 (I-06, m92)

`DB_RLS_ENABLED`는 기본값 `false`입니다. 켜기 전에는 m92가 정책만 만들어 두고(비활성) 동작은 바뀌지 않습니다.

### 8.1 역할

| 역할 | 접속 설정 | 권한 |
|------|-----------|------|
| 마이그레이션 역할 | `MIGRATION_DATABASE_URL` | 모든 테이블 소유자, `BYPASSRLS`(또는 superuser). Alembic과 `rls_activation`만 사용 |
| 앱 역할 | `DATABASE_URL`, `DATABASE_URL_SYNC` | 소유자가 아님, `NOBYPASSRLS`, superuser/우회 역할의 구성원도 아님. DML 권한만 |

체크포인터(`DATABASE_URL_SYNC`)와 APScheduler 작업 저장소는 시작할 때 테이블을 만들려고 합니다. 역할을 분리하면 m92가 마이그레이션 역할로 `checkpoint*`·`apscheduler_jobs` 테이블을 미리 만들고, 앱은 LangGraph 마이그레이션이 모두 기록되어 있으면 `setup()`을 건너뜁니다. 그래서 앱 역할에는 스키마 `CREATE` 권한이 필요 없습니다. LangGraph를 올려 새 체크포인트 마이그레이션이 생기면 앱이 `setup()`을 다시 시도하므로, 업그레이드 전에 마이그레이션 역할로 `alembic upgrade head`를 먼저 실행하거나 그 한 번만 `CREATE` 권한이 있는 역할로 기동합니다.

### 8.2 기존 설치 전환 순서

1. 앱 역할 생성: `CREATE ROLE moldy_app LOGIN PASSWORD '...' NOBYPASSRLS;`
2. 보통은 지금까지 테이블을 소유한 기존 역할(예: `moldy`)을 마이그레이션 역할로 두고 새 앱 역할만 만듭니다. 반대로 기존 소유 역할을 앱 역할로 계속 쓰려면 superuser로 소유권을 옮깁니다: `REASSIGN OWNED BY moldy_app TO moldy_migrator;` m92와 `rls_activation enable`은 앱 역할이 테이블을 소유하고 있으면 이 명령을 출력하고 멈춥니다.
3. `MIGRATION_DATABASE_URL`(마이그레이션 역할)과 `DATABASE_URL`/`DATABASE_URL_SYNC`(앱 역할)를 설정하고 `alembic upgrade head`. m92가 런타임 테이블을 만들고 앱 역할에 DML 권한과 기본 권한을 줍니다.
4. 앱 설정에 `DB_RLS_ENABLED=true`를 넣고 재시작합니다. 이때부터 세션이 트랜잭션마다 회사 값을 설정하지만 정책은 아직 꺼져 있습니다.
5. `python -m app.migrations.org_authz_v1.rls_activation enable [--lock-timeout 5s]` (마이그레이션 역할 환경, `DB_RLS_ENABLED=true` 필요). 정책을 다시 설치한 뒤 `ENABLE`·`FORCE ROW LEVEL SECURITY`를 겁니다. 테이블 잠금을 기다리다 시간이 지나면 실패하므로 한가한 시간에 다시 실행합니다.
6. 운영 모드(`APP_ENV=production`)는 시작 시 앱 역할이 소유자·우회 역할(구성원 포함)이 아닌지, 보호 대상 테이블마다 `tenant_boundary` 정책과 강제 RLS가 있는지 확인하고 아니면 기동을 거부합니다.

되돌리기: `python -m app.migrations.org_authz_v1.rls_activation disable` 후 `DB_RLS_ENABLED=false`로 재시작.

### 8.3 점검

- `python -m app.tenancy.checkpoint_audit --json`: 체크포인트 메타데이터의 회사와 대화의 회사를 대조합니다. 다른 회사로 기록된 스레드가 있으면 종료 코드 1.
- m92 이후 새 테이블은 `app/migrations/org_authz_v1/rls_manifest.py`에 분류하고 정책을 넣는 마이그레이션이 필요합니다(분류가 없으면 단위 테스트와 `enable`이 실패).
