"""Builder v3 그래프 노드.

각 phase 노드는 ``async def phase_X(state: BuilderState) -> dict | Command``
시그니처를 가지며, dict 반환 시 state에 머지, Command 반환 시 분기/self-loop.
"""
