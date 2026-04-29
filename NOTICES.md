# Third-party Notices

이 문서는 Moldy(natural-mold) 프로젝트가 외부 오픈소스에서 **알고리즘·패턴을 차용한 영역**의 출처와 라이선스 메모를 기록한다.
**모든 식별자·문자열·자산은 Moldy 도메인에 맞춰 자체화되었으며**, 외부 프로젝트의 라이선스 헤더 텍스트나 브랜드 자산을 포함하지 않는다.

본 파일은 `scripts/check_branding.py`의 화이트리스트 — 출처 명기 목적의 단일 예외.

---

## n8n (https://github.com/n8n-io/n8n)

n8n은 Sustainable Use License와 Apache 2.0의 듀얼 라이선스 구조를 가진다. Moldy는 다음 알고리즘·패턴을 **알고리즘만** 차용했으며, 식별자/문자열/자산/EE 모듈은 사용하지 않는다.

### 차용 영역

| Moldy 파일 | 출처 모듈 | 라이선스 |
|---|---|---|
| `backend/app/security/cipher.py` | `packages/core/src/encryption/{cipher,aes-256-gcm}.ts` | Apache-2.0 |
| `backend/app/security/key_provider.py` | `packages/core/src/encryption/encryption-key-proxy.ts` (인터페이스 패턴) | Apache-2.0 |
| `backend/app/credentials/domain.py` (CredentialDefinition) | `packages/workflow/src/interfaces.ts` (ICredentialType) | Apache-2.0 |
| `backend/app/credentials/field.py` (FieldDef) | `packages/workflow/src/interfaces.ts` (INodeProperties) | Apache-2.0 |
| `backend/app/credentials/authenticate.py` (GenericAuth) | `packages/cli/src/credentials-helper.ts` (IAuthenticateGeneric) | Sustainable Use License |
| `backend/app/credentials/oauth2_base.py` (preAuthentication 패턴) | `packages/cli/src/controllers/oauth/oauth2-credential.controller.ts` | Sustainable Use License |
| `backend/app/credentials/external_secrets/base.py` (SecretsProvider ABC 시그니처) | `packages/cli/src/modules/external-secrets.ee/types.ts` (구조만 참조, EE 코드 미사용) | Sustainable Use License |
| `backend/app/credentials/interpolation.py` | n8n 표현식 엔진의 `={{ $credentials.X }}` 형식만 차용 (전체 엔진은 미차용) | Apache-2.0 |
| `frontend/src/components/shared/dynamic-fields-form.tsx` (정보 구조) | `packages/frontend/editor-ui/src/features/ndv/parameters/components/ParameterInput.vue` (UI 패턴만, Vue 코드 미사용) | Sustainable Use License |
| `frontend/src/components/credential/credential-create-modal.tsx` (Step UX) | `packages/frontend/editor-ui/src/features/credentials/components/CredentialEdit/CredentialEdit.vue` (단계 구성만) | Sustainable Use License |

### 차용 원칙 (이 프로젝트의 자체 규칙)

1. 알고리즘 본체와 인터페이스 시그니처만 차용 — 코드 한 줄도 직접 복사하지 않는다.
2. 모든 식별자(클래스명, 함수명, 변수명, 라우트, 라벨)를 Moldy 도메인에 맞춰 새로 명명한다.
3. Cipher의 HKDF info string은 `b'moldy-encryption-v1'` (n8n의 `'n8n-encryption-v1'`과 의도적으로 다름).
4. n8n 라이선스 헤더 텍스트는 어떤 파일에도 포함하지 않는다.
5. n8n 로고/아이콘/스크린샷 자산은 어떤 형태로도 포함하지 않는다.
6. `@n8n/*` npm 패키지를 import하지 않는다 — 동등한 generic 라이브러리(`cryptography`, `httpx`, `authlib`, `hvac` 등) 사용.
7. CI 게이트(`scripts/check_branding.py`)가 위 규칙을 자동 검증한다.

### 라이선스 적합성

- Apache-2.0 영역: 자유 차용 가능. 출처 명기 의무 — 이 문서가 그 의무 충족.
- Sustainable Use License 영역: "n8n과 경쟁하는 호스티드 워크플로우 자동화 SaaS"가 아닌 한 사용 가능. Moldy는 AI 에이전트 빌더이며 n8n과 직접 경쟁하지 않는다 (PoC + 사내 활용).
- 머지 전 변호사 1회 검토 권장 (외부 배포 시).

---

## 사용 라이브러리

다음 라이브러리는 표준 의존성으로, 자체 라이선스를 따른다.

- `cryptography` (BSD/Apache-2.0) — Cipher V2 구현
- `hvac` (Apache-2.0) — Vault SDK
- `authlib` 또는 `httpx-auth` — OAuth2 클라이언트
- `langchain`, `langgraph`, `langchain-mcp-adapters` (MIT) — AI 런타임
- `lucide-react` (ISC) — 프론트엔드 아이콘
- `shadcn/ui` (MIT) — 프론트엔드 컴포넌트

각 의존성은 `backend/pyproject.toml`, `frontend/package.json`에 명시.
