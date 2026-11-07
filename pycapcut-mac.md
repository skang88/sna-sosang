# pycapcut-mac — 스킬로 이동됨

이 파일에 있던 CapCut Mac 드래프트 실측 노트는 **Claude Code 스킬**로 옮겼다.

```
~/.claude/skills/pycapcut-mac/
├── SKILL.md                   즉시 무는 함정 + 작업 흐름 + 검증 원칙
├── references/
│   ├── draft-format.md        draft_info.json 전체 스키마
│   └── korean-asr.md          한국어 ASR 파이프라인 함정
└── scripts/
    └── validate_draft.py      드래프트 불변식 검사기
```

스킬은 CapCut·캡컷·draft_info.json·자동 컷편집·자막 생성·whisper 한국어 인식이
언급되면 자동으로 참조된다. 이 레포의 코드(`agent/draft.py` 등)가 "실측 근거는
pycapcut-mac.md 참조"라고 적은 항목들은 모두 위 스킬 문서에 있다:

| 옛 참조 | 새 위치 |
|---|---|
| 샌드박스, 경로 정규화 | SKILL.md §2, draft-format.md §5 |
| tm_duration, 프레임 정렬 | SKILL.md §3 |
| extra_material_refs 6종 | draft-format.md §4 |
| 자막 스키마, transform_y | draft-format.md §6–7 |
| 배경 check_flag 비트 | SKILL.md §5.4, draft-format.md §6 |
| 고유명사 치환, 2단계 인식 | SKILL.md §6, korean-asr.md |

드래프트를 만든 뒤에는 검사기를 돌린다:

```bash
python3 ~/.claude/skills/pycapcut-mac/scripts/validate_draft.py <드래프트 폴더>
```

> **원칙은 그대로**: 빌드 성공 ≠ 검증. 불변식을 다 통과해도 CapCut에서 재생해 봐야 한다.
