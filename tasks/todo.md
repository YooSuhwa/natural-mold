# QuestionFlow / OptionList Todo

- [x] Confirm the implementation plan in `docs/superpowers/plans/2026-05-31-question-flow-option-list.md`.
- [x] Keep `ask_user` as the only canonical user-input HiTL tool; do not add separate `question_flow` or `option_list` tools.
- [x] Extend backend `ask_user` args while preserving legacy `question/options`.
- [x] Preserve full native ask_user interrupt args in streaming.
- [x] Add frontend v2 ask_user types and decision mapper serializers.
- [x] Add Moldy-adapted QuestionFlow and OptionList renderers.
- [x] Normalize legacy single-question payloads into the same `UserInputUI` routing surface when `mode` is absent.
- [x] Update Builder phase 2 as the first 3-question `question_flow` adopter.
- [x] Add backend and frontend regression tests.
- [x] Run backend pytest, frontend unit tests, lint, and build.
