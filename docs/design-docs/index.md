# Architecture Decision Records (ADR) Index

> This index lists every tracked ADR file. Titles and statuses reproduce the
> source document's heading or explicit status declaration; where a document
> declares neither, that absence is recorded instead of inferred.

| ADR | Title | Status |
|-----|-------|--------|
| ADR-001 | [Deep Agent 엔진 교체](adr-001-deep-agent-engine.md) | 제안됨 |
| ADR-002 | [Checkpointer 기반 대화 관리](adr-002-checkpointer.md) | 승인됨 |
| ADR-003 | [스킬 + 메모리 전환 설계](adr-003-skills-memory.md) | 승인됨 |
| ADR-004 | [M4 정리 — Creation Agent + Trigger + Streaming](adr-004-m4-cleanup.md) | 승인됨 |
| ADR-005 | [Builder/Assistant 아키텍처](adr-005-builder-assistant.md) | 제안됨 |
| ADR-006 | [assistant-ui ExternalStoreRuntime 어댑터](adr-006-assistant-ui-runtime.md) | 승인됨, 2026-06-13 LangGraph v3 확장 승인 |
| ADR-007 | [Credentials `field_keys` 비암호화 캐시 컬럼](adr-007-credentials-field-keys-cache.md) | 승인됨 |
| ADR-008 | [Connection 엔티티 — Credential 바인딩 통합](adr-008-connection-entity.md) | 제안됨 |
| ADR-009 | [Credential / Tools / Skills 그린필드 리라이트](adr-009-greenfield-credentials.md) | Accepted |
| ADR-010 | [Sprint 1 / Story S2 — 디자인 토큰 oklch 픽스 + DialogShell 비주얼 스펙 (팀쿡)](ADR-010-ui-tokens-and-dialog-shell.md) | 명시적 상태 없음 |
| ADR-011 | [SSE Stream Resume (W3-out)](adr-011-sse-stream-resume.md) | M1-M6 구현 완료 (M6 PR 머지 대기) |
| ADR-012 | [HiTL — 자체 구현에서 LangChain `HumanInTheLoopMiddleware` 로 마이그레이션](adr-012-hitl-middleware-migration.md) | Phase 1~4 완료, Phase 5 진행 중 (Builder v3 wire 통일) |
| ADR-013 | [Service-side LLM Key from Credentials (Builder/Assistant Sub-agent)](adr-013-service-llm-key-from-credentials.md) | 승인됨 (2026-05-06) |
| ADR-014 | [Chat Model Factory — Provider Quirks 분리 (Strategy 패턴 도입)](adr-014-chat-model-factory-strategy.md) | 승인됨 (2026-05-08) |
| ADR-016 | [멀티유저 인증 도입 (HttpOnly Cookie + JWT + super_user)](adr-016-multiuser-auth.md) | Accepted |
| ADR-017 | [Marketplace Resources (Skill / MCP / Agent 공유 레이어, Phase 1: Skill)](adr-017-marketplace-resources.md) | Proposed |
| ADR-018 | [Relative `storage_path` for Skills & Marketplace Versions](adr-018-relative-storage-path.md) | Proposed |
| ADR-019 | [System LLM Settings (역할별 모델 선택 + base_url 주입)](adr-019-system-llm-settings.md) | 제안됨 (2026-05-26) |
| ADR-020 | [Chat Run AG-UI Adapter](adr-020-chat-run-ag-ui-adapter.md) | Accepted |
| ADR-021 | [Value-Based Trace Redaction (값 기반 트레이스 시크릿 마스킹)](adr-021-value-based-trace-redaction.md) | Proposed (제안됨, 2026-06-24) |
