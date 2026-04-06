# CHECKPOINT — 채팅 UI 개선

## M1: P0 수정 — 도구 결과 파싱 + 이미지 크기 제한
- [x] 도구 결과 raw JSON/Python dict 파싱 (text 필드 추출)
- [x] 제어 문자(\xa0, \n 반복 등) 정규화
- [x] 도구 결과를 마크다운으로 렌더링
- [x] 이미지 max-w-md + rounded-lg + 클릭 확대(Dialog)
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공
- 상태: done

## M2: 마크다운 강화
- [x] 코드 블록 문법 강조 (react-syntax-highlighter)
- [x] GFM 테이블 (remark-gfm)
- [x] LaTeX 수식 (remark-math + rehype-katex)
- [x] markdown-styles.css
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공
- 상태: done

## M3: 입력 영역 리디자인 + 토큰 표시
- [ ] 둥근 입력 박스 + 모델명 바 + 파일 첨부 버튼(UI) + 전송 버튼 스타일
- [ ] 토큰/비용 메시지별 표시 + 입력 영역 누적 표시
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공
- 상태: pending

## M4: 전체 디자인 다듬기
- [ ] 봇 아이콘, 메시지 간격, 복사 버튼, 도구 카드, 스트리밍 애니메이션
- [ ] 채팅 영역 max-width + 그라데이션 페이드
- [ ] 빈 채팅 초기 화면
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공
- 상태: pending

## M5: 통합 검증
- [x] 전체 빌드 + 백엔드 테스트 (pnpm build PASS, pytest 332 passed)
- [x] agent-browser E2E 시각 검증
- 검증: `cd frontend && pnpm build && cd ../backend && uv run pytest`
- done-when: 빌드+테스트 통과, 시각적 확인 완료
- 상태: done
