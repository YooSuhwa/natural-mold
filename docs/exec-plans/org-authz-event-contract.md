# A13 원문·스키마·이벤트 계약

이 문서는 보존된 [원문 기획](org-authz-plan.html)의 6.1·6.2·6.3·7.2·7.2.1을
마이그레이션 초안과 대조한 A13 계약이다. 이벤트 서비스, 아웃박스 작업기,
OpenFGA 반영이나 요청 단계의 차단을 구현했다는 뜻이 아니다. 실제 이벤트를
실행한 뒤 기대 튜플과 OpenFGA Read를 비교하는 C-04 완료 조건은 후속 작업이다.

계약 원본은 다음 파일이다.

- `backend/schema_drafts/org_authz/event_contract.json`: 원문 19행의 5개 셀 전체,
  변경 테이블·열, 튜플 쓰기·삭제의 원본 열, 회수 범위·대상·순서와 결정 번호.
- `backend/schema_drafts/org_authz/schema_contract.json`: 22개 신규 테이블과
  6.2 변경 대상의 실제 m89 반영 후 열 타입·NULL·DB 기본값·PK·FK 이름/동작·
  CHECK·유일 제약·인덱스/부분 조건. 기존 열도 포함한 56개 테이블의 고정 계약.
- `event_contract.py`, `schema_contract.py`, `plan_source.py`: typed JSON 경계,
  실제 반영 스키마의 정규화, 원문 HTML 셀 추출. 애플리케이션 모델·런타임과 분리.

`type`은 SQLite에서 실제 반영 후 읽은 저장 타입이다(UUID는 `CHAR(32)`).
PostgreSQL 타입 계약은 초안의 `Uuid`, `DateTime(timezone=True)`, `Numeric` 등
선언과 별도 PostgreSQL 마이그레이션 검증이 담당한다. 이 파일을 PostgreSQL
반영 결과와 문자열 비교하여 방언 차이를 오류로 취급하지 않는다.

## 원문 충돌에 대한 A13 결정

다음은 원문을 몰래 고친 결과가 아니라 ADR-023에 연결하는 명시적 결정이다.
원문 literal 의미와 차이 0이라는 주장은 하지 않는다. 원문 셀 보존·19개 이벤트
누락 여부·결정 후 스키마 열 지원 여부는 각각 검사한다.

| ID | 원문 충돌 또는 생략 | 선택한 계약과 후속 실행 조건 |
|---|---|---|
| R1 | 7.2는 보관 에이전트의 튜플 없음, 7.2.1은 소유자·조직·거주 튜플 유지 | 상세 이벤트 규칙 7.2.1을 따른다. `owner`, `org`, `resident_agent`는 복원·관리용으로 유지한다. 공유/위임 관계는 대상 객체와 `agent:A` 주체 양쪽에서 회수한다. 실행·공유·트리거·배포는 `archived_at` 상태 검사로 차단한다. reconcile도 기본 튜플을 유지해야 한다. C-02/C-04/E-08이 이를 검증한다. |
| R2 | 그룹 삭제는 역할·기능 권한도 revoking이라 표현하지만 해당 테이블에는 state 열 없음 | `resource_grants`만 active→revoking→revoked로 전환한다. 역할·기능 권한·그룹 구성원은 삭제 전에 정확한 기존 튜플을 캡처하고, 표식·삭제 아웃박스와 원본 변경을 같은 트랜잭션으로 기록한다. 그룹 대상 역할/기능 권한/구성원을 정리한 후 그룹을 삭제한다. 해당 조직의 모든 기존 그룹 구성원에게 표식을 남긴다. |
| R3 | tenant 역할 회수도 role 표식인데 표식에는 필수 org_id 있음 | 회사 역할 회수는 회사의 각 조직으로 role 표식을 팬아웃하고 각 authz_epoch를 올린다. 회사 역할 원본 삭제와 모든 표식/아웃박스를 같은 트랜잭션에 기록한다. 미래에 생성되는 조직은 이미 삭제된 회사 역할을 투영하지 않는다. 별도 tenant 역할 표식 테이블은 추가하지 않는다. |
| R4 | 6.2 소유자 FK 문장은 모두 user_id라 표현, 7.2는 marketplace의 owner_user_id 지정 | marketplace는 `owner_user_id`, 나머지 5개 공유 자원은 `user_id`를 원본으로 사용한다. 모든 소유자 FK는 RESTRICT. 시스템 행은 org/owner 없이 타입별 팬아웃만 하며, 자원 생성의 owner/org는 비시스템 행에 적용한다. |
| R5 | 이벤트는 모든 시스템 카탈로그에 allowed_org/consumer라고 축약, 7.2와 모델은 타입별 관계가 다름 | tool/skill/mcp_server는 allowed_org+consumer, llm_model은 consumer, 시스템 marketplace_item은 viewer를 쓴다. org 설정은 표시 정책, `grant_kind=system_fanout` 행이 실제 튜플 원본이다. 정책 끄기는 그 조직/자원 범위만 회수한다. |
| R6 | 회사 정지·조직 보관은 tuple/marker 없음 | 튜플을 유지하고 DB status 요청 가드로 차단한다. 조직 보관은 진행 중 실행을 완료시키고 새 실행을 막는 원문 정책을 따른다. 에이전트 보관 R1과 구별한다. |
| R7 | 8.6은 삭제마다 표식, 회사 영구 삭제 이벤트는 표식 '-' | 회사 영구 삭제는 I-07의 회사 삭제 가드를 먼저 닫아 두는 예외다. 삭제할 회사/조직/자원의 객체 튜플과 agent/userset 주체 튜플을 원본 삭제 전에 캡처한다. 하위 데이터 정리와 삭제 아웃박스를 기록하고 삭제 재시도를 끝낸 후 최종 회사 행을 제거한다. 회사 가드는 전체 정리 동안 닫힌 상태를 유지한다. 개별 자원 표식의 FK가 연쇄 삭제되어 차단을 잃는 구현은 금지한다. |
| R8 | 7.2는 모든 만료 grant에 non_expired_grant, 현재 canonical model은 agent.consumer의 user만 이 조건을 허용. MCP의 agent.consumer는 allowed_run_source만 허용 | DB의 조건 원본 저장 지원과 OpenFGA에 쓸 수 있는 모델 계약은 별개다. C-02/model 변경 PR에서 만료를 허용할 관계·주체 타입을 명시하고, MCP의 만료+run_source를 함께 판정하는 단일 조건을 도입해야 한다. 그 전에는 이 교차표를 실행 가능한 FGA payload라 주장하거나 두 조건을 하나의 튜플에 임의로 붙이지 않는다. 실제 FGA Write, 만료 경계, cross-source 거부를 테스트한다. 원본 canonical model은 이번 작업에서 보존한다. |

회사 공용 자격증명도 `allowed_org`와 `consumer` 두 관계를 허용 조직마다
`resource_grants`에 기록한다. 만료/조건 튜플은 삭제 시 원본의 조건까지 동일하게
캡처한다. `non_expired_grant`의 grant_time/grant_duration, MCP 위임의
`allowed_run_source`는 저장된 조건 원본으로부터 계산하며 이후 C-02가 정확한
조건 payload를 검증한다.

위 수식은 후속 projection의 요구사항이다. R8의 모델 선행 작업이 끝나기 전에는
모든 관계의 조건 튜플이 현재 OpenFGA 모델에 이미 호환된다는 뜻이 아니다.

## 19개 이벤트 교차표

각 행의 정확한 열 목록과 튜플 수식은 `event_contract.json`에 있다. 아래 표는
그 계약을 사람이 검토할 수 있도록 핵심 변경과 표식 범위를 보여 준다.

| 원문 이벤트 | DB/튜플 원본 | 쓰기 / 삭제 | 회수 표식 |
|---|---|---|---|
| 조직 생성 | organizations, organization_members, organization_role_grants, resource_grants | tenant, 생성자 member/admin, 타입별 시스템 팬아웃 / 없음 | 없음 |
| 구성원 추가(초대 수락) | organization_members, tenant_members, org_invitations | 조직 member, 필요 시 회사 member / 없음 | 없음 |
| 구성원 제거 | organization_members, group_members, organization_role_grants, org_capability_grants, resource_grants | 없음 / 조직 member·역할·기능·그룹 member·직접 grant | membership |
| 역할 부여·회수 | organization_role_grants, tenant_role_grants | 역할 / 동일 이전 역할 | 회수 시 role, 회사 역할은 R3 |
| 그룹 구성원 추가·제거 | groups, group_members | user member group / 동일 이전 튜플 | 제거 시 group_member |
| 그룹 삭제 | groups, group_members, organization_role_grants, org_capability_grants, resource_grants | 없음 / group#member 주체, 그룹 org, 그룹 구성원 | 모든 구성원의 group_member, R2 |
| 기능 권한 부여·회수 | org_capability_grants | cap_* / 동일 이전 튜플 | 회수 시 capability |
| 자원 생성 | agents, skills, tools, mcp_servers, credentials, marketplace_items | 비시스템 org/owner, agent resident_agent, marketplace tenant / 없음 | 없음, R4 |
| 공유 추가 | resource_grants | 조건 포함 주체 관계 / 없음 | 없음 |
| 공유 회수·만료 | resource_grants.state/조건/주체/객체 | 없음 / 동일 이전 조건 튜플 | grant |
| 위임 연결·해제 | resource_grants, agent_tools, agent_mcp_tools, agent_skills, agent_subagents, agents.llm_credential_delegated_by | agent consumer(서버 조건 필수) / 해제 시 동일 튜플 | 해제 시 grant |
| 소유권 이전 | 자원 소유자 열, resource_grants | 새 owner, 선택한 이전 owner manager / 이전 owner | ownership, R4 |
| 에이전트 보관 | agents.archived_at/archived_by, agent_triggers.status, agent_deployments.status, resource_grants | 없음 / 공유·위임, 기본 튜플 유지 | resource, R1 |
| 자원 삭제 | 공유 자원, mcp_tools, resource_grants | 없음 / 객체 전체, agent 주체 전체 | resource |
| 카탈로그 항목 켜기·끄기 | organizations.settings, models.is_visible/tenant_id, resource_grants | 타입별 팬아웃 / 끈 조직의 동일 튜플 | 조직 범위 resource, R5 |
| 회사 공용 자격증명 허용 조직 변경 | credentials.scope/tenant_id, resource_grants | 추가된 조직 관계 / 제거된 조직 관계 | 제거된 조직마다 grant |
| 마켓플레이스 게시·회사 공개 | marketplace_items, marketplace_publication_links, resource_grants | org/tenant/owner, org·tenant viewer/installer / 이전 공개 관계 | 비공개 전환 시 grant |
| 회사 정지, 조직 보관 | tenants.status, organizations.status | 없음 / 없음, 요청 status 차단 | 없음, R6 |
| 회사 영구 삭제 | tenants, organizations, 공유 자원, resource_grants와 회사 하위 데이터 | 없음 / 회사·조직·자원 객체와 관련 주체 전체 | 회사 삭제 가드 예외 R7 |

모든 쓰기는 원본+아웃박스 커밋 → FGA 쓰기 순서다. 표식이 있는 모든 삭제는
표식+원본+아웃박스 커밋 → FGA 삭제 → 표식 종결 순서이며 해당 조직의 epoch를
올린다. 삭제 실패 중 표식은 종결하지 않는다. 역할·구성원처럼 원본이 이미
삭제된 경우에도 저장된 아웃박스 튜플로 재시도하므로 reconcile이 다시 쓰지 않는다.
FGA 중복 쓰기·없는 관계 삭제는 원문대로 IGNORE를 사용한다.

## 6.2 최종 inventory 해석

- `org_id`가 추가되는 기존 테이블은 `scope_columns.M81_SCOPED` 20개와
  `M89_SCOPED` 6개다. 모두 tenant_id와 복합 organization FK를 갖는다.
  audit는 회사 이벤트에 org NULL, 플랫폼 이벤트에 tenant NULL을 허용한다.
  시스템 자원은 org NULL을 허용하며 비시스템 자원은 CHECK로 org/tenant를 요구한다.
- `share_links`가 실제 대화 공유 테이블이다. 6.2의 `conversation_shares`는
  8.9에 쓰인 기능 이름이며 새 물리 테이블을 만들지 않는다. share_links에
  org/tenant를 넣고 복합 FK로 강제한다.
- 3단 설정은 credentials.scope/tenant_id, models.tenant_id,
  system_llm_settings.tenant_id와 플랫폼/회사 부분 유일 인덱스다. retention,
  marketplace_tenant_publish, tenants.limits는 JSON 내부 설정 계약이며 개별 SQL
  열로 추가하지 않는다. 정적 JSON 구조 검증은 후속 정책 서비스가 담당한다.
- 4개 에이전트 연결은 required/delegated_by/link_status/broken_reason, 에이전트는
  llm_credential_delegated_by, tools/MCP는 credential_mode/credential_definition_key,
  credentials는 allowed_hosts를 갖는다. 기존 FK source_link는 polymorphic JSON이다.
- conversations는 독립 user_id NOT NULL, nullable agent_id SET NULL,
  agent_name_snapshot/last_activity_at NOT NULL을 갖는다. ORM delete-orphan 제거는
  P1/E-08 애플리케이션 변경이며 A13 DDL만으로 구현됐다고 주장하지 않는다.
- 공유 자원 소유자 FK RESTRICT, 조직 slug/credential_defaults 유일 키, spend의
  (org_id,date,axis_id), token caller, trigger reason CHECK, 감사/소유/보존 인덱스는
  실제 migration 반영 inventory에 포함한다. runtime_name 전역 유일과
  mcp_tools(server_id,name)은 원문대로 유지한다.
- health_check_history 조회 권한과 spend_writer UPSERT 키, 설정 값 범위, 대화
  보존/정리 및 archive 실행 차단은 runtime 후속 작업이다. 스키마 계약의 지원과
  기능 구현을 구별한다.
- m89는 22개 신규 테이블을 포함한 활성 85개(63+22)다. m90/m91 후에는 ACL과
  shadow 테이블 2개가 삭제되어 83개다. 실 m77 PostgreSQL의 역사 테이블 5개는
  별도로 보존한다. m90의 is_shared/is_favorite 삭제는 후속 gated cleanup이다.

검증은 `tests/authz/test_event_source_contract.py`와
`tests/authz/test_schema_source_contract.py`를 실행한다. 원문 모든 셀·효과·표식,
반영 후 실제 필드 inventory, 모든 이벤트 원본 열, 잘못된 nullable 변경과
누락 테이블의 검출, disposable 최종 cleanup 후 이벤트 원본 보존을 검사한다.
계약 수정 시 JSON을 자동 덮어써 통과시키지 말고 원문·ADR·DDL의 변경 이유와
새 반영 결과를 함께 검토해야 한다.
