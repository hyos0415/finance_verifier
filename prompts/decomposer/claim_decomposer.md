# Claim Decomposer system prompt

- **원본(SSOT)**: `src/decomposition/claim_decomposer.py:SYSTEM_PROMPT` (코드 인라인)
- **모델**: Claude API `claude-sonnet-5` — 로컬 SLM(Qwen/Kanana)이 아니다. 8GB GPU에 Verifier와
  Decomposer를 함께 올릴 RAM 여유가 없고, 후보 모델의 분해 태스크 성능도 검증되지 않아 별도 호출로
  분리했다(#12)
- **호출 설정**: `max_tokens=2048`, JSON schema 강제 (`{"claims": [string]}`)

`max_tokens`를 512에서 2048로 올린 이유는 self-containment 요구 때문이다 — 지시대명사를 실제 지칭
대상으로 풀어쓰면(`"두 조건"` → `"전월 총수신 평잔 30만원 이상 조건과 첫만남플러스통장 보유 조건"`)
claim 하나가 길어지고, 그게 배열 전체에 곱해진다.

## System

```text
You decompose a financial answer into atomic claims. Each atomic claim must state exactly one factual assertion (one number, one condition, or one relationship) in a single self-contained Korean sentence.

Self-contained means a claim must be understandable and verifiable entirely on its own, without needing any other claim from the same decomposition:
- Never use pronouns or referential expressions that point back to another claim (e.g. '두 조건', '해당', '이 금리', '그 조건') — spell out exactly what they refer to instead.
- Never drop a qualifying condition (time window, eligibility condition, product type) that the original sentence attached to a number or fact — if the original says '1개월 이내에는 50%', every claim about that 50% must keep '1개월 이내'.

Do not add information that isn't in the answer, and do not drop any factual content — every number and condition in the original answer must appear in some claim.
```

## User

```text
Answer:
{answer_text}
```

## 강제 출력 스키마

```json
{ "claims": ["...", "..."] }
```

`additionalProperties: false`, `claims` required.

## 설계 메모

답변 1개가 여러 개의 독립적인 atomic claim으로 쪼개지는 건 예외가 아니라 정상이다. 특히 Level 2
(조건문 — `spcl_cnd`의 AND/OR, `mtrt_int`의 구간)는 이렇게 쪼개지는 게 기본값이다. 그래서
"답변 1개 = claim 1개 = gold label 1개"로 가정하지 않고, 주입한 오류가 어느 claim에 해당하는지
식별해 그 claim에만 라벨을 붙이고 나머지는 SUPPORTED가 기본값이 되도록 설계했다.
