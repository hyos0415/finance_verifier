# prompts/

이 디렉토리는 프로젝트에서 실제로 쓴 프롬프트 전문을 **읽기용으로 내려받아 둔 스냅샷**이다.

## 원본(SSOT)은 어디에 있나

| 프롬프트 | 실행 시 실제로 읽는 곳 |
|---|---|
| Verifier system prompt | **Langfuse Prompt Management** (`verifier-system-prompt`, `production` 라벨). 코드의 `src/verifier/client.py:SYSTEM_PROMPT`는 Langfuse에 프롬프트가 아직 없을 때 최초 시딩/폴백으로만 쓰인다 |
| Claim Decomposer system prompt | `src/decomposition/claim_decomposer.py:SYSTEM_PROMPT` (코드 인라인) |
| Synthetic answer generator system prompt | `src/decomposition/generate_synthetic_answers.py:SYSTEM_PROMPT` (코드 인라인) |

Verifier 프롬프트는 **버전 관리를 Langfuse로 했다.** `get_or_create_prompt()`가 실행 시점의
`production` 버전을 끌어오고, 그 버전 번호가 Langfuse generation observation에 자동으로 링크되어
`prompt_version` 메타데이터로 남는다. 그래서 repo 안에서 프롬프트 버전을 수동으로 관리하는 파일은
없고, eval 결과 파일명(`{split}_{model}_prompt-v{N}.json`)의 `N`이 Langfuse 버전 번호와 그대로
일치한다.

이 디렉토리의 `.md`는 그 스냅샷을 파이썬 파일을 열지 않고도 읽을 수 있게 뽑아둔 것이다 —
프로젝트가 종료(freeze)된 상태라 원본과 어긋날 여지는 없지만, 값을 고칠 때는 항상 위 표의
원본을 고쳐야 한다.

## Verifier 프롬프트 버전 이력

Langfuse는 버전을 1부터 매긴다. 실험 과정에서 **v3·v5를 시도했지만 둘 다 기각**하고 v2 내용으로
되돌렸으므로, 최종 production(version 6)의 내용은 version 2와 동일하다.

| Langfuse version | 내용 | 판정 |
|---|---|---|
| 1 | 판정 기준만. `reason` 길이 제약 없음 | Qwen의 `reason`이 512토큰에서도 안 끝나 스키마가 깨짐 → 개정 |
| 2 | `reason` 1문장·100자 이내 제약 추가 | **채택** |
| 3 | v2 + "판정 순서" 규칙 + worked example 1개 | 기각 — INSUFFICIENT 4건은 고쳤지만 원래 맞히던 UNSUPPORTED가 흔들림(Kanana Macro F1 0.7652→0.5714, Qwen FAR 0.111→0.1667) |
| 4 | v2 내용으로 롤백 | — |
| 5 | v2 + "INSUFFICIENT를 회피 수단으로 쓰지 마라" 부정 규칙 1문단 | 기각 — Qwen INSUFFICIENT 정확도 1/4→0/4, Macro F1 0.6656→0.5464 |
| 6 | v2 내용으로 롤백 = **production, Test 단계까지 고정** | **최종** |

기각된 두 변형의 실제 문구는 [`verifier/rejected_variants.md`](verifier/rejected_variants.md),
판정 근거의 상세 분석은 [`../results/eval/smoke_eval_review.md`](../results/eval/smoke_eval_review.md)에 있다.

**결론**: 접근이 서로 반대인 두 시도(v3=절차+예시 추가, v5=짧은 부정 규칙만 추가)가 모두 실패했으므로,
Qwen의 INSUFFICIENT↔UNSUPPORTED 경계 오분류는 프롬프트 엔지니어링으로 해결되는 문제가 아니라고
결론지었다.
