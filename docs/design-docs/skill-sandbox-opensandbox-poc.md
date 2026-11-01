# 스킬 격리 실행 PoC 결과 — OpenSandbox (2026-11-02)

> 기능 확장 기획서 v2의 0단계 4번(C3 도구 실행 샌드박스, 15.3절 "선행 조건"의 격리 PoC) 결과다.
> 범위: OpenSandbox 기동, deepagents `BaseSandbox` 구현(`MoldySandbox`)으로 실제 스킬 하나 실행, 탈출·외부 통신 시험.
> 제품 코드는 바꾸지 않았다. PoC 코드와 원본 증거는 `poc/opensandbox/`에 있다.
> 선행 기획: [skill-execution-sandbox-isolation-plan.md](skill-execution-sandbox-isolation-plan.md)(B안 사이드카). 이 PoC는 기획서 v2가 제안한 OpenSandbox 경로를 검증한다.

## 1. 결론

| 질문 | 답 |
|---|---|
| 지금 스킬 스크립트는 격리되어 있나 | **아니다.** allowlist를 통과한 `python scripts/probe.py`가 백엔드 프로세스의 `ENCRYPTION_KEYS`·`JWT_SECRET`·`DATABASE_URL`을 `/proc/1/environ`에서 읽고, 다른 회사 대화 파일을 읽고 썼으며, 외부와 호스트 서비스에 자유롭게 접속했다. 실행 env를 최소로 넘기는 현재 계약은 이 경로를 막지 못한다. |
| OpenSandbox로 실제 스킬이 돌아가나 | **돌아간다.** `patent-hwpx-generator`가 SKILL.md 수정 없이 실행되어 유효한 `.hwpx`(101 KB, CRC 정상)를 만들었다. 샌드박스 생성 1.9초, 실행 1.1초. |
| deepagents 연결이 되나 | **된다.** `MoldySandbox(BaseSandbox)`는 `execute`·`upload_files`·`download_files`·`id` 4개만 구현했고, `write`·`edit`·`read`·`ls`·`grep`·`glob`이 모두 동작했다. |
| 격리가 충분한가 | **조건부.** `egress.mode = "dns+nft"`면 이번 시험의 탈출이 모두 막혔다. `"dns"`(예시 설정 기본값)면 **다른 회사 샌드박스의 execd에 직접 접속해 그 샌드박스의 자격 증명을 읽었다.** Docker 런타임은 execd 인증(`secure_access`)을 지원하지 않는다. |
| 다음 단계 | 운영 구성은 Kubernetes + gVisor + `secure_access` + `dns+nft`가 전제다. 이 조건으로 2차 PoC를 한 뒤 제품 연동(기획서 v2 15.3절)을 시작한다. |

## 2. 시험 환경

| 항목 | 값 |
|---|---|
| 호스트 | macOS, Docker Desktop 29.6.2 (linux/arm64, runc만 있음. gVisor 없음) |
| 서버 | `opensandbox-server==1.1.1`, runtime `docker`, network `bridge`, API 키 필수, 포트는 127.0.0.1에만 게시 |
| SDK | `opensandbox==1.2.0` (Python sync SDK) |
| 이미지 | `opensandbox/execd:v1.1.0` (sha256:6cf7dba2…), `opensandbox/egress:v1.1.7` (sha256:db7345d5…), 샌드박스 `python:3.12-slim` (sha256:aa943ce4…) |
| deepagents | 0.7.32 (main과 동일) |
| 기준선 | `moldy-enforce-verify-backend` 이미지(root 실행)에 현재 `backend/app`을 마운트하고 가짜 시크릿을 env로 넣음 |

## 3. 결과

### 3.1 기준선(현재 호스트 실행 경로)과 샌드박스 비교

같은 `probe.py`를 두 경로에서 실행했다. 증거: `poc/opensandbox/evidence/baseline-host-executor.txt`, `sandbox-dns-nft-pids128.json`, `sandbox-dns-only-escape.json`.

| 시도 | 현재 경로 | 샌드박스 `dns+nft` | 샌드박스 `dns` |
|---|---|---|---|
| allowlist 통과 (`python scripts/probe.py`) | 통과 | 해당 없음 | 해당 없음 |
| 실행 uid | 0 (root) | 0 (기본) / 1000 (`uid` 지정 시) | 0 |
| 백엔드 시크릿 읽기 (`/proc/1/environ`) | **읽음** (ENCRYPTION_KEYS, JWT_SECRET, DATABASE_URL) | 없음 (백엔드 프로세스가 없음) | 없음 |
| 다른 회사 파일 읽기·쓰기 | **읽고 씀** | 없음 (호스트 데이터 미마운트) | 없음 |
| 선언하지 않은 도메인 (example.com) | **접속** | 차단 (DNS 거부) | 차단 (DNS 거부) |
| 허용한 도메인 (pypi.org) | 접속 | 접속 | 접속 |
| IP 직접 접속 (1.1.1.1:443) | **접속** | 차단 | **접속** |
| 호스트 서비스 (host.docker.internal) | **접속** | 차단 | 차단 |
| 다른 샌드박스 execd (172.17.0.x:44772) | 해당 없음 | 차단 | **접속, 명령 실행, 그 샌드박스의 자격 증명 읽음** |
| 자기 egress 정책 변경 (127.0.0.1:18080/policy) | 해당 없음 | 401 거부 | 401 거부 |
| 권한 도구 (`mount`, NET_ADMIN) | 해당 없음 | 거부 (CapEff 0x800405fb, NET_ADMIN/SYS_ADMIN 없음) | 거부 |
| docker 소켓 | 없음 | 없음 | 없음 |

메타데이터 주소(169.254.169.254)는 기준선에서도 막혔지만, Docker Desktop에 메타데이터 서비스가 없어서다. 클라우드 노드에서는 별도 차단 규칙이 필요하다.

### 3.2 실행 계약

| 항목 | 결과 |
|---|---|
| 실제 스킬 실행 | `patent-hwpx-generator` 파일 6개 업로드 → `generate_hwpx.py` 실행(exit 0) → 산출물 다운로드. mimetype `application/hwp+zip`, 항목 11개, CRC 정상 |
| 명령 시간 제한 | `sleep 300`에 3초 제한 → 4.0초에 exit -1로 반환, 남은 프로세스 0 |
| 백엔드 쪽 중지 | 백그라운드 `sleep 600`을 `commands.interrupt`로 중지 → 프로세스 1 → 0 |
| 샌드박스 삭제 | `kill()` 뒤 샌드박스·egress 컨테이너 둘 다 사라짐 |
| 메모리 한도 (256Mi) | 1GB 할당 프로세스만 OOM으로 종료(137), execd는 살아 있음 |
| 프로세스 수 한도 | 서버 기본 `pids_limit=4096`에서는 fork 폭탄이 메모리 한도에 먼저 닿아 **OOM이 execd까지 죽였다.** 컨테이너는 running인데 명령을 받지 못한다(`sandbox-dns-nft-pids4096-resources.json`). `pids_limit=128`이면 117개에서 멈추고 execd가 살아 있다. |
| 명령별 env | `RunCommandOpts.envs`로 넣은 값은 그 프로세스 트리에만 있고, 끝나면 사라진다. 생성 시 `env`로 넣은 값은 pid 1·execd에 남아 샌드박스 수명 내내 모든 명령에 보인다. |
| execd 인증 | Docker 런타임은 `secure_access`를 거부한다("Use the Kubernetes runtime"). 인증 없이 호스트 게시 포트로 `POST /command`를 보내면 root로 실행된다. |

## 4. 발견과 제품 반영 요구

1. **현재 경로의 시크릿 노출은 실행 env 최소화로 막을 수 없다.** 스킬 자식 프로세스는 백엔드와 같은 uid라 `/proc/<pid>/environ`을 읽는다. 격리 전까지 운영에서 외부(마켓) 스킬 실행을 막는 것이 맞다. 기획서 v2 15.1절의 "실행 시험으로 확인" 항목은 이 PoC로 확인됐다(읽힘).
2. **egress는 `dns+nft`만 쓴다.** `dns` 모드는 도메인 질의만 막아 IP 직접 접속이 통과하고, 같은 브리지의 다른 샌드박스 execd에 닿는다. 서버 설정 검증에서 `dns`를 거부하고, 운영자 항상 차단 규칙에 샌드박스 네트워크 대역·메타데이터 주소·Moldy 내부 주소를 넣는다.
3. **execd는 인증 없이 열려 있다.** Docker 런타임은 개발 전용으로 두고, 운영은 Kubernetes + `secure_access=true`로 한다. 게시 포트는 127.0.0.1로 묶고 백엔드 외 접근을 네트워크 정책으로 막는다.
4. **샌드박스 명령은 비root(uid 1000)로 실행한다.** 이미지 기본은 root다. `RunCommandOpts.uid/gid`로 지정하면 시스템 경로 쓰기가 거부된다.
5. **자격 증명은 명령별 `envs`로만 넣는다.** 생성 시 `env`에 넣으면 샌드박스 수명 동안 모든 명령이 읽는다. 기획서 v2의 2단계(egress 자리표시 값 치환)는 그 다음이다.
6. **`pids_limit`을 낮추고 execd 생존을 확인한다.** 128~256 권장. 그래도 메모리 압박으로 execd가 죽을 수 있으니 MoldySandbox는 명령 실패 시 헬스 체크 후 샌드박스를 다시 만들고, 결과를 "샌드박스 재시작" 오류로 돌려준다(호스트 대체 실행 없음).
7. **작업 폴더는 미리 만든다.** execd는 없는 `working_directory`를 400으로 거부한다.
8. **5개 계약은 이 PoC 범위 밖이다.** redaction·audit·HiTL·artifact 인덱싱·credential 감사는 `execute_in_skill` 래퍼에 그대로 남아야 한다. artifact는 공유 볼륨이 없으므로 실행 뒤 `download_files`로 가져와 recorder에 넘기는 경로가 필요하다(기획서 v2 11.6절).

## 5. 시험하지 않은 것 (2차 PoC 범위)

- Kubernetes 런타임, gVisor/Kata, `secure_access` 토큰 흐름
- 회사별 네임스페이스와 동시 실행 한도
- Credential Vault·egress 헤더 치환
- 오피스 툴체인 이미지(LibreOffice/pandoc) 크기와 콜드 스타트
- prepared lease 경로(`LeaseBoundToolMiddleware`)에서의 허용 조건
- `execute_in_skill` 래퍼 연동과 5개 계약 회귀

## 6. 재현 방법

```bash
# 1) 서버 (별도 venv)
uv venv .osb && uv pip install --python .osb/bin/python "opensandbox-server==1.1.1" opensandbox "deepagents==0.7.32"
.osb/bin/opensandbox-server init-config ./sandbox.toml --example docker
# sandbox.toml 수정: port=18080, api_key 설정, publish_host="127.0.0.1",
#   [proxy] resolve_internal=false (macOS), [egress] mode="dns+nft", [docker] pids_limit=128
docker pull opensandbox/execd:v1.1.0 opensandbox/egress:v1.1.7 python:3.12-slim
.osb/bin/opensandbox-server --config ./sandbox.toml

# 2) 샌드박스 시험 (api_key를 담은 파일 경로를 넘긴다)
cd poc/opensandbox
OSB_KEY_FILE=/path/to/api_key ../../.osb/bin/python run_poc.py --label dns+nft --out /tmp/osb.json

# 3) 기준선 (backend 이미지 + 현재 코드 + 가짜 시크릿)
docker run --rm -w /app -e PYTHONPATH=/app -v "$PWD/backend/app:/app/app:ro" \
  -v "$PWD/poc/opensandbox:/poc:ro" --tmpfs /app/data -e MOLDY_DISABLE_ENV_FILE=true \
  -e ENCRYPTION_KEYS=<가짜 64hex> -e JWT_SECRET=fake -e DATABASE_URL=postgresql+asyncpg://fake:fake@db/moldy \
  --entrypoint /app/.venv/bin/python <backend 이미지> /poc/baseline_host.py
```

PoC 코드: `poc/opensandbox/moldy_sandbox.py`(deepagents 어댑터), `run_poc.py`(시험 8종), `probe.py`(탈출 프로브, 비밀값은 이름과 길이만 출력), `baseline_host.py`(현재 실행 계약 재현).
