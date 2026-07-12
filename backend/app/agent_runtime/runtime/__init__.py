"""Runtime component clusters — ``runtime_component_builder`` 분리 패키지 (BE-S10).

모듈 구성:

* ``models`` — 모델 후보/폴백/재시도 판정
* ``reliability`` — 빈 응답 재시도 미들웨어 + 기본 신뢰성 미들웨어 조립
* ``interrupts`` — HiTL ``interrupt_on`` 정책 조립
* ``prompts`` — 시스템 프롬프트 블록 빌더
* ``memory_context`` — 장기 기억 프롬프트/회상 브리프/쓰기 정책

호환성: 기존 심볼은 ``app.agent_runtime.runtime_component_builder`` 가 계속
재-export 하며, 테스트 monkeypatch 계약도 그 모듈 경로 기준으로 유지된다.
"""
