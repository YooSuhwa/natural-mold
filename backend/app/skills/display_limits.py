"""표시-계층 텍스트 파일 계약 상한 — 단일 정본.

드래프트 워크스페이스 어댑터(skill_draft_workspace)와 리비전 스냅샷 뷰어
(skill_revision_service)가 같은 fail-closed 계약(앞 N바이트 널바이트 sniff,
파일당 표시 상한)을 공유한다. 한쪽만 바꾸면 드래프트 뷰어와 리비전 뷰어가
같은 파일을 다르게 판정하므로 반드시 여기서만 조정한다.
"""

# 바이너리 판정용 head sniff 크기.
DISPLAY_TEXT_SNIFF_BYTES = 8192

# 표시 계층이 읽어주는 파일당 최대 바이트 (초과분은 fail-closed).
MAX_DISPLAY_TEXT_BYTES = 2 * 1024 * 1024
