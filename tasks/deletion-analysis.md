# 삭제 분석 보고서 — Frontend (Musk Step 2)

> 분석일: 2026-04-07
> 분석 대상: /Users/chester/dev/natural-mold/frontend/
> 분석자: bezos (QA Engineer)

---

## 즉시 삭제 가능

### 미사용 의존성
- **@tanstack/react-query-devtools**: 코드 어디에서도 import되지 않음. 개발 디버깅용이지만 현재 활성화되어 있지 않아 불필요

### 미사용 export (죽은 코드)
- **lib/api/usage.ts:`agentUsage`**: export되지만 어디에서도 import되지 않음. `summary` 메서드만 사용 중
- **lib/hooks/use-skills.ts:`useSkill`**: 개별 스킬 조회 hook이지만 미사용. `useSkills()`만 사용 중
- **lib/hooks/use-models.ts:`useCreateModel`**: 단일 모델 생성 hook이지만 미사용. `useBulkCreateModels`만 사용 중

---

## 삭제 검토 필요 (사티아 확인 필요)

### 미사용 export (향후 사용 가능성)
- **lib/api/client.ts:`ApiError` export**: 직접 참조 2곳(skills.ts)뿐, 나머지는 `apiFetch` 내부 throw만. export 제거 가능하나 외부 에러 핸들링 확장 시 필요할 수 있음 — 리스크 낮음

---

## 단순화 제안

### 1. Provider 유틸리티 switch 문 중복
- **현재**: `lib/utils/provider.ts`에서 `getProviderIcon()`과 `getProviderLabel()`이 동일한 provider type에 대해 별도 switch 문 반복
- **제안**: 단일 `PROVIDER_CONFIG` 객체로 통합 (`{ openai: { icon: 'OAI', label: 'OpenAI' }, ... }`)

### 2. CRUD Hook 보일러플레이트
- **현재**: `lib/hooks/` 내 7개 파일(use-agents, use-tools, use-models, use-providers, use-skills, use-conversations, use-triggers)이 동일한 useQuery/useMutation 패턴 반복
- **제안**: 현재 PoC 단계에서는 명시적 반복이 유지보수에 오히려 유리. 패턴이 안정화된 후 제네릭 factory 도입 검토
- **판단**: 삭제/단순화 보류 (premature abstraction 회피)

### 3. API Client 보일러플레이트
- **현재**: `lib/api/` 내 7개 파일이 동일한 `apiFetch` 기반 CRUD 구조 반복
- **제안**: CRUD hook과 동일 판단 — PoC에서는 명시적 반복 유지
- **판단**: 삭제/단순화 보류

---

## 요약

| 카테고리 | 항목 수 | 영향도 |
|----------|---------|--------|
| 즉시 삭제 (의존성) | 1 | 낮음 (번들 크기 미미) |
| 즉시 삭제 (죽은 코드) | 3 | 낮음 (미사용 export) |
| 삭제 검토 | 1 | 매우 낮음 |
| 단순화 제안 | 1 (provider utils) | 낮음 |
| 단순화 보류 | 2 (CRUD 패턴) | 보류 — PoC 단계 |

**총평**: 프론트엔드 코드베이스가 비교적 깨끗함. 미사용 컴포넌트 파일 0건, 미사용 의존성 1건. 주요 개선 여지는 CRUD 보일러플레이트이나 PoC 단계에서 premature abstraction은 지양.
