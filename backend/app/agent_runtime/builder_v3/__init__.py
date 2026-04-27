"""Builder v3 — LangGraph StateGraph 기반 8-phase 대화형 빌더.

순서를 그래프 토폴로지로 강제 (LLM이 어길 수 없음).
HiTL: ask_user / approval / image choice (interrupt 기반).
일반 채팅과 동일한 streaming.py / checkpointer 인프라 재사용.
"""
