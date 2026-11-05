# Docker 빠른 시작

저장소를 clone한 뒤 명령 하나로 Moldy 전체(DB, OpenFGA, 스킬 실행 샌드박스, 가드레일 한국어 모델, 백엔드, 프론트엔드)를 로컬에 띄우는 방법입니다.
`.env` 파일을 만들거나 고치지 않아도 됩니다. 개발용 구성이므로 운영 설치는
[운영 환경 셋업](operator-setup.md)과 [조직·권한 운영 가이드](org-authz-operations.md)를 따릅니다.

## 1. 실행

필요한 것은 Docker(Compose v2 포함)뿐입니다. 저장소 루트에서 실행합니다.

```bash
docker compose up -d --build
```

처음에는 이미지 빌드 때문에 몇 분 걸립니다. 모든 서비스가 준비될 때까지 기다리려면 `--wait`를 붙입니다.

```bash
docker compose up -d --build --wait
docker compose ps
```

## 2. 무엇이 뜨는가

서비스는 아래 순서로 올라갑니다. 앞 단계가 healthy이거나 정상 종료(`exit 0`)해야 다음 단계가 시작됩니다.

| 서비스 | 종류 | 하는 일 |
|--------|------|---------|
| `postgres` | 상시 | Moldy DB (PostgreSQL 16) |
| `openfga-postgres` | 상시 | OpenFGA 전용 DB. 호스트 포트 없음 |
| `openfga-migrate` | 1회 | OpenFGA 스키마 마이그레이션 |
| `openfga` | 상시 | 권한 판정 서버. 호스트 포트 없음 |
| `init-secrets` | 1회 | `ENCRYPTION_KEYS`, `JWT_SECRET`, `SKILL_SANDBOX_API_KEY`를 생성해 `moldy_runtime` 볼륨에 저장. 두 번째 실행부터는 기존 값을 재사용하고, 파일에 없는 키만 추가 |
| `skill-sandbox-image` | 1회 | 스킬 샌드박스가 쓰는 이미지 `moldy-skill-sandbox:local`을 빌드(Python 3.12, Node 22, skill-node 패키지, curl, git) |
| `opensandbox` | 상시 | 스킬 실행 샌드박스 서버(OpenSandbox). 호스트 포트 없음. 아래 "스킬 실행 샌드박스" 참고 |
| `pii-model-fetch` | 1회 | 가드레일 한국어 모델(schift-ko-pii-v7, 고정 리비전)을 `pii_model` 볼륨에 내려받음. 이미 있으면 재사용. 받지 못해도 `Exited (0)`으로 끝남 |
| `pii-detector` | 상시 | 가드레일 한국어 개인정보 모델 서비스. 호스트 포트 없음. 아래 "가드레일 한국어 모델" 참고 |
| `migrate` | 1회 | `alembic upgrade head` |
| `authz-bootstrap` | 1회 | `python -m app.authz.cli bootstrap`으로 store와 모델을 준비하고, store ID와 model ID를 `moldy_runtime` 볼륨에 고정 |
| `backend` | 상시 | FastAPI(uvicorn). `GET /api/health`로 상태 확인 |
| `frontend` | 상시 | Next.js |

1회성 서비스는 `docker compose ps -a`에서 `Exited (0)`으로 보이면 정상입니다.

### 스킬 실행 샌드박스

스킬 스크립트(`execute_in_skill`)는 호출마다 새 컨테이너(`sandbox-…`)에서 실행되고, 끝나면 그
컨테이너는 지워집니다. 백엔드 컨테이너의 비밀 값과 다른 사용자 파일에 닿지 않고, 네트워크는
스킬이 `requires_network`를 선언했을 때만 외부로 열립니다(내부 주소는 항상 차단). 설계와 실측은
[skill-sandbox-docker.md](design-docs/skill-sandbox-docker.md)에 있습니다.

- `opensandbox`는 Docker 소켓(`/var/run/docker.sock`)으로 샌드박스 컨테이너를 만듭니다. 즉 이
  서비스는 Docker 호스트를 제어할 수 있습니다.
- 처음 기동할 때 OpenSandbox 이미지(`opensandbox/execd`, `opensandbox/egress`, 합쳐 약 730 MB)를
  받으므로 몇 분 더 걸립니다.
- 끄려면 `SKILL_SANDBOX_ENABLED=false docker compose up -d`. 그러면 예전처럼 백엔드 컨테이너
  안에서 스크립트를 실행합니다.

### 가드레일 한국어 모델

가드레일의 한국어 개인정보 탐지(이름·주소 등)는 모델 서비스 `pii-detector`가 맡습니다
([guardrail-minimum-plan.md](design-docs/guardrail-minimum-plan.md) 2절). 아직 채팅에 적용되는
가드레일 미들웨어는 없고, 이 서비스는 그 준비입니다.

- `pii-model-fetch`가 처음 기동할 때 Hugging Face에서 고정 리비전의 파일 6개(약 160 MB)를 받고
  sha256을 확인합니다. 서비스는 그 해시와 맞는 파일만 읽습니다.
- 모델이 없거나 서비스가 응답하지 않으면 백엔드는 규칙 묶음(ko-pii)으로 대신 검사합니다. 검사를
  건너뛰지는 않습니다. 이때 `docker compose ps pii-detector`는 unhealthy입니다.
- Hugging Face에 접속할 수 없는 망에서는 받아 둔 폴더를 넣고 다시 시작합니다.

  ```bash
  for f in config.json model.safetensors modeling_lfm2_bidirectional.py schift_heads.json \
           tokenizer.json tokenizer_config.json; do
    docker compose cp <받아 둔 폴더>/$f pii-model-fetch:/models/schift-ko-pii-v7/$f
  done
  docker compose restart pii-detector
  ```

- 이미지(약 1.6 GB)는 PyTorch CPU 인덱스(`download.pytorch.org`)에서 torch를 받습니다. TLS를
  검사하는 사내망이면 루트 `.env`에 `MOLDY_BUILD_EXTRA_CA=<회사 CA 파일 경로>`를 넣습니다. 이
  CA는 빌드 중 설치에만 쓰고 이미지에 남기지 않습니다.
- 운영자는 웹 UI의 **설정 → 가드레일 모델**(`/settings/guardrail-model`)에서 모델을 켜고 끄고, 주소를 바꾸고, 연결 테스트를 할 수 있습니다. 저장한 값이 환경변수보다 우선하고, "기본값으로 되돌리기"를 누르면 환경변수 값으로 돌아갑니다.
- 모델 없이 규칙 묶음만 쓰려면 위 화면에서 끄거나, `GUARDRAIL_PII_MODEL_URL= docker compose up -d`로 띄웁니다.
- 처리 시간: Apple Silicon의 Docker Desktop에서 문서당 약 60 ms(스레드 1개, 동시 2건)입니다.

## 3. 접속 주소

| 대상 | 기본 주소 |
|------|-----------|
| 웹 UI | http://localhost:3000 |
| API | http://localhost:8001 (Swagger: http://localhost:8001/docs) |
| PostgreSQL | `localhost:5432`, 계정·비밀번호·DB 모두 `moldy` |

## 4. 첫 로그인

시드 계정은 없습니다. http://localhost:3000/register 에서 처음 가입한 사용자가 자동으로
운영자(super_user)가 됩니다(`ALLOW_FIRST_USER_AS_ADMIN=true`, 개발 기본값). 그 뒤 가입하는 사용자는
일반 사용자입니다. 가입 후 같은 이메일·비밀번호로 http://localhost:3000/login 에서 로그인합니다.

LLM을 쓰려면 운영자로 로그인한 뒤 Credentials 화면에서 API 키를 등록합니다(ADR-013).

API 키 없이 채팅 화면을 시험하려면 키가 필요 없는 scripted 모델을 켭니다(개발 전용,
`APP_ENV=production`에서는 무시).

```bash
MOLDY_DEMO_SCRIPTED_MODEL=true docker compose up -d
```

## 5. 포트 바꾸기

5432·8001·3000을 다른 프로그램이 쓰고 있으면 환경 변수로 호스트 포트를 바꿉니다. 루트 `.env`에
적어 두거나 명령 앞에 붙입니다.

```bash
MOLDY_POSTGRES_PORT=15432 MOLDY_BACKEND_PORT=18001 MOLDY_FRONTEND_PORT=13000 \
  docker compose up -d --build
```

| 변수 | 기본값 | 대상 |
|------|--------|------|
| `MOLDY_POSTGRES_PORT` | `5432` | PostgreSQL |
| `MOLDY_BACKEND_PORT` | `8001` | 백엔드 API |
| `MOLDY_FRONTEND_PORT` | `3000` | 웹 UI |

API 주소(`NEXT_PUBLIC_API_BASE_URL`)는 빌드할 때 브라우저 번들에 들어가고 기본값이
`http://localhost:<MOLDY_BACKEND_PORT>`입니다. CORS 허용 출처(`CORS_ALLOWED_ORIGINS`)도
`MOLDY_FRONTEND_PORT`를 따라갑니다. 그래서 `MOLDY_BACKEND_PORT`를 바꾸면 `--build`를 붙여
프론트엔드를 다시 빌드해야 합니다. 이 구성은 브라우저에서 `localhost`(또는 `127.0.0.1`)로
접속한다고 가정합니다. 다른 호스트 이름으로 접속하면 `NEXT_PUBLIC_API_BASE_URL`과
`CORS_ALLOWED_ORIGINS`를 함께 지정합니다.

## 6. 설정 우선순위와 `backend/.env`

`backend/.env`는 없어도 됩니다. 있으면 무시하지 않고 읽습니다. 백엔드 컨테이너(`backend`, `migrate`,
`authz-bootstrap`)에서 같은 변수가 여러 곳에 있으면 위쪽이 이깁니다.

1. `docker-compose.yml`의 `environment:` 값. 셸 환경 변수나 루트 `.env`로 바꿀 수 있습니다.
   `DATABASE_URL`, `DATABASE_URL_SYNC`, `OPENFGA_API_URL`은 Compose 네트워크 주소로 고정되므로
   `backend/.env`의 `localhost` 값은 쓰이지 않습니다.
2. `backend/.env` (있을 때만).
3. `moldy_runtime` 볼륨에 생성된 값. 1·2에서 **비어 있거나 없는 변수만** 채웁니다:
   `ENCRYPTION_KEYS`, `JWT_SECRET`, `SKILL_SANDBOX_API_KEY`(`init-secrets`), `OPENFGA_STORE_ID`,
   `OPENFGA_MODEL_ID`(`authz-bootstrap`). `opensandbox`도 같은 방식으로 `SKILL_SANDBOX_API_KEY`를
   읽으므로 `backend/.env`에 값을 적으면 두 서비스에 같은 값이 들어갑니다.

`backend/.env`에 `ENCRYPTION_KEYS`나 `JWT_SECRET`을 적어 두었다면 그 값이 쓰입니다. 그 값을 나중에
바꾸거나 지우면 이미 저장한 자격 증명을 복호화하지 못하므로, 바꿀 때는 아래 초기화를 함께 합니다.

생성된 비밀 값은 볼륨 안에만 있고 저장소에는 들어가지 않습니다. 값을 직접 볼 필요는 없습니다.

OpenFGA 접속 토큰(`OPENFGA_API_TOKEN`)과 OpenFGA DB 비밀번호(`OPENFGA_DB_PASSWORD`)는 개발용 기본값을
씁니다. 둘 다 Compose 내부 네트워크에서만 쓰이고 호스트 포트가 없습니다. 여러 사람이 쓰는 서버나 운영
설치에서는 루트 `.env`에 임의 값을 넣습니다. `OPENFGA_DB_PASSWORD`는 OpenFGA DB를 처음 만들 때만
적용되므로, 나중에 바꾸려면 초기화(`down -v`)가 필요합니다.

## 7. 로그와 상태

```bash
docker compose ps -a                      # 1회성 서비스 포함
docker compose logs -f backend            # 백엔드 로그
docker compose logs authz-bootstrap       # 고정된 store_id / model_id 확인
docker compose logs init-secrets          # generated / reusing
docker compose logs opensandbox           # 스킬 샌드박스 서버
docker compose logs pii-model-fetch       # downloaded / reusing / download failed
docker compose ps pii-detector            # 가드레일 한국어 모델: healthy
curl http://localhost:8001/api/health     # {"status":"ok"}
```

## 8. 중지와 초기화

```bash
docker compose down        # 중지. 데이터(볼륨)는 유지
docker compose down -v     # 중지 + 모든 볼륨 삭제: DB, 업로드·스킬 파일, OpenFGA, 생성된 비밀 값
```

데이터는 이름 있는 볼륨 `postgres_data`, `backend_data`, `openfga_data`, `moldy_runtime`,
`opensandbox_data`(샌드박스 서버 상태), `pii_model`(가드레일 한국어 모델)에 남습니다.
`down -v` 후 다시 `up`하면 비밀 값이 새로 생성되고 첫 가입자가 다시 운영자가 됩니다.

## 9. 문제 해결

| 증상 | 확인할 것 |
|------|-----------|
| `port is already allocated` | 5절처럼 `MOLDY_*_PORT`로 포트를 바꿉니다. 5432는 로컬 PostgreSQL과 자주 겹칩니다 |
| `migrate` 또는 `authz-bootstrap`이 `Exited (1)` | `docker compose logs migrate` / `docker compose logs authz-bootstrap`. 고친 뒤 `docker compose up -d`를 다시 실행하면 1회성 서비스부터 다시 돕니다 |
| `authz-bootstrap`이 `Multiple matching stores` | 같은 OpenFGA에 `moldy` store가 여러 개입니다. `OPENFGA_STORE_ID`를 `backend/.env`에 지정하거나 `down -v`로 초기화합니다 |
| 브라우저에서 API 호출이 CORS 오류 | 접속한 주소와 `CORS_ALLOWED_ORIGINS`, `NEXT_PUBLIC_API_BASE_URL`이 맞는지 봅니다(5절) |
| 로그인 직후 다시 로그인 화면 | `backend/.env`에 `COOKIE_SECURE=true`가 있으면 http에서 쿠키가 저장되지 않습니다. 개발 구성은 `false`(기본값)로 둡니다 |
| 자격 증명 복호화 실패 | `ENCRYPTION_KEYS`가 바뀌었습니다(6절). 원래 값을 되돌리거나 `down -v`로 초기화합니다 |
| OpenFGA DB 인증 실패 | `OPENFGA_DB_PASSWORD`를 첫 기동 뒤에 바꿨습니다. 원래 값으로 되돌리거나 `down -v` |
| 빌드 중 `download.pytorch.org` `CERTIFICATE_VERIFY_FAILED` | TLS를 검사하는 망입니다. 루트 `.env`에 `MOLDY_BUILD_EXTRA_CA=<회사 CA 파일 경로>`를 넣고 다시 빌드합니다(2절 "가드레일 한국어 모델") |
| `pii-detector`가 unhealthy | `docker compose logs pii-model-fetch`에서 다운로드 결과를 봅니다. 받을 수 없는 망이면 2절의 `docker compose cp`로 넣습니다. 그동안에는 규칙 묶음으로 검사합니다 |
| 스킬 실행이 `Error: skill sandbox is unavailable.` | `docker compose ps opensandbox`가 healthy인지, `docker compose logs opensandbox`에 오류가 없는지 봅니다. Docker 소켓을 쓸 수 없는 환경이면 `SKILL_SANDBOX_ENABLED=false`로 끕니다 |

## 10. 다른 작업 방식과의 관계

- **uv/pnpm 개발 환경** (CLAUDE.md "로컬 개발 환경 세팅"): 코드를 고치며 hot reload로 개발할 때 씁니다.
  `docker compose up -d postgres`로 DB만 띄우고 백엔드와 프론트엔드는 호스트에서 실행할 수 있습니다.
  이 경우 `backend/.env`의 `DATABASE_URL`이 `localhost:<MOLDY_POSTGRES_PORT>`를 가리켜야 합니다.
  Docker 전체 스택과 호스트 개발 서버를 동시에 띄우면 8001·3000 포트가 겹치므로 둘 중 하나의 포트를 바꿉니다.
- **기본 권한 모드**: `AUTHZ_MODE=legacy`로 뜹니다. OpenFGA와 bootstrap은 준비만 해 두고, shadow·enforce
  전환은 [조직·권한 운영 가이드](org-authz-operations.md) 2절을 따릅니다. 고정된 ID가 있으므로
  `AUTHZ_MODE=shadow docker compose up -d`로 바꾸면 아웃박스·대조 작업이 바로 등록됩니다.
- **내부 구간 TLS 오버레이**: 운영에 가까운 구성은 같은 서비스 위에
  [운영 환경 셋업 7절](operator-setup.md#7-내부-구간-tls와-인증서)의 오버레이를 겹칩니다. 오버레이는
  `migrate`, `authz-bootstrap`, `backend`의 DB·OpenFGA 접속을 모두 검증된 TLS로 바꾸고, 개발용 OpenFGA
  기본값을 받지 않으므로 루트 `.env`에 `OPENFGA_API_TOKEN`, `OPENFGA_DB_PASSWORD`, `MOLDY_TLS_KEY_GID`를
  넣어야 합니다.

  ```bash
  bash scripts/internal-tls-certs.sh
  docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
  ```

  `APP_ENV=production`으로 올릴 때는 [운영 환경 셋업](operator-setup.md) 1절의 필수 값
  (`COOKIE_SECURE=true`, `ALLOW_FIRST_USER_AS_ADMIN=false` 등)을 함께 설정해야 부팅 검증을 통과합니다.
