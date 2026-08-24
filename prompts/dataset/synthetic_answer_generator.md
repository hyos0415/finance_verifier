# Synthetic answer generator system prompt

- **원본(SSOT)**: `src/decomposition/generate_synthetic_answers.py:SYSTEM_PROMPT` (코드 인라인)
- **모델**: Claude API `claude-sonnet-5`
- **호출 설정**: `max_tokens=512`, JSON schema 강제 (`{"answer_text": string}`)
- **용도**: Verifier 평가용 데이터셋 구축. 대형 LLM이 특정 예금상품 질문에 답한 것처럼 답변을
  합성하되, 시나리오가 지정한 오류를 **의도적으로** 한 개 주입한다

## System

```text
You are simulating a large financial LLM answering a bank customer's question about a specific deposit product, based only on the evidence given. Write your answer in natural Korean, as a real answer would sound — not a list, not a quotation of the evidence. Follow the instruction exactly, even if it means stating something incorrect: this is for building a test dataset.
```

## User

시나리오별 `instruction`과 해당 상품의 evidence를 합쳐 전달한다.

## 강제 출력 스키마

```json
{ "answer_text": "..." }
```

## 시나리오 설계 원칙

- 시나리오 하나에 **틀린 사실 하나만** 주입한다(순수 SUPPORTED 케이스는 0개).
- 복합 답변(조건문, 다구간 `mtrt_int`)은 여러 atomic claim으로 쪼개지므로, 주입한 오류는 그중
  1~2개에만 존재하고 나머지는 evidence를 충실히 반영한 SUPPORTED가 된다.
- `error_match`(대체 키워드 목록 — 하나라도 매칭되면 해당)로 주입 오류를 가진 claim을 식별하고,
  그 외에는 SUPPORTED를 기본값으로 둔다. 모델이 오류를 claim마다 다르게 바꿔 말할 수 있어
  표현을 여러 개 나열한다.
- Failure taxonomy: `numeric_error`, `term_error`, `eligibility_error`, `condition_reversal`,
  `condition_omission`, `base_vs_max_rate`, `conditional_benefit_generalization`,
  `missing_information`
