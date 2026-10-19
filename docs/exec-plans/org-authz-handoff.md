# Moldy 조직·권한·공유 개발 전달 묶음

회사(테넌트) > 조직 계층, 역할·기능 권한, 공유, 사용자별 연결, 사용 한도, 보존 정책, 회사별 SSO를 OpenFGA 기반으로 구현하기 위한 자료입니다. 기준 코드는 `origin/main` `9657ca60`(2026-10-05)입니다.

## 먼저 읽을 것

`docs/exec-plans/org-authz-plan.html` 하나에 모든 내용이 있습니다. 브라우저로 열면 됩니다(외부 파일 없이 열림).

읽는 순서:

1. 0장 한눈에 보기, 1장 목표와 완료 기준(1.3)
2. 3장 핵심 결정, 19장 결정 사항
3. 17장 작업 계획과 진행 방식(17.1 실행 순서, 17.2 브랜치·커밋, 17.3 단계 완료 확인, 17.4 PR)
4. 맡은 단계의 본문 절(작업 표의 "근거" 열이 가리키는 절)
5. 20장 기획 검증 결과(확인한 것과 확인하지 못한 것)

## 저장소에 넣을 위치

폴더 구조가 저장소 경로와 같습니다. P0 첫 커밋에서 그대로 복사합니다(17.3).

| 이 묶음 | 저장소 위치 | 관련 작업 |
|---|---|---|
| `docs/exec-plans/org-authz-plan.html` | 같은 경로. `docs/exec-plans/index.md`의 Active에 등록 | P0 첫 커밋 |
| `backend/app/authz/model.fga`, `model.fga.yaml` | 같은 경로 | A-04 |
| `scripts/p0/` | 같은 경로 | A-11, A-12 |

진행 기록 `docs/exec-plans/org-authz-progress.md`는 개발팀이 P0 첫 커밋에서 새로 만듭니다(형식은 17.3).

## 권한 모델 테스트

```bash
# OpenFGA CLI v0.8.1 기준
fga model test --tests backend/app/authz/model.fga.yaml
# 기대 결과: Tests 17/17, Checks 144/144, ListObjects 6/6
```

## P0 확인 스크립트 (A-11, A-12)

기획 단계에서 독립 시제품으로 한 번 돌린 결과가 `scripts/p0/results/`에 있습니다. P0에서는 같은 확인을 Moldy 저장소 안에서 다시 돌립니다.

### A-11 RLS 시제품 (기획서 8.8)

```bash
# 1) 새 PostgreSQL 16 (매번 새 DB에서 실행. 시나리오가 행을 추가하므로 두 번째 실행은 실패함)
docker run -d --name rls-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_USER=owner \
  -e POSTGRES_DB=moldy -p 127.0.0.1:55491:5432 postgres:16
docker exec -i rls-pg psql -v ON_ERROR_STOP=1 -U owner -d moldy < scripts/p0/rls_setup.sql

# 2) backend 가상환경으로 실행 (SQLAlchemy 2.0.48, asyncpg 0.31.0)
export RLS_DATABASE_URL=postgresql+asyncpg://app_user:pw@127.0.0.1:55491/moldy
uv run --directory backend python ../scripts/p0/rls_proto.py    # 기대: SUMMARY 15 / 15
uv run --directory backend python ../scripts/p0/rls_counter.py  # 반례 2건 재현
```

`rls_counter.py`는 잘못 구현하면 생기는 문제 두 가지를 보여 줍니다.

- 정책에서 `nullif` 없이 캐스팅하면 재사용 연결에서 uuid 오류가 납니다.
- 회사 값을 세션 범위로 설정하면 다음 연결 사용자에게 다른 회사 행이 보입니다.

둘 다 8.8 구현 규칙과 래칫 테스트로 막습니다.

### A-12 경계 교차 판정 부하 (기획서 8.10)

```bash
docker network create fga-net
docker network connect --alias fgapg fga-net rls-pg
docker exec rls-pg psql -U owner -d moldy -c "create database openfga"
docker run --rm --network fga-net openfga/openfga:v1.22.0 migrate \
  --datastore-engine postgres --datastore-uri 'postgres://owner:pw@fgapg:5432/openfga?sslmode=disable'
docker run -d --name fga --network fga-net -p 127.0.0.1:18091:8080 openfga/openfga:v1.22.0 run \
  --datastore-engine postgres --datastore-uri 'postgres://owner:pw@fgapg:5432/openfga?sslmode=disable'

export OPENFGA_URL=http://127.0.0.1:18091
uv run --directory backend python ../scripts/p0/fga_bench.py   # 약 2분, 결과는 scripts/p0/results/
```

비교하는 두 모델은 다음과 같습니다.

- `models/model_baseline.json`: 경계 교차 판정이 없는 이전 초안
- `models/model_current.json`: 최종 모델(`backend/app/authz/model.fga`를 `fga model transform`으로 변환)

모델을 바꾸면 이 파일을 다시 만듭니다. 목표값은 8.10에 있습니다. `results/`의 값은 개발 PC 한 대(같은 호스트 통신)에서 잰 것이라 운영 네트워크 왕복 시간이 빠져 있습니다. 최종 모델 기준으로 다시 잰 값은 다음과 같고, 모두 목표 안입니다.

| 판정 | p95 | 목표 |
|---|---|---|
| Check | 약 2.8ms | 20ms |
| BatchCheck 50건 | 약 22~24ms | 60ms |
| ListObjects | 약 4ms | - |

잘못 기록된 다른 회사 튜플의 허용 건수는 0/100입니다. 기획서 8.10 표는 직전 모델로 잰 값이라 BatchCheck가 약 20ms로 조금 다릅니다.

## 결정과 범위

- 19장의 결정은 모두 확정되었습니다. 기획과 다르게 구현해야 하면 진행 기록의 "결정 변경"과 ADR-023에 이유를 남깁니다(17.2).
- 후속(이번 범위 밖)은 1.2 표에 있습니다: 메일 발송, 회사 셀프 가입, 컴플라이언스 대화 열람, SAML 직접 구현과 SCIM, 고객 키(BYOK), 외부 IdP 에이전트 ID와 토큰 교환 등.
- PR은 두 개로 나눕니다.
  - PR 1: 구현 완료. 기존 동작을 유지하는 기본값으로 머지합니다.
  - PR 2: 강제 전환 후 legacy 코드와 옛 열을 정리합니다.

## 포함하지 않은 것

기획 과정에서 만든 예전 모델(`moldy-authz-model-v1.*`, `moldy.fga*`)과 초기 설계 문서(`moldy-authz-readiness.html`)는 지금 기획과 맞지 않아 넣지 않았습니다.
