# 스킬 격리 실행 — OpenSandbox docker 런타임 (1단계)

> 선행: [skill-sandbox-opensandbox-poc.md](skill-sandbox-opensandbox-poc.md)(1차 PoC), [skill-execution-sandbox-isolation-plan.md](skill-execution-sandbox-isolation-plan.md)(기획서).
> 코드: `backend/app/agent_runtime/skill_sandbox.py`, 연결 지점 `skill_executor.py`, 테스트 `backend/tests/test_skill_sandbox.py`.

## 1. 무엇을 하나

`SKILL_SANDBOX_ENABLED=true`이면 `execute_in_skill` 호출 **한 번마다** OpenSandbox 샌드박스를 하나 만들고, 명령이 끝나면 지운다. 끄면(기본값) 지금처럼 호스트에서 실행한다.

| 단계 | 동작 |
|---|---|
| 준비 | 스킬 폴더 사본(`/workspace/skill`)과 대화의 현재 출력 파일(`/workspace/outputs`)을 올린다. 심볼릭 링크는 올리지 않고, 스킬 폴더는 `SKILL_MAX_PACKAGE_BYTES`를 넘으면 거부한다. |
| 실행 | 허용 목록 검사(python/node/curl)는 그대로 통과해야 한다. 명령은 uid 1000으로, 작업 폴더 `/workspace/skill`에서 실행한다. 인자는 모두 따옴표 처리한다. |
| 자격 증명 | 명령별 env(`RunCommandOpts.envs`)로만 넣는다. 생성 시 env에는 넣지 않는다(생성 시 env는 샌드박스의 모든 명령이 읽는다). |
| 결과 | 명령이 새로 쓰거나 고친 출력 폴더 최상위 파일만 `output_dir`로 가져온다(파일당 50 MB, 합계 200 MB). 이름에 `/`·`..`가 있으면 버린다. 결과 문자열의 `/workspace/outputs/` 경로는 실제 출력 경로로 바꾼 뒤 기존 URL 치환을 탄다. |
| 정리 | 성공·실패·시간 초과·취소 모두 샌드박스를 지운다. 백엔드가 죽어도 샌드박스 수명(명령 시간 제한 + 120초)이 지나면 서버가 지운다. |
| 서버 장애 | 호스트로 대신 실행하지 않고 `Error: skill sandbox is unavailable.`을 돌려준다. |
| 감사 | 호출마다 `skill_security.sandbox_run` 이벤트 1건(기획서 v2 15.3절·27장). 결과는 success 또는 failure이고 사유 코드는 `timeout`·`nonzero_exit`·`cancelled`·`sandbox_unavailable`. 메타데이터에 샌드박스 ID, 종료 코드, 소요 시간, 실행 파일 이름, 허용 도메인이 들어가고 명령 인자와 자격 증명 값은 넣지 않는다. 차단된 외부 접속 건수는 egress 사이드카에서 가져오지 않아 기록하지 않는다. |
| 추적 | Langfuse가 켜져 있으면 실행의 trace에 `sandbox` span(같은 메타데이터, 출력에 종료 코드와 시간 초과 여부)을 남긴다. |
| 공유 작업 폴더 스킬 | 대화 밖 작업 폴더를 쓰는 스킬(`openwiki`)은 샌드박스에서 실행하지 않고 오류를 돌려준다(감사 사유 `sandbox_unsupported`). 임시 조치이며, 작업 폴더를 샌드박스로 옮기는 일은 따로 정한다. |

redaction·HiTL·사용량 기록·artifact 인덱싱은 바뀌지 않는다. 가져온 파일이 `output_dir`에 쓰이므로 artifact recorder는 호스트 실행 때와 같은 파일을 본다.

**실행 경로.** 샌드박스는 기본 실행 경로에만 해당한다. prepared lease 경로는 `execute_in_skill`을 디스패치할 수 없는 도구로 거부하며(`lease_bound_tool.py`, 시험 `tests/authz/test_lease_tool_denials.py`), 이 동작을 유지한다. lease 경로에서 스킬을 실행하려면 샌드박스 백엔드를 lease 계약에 넣는 별도 작업이 필요하다.

## 2. 네트워크

| 스킬 | 정책 |
|---|---|
| `requires_network` 없음 | 전부 차단 (`defaultAction=deny`, 규칙 없음) |
| `requires_network: true` | 기본 차단. `execution_profile.network_domains`에 선언한 도메인과 배포 허용 목록(`SKILL_SANDBOX_ALLOWED_DOMAINS`, 콤마 구분)만 연다. `*.` 와일드카드 가능. 선언한 도메인이 내부 주소로 풀려도 막히도록 내부 대역 차단 규칙을 허용 규칙보다 먼저 둔다: `10/8`, `172.16/12`(docker 브리지), `192.168/16`, `100.64/10`, `127/8`, `169.254/16`(메타데이터), `0/8`, `::1`, `fc00::/7`, `fe80::/10` |

`requires_network`인데 도메인이 하나도 없으면 네트워크가 없다. 스킬 검증기는 이 경우 `NETWORK_DOMAINS_MISSING` 경고, 형식이 틀린 도메인은 `NETWORK_DOMAINS_INVALID` 오류를 낸다. 회사별 허용 목록은 아직 없고 배포 단위 설정만 있다.

서버 설정은 반드시 `egress.mode = "dns+nft"`. `dns` 모드는 IP 직접 접속을 막지 못한다(1차 PoC §3.1).

## 3. 회사 구분 없이 되는 이유와 남은 위험

샌드박스는 호출마다 새로 만들고 지우므로, 다음 실행에 남는 데이터가 없다. 남는 위험은 **같은 시각에 떠 있는 샌드박스끼리**다.

| 위험 | 막는 방법 |
|---|---|
| 다른 샌드박스의 execd(인증 없음)에 접속 | 내부 대역 차단 + `dns+nft`. 아래 실측 참고 |
| 다른 사용자 파일 읽기 | 그 호출의 스킬 폴더와 출력 파일만 올린다. 호스트 볼륨을 마운트하지 않는다 |
| 자원 독점 | 서버 `pids_limit=128`, 샌드박스 CPU 1·메모리 512Mi(`SKILL_SANDBOX_CPU`/`_MEMORY`) |

docker 런타임은 execd 인증(`secure_access`)과 gVisor를 쓰지 않는다. 여러 회사가 쓰는 운영 환경은 Kubernetes + gVisor + `secure_access`로 옮긴다(2단계). 몰디 코드는 연결 주소(`SKILL_SANDBOX_DOMAIN`)만 바꾸면 된다.

## 4. 실측 (2026-11-05, macOS Docker Desktop arm64, opensandbox-server 1.1.1, SDK 1.2.0, execd v1.1.0)

실제 `execute_in_skill` 도구로 확인했다(테스트가 아니라 실제 서버 대상). 세 경로에서 같은 결과였다: ① 호스트에서 띄운 서버 + `python:3.12-slim`, ② Compose `opensandbox` 서버 + `moldy-skill-sandbox`(호스트에서 서버 경유 호출), ③ ②를 Compose `backend` 컨테이너 안에서 호출. ②·③에서는 `docx-document`(node)도 성공했다.

| 항목 | 결과 |
|---|---|
| `patent-hwpx-generator` | 성공, `.hwpx` 생성(CRC 정상), 결과 경로가 `/api/conversations/<id>/files/...`로 바뀜. 호출당 약 6초(생성 포함) |
| 실행 uid | 1000 |
| `/proc/1/environ` 읽기 | PermissionError |
| 주입한 자격 증명 | 스크립트에 보이고, 결과에서는 `<redacted:...>` |
| 이전 출력 파일 읽기 / 새 파일 가져오기 | 됨 / 됨 |
| `requires_network` 없음 | pypi·1.1.1.1·docker 게이트웨이·`host.docker.internal`·10.x 모두 실패 |
| `requires_network: true` (도메인 제한 전, #339) | pypi 200, 1.1.1.1 열림, docker 게이트웨이·`host.docker.internal`·10.x 실패 |
| `requires_network: true`, `network_domains: [pypi.org]` (도메인 제한 후) | pypi 200, 선언 안 한 example.com 실패, 1.1.1.1 실패, docker 게이트웨이 실패 |
| `requires_network: true`, 도메인 없음 | pypi·example.com·1.1.1.1·docker 게이트웨이 모두 실패 |
| 동시에 떠 있는 다른 샌드박스 execd(172.17.0.2:44772) | 접속 시간 초과. 대조로 일반 컨테이너에서 같은 주소에 접속하면 연결 거부(샌드박스 쪽 입력도 막힘) |
| 시간 제한 3초 | `Error: script execution timed out (3s).` |
| 실행 뒤 남은 컨테이너 | 0 |

## 5. 설정과 실행

### Docker Compose (기본)

`docker compose up -d --build`가 전부 띄운다([docker-quickstart.md](../docker-quickstart.md)).

| 구성 | 내용 |
|---|---|
| `skill-sandbox-image` | `docker/skill-sandbox/Dockerfile` → `moldy-skill-sandbox:local`. 백엔드 이미지와 같은 런타임(Python 3.12, Node 22, skill-node 패키지)에 curl·git |
| `opensandbox` | `docker/opensandbox/` (서버 1.1.1, `sandbox.toml`, `start.py`). Docker 소켓을 마운트한다. 호스트 포트 없음 |
| API 키 | `init-secrets`가 `SKILL_SANDBOX_API_KEY`를 만들고, 백엔드와 서버가 같은 값을 읽는다 |
| 백엔드 | `SKILL_SANDBOX_ENABLED=true`(기본), `SKILL_SANDBOX_DOMAIN=opensandbox:8090`, 서버 경유(`USE_SERVER_PROXY`) |

샌드박스의 네트워크 정책은 Docker 기본 `bridge`에서만 동작한다(사용자 정의 네트워크는 서버가 거부). Compose는 서비스를 기본 `bridge`에 붙이지 못하므로 `start.py`가 기동할 때 서버 컨테이너를 `bridge`에 연결한다. 백엔드와 DB는 `bridge`에 없다.

서버는 컨테이너 안에 있으므로, 샌드박스 포트를 게시하는 주소(`publish_host`)와 서버가 그 포트에 닿는 주소(`host_ip`)를 `start.py`가 정한다.

| 환경 | `publish_host` | `host_ip` | 확인 |
|---|---|---|---|
| Docker Desktop | `127.0.0.1` | `host.docker.internal` | 실측 |
| Linux Docker | bridge 게이트웨이(보통 `172.17.0.1`) | 같음 | **미확인** |

`0.0.0.0`은 쓰지 않는다. execd는 인증이 없어서 외부에 게시하면 누구나 root로 명령을 실행할 수 있다. 바꿔야 하면 `OPENSANDBOX_PUBLISH_HOST`, `OPENSANDBOX_HOST_IP`.

### 백엔드를 호스트에서 띄울 때 (uv)

```bash
# 서버 (별도 venv, 1차 PoC §6과 같음)
uv venv .osb && uv pip install --python .osb/bin/python "opensandbox-server==1.1.1"
.osb/bin/opensandbox-server init-config ./sandbox.toml --example docker
# sandbox.toml: port=18080, api_key, publish_host="127.0.0.1", [egress] mode="dns+nft",
#   [docker] pids_limit=128, macOS면 [proxy] resolve_internal=false
docker pull opensandbox/execd:v1.1.0 opensandbox/egress:v1.1.7 python:3.12-slim
.osb/bin/opensandbox-server --config ./sandbox.toml
```

backend `.env`:

```
SKILL_SANDBOX_ENABLED=true
SKILL_SANDBOX_DOMAIN=127.0.0.1:18080
SKILL_SANDBOX_API_KEY=<sandbox.toml의 api_key>
# 백엔드를 컨테이너로 띄우면 서버가 호스트에 게시한 포트에 닿지 않으니 서버 경유로 바꾼다
SKILL_SANDBOX_USE_SERVER_PROXY=false
SKILL_SANDBOX_IMAGE=moldy-skill-sandbox:local   # docker compose build skill-sandbox-image
```

Compose 서버를 그대로 쓰려면 위 서버 대신 `opensandbox`에 호스트 포트를 열고 `SKILL_SANDBOX_USE_SERVER_PROXY=true`로 둔다.

## 6. 아직 안 한 것

- **이미지:** `moldy-skill-sandbox`에는 스킬별 pip 패키지와 오피스 툴체인(LibreOffice·pandoc)이 없다. 허용 목록 검사가 백엔드에서 node 실행 파일을 찾으므로, 백엔드에 node가 없으면 node 스킬은 샌드박스에서도 거부된다(Compose 백엔드 이미지에는 있다).
- **Linux 호스트:** Compose 구성은 Docker Desktop(macOS)에서만 실측했다.
- **삭제 전파:** 명령이 지운 출력 파일은 호스트에서 지워지지 않는다.
- **동시 실행 한도:** 샌드박스 수를 제한하지 않는다(기획서 v2 11·13번과 함께).
- **회사별 네트워크 허용 목록:** 배포 단위 설정만 있다.
- **공유 작업 폴더:** `openwiki`는 샌드박스에서 실행하지 않는다(위 1절).
- 2단계(Kubernetes + gVisor + `secure_access`), Credential Vault.
