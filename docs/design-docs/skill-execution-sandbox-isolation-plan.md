# 스킬 격리 실행 샌드박스 마이그레이션 개발 기획서 (B안 → A안)

> **문서 목적**: Moldy의 스킬 실행기를 "호스트 로컬 subprocess + 명령 allowlist"에서 "격리 컨테이너 실행"으로 전환하여, Anthropic 공식 오피스 스킬(docx/xlsx/pptx/pdf)을 **거의 그대로** 실행할 수 있게 한다. 이 문서 하나로 처음부터 끝까지 구현 가능하도록 현재 아키텍처 정밀 분석, 목표 설계, 파일 단위 태스크, 테스트/보안/롤아웃까지 담는다.
>
> **독자**: Moldy 백엔드/인프라 개발자.
> **작성일 기준 브랜치**: `feature/skill-studio-phase3` / `main` (deepagents 0.6.9).
> **전략**: **B안(컨테이너-백드 `execute_in_skill`, 계약 보존)** 을 먼저 달성하고, 그 산출물을 디딤돌로 **A안(deepagents 네이티브 `SandboxBackendProtocol`)** 으로 수렴한다.

---

## 0. 용어

| 용어 | 정의 |
|---|---|
| **seam** | 실행 방식을 교체하는 단일 코드 지점. 여기서는 `skill_executor.py:154-160`의 `asyncio.create_subprocess_exec`. |
| **runner** | 스킬 명령을 실제로 실행하는 백엔드. `host`(현재, 로컬 subprocess) / `container`(신규, 격리). |
| **사이드카(skill-sandbox)** | 툴체인을 담은 별도 컨테이너 서비스. 백엔드와 `backend_data` 볼륨을 공유하고 exec API로 명령을 대신 실행. |
| **5개 계약** | redaction·credential·audit·HiTL·artifact — 실행 위치가 바뀌어도 유지돼야 하는 보안/관측 불변식. |
| **SandboxBackendProtocol** | deepagents가 정의한 격리 실행 백엔드 인터페이스(`execute()` 제공). A안의 목표. |

---

## 1. 배경 & 목표

### 1.1 문제

Anthropic 오피스 스킬은 **풍부한 툴체인 + 임의 코드 실행**을 전제로 설계됐다:
- READ: `pandoc`, `python -m markitdown`, `pdftotext`, `pdfplumber` …
- CREATE: 에이전트가 **새 코드 파일을 작성**해 실행(docx-js, pptxgenjs, reportlab).
- 변환/렌더: LibreOffice(`soffice`), poppler(`pdftoppm`).

반면 Moldy의 현재 실행기는 **백엔드 호스트에서 직접 도는 로컬 subprocess**이며, 유일한 방어막이 `python scripts/<f>.py` / `node scripts/<f>.cjs` / `curl` **3개짜리 명령 allowlist**다. 즉:
- 툴체인 미설치(`pandoc`/`soffice`/`markitdown` + pip/npm 라이브러리 전무).
- allowlist가 위 명령을 전부 차단.

### 1.2 보안 핵심 (왜 "그냥 allowlist를 풀면" 안 되는가)

현재 스킬 subprocess는 **`ENCRYPTION_KEYS`·`JWT_SECRET`·`DATABASE_URL`·모든 API 시크릿을 보유한 백엔드 호스트**에서 실행된다(`skill_executor.py:154` `create_subprocess_exec`, 컨테이너 격리 없음). allowlist를 풀어 임의 명령을 허용하면 = **멀티유저(ADR-016) 환경에서 사용자 에이전트가 호스트 RCE** → 시크릿·DB 탈취. allowlist는 의도적 샌드박스다.

**따라서 격리는 "명령 allowlist"가 아니라 "컨테이너 경계"로 옮긴다.** 컨테이너 안에서는 임의 명령/툴체인을 허용하되, 호스트 시크릿과 물리적으로 분리한다.

### 1.3 목표

| # | 목표 |
|---|---|
| G1 | 오피스 스킬(docx/xlsx/pptx/pdf)을 SKILL.md 수정 없이 실행. |
| G2 | 실행을 호스트에서 격리 컨테이너로 이동, 호스트 시크릿 노출 0. |
| G3 | 기존 5개 계약(redaction/credential/audit/HiTL/artifact) 보존. |
| G4 | 기존 스킬(image-generation, deep-research, openwiki, k-skill 등)은 무회귀. |
| G5 | B안 산출물이 A안(deepagents 네이티브)의 하위 구현이 되도록 설계. |

### 1.4 비목표 (이번 범위 아님)

- 오피스 스킬 페이로드를 Moldy 레포에 커밋(vendoring) — **라이선스상 금지**(§4.5). 툴체인 이미지만 관리, 스킬은 런타임 설치/마운트.
- 완전 hosted 샌드박스(Modal/Daytona) 전환 — A안 확장 옵션으로만 기술.
- per-run 일회용 컨테이너 스폰(강한 크로스-런 격리) — B안 하드닝 후속(§11).

---

## 2. 현재 아키텍처 정밀 분석 (as-is)

> 근거: 소스 정밀 조사. 모든 경로는 `backend/` 기준.

### 2.1 실행 경로 전체 (`execute_in_skill`)

도구는 `app/agent_runtime/skill_executor.py:37` `_create_skill_execute_tool(ctx: SkillToolContext) -> BaseTool`의 클로저. 내부 코루틴 `execute_in_skill(skill_directory, command)`(`:53`) 흐름:

1. **slug 해석 + 부착 게이트**(`:64-78`): `ctx.descriptors[slug]` 없으면 거부. `resolved = descriptor.runtime_storage_path.resolve()`가 `cwd` 겸 검증 루트.
2. **base env 구성**(`:80-94`): **`os.environ` 통째 복사 아님** — `PATH`만 호스트에서 상속, `PYTHONPATH=HOME=resolved`(스킬 dir), `SKILL_OUTPUT_DIR/OUTPUTS_DIR=output_dir`, `NODE_PATH`(있으면), `SSL_CERT_FILE/REQUESTS_CA_BUNDLE`(있으면).
3. **credential env 주입**(`:100-112`): `descriptor.credential_bindings[key].env_map`(`{field: env_var}`)로 `env[env_name]=rc.decrypted[field]`, 동시에 `injected_env[env_name]=value`(→ 나중 redaction 키).
4. **allowlist 검증**(`:114`): `_prepare_skill_subprocess_args(command, resolved, env)` → `(args, error)`. 실패 시 sandbox denial 감사 + 에러 반환.
5. **timeout 정책 + 네트워크 게이트**(`:126-151`): `curl`은 `execution_profile.requires_network=True`일 때만. credential invoke 감사 기록.
6. **🔴 SEAM — subprocess 스폰**(`:153-160`):
   ```python
   await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
   proc = await asyncio.create_subprocess_exec(
       *args, cwd=str(resolved),
       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
   )
   ```
7. **capture + timeout + cancel**(`:161-175`): `wait_for(proc.communicate(), timeout)`; `TimeoutError`/`CancelledError`에서 `proc.kill()`+`wait()` (고아 방지, cancel 재전파).
8. **반환 조립**(`:177-217`): exit≠0면 STDERR 부착. exit 0 + `audit_kind=="execute_in_skill"` + user_id면 usage ledger(`record_chat_execution_nonfatal`). 절대경로→`/api/conversations/<thread>/files/...` URL 치환. `OUTPUT_FILES:` 목록.
9. **stdout redaction**(`:224-227`): `redact_credential_values(result, injected_env)`.
10. 도구는 `StructuredTool` + `attach_tool_risk(execute_in_skill_risk())`(HiTL 메타)로 래핑(`:231-240`).

**핵심**: seam 시점에 `args`/`cwd=resolved`/`env`/`timeout_seconds`가 이미 완성돼 있다. 후처리(URL 치환, OUTPUT_FILES, usage, redaction, audit)는 **전부 부모(백엔드) 프로세스**에 남고 `output_dir`를 파일시스템으로 읽는다.

### 2.2 deepagents 통합 (결정적 사실)

- `create_deep_agent` 단일 호출: `runtime_component_builder.py:116-147` `build_agent(...)`.
- Moldy가 넘기는 backend = **`FilesystemBackend(root_dir=str(_DATA_DIR), virtual_mode=True)`**(`:429`, `:288`). `_DATA_DIR = backend/data`.
- `FilesystemBackend`는 `BackendProtocol`이며 **`execute` 메서드가 없다** → **deepagents 내장 `execute` 도구는 비활성**. Moldy는 자체 `execute_in_skill` 도구로 호스트 subprocess를 직접 돌린다(2.1). **deepagents backend를 경유하지 않는다.**
- `virtual_mode=True`는 경로 봉쇄만 제공, **프로세스 격리 아님**(deepagents 문서 명시).
- skills는 `create_deep_agent(skills=[prefix])` → `SkillsMiddleware(backend=FilesystemBackend, sources=[prefix])`. prefix = `/runtime/<thread_id>/[agents/<name>/]skills/` → 디스크 `data/runtime/<thread>/.../skills/`.

### 2.3 allowlist 정책 (`skill_execution_policy.py`)

`_prepare_skill_subprocess_args(command, *, resolved, env)`(`:46`):
- `shlex.split` + 선행 `NAME=value` 로컬변수 + `${VAR}` 확장(셸 미기동, argv 직접 실행).
- **허용 3종**: `python scripts/<f>.py`(→ `args[0]=sys.executable`), `node scripts/<f>.{js,cjs,mjs}`(→ `_resolve_node_binary()`), `curl <one-url>`. 스크립트는 `resolved` 내부여야 함.
- **거부 코드**: `unsupported_executable`(python/node/curl 외), `inline_python`(`python -m`/`-c`), `path_traversal`, `curl_url_policy`, + 실행기 발행 `timeout_policy`/`undeclared_network`.
- curl SSRF 가드: private/loopback/link-local/metadata 차단, `--resolve`로 검증 IP 핀.

### 2.4 컨텍스트/디스크립터/프로파일

- `SkillToolContext`(`skill_runtime.py:112`): `thread_id, output_dir, runtime_root, descriptors{slug→desc}, user_id, agent_id, run_id, audit_kind`.
- `SkillRuntimeDescriptor`(`:88`): `id, slug, name, description, original_storage_path, runtime_storage_path, execution_profile, credential_bindings`.
- `output_dir` = `data/conversations/<thread_id>` = **`/app/data/conversations/<thread>`**(컨테이너).
- `runtime_root` = `data/runtime/<thread>/[agents/<name>/]skills` = **`/app/data/runtime/<thread>/.../skills`**; 스킬은 `_materialize_skill`이 `copytree`로 per-thread 복제(slug 키).
- **execution_profile은 런타임에서 오직 `timeout_seconds`(clamp 0<t≤420, 기본 30)와 `requires_network`(curl 게이트)만 소비**. `support_level/runners/requires_*`는 마켓 배지용, 실행기가 안 읽음.

### 2.5 5개 계약 (실행 위치가 바뀌면 위협받는 불변식)

| 계약 | 현재 구현 | 위치 |
|---|---|---|
| **A. Redaction** | `redact_credential_values(result, injected_env)` — 실제 주입값 리스트 기반, `str.replace`(ReDoS 불가). 값≥5자. + run-scoped `_add_skill_secrets_to_run`(protocol/SSE/영속 마스킹). | `marketplace/redaction.py:110`, `skill_executor.py:224`, `runtime_component_builder.py:210` |
| **B. Credential** | `resolve_credential_bindings`(override: agent_skills.config > SkillCredentialBinding > 없음) → 빌드 시 decrypt → `descriptor.credential_bindings` → hot path에서 `env_map`으로 env 주입. **최소 env(호스트 leak 없음)**. 미충족 시 빌드 시점 fail-fast. | `credential_requirements.py:308`, `skill_runtime.py:306`, `skill_executor.py:81-112` |
| **C. Audit** | `record_sandbox_denial`(action `skill_security.sandbox_denied`, reason_code) + `record_credential_audits`(action `credential.invoke`, per binding, 스폰 전). 자체 세션, best-effort. `audit_events` 테이블. | `skill_executor_audit.py`, `credentials/service.py:267` |
| **D. HiTL** | `execute_in_skill_risk()`=`CODE_EXECUTION`, `allowed_decisions=(approve,reject)`, `trigger_safe=False` → `interrupt_on={"execute_in_skill":...}`. 인터럽트는 **도구호출 경계**(스폰 전)라 실행 substrate와 무관. 트리거 모드는 `interrupt_on=None` + `execute_in_skill` 하드 차단. | `tools/risk.py:260`, `runtime/interrupts.py`, `runtime_component_builder.py:502` |
| **E. Artifact** | `ArtifactDeltaRecorder` — **파일시스템 diff 기반**(반환 문자열 파싱 아님). `ARTIFACT_SOURCE_TOOL_NAMES={execute_in_skill,write_file,edit_file}`. `output_dir` snapshot→diff→`conversation_artifacts`+`artifact_versions`. | `services/artifacts/recorder.py:33,70`, `conversation_stream_service.py:435` |

**호스트 잔류 4대 불변식**: ①평문 value→env-name 맵(A/C용) 호스트 상주, ②`output_dir`가 stat-충실 **공유 마운트**(E용), ③사전 정책 게이트(C denial 코드용) 호스트·스폰 전, ④도구호출 인터럽트(D)는 스폰 위에 있어 재배치 불필요.

### 2.6 시딩/패키징/버전

- 오피스 빌트인(docx-document/xlsx-spreadsheet/pptx-presentation/patent-hwpx-generator)은 `app/seed/system_skill_packages/`에 vendored, `DOCUMENT_SKILL_SPECS`로 마켓 시드.
- **content-hash가 바이트 단위**(`_content_hash`, `default_marketplace_skills.py:181`) — 어떤 바이트 변경도 스퓨리어스 버전 범프. (교훈: 이 디렉토리 손대지 말 것.)
- inspector 필수 프론트매터: **`name`, `description`만**. `version`은 선택(없으면 None). `SkillMetadataError(ValueError)`로 leaf 정규화.

### 2.7 배포 인프라 (인에이블러)

- `docker-compose.yml`: backend는 **이미 컨테이너**. `build: backend/Dockerfile`, 볼륨 `backend_data:/app/data`, 기본 compose 네트워크, **docker socket 미마운트**.
- `backend/Dockerfile`: 최종 `python:3.12-slim`. node 바이너리는 `node:22-slim`에서 복사, `skill-node/node_modules`(docx@9.7.1, pptxgenjs@4.0.1, xlsx)는 빌드 스테이지에서 pnpm 설치 후 복사. `ENV SKILL_NODE_BINARY=/usr/local/bin/node`, `SKILL_NODE_MODULES_DIR=/app/backend/skill-node/node_modules`.
- **🟢 결정적**: `_DATA_DIR=/app/data`. `runtime_root`(스킬 cwd)와 `output_dir`가 **둘 다 `/app/data` 하위** → **사이드카가 `backend_data:/app/data`를 마운트하면 경로가 바이트 동일**. ADR-018 상대경로라 DB에 호스트 절대경로가 안 박혀 이식 안전.
- config: `skill_node_binary`, `skill_node_modules_dir`, `conversation_output_dir=./data/conversations`, `data_root=./data`. **사전 정의된 sandbox/container/docker-socket 설정 없음**(신설 필요).

---

## 3. 목표 아키텍처

### 3.1 B안 — 컨테이너-백드 `execute_in_skill` (권장 1단계)

```
┌──────────────────────────── backend 컨테이너 ────────────────────────────┐
│  create_deep_agent(FilesystemBackend, tools=[..., execute_in_skill])      │
│  execute_in_skill 코루틴:                                                  │
│    ① 부착 게이트 ② env 구성 ③ credential 주입 ④ (host runner면) allowlist   │
│    ⑤ audit/HiTL ── (이하 전부 호스트 잔류) ──                              │
│    ⑥ SkillRunner.run(command|args, cwd, env, timeout, network) ───────────┼──┐
│    ⑦ URL치환·OUTPUT_FILES·usage ⑧ redaction (호스트)                       │  │ HTTP
└───────────────────────────────────────────────────────────────────────────┘  │ (compose net)
        │ backend_data:/app/data (공유)                                          ▼
┌──────────────────────────── skill-sandbox 사이드카 ─────────────────────────┐
│  toolchain 이미지(LibreOffice/pandoc/poppler/pip/npm) + POST /run           │
│  backend_data:/app/data (동일 마운트) → cwd/output_dir 경로 바이트 동일       │
│  시크릿/ DB 접근 없음. non-root, read-only rootfs(+ /tmp,/app/data 쓰기)      │
└─────────────────────────────────────────────────────────────────────────────┘
```

- 실행만 사이드카로 이동. **allowlist/redaction/audit/HiTL/artifact/credential 계약은 전부 백엔드에 잔류**(§2.5 4대 불변식 유지). 공유 볼륨 덕에 경로 변환 불필요.
- 사이드카는 백엔드가 아니라 **격리 경계** — 호스트 시크릿과 분리.

### 3.2 A안 — deepagents 네이티브 `SandboxBackendProtocol` (정석 2단계)

deepagents 스킬 문서의 정식 패턴:
```python
backend = CompositeBackend(
    default=MoldySandbox(...),                     # SandboxBackendProtocol.execute
    routes={"/skills/": StoreBackend(store, ns)},  # 스킬 파일은 Store
)
agent = create_deep_agent(backend=backend, skills=["/skills/"], store=store,
                          middleware=[SkillSandboxSyncMiddleware(backend), CredentialRedactionMiddleware(...)])
```
- 커스텀 `execute_in_skill` 폐기 → deepagents 내장 `execute`(이제 backend가 Sandbox라 활성).
- 5개 계약을 **middleware/wrapper로 재구현**(§7).
- **B의 `ContainerSandboxRunner`가 A의 `MoldySandbox.execute` 하위 구현이 된다** → B는 버리는 길이 아니라 A의 디딤돌.

### 3.3 왜 B→A (트레이드오프)

| | B (컨테이너-백드 execute_in_skill) | A (deepagents 네이티브) |
|---|---|---|
| 계약 | 전부 보존(코드 무이동) | middleware로 재구현 |
| 변경 범위 | seam 1곳 + runner 추상화 + 사이드카 | backend/스킬저장/도구/미들웨어 대개편 |
| 리스크 | 낮음(점진, feature flag) | 높음(redaction/audit 회귀) |
| 프레임워크 정합 | 커스텀 유지 | 정석, 업스트림 흡수 |
| 목표(오피스 실행) 달성 | ✅ 즉시 | ✅ (더 늦게) |

**결론**: G1~G4는 B로 먼저 달성, G5(정합)는 A로 수렴. 아래 §5~6이 B, §7~8이 A.

---

## 4. Anthropic 오피스 스킬 요구사항 & 툴체인

### 4.1 스킬별 플로우/도구

| 스킬 | READ | CREATE | EDIT/변환 |
|---|---|---|---|
| **docx** | `pandoc --track-changes=all`, `unpack.py` | **에이전트가 Node/docx-js 스크립트 작성** | `unpack.py`→XML편집→`comment.py`/`pack.py`; `.doc`변환·accept-changes = `soffice` |
| **xlsx** | pandas | **에이전트가 Python(openpyxl) 작성** | 수식 재계산 `recalc.py`(→`soffice`, 필수) |
| **pptx** | `python -m markitdown`, `thumbnail.py`(→soffice+pdftoppm+Pillow) | **에이전트가 Node/pptxgenjs 작성**(아이콘 sharp) | `unpack.py`→`add_slide.py`→XML편집→`clean.py`→`pack.py` |
| **pdf** | pypdf/pdfplumber, `pdftotext` | **에이전트가 Python(reportlab) 작성** | pypdf(merge/split/rotate/encrypt), 폼필=번들 스크립트+JSON, OCR=pdf2image+tesseract |

- **공유 `scripts/office/` 패키지**(docx/xlsx/pptx 동일): soffice 심, unpack/pack/validate, helpers, validators(`lxml`+`defusedxml`), 번들 OOXML XSD.
- **암묵 네임스페이스 상대 import**(`from office.soffice import …` 등) → 실행 시 `cwd=스킬dir` + `scripts/`가 import 가능해야 함(현 코드가 이미 `cwd=resolved`, `PYTHONPATH=resolved` 세팅 → 부합).

### 4.2 통합 툴체인 인벤토리 (사이드카 이미지)

**apt** (Debian/Ubuntu 기준):
```
libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress   # 무거움(~500MB~1GB), soffice 콜드스타트 느림
pandoc poppler-utils qpdf git
fonts-liberation fonts-dejavu           # Arial/Times 대체
fonts-noto-cjk fonts-nanum              # ★ 한글/CJK 렌더 필수 (없으면 tofu)
nodejs npm                              # docx-js/pptxgenjs/pdf-lib
# 선택(플로우별): imagemagick tesseract-ocr tesseract-ocr-kor pdftk-java build-essential(AF_UNIX 차단 샌드박스만)
```
**pip**:
```
defusedxml lxml openpyxl pandas Pillow pypdf pdfplumber pdf2image reportlab "markitdown[pptx]"
# 선택: pypdfium2 pytesseract
```
**npm(-g)**: `docx pptxgenjs sharp react-icons react react-dom` (+ 선택 `pdf-lib pdfjs-dist`). → 이미지에 **전역 설치**해 런타임 `npm install` 불필요(네트워크 회피). `NODE_PATH`를 전역 node_modules로.

### 4.3 시스템 요구

- `SAL_USE_VCLPLUGIN=svp`(soffice.py가 자동 설정) — X 서버 없이 헤드리스.
- **쓰기 가능 `HOME`/`/tmp` 필수** — `recalc.py`가 `~/.config/libreoffice/...`에 매크로 기록, accept-changes가 `/tmp/libreoffice_docx_profile` 사용. → 컨테이너 러너는 `HOME`을 **per-run 쓰기 tmp**(예: `/tmp/skillhome-<runid>`)로 두어 공유 스킬 dir 오염 방지 + soffice 프로파일 격리.
- **CJK 폰트 사전 설치** — 첫 렌더 시 폰트 캐시 빌드, 누락 시 조용히 박스.
- **콜드스타트** — 첫 `soffice` 수초. 이미지 빌드/부팅 시 `soffice --headless --terminate_after_init`로 프리웜.
- **poppler PATH** — `pdf2image`가 `pdftoppm` 셸아웃.

### 4.4 "에이전트가 코드 작성→실행" 플로우

CREATE는 에이전트가 새 스크립트(`node create.js`/`python gen.py`)를 **써서** 실행한다. Moldy에서:
1. 에이전트가 `write_file`(deepagents FilesystemBackend, root=`data/`)로 `/runtime/<thread>/.../skills/docx/create.js` 작성 → **공유 볼륨이라 사이드카가 봄**.
2. `execute_in_skill(command="node create.js")` → 컨테이너 러너가 사이드카에서 실행.

→ 현 host allowlist는 `scripts/<f>` 고정이라 이걸 막음. **컨테이너 러너는 allowlist 완화**(§5.3)로 임의 명령 허용. write_file이 runtime 스킬 dir에 쓸 수 있어야 함(FilesystemBackend virtual_mode가 root 하위 쓰기 허용 — 부합).

### 4.5 🔴 라이선스 경고 (vendoring 금지)

- 네 스킬 프론트매터 모두 `license: Proprietary. LICENSE.txt has complete terms` — **그런데 LICENSE.txt가 어디에도 없음**. 재배포 권리 불명 → **레포에 커밋하지 말 것**(§2.6 content-hash 시드 경로 금지).
- 툴체인(pypdf/reportlab BSD, pdfplumber/pdf-lib MIT, **poppler GPL-2**, **LibreOffice MPL-2/LGPL-3**)은 **별도 바이너리로 apt/pip/npm 설치·subprocess 호출** → GPL 파생물 얽힘 회피. **소스 정적 링크/벤더링 금지**.
- 번들 OOXML XSD(ECMA/MS)도 제3자 스펙 재배포 항목.
- **채택 패턴(확정)**: 툴체인은 **이미지**에만(apt/pip/npm, 정적링크/벤더링 금지). 스킬 페이로드는 운영자 결정에 따라 **빌트인 시드**(§5.7). ⚠️ 단 **자체/사내 배포 한정** — Moldy를 외부에 배포/공개하기 전 실제 라이선스 확보 필수. content-hash 바이트 민감성(§2.6) 유의.

---

## 5. B안 상세 설계

### 5.1 실행 추상화: `SkillRunner`

신규 `app/agent_runtime/skill_runner.py`:
```python
@dataclass(frozen=True)
class SkillRunResult:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    timed_out: bool = False

class SkillRunner(Protocol):
    async def run(self, *, args: list[str] | None, command: str | None,
                  cwd: str, env: dict[str, str], timeout_seconds: float,
                  network: bool) -> SkillRunResult: ...
    async def cancel(self, handle) -> None: ...   # CancelledError 경로

class LocalSubprocessRunner:      # 현재 동작 이관 (host runner)
    # asyncio.create_subprocess_exec(*args, cwd, env) + wait_for + kill
    ...

class ContainerSandboxRunner:     # 신규 (사이드카 HTTP)
    # POST {command, cwd, env, timeout, network} → 사이드카, 스트리밍/폴링으로 결과
    ...
```
- `LocalSubprocessRunner`는 §2.1 6~7단계를 **그대로** 이관(회귀 0 목표).
- `ContainerSandboxRunner`는 §5.4 사이드카 API 호출.

### 5.2 execution_profile 확장 (무마이그레이션)

`execution_profile`은 JSON 컬럼이라 **DB 마이그레이션 불필요**. 신규 필드(전부 선택, 기본 host):
```jsonc
{
  "runner": "container",          // "host"(기본) | "container"
  "image": "moldy-skill-sandbox", // container일 때 사용할 사이드카/이미지 라벨
  "timeout_seconds": 600,         // 오피스는 soffice 콜드스타트 감안 상향
  "requires_network": false,      // 컨테이너 네트워크 모드로 매핑
  "relaxed_commands": true        // container일 때 allowlist 우회
}
```
- 실행기 소비 지점 확장: 현재 `_skill_timeout_seconds`/`_requires_network`만 읽으므로 여기에 `runner`/`relaxed_commands` 분기 추가. `_MAX_SKILL_TIMEOUT_SECONDS`(420)를 container일 때 상향(예: 900)하도록 clamp 조건 분기.

### 5.3 조건부 allowlist

`skill_executor.py`의 4단계 분기:
```python
runner_kind = _runner_kind(descriptor)   # "host" | "container"
if runner_kind == "host":
    args, error = _prepare_skill_subprocess_args(command, resolved=resolved, env=env)
    # ... 기존 거부/denial 경로 그대로
    run_input = {"args": args, "command": None}
else:  # container
    error = _prepare_container_command(command)   # 경량 검증 (§5.3.1)
    run_input = {"args": None, "command": command}
```
**컨테이너 경량 검증**(`_prepare_container_command`): 명령 길이 상한, NUL/제어문자 차단, `shlex.split` 파싱 가능 확인(실패 시 거부). **executable allowlist·path 강제 없음** — 격리가 컨테이너 경계이므로. `curl`/네트워크는 `requires_network`→컨테이너 네트워크 모드로 강제(§5.9). denial 감사는 유지(reason_code `container_command_policy` 신설).

> ⚠️ 컨테이너 모드에서도 **호스트 시크릿 env는 여전히 최소 주입**(§2.5-B) — 컨테이너가 격리돼도 불필요 시크릿을 넣지 않는다.

### 5.4 사이드카 서비스 `skill-sandbox`

**Dockerfile** `skill-sandbox/Dockerfile` (신규 디렉토리, 레포 커밋 OK — 툴체인만):
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
    pandoc poppler-utils qpdf git \
    fonts-liberation fonts-dejavu fonts-noto-cjk fonts-nanum \
    nodejs npm ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir defusedxml lxml openpyxl pandas Pillow pypdf \
    pdfplumber pdf2image reportlab "markitdown[pptx]"
RUN npm install -g docx pptxgenjs sharp react-icons react react-dom
ENV NODE_PATH=/usr/local/lib/node_modules
ENV SAL_USE_VCLPLUGIN=svp
# 폰트 캐시 + soffice 프리웜
RUN fc-cache -f && soffice --headless --terminate_after_init || true
COPY skill-sandbox/runner /app/runner
CMD ["uvicorn", "runner.main:app", "--host", "0.0.0.0", "--port", "8899"]
```

**exec API**(`runner/main.py`, 최소 FastAPI):
- `POST /run` body `{command, cwd, env, timeout_seconds, network, run_id}` → 사이드카 내부에서 `HOME`을 `/tmp/skillhome-<run_id>`로 세팅(§4.3), `asyncio.create_subprocess_shell(command, cwd=cwd, env=env)` 또는 `create_subprocess_exec(["/bin/sh","-c",command])`, `wait_for(timeout)`. 반환 `{stdout, stderr, returncode, timed_out}`.
- `POST /cancel/{run_id}` → 해당 subprocess kill(백엔드 CancelledError 경로용).
- 인증: 공유 시크릿 헤더(`X-Sandbox-Token`, compose env). compose 내부 네트워크 전용, 외부 미노출.

**docker-compose 추가**:
```yaml
  skill-sandbox:
    build: { context: ., dockerfile: skill-sandbox/Dockerfile }
    environment:
      SANDBOX_TOKEN: ${SANDBOX_TOKEN:?set-a-token}
    volumes:
      - backend_data:/app/data          # ★ 동일 볼륨 → 경로 바이트 동일
    tmpfs: [ "/tmp" ]                    # HOME/프로파일 쓰기 (또는 writable layer)
    read_only: true                     # rootfs 읽기전용 (/app/data, /tmp만 쓰기)
    mem_limit: 2g
    pids_limit: 256
    cpus: "2.0"
    networks: [ default ]               # network 정책은 §5.9
    # docker socket 미마운트, no privileged
  backend:
    environment:
      SKILL_SANDBOX_ENABLED: ${SKILL_SANDBOX_ENABLED:-false}
      SKILL_SANDBOX_URL: http://skill-sandbox:8899
      SANDBOX_TOKEN: ${SANDBOX_TOKEN:-}
    depends_on: { skill-sandbox: { condition: service_started } }
```

> **격리 수준(솔직히)**: 이 사이드카는 **호스트로부터** 격리(시크릿/DB 없음). 다만 **모든 스킬 런이 한 사이드카를 공유** → 크로스-런 격리는 프로세스 수준(사이드카 내 별도 유저/`/tmp` 분리). 강한 per-run 컨테이너 격리는 §11 하드닝(러너를 per-run `docker run`으로 스폰; 또는 A안에서 hosted 프로바이더). B의 목표(호스트 RCE 제거)는 충족.

### 5.5 Seam 교체

`skill_executor.py:153-175`를 러너 호출로:
```python
runner = get_skill_runner(runner_kind)          # 팩토리 (config 기반)
await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
try:
    res = await runner.run(
        args=run_input["args"], command=run_input["command"],
        cwd=str(resolved), env=env,
        timeout_seconds=timeout_seconds,
        network=_requires_network(descriptor),
    )
except asyncio.CancelledError:
    await runner.cancel(handle)   # 컨테이너: POST /cancel
    raise
if res.timed_out:
    return f"Error: script execution timed out ({timeout_seconds:g}s)."
stdout, stderr = res.stdout, res.stderr
returncode = res.returncode
# ↓ 이후 8~10단계(URL치환/OUTPUT_FILES/usage/redaction) 완전 동일
```
- `LocalSubprocessRunner`는 위 `except`에서 `proc.kill()`+`wait()` 유지.
- 8~10단계와 audit/HiTL은 **미변경** → 계약 자동 보존.

### 5.6 계약 보존 매핑 (B)

| 계약 | B에서 보존 방법 |
|---|---|
| A. Redaction | 러너가 `stdout/stderr`를 그대로 반환 → 호스트에서 `redact_credential_values(result, injected_env)` **동일 적용**. `injected_env` 맵은 호스트 상주. |
| B. Credential | env dict를 러너에 그대로 전달(사이드카 요청 body). 최소-env 불변식 유지. `PYTHONPATH/HOME/SKILL_OUTPUT_DIR`는 **공유 경로라 그대로 유효**(단 HOME은 §4.3대로 컨테이너 tmp 권장 → env override). fail-fast는 빌드 시점이라 무관. |
| C. Audit | 사전 정책 게이트(host allowlist 또는 container 경량검증)·credential invoke·denial 전부 **호스트·스폰 전 유지**. container denial reason `container_command_policy` 추가. |
| D. HiTL | 인터럽트가 도구호출 경계라 **무변경**. `execute_in_skill` 이름 유지 → risk 매핑 그대로. |
| E. Artifact | `output_dir`가 **공유 stat-충실 마운트**라 recorder의 fs diff가 사이드카 산출 파일을 그대로 관측. `ARTIFACT_SOURCE_TOOL_NAMES` 변경 불필요(이름 유지). |

### 5.7 격리 대상 결정 — 능력(capability) 기반 (오피스 특화 아님)

> **원칙(2026-07-14 확정)**: 격리는 "오피스냐"가 아니라 **"스킬이 실제로 코드를 실행/파일을 생성하느냐"**로 정한다. 부수효과가 있는 스킬은 모두 격리한다.

스킬은 두 종류다:
- **텍스트/참조 스킬** — 실행 없음(deepagents 정의상 "skills = TEXT"). `execute_in_skill` 호출 자체가 없음 → **격리 무관**(아무것도 실행 안 함).
- **실행형 스킬** — 스크립트 실행/파일 쓰기 등 부수효과 있음(오피스·image-generation·deep-research·openwiki·patent-hwpx·k-skill 등) → **전부 격리 컨테이너에서 실행**.

즉 **"실행형 스킬 = 격리 by default"**. `execution_profile.runner`의 실행형 기본값을 `container`로 두고, `host`(현 allowlist 로컬 subprocess)는 **레거시 opt-out → Phase 2에서 폐기**. (파일 쓰기는 거의 모든 실행형 스킬의 정상 동작이라 단독 기준이 아니며, "코드를 실행하는가"가 실질 기준 — `execute_in_skill`을 호출하는 모든 스킬이 해당.)

- **공유 warm 사이드카라 격리 비용이 작다**(per-run 컨테이너 스폰 아님, HTTP 왕복+subprocess). 단순 스킬을 컨테이너로 돌려도 오버헤드 미미 → host 빠른경로를 유지할 이유가 약함.
- 대가: **사이드카 이미지가 모든 실행형 스킬의 런타임 의존성을 담아야 함**(오피스 툴체인 §4.2 + 기존 스킬 python/네트워크 의존). Phase 2에서 기존 스킬 의존성 인벤토리 필요.
- `requires_network`는 스킬별로 사이드카 네트워크 정책에 매핑(image-gen/deep-research/openwiki=네트워크 필요, 오피스/pdf=대개 불필요).

**롤아웃(점진, §12)**: Phase 1(B) = **오피스 스킬만** 사이드카로(경로 검증, `SKILL_SANDBOX_ENABLED` 게이트, 기존 스킬은 host 유지) → Phase 2 = **모든 실행형 스킬을 사이드카로 이전 + host allowlist 경로 폐기**(격리 by default 완성).

**오피스 스킬 공급(확정: 빌트인)**: 운영자 결정에 따라 오피스 스킬을 **빌트인 시드**(`system_skill_packages/` + 마켓 시드, 현 docx-document 방식)로 번들하고 `execution_profile.runner="container"` 부여.
> ⚠️ **라이선스 주의(유지)**: Anthropic 오피스 스킬은 `Proprietary`(LICENSE.txt 부재). **자체/사내 배포**는 운영자 판단으로 진행하되, **외부 공개/재배포 전 실제 라이선스 확보 필수**. 툴체인 바이너리(poppler GPL-2, LibreOffice MPL/LGPL)는 이미지에 apt로만 설치·subprocess 호출(정적링크/벤더링 금지)로 얽힘 회피. content-hash 바이트 민감성(§2.6) 유의 — 시드 후 파일 바이트 변경 금지.

### 5.8 신규 config (`app/config.py`)

```python
skill_sandbox_enabled: bool = False
skill_sandbox_url: str = "http://skill-sandbox:8899"
skill_sandbox_token: str = ""            # SANDBOX_TOKEN
skill_sandbox_default_timeout_seconds: int = 600
skill_sandbox_max_timeout_seconds: int = 900
skill_sandbox_home_dir: str = "/tmp"     # per-run HOME 베이스
```

### 5.9 네트워크/보안 정책

- `requires_network=false`(기본) → 사이드카를 **egress 차단 네트워크**(compose internal network, 외부 라우트 없음)로. `true`면 제한 egress 네트워크.
- 사이드카: `read_only` rootfs, `mem_limit`/`pids_limit`/`cpus`, non-root user, `no-new-privileges`, seccomp 기본. docker socket/privileged 금지.
- SANDBOX_TOKEN 헤더 검증. 백엔드↔사이드카는 compose 내부 전용.
- 시크릿은 요청 **body**로만(프로세스 args/URL 금지), 사이드카 로그에 env 미기록.

### 5.10 취소/타임아웃

- 타임아웃: 사이드카가 자체 `wait_for` + kill, `{timed_out:true}` 반환.
- 취소: 백엔드 `CancelledError` → `runner.cancel(run_id)` → `POST /cancel/{run_id}` → 사이드카 kill. "고아/죽은 대화 dir 기록 방지" 불변식 유지.

---

## 6. B안 구현 태스크 (파일 단위 체크리스트)

### Phase B0 — 사이드카 스캐폴딩
- [ ] `skill-sandbox/Dockerfile`(§5.4), `skill-sandbox/runner/main.py`(`/run`,`/cancel`,토큰).
- [ ] `skill-sandbox/runner/package` node 전역 설치 검증(`node -e "require('docx');require('pptxgenjs')"`).
- [ ] 이미지 빌드 + `soffice --headless --terminate_after_init` 프리웜 확인, CJK 렌더 스모크(한글 docx→pdf 박스 없음).
- [ ] `docker-compose.yml`에 `skill-sandbox` 서비스 + backend env/depends_on.

### Phase B1 — 러너 추상화 (무동작변경)
- [ ] `app/agent_runtime/skill_runner.py`: `SkillRunner`/`SkillRunResult`/`LocalSubprocessRunner`(현 로직 이관)/`get_skill_runner` 팩토리.
- [ ] `skill_executor.py:153-175`를 `LocalSubprocessRunner` 경유로 리팩터(seam만). **회귀 테스트: 기존 스킬 스모크 그대로 그린**.

### Phase B2 — 컨테이너 러너 + 조건부 allowlist
- [ ] `ContainerSandboxRunner`(HTTP, 토큰, 타임아웃, cancel).
- [ ] `skill_execution_policy.py`: `_prepare_container_command`(경량검증) + `_runner_kind(descriptor)` + `runner`/`relaxed_commands` 소비.
- [ ] `skill_executor.py`: host/container 분기(§5.3), container denial reason 추가.
- [ ] config 신규 필드(§5.8). `_skill_timeout_seconds` container clamp 상향.

### Phase B3 — 오피스 스킬 빌트인 시드 + HOME 처리
- [ ] 오피스 스킬 4종을 `system_skill_packages/` + `DOCUMENT_SKILL_SPECS`(현 docx-document 방식)로 빌트인 시드, `execution_profile.runner="container"` 부여. (라이선스 캐비엇 §5.7)
- [ ] `runner` 필드 소비 + `SKILL_SANDBOX_ENABLED` 게이트(Phase 1은 오피스만 container).
- [ ] container 러너에서 `HOME`을 per-run tmp로 override(§4.3), 사이드카가 생성/정리.
- [ ] 한글 렌더 스모크(폰트) + soffice 콜드스타트 프리웜 검증.

### Phase B4 — 계약 보존 검증
- [ ] redaction: 컨테이너 stdout에 주입 시크릿이 들어가도 `<redacted:...>` 확인.
- [ ] artifact: 사이드카가 쓴 `.docx/.pdf`가 `conversation_artifacts`에 인덱싱되는지(공유 볼륨 diff).
- [ ] audit: container_command_policy denial + credential.invoke 이벤트.
- [ ] HiTL: 오피스 스킬 실행 전 승인 카드(무변경 확인).

### Phase B5 — 테스트/보안/문서 (§10, §11)
- [ ] 유닛(러너 추상화, 조건부 정책), 통합(사이드카 실왕복), E2E(오피스 실문서 생성), 보안(호스트 격리·시크릿 비노출).
- [ ] 운영 문서: 오피스 스킬 설치·`SKILL_SANDBOX_ENABLED` 켜기·이미지 빌드.

**B안 done-when**: `SKILL_SANDBOX_ENABLED=true` + 오피스 스킬 install 상태에서 (1) 채팅에서 "한글 보고서 docx 만들어줘" → `.docx` 산출+아티팩트 인덱싱+미리보기, (2) 시크릿 미노출, (3) 호스트에서 임의 프로세스 스폰 불가(격리), (4) 기존 스킬 무회귀.

---

## 7. A안 상세 설계 (정석 수렴)

### 7.1 `MoldySandbox(SandboxBackendProtocol)`
- `deepagents/backends/sandbox.py`,`local_shell.py`,`langsmith.py` 참조. `execute(command)->ExecuteResponse`를 **B의 `ContainerSandboxRunner`로 위임** + `BaseSandbox`가 파일연산을 execute 위에 자동 구성.
- `upload_files`/`download_files`는 공유 볼륨(사이드카) 또는 사이드카 파일 API로.

### 7.2 백엔드 교체
```python
backend = CompositeBackend(
    default=MoldySandbox(runner=ContainerSandboxRunner(...)),
    routes={"/skills/": StoreBackend(store=store, namespace=...)},  # 또는 공유볼륨 FilesystemBackend 유지
)
```
- 옵션 A1(최소 계약 재구현): `/skills/`와 `output_dir`를 **공유 볼륨**에 유지 → artifact recorder fs diff 그대로. `default=MoldySandbox`만 실행 격리.
- 옵션 A2(완전 네이티브): 스킬을 `StoreBackend`로 이전 + `SkillSandboxSyncMiddleware`(store→sandbox 업로드) + 산출물은 sandbox download API → recorder 재구현.

### 7.3 도구 전환
- 커스텀 `execute_in_skill` → deepagents 내장 `execute`(backend가 Sandbox라 활성). 또는 `execute_in_skill`을 `MoldySandbox.execute` 얇은 래퍼로 유지(계약 훅 보존, 더 안전).

### 7.4 5개 계약 재구현 (middleware/wrapper)
| 계약 | A 재구현 |
|---|---|
| Redaction | `execute` 후처리 미들웨어가 `redact_credential_values(out, injected_env)` 재적용. `injected_env`를 credential 해석에서 미들웨어로 주입. |
| Credential | 바인딩 해석→env를 `execute`의 per-call env로 전달(Cipher decrypt는 호스트 잔류, 평문만 샌드박스로). |
| Audit | `execute` 래퍼가 denial/ invoke 이벤트 기록. 사전 정책은 wrapper 유지. |
| HiTL | `interrupt_on["execute"]`(base policy에 **이미 존재**) + `trigger_blocked_tools`에 `"execute"` 추가(현재 `execute_in_skill`만 특례). |
| Artifact | A1: 공유볼륨 유지 → 무변경. A2: `ARTIFACT_SOURCE_TOOL_NAMES`에 `"execute"` 추가 + sandbox 파일 API로 diff. |

### 7.5 B→A 승격
- `ContainerSandboxRunner`(B) → `MoldySandbox.execute`(A) **직접 승격**.
- 사이드카/이미지/네트워크 정책/취소 로직 재사용.
- 선택: hosted 프로바이더(LangSmith/Daytona/Modal/Harbor)로 per-run 격리 확장(§11).

---

## 8. A안 구현 태스크 (요약)
- [ ] `MoldySandbox(SandboxBackendProtocol)` + `ContainerSandboxRunner` 승격.
- [ ] `build_agent` backend를 `CompositeBackend(default=MoldySandbox, routes=...)`로.
- [ ] 5개 계약 미들웨어(§7.4). `trigger_blocked_tools`에 `execute` 추가.
- [ ] (A2) 스킬 Store 이전 + `SkillSandboxSyncMiddleware` + recorder sandbox 파일 API.
- [ ] 계약 등가성 테스트(B와 동일 오라클로 양쪽 고정 — 위임 등가성 함정 회피).
- [ ] 커스텀 `execute_in_skill` 제거 또는 얇은 래퍼로 축소.

**A안 done-when**: deepagents 네이티브 `execute` + Sandbox backend로 오피스 스킬 실행, 5개 계약 회귀 테스트 그린, B와 동작 등가.

---

## 9. 데이터 모델 / 마이그레이션

- **DB 마이그레이션 불필요**: `execution_profile`은 JSON 컬럼. `runner/image/relaxed_commands` 추가는 스키마 무변경.
- 오피스 스킬은 **시드 아님**(라이선스) → `default_marketplace_skills.py`/`system_skill_packages` **미변경**(content-hash 함정 회피).
- 신규 관측 컬럼 불필요. audit는 기존 `audit_events` 재사용(reason_code 추가만).

---

## 10. 테스트 전략

| 레이어 | 항목 |
|---|---|
| **유닛(backend)** | `SkillRunner` 추상화(Local/Container mock), `_prepare_container_command` 경계, `_runner_kind`/profile 소비, redaction이 컨테이너 stdout에도 적용, timeout clamp(container 상향). mutation 실증(가드 제거 시 FAIL). |
| **통합** | 사이드카 실왕복(`POST /run` 실행/타임아웃/취소), 공유 볼륨 경로 동일성(사이드카가 쓴 파일 호스트 관측). `-m integration`. |
| **E2E** | 오피스 실문서 생성 투어: 한글 docx(폰트), xlsx(수식 recalc), pptx(pptxgenjs), pdf(reportlab/폼). 아티팩트 인덱싱+미리보기. 스크립티드 모델로 결정화. |
| **보안** | (1) 컨테이너 명령이 호스트 프로세스/파일에 도달 불가, (2) 주입 시크릿이 stdout/영속 이벤트에 평문 미노출, (3) `requires_network=false`면 egress 차단, (4) 사이드카에 DB/시크릿 env 부재. |
| **계약 회귀** | 5개 계약 각각 독립 오라클(위임 등가성 tautology 금지). audit action/outcome/reason_code 내용 단언. |

---

## 11. 보안 검토 & 하드닝

- **위협 모델**: 사용자 에이전트가 임의 명령을 실행 → (완화) 격리 컨테이너 경계, 호스트 시크릿/DB 분리, egress 차단, 리소스 상한.
- **잔여 리스크(B)**: 단일 사이드카 공유 → 크로스-런 격리 약함(프로세스 수준). **하드닝**: per-run `docker run`(요구 시 docker socket + rootless/gVisor), 또는 A안 hosted per-run 샌드박스.
- **시크릿**: body 전달·로그 미기록·이미지 레이어/`docker inspect` 미포함. redaction 이중(값 기반 + run-scoped).
- **DoS**: `mem_limit/pids_limit/cpus/timeout`, 스킬 usage/queue와 연계.
- **라이선스**: §4.5 — 툴체인만 이미지, 스킬 페이로드 미커밋.

---

## 12. 롤아웃 / 롤백

- **feature flag `SKILL_SANDBOX_ENABLED`**(기본 false). off면 전 스킬 host runner = **현재와 100% 동일**(무회귀).
- 점진: (1) 사이드카 배포 + flag off 스모크, (2) **Phase 1: 오피스 스킬만** container로 dogfood(§5.7), (3) 안정화 후 **Phase 2: 전 실행형 스킬을 사이드카로 이전 + host allowlist 경로 폐기**(격리 by default 완성) — 이땐 사이드카 이미지에 기존 스킬 의존성 인벤토리 추가 필요.
- **롤백**: Phase 1 동안은 flag off로 즉시 host 복귀(사이드카 다운돼도 오피스만 실패, 나머지 무영향). Phase 2 이후엔 사이드카가 필수 경로가 되므로 사이드카 HA/헬스체크 필요.

---

## 13. 리스크 & 완화

| 리스크 | 완화 |
|---|---|
| LibreOffice 콜드스타트/이미지 크기 | 프리웜, 이미지 캐시, timeout 상향(600s), 필요시 워밍된 사이드카 상주. |
| CJK 폰트 누락(박스) | `fonts-noto-cjk`/`fonts-nanum` + `fc-cache` 빌드 단계, 한글 렌더 스모크. |
| 공유 볼륨 stat 변화(inode/ctime) → 스퓨리어스 artifact "updated" | 사이드카가 in-place로 쓰게(복사아웃 지양), diff 허용범위. |
| 크로스-런 격리 약함(B) | per-run HOME/tmp 분리, §11 하드닝, A안 승격. |
| 라이선스 | vendoring 금지, 런타임 설치, 툴체인 별도 바이너리. |
| 러너 리팩터 회귀 | B1(무동작변경 이관) 후 기존 스킬 스모크 그린 게이트. |

---

## 14. 결정 확정 (2026-07-14)

| # | 결정 | 확정 | 비고 |
|---|---|---|---|
| 1 | 컨테이너 런타임 | **공유 사이드카 exec API** | docker socket 불필요, 호스트 분리. per-run 스폰/hosted는 §11 하드닝·A 확장 옵션. |
| 2 | 스킬 페이로드 공급 | **빌트인 시드** | `system_skill_packages/`+마켓 시드. ⚠️ 라이선스는 자체배포 한정·외부공개 전 확보(§5.7). |
| 3 | 격리 대상 | **능력 기반 — 실행형 스킬 전부 격리**(오피스 특화 아님) | 사용자 지적 반영. 텍스트 스킬은 실행無→무관. §5.7. |
| 4 | A안 범위 | **A1(공유볼륨 유지, 계약 최소 재구현) 먼저 → A2 점진** | |
| 5 | 네트워크 | **기본 차단**, npm/pip는 이미지 baked, `requires_network=true` 스킬만 egress | |

**롤아웃 결론**: 아키텍처는 "실행형 스킬 = 격리 by default"로 설계하되, 구현은 **Phase 1 오피스만 사이드카(검증) → Phase 2 전 실행형 스킬 이전 + host allowlist 폐기**로 점진(§12).

---

## 15. 부록 — 핵심 파일 참조 인덱스

| 관심사 | 파일:라인 |
|---|---|
| 실행 seam | `app/agent_runtime/skill_executor.py:153-175` |
| allowlist 정책 | `app/agent_runtime/skill_execution_policy.py:46-160` |
| SkillToolContext/descriptor | `app/marketplace/skill_runtime.py:70-138, 257-303` |
| create_deep_agent 호출 | `app/agent_runtime/runtime_component_builder.py:116-147, 429/288, 431-473, 502-507` |
| execution_profile 소비 | `skill_execution_policy.py:144-160`(timeout/network) |
| redaction | `app/marketplace/redaction.py:110`, `runtime_component_builder.py:210` |
| credential 해석/주입 | `app/marketplace/credential_requirements.py:308`, `skill_runtime.py:306`, `skill_executor.py:81-112` |
| audit | `app/agent_runtime/skill_executor_audit.py`, `app/credentials/service.py:267` |
| HiTL risk/interrupt | `app/tools/risk.py:260-326`, `app/agent_runtime/runtime/interrupts.py:21-59` |
| artifact recorder | `app/services/artifacts/recorder.py:33,70,199`, `conversation_stream_service.py:435` |
| 시드/버전/content-hash | `app/seed/default_marketplace_skills.py:45-155,181-193,459-563` |
| 배포 | `docker-compose.yml`(backend/volumes), `backend/Dockerfile`, `docs/design-docs/adr-018-relative-storage-path.md` |
| deepagents 참조 | `.venv/.../deepagents/backends/{sandbox,langsmith,local_shell,filesystem}.py`, docs.langchain.com/oss/python/deepagents/{sandboxes,backends,skills} |

### execution_profile 스키마(제안, 최종)
```jsonc
{
  "support_level": "node_package|ready_python|...",  // 기존(배지)
  "runners": ["node"|"python"|...],                  // 기존(배지)
  "requires_python": true, "requires_node": true,    // 기존(배지)
  "requires_network": false,                          // 런타임 소비(네트워크 모드)
  "timeout_seconds": 600,                             // 런타임 소비(clamp)
  "runner": "host|container",                         // 신규(런타임 분기)
  "image": "moldy-skill-sandbox",                     // 신규(container)
  "relaxed_commands": true                            // 신규(allowlist 우회)
}
```

### 명령 예시 (오피스, container runner)
```
# docx 생성: 에이전트가 write_file로 create.js 작성 후
execute_in_skill(skill_directory="docx", command="node create.js")
# xlsx 수식 재계산
execute_in_skill(skill_directory="xlsx", command="python scripts/recalc.py out.xlsx")
# pdf 텍스트 추출
execute_in_skill(skill_directory="pdf", command="python scripts/extract_form_structure.py in.pdf")
# pptx 썸네일
execute_in_skill(skill_directory="pptx", command="python scripts/thumbnail.py deck.pptx")
```

---

**끝. 구현은 §6(B) → §8(A) 순서, §14 결정 사항을 먼저 확정할 것.**
