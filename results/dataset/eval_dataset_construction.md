# 평가 데이터셋 설계·구축 (Evaluation Dataset Design)

이 프로젝트에서 가장 손이 많이 간 작업은 모델을 돌리는 쪽이 아니라 **모델을 무엇으로 판별할지
정하는 쪽**이었다. 이 문서는 Verifier를 평가하기 위한 데이터셋을 어떻게 설계하고 만들었는지,
그 과정에서 무엇을 검수하고 무엇을 못 잡았는지 정리한다.

한 줄로 요약하면: **금융상품 공시를 근거(evidence)로 삼아, 실제로 일어날 법한 오류 유형을
분류 체계로 정의하고, 그 오류를 의도적으로 주입한 답변을 만들어 atomic claim 단위로 3분류
정답(gold label)을 붙인 도메인 특화 평가셋**이다. 모델 선정용 Pilot 64건과, 선정에 쓰지 않은
held-out Test 53건을 분리해 회귀 평가가 가능한 형태로 남겼다.

이건 데이터 전처리가 아니라 벤치마크 구축에 가깝다. 전처리는 이미 있는 데이터를 쓸 수 있게
다듬는 일이지만, 여기서는 **판별 대상(오류 유형)과 정답 기준(3분류 경계)을 먼저 정의하고 그에
맞는 데이터를 만들어냈다.**

## 1. 왜 직접 만들었나

기성 사실검증(fact verification) 벤치마크를 그대로 쓸 수 없는 이유가 세 가지였다.

1. **도메인이 다르다.** 한국어 금융상품 공시의 우대조건은 `①②③` 나열, "~중 1가지 이상",
   "단, ~ 제외", "만기 후 N개월 이내" 같은 조건문 덩어리다. 일반 위키 기반 벤치마크와 문장
   구조도, 필요한 추론도 다르다.
2. **3분류 경계가 이 프로젝트의 핵심이다.** SUPPORTED / UNSUPPORTED / **INSUFFICIENT**를
   구분하는 게 목표인데, 대부분의 공개 데이터셋은 "정보 부재"와 "명시적 충돌"을 구분하지
   않거나 그 비율이 극히 적다.
3. **실패 유형별로 봐야 한다.** "정확도 몇 %"가 아니라 *어떤 종류의 오류를 못 잡는가*를 알아야
   Verifier로 쓸지 판단할 수 있다. 그러려면 오류 유형이 메타데이터로 붙어 있어야 한다.

## 2. 구축 파이프라인

```
금융상품 공시 (Finlife API, 은행권 정기예금)
   │
   ├─ ① canonical record 정규화 ......... 상품 단위 레코드 + 조건 텍스트 필드
   │
   ├─ ② evidence 선택 ................... (product_id, source_field) 쌍으로 근거 확정
   │
   ├─ ③ 오류 시나리오 설계 .............. 오류 유형 + 주입 지시문 + reasoning_type
   │
   ├─ ④ 답변 합성 (Claude API) .......... 지시문대로 "그럴듯하게 틀린" 답변 생성
   │
   ├─ ⑤ Claim Decomposition (Claude API)  답변 → 독립적으로 검증 가능한 atomic claim들
   │
   ├─ ⑥ Gold label 부여 ................. 오류가 실린 claim만 UNSUPPORTED/INSUFFICIENT,
   │                                       나머지는 SUPPORTED가 기본값
   │
   └─ ⑦ 자동 검수 + 사람 검수 ........... atomicity / coverage / self-containment / 오라벨
```

핵심 설계 결정은 **④와 ⑥ 사이에 ⑤를 넣은 것**이다. 답변 하나를 통째로 "맞다/틀리다"로 채점하면
"조건 두 개 중 하나만 틀린 답변"을 어떻게 셀지 애매해진다. 그래서 답변을 먼저 atomic claim으로
쪼갠 뒤 **오류가 실제로 담긴 claim에만 오답 라벨을 붙이고 나머지는 SUPPORTED로 둔다.** 조건문
답변이 claim 4~5개로 쪼개지는 건 예외가 아니라 기본값이었다(Pilot에서 시나리오 23개 → claim 64개).

`evidence`는 모델이 만들어낸 게 아니라 **실제 공시 필드에서 그대로 가져온다**(`spcl_cnd`
우대조건, `mtrt_int` 만기후이자, 기간별 금리 옵션). 즉 답변은 합성이지만 근거는 실제 데이터다.

## 3. 오류 분류 체계 (taxonomy)

설계 초기에 8종으로 시작했고, 시나리오를 실제로 쓰면서 **13종으로 늘었다.** 늘어난 5종은
"공시 텍스트를 읽다 보니 실제로 이런 식으로 틀릴 수 있겠다"를 발견해 추가한 것이다.

| 오류 유형 | 뜻 | 출처 |
|---|---|---|
| `numeric_error` | 숫자를 잘못 씀 | 초기 설계 |
| `term_error` | 기간·가입기간을 바꿔 씀 | 초기 설계 |
| `eligibility_error` | 가입 대상·자격을 잘못 씀 | 초기 설계 |
| `condition_reversal` | OR을 AND로(또는 반대로) 뒤집음 | 초기 설계 |
| `condition_omission` | 복합조건 중 일부를 빼고 말함 | 초기 설계 |
| `base_vs_max_rate` | 기본금리와 최고금리를 혼동 | 초기 설계 |
| `conditional_benefit_generalization` | 조건부 혜택을 무조건인 것처럼 일반화 | 초기 설계 |
| `missing_information` | 근거에 없는 내용을 단정 | 초기 설계 |
| `boundary_condition_error` | 구간 경계를 잘못 적용(1개월 이내 vs 초과) | 구축 중 추가 |
| `mutually_exclusive_ignored` | "중복적용 불가"를 무시하고 합산 | 구축 중 추가 |
| `exception_omission` | "단, ~ 제외" 같은 예외 단서를 삭제 | 구축 중 추가 |
| `evidence_mismatch` | 근거 자체가 claim과 다른 항목을 가리킴 | 구축 중 추가 |
| `fabricated_condition` | 근거에 없는 조건을 지어냄 | 구축 중 추가 |

추론 성격(`reasoning_type`)도 별도 축으로 붙였다 — 실패가 특정 오류 유형이 아니라 특정 *추론
방식*에 몰리는지 보기 위해서다: `any_of`(1가지 이상 충족), `all_of`(전부 충족),
`numeric_threshold`(수치 임계), `temporal_scope`(시점·구간), `mutually_exclusive`(중복 불가),
`exception`(예외 단서), `cross_field_mismatch`(다른 필드 참조).

INSUFFICIENT는 출처를 따로 구분했다 — `natural_missing`(공시 자체에 우대조건 정보가 없는 상품)과
`wrong_field_retrieved`(엉뚱한 필드가 근거로 딸려온 경우). 억지로 지워 만든 결측이 아니라
**원본 데이터에 실제로 존재하는 결측**을 골라 쓴 게 중요한 부분이다.

## 4. Gold label 부여 규칙

시나리오 하나에는 **오류를 최대 하나만** 주입한다. decompose 결과 중 그 오류가 실린 claim만
시나리오의 라벨을 물려받고, 나머지는 전부 SUPPORTED가 된다.

- `error_match`: 주입한 오류를 식별하는 **대체 표현 목록**. 하나라도 걸리면 그 claim이 오답 claim.
  모델이 오류를 매번 같은 문구로 쓰지 않기 때문에 여러 표현을 나열한다.
- `error_match`가 **하나도 안 걸리면** 추측하지 않고 `needs_manual_review: true`로 표시한다
  (주입한 오류가 분해 과정에서 사라졌다는 뜻이라 사람이 봐야 한다).
- 최종 데이터셋에서 `needs_manual_review`는 Pilot 64건·Test 53건 모두 0건이다.

## 5. 품질 관리 — 실제로 잡아낸 문제들

자동 체크 두 가지를 파이프라인에 넣었다.

- **Atomicity**: claim 하나에 사실 판단이 하나인지(문장 종결 표지가 2개 이상이면 비atomic 판정)
- **Coverage**: 원본 답변의 모든 숫자가 분해 결과 어딘가에 살아있는지

두 체크 모두 최종본에서 100% 통과다(Pilot 64/64, Test 53/53). 다만 **자동 체크만으로는
부족했고, 정작 중요한 결함 세 가지는 사람이 대조하다가 발견했다.**

1. **Self-containment 결함 (Pilot 단계에서 재오픈)** — 분해된 claim이 "두 조건", "해당" 같은
   지시어를 남기거나, 숫자에 붙어 있던 시점 조건("1개월 이내")을 빠뜨렸다. Verifier는 claim을
   하나씩 독립적으로 보기 때문에 이런 claim은 **원리적으로 판정이 불가능하다** — Verifier 성능
   문제로 오해할 뻔한 걸 데이터 결함으로 확인했다. Decomposer 프롬프트에 금지 규칙을 명시해
   재분해했고, claim 29개 → 27개로 병합되며 해소됐다(경위는
   [`../decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md)).
2. **단위 불일치 (사람이 만든 버그)** — 근거는 "20백만원"인데 시나리오 지시문을 "2천만원"으로
   썼다. 금액은 같지만 표기가 달라 두 모델 모두 "다른 값"으로 판정했다. 모델 실패로 집계될 뻔한
   케이스를 데이터 버그로 분리했다.
3. **오라벨 의심 케이스 재검토** — "모델 둘 다 틀린 문항"을 전수 재확인해서, 실제로는 라벨이
   맞았던 케이스(근거에 상품명이 포함돼 있어 정상)를 재검토 대상에서 제외했다. 반대로 "미세한
   소수점 차이(3.8% vs 3.85%)를 놓친 케이스"는 데이터 문제가 아니라 진짜 어려운 문항으로 남겼다.

교훈은 단순하다 — **모델이 틀린 문항은 먼저 데이터를 의심한다.** 이 검수를 안 했으면 데이터
결함 3건이 그대로 "모델 성능"으로 보고됐을 것이다.

## 6. Split 설계 — Pilot / held-out Test 분리

| | Pilot | Test |
|---|---|---|
| claim 수 | 64 | 53 |
| 시나리오 수 | 23 | 53 |
| 상품 수 | 11 | 28 |
| 용도 | 모델 선정, 프롬프트 개선 | **최종 확인 (한 번만 사용)** |
| 저자 | Claude Code | Claude Code 28 + Codex 25 |

분리 원칙은 두 가지다.

- **(상품, 근거 필드) 쌍이 완전히 분리돼 있다.** 상품 5개(`p002`/`p013`/`p018`/`p021`/`p022`)는
  양쪽에 등장하지만 **참조하는 필드가 겹치지 않는다** — 예를 들어 Pilot에서 우대조건(`spcl_cnd`)을
  쓴 상품은 Test에서 만기후이자(`mtrt_int`)를 쓴다. 근거 텍스트 기준으로는 겹치는 문항이 없다.
  나머지 23개 상품은 Test에서 처음 등장한다.
- **저자를 분리했다.** Test 시나리오 53건 중 25건은 다른 에이전트(Codex)가 작성했다. 한 사람이
  Pilot과 Test를 다 쓰면 같은 문체·같은 함정 패턴이 반복돼 held-out의 의미가 약해지기 때문이다.

Test는 Pilot에서 모델·프롬프트를 확정한 **뒤에** 한 번만 실행했다.

## 7. 데이터셋 통계

**라벨 분포**

| | SUPPORTED | UNSUPPORTED | INSUFFICIENT |
|---|---|---|---|
| Pilot (64) | 46 | 14 | 4 |
| Test (53) | 25 | 26 | 2 |

Pilot이 SUPPORTED 쪽으로 기운 건 "오류 하나만 주입, 나머지 claim은 충실한 재진술"이라는 설계의
자연스러운 결과다(조건문 시나리오 하나가 claim 4~5개로 쪼개지면서 SUPPORTED가 늘어난다).
Test는 시나리오당 claim 수를 줄이고 시나리오 수를 늘려 균형을 맞췄다.

**근거 필드 분포**

| 필드 | Pilot | Test |
|---|---|---|
| `spcl_cnd` (우대조건, 자유서술) | 45 | 32 |
| `mtrt_int` (만기후이자, 구간형) | 6 | 12 |
| 금리 옵션 (정형 필드) | 9 | 9 |
| 복합 (금리 + 조건) | 4 | – |

의도적으로 자유서술 조건문(`spcl_cnd`, `mtrt_int`)에 무게를 뒀다. 정형 숫자 필드는 애초에
Verifier가 잘 맞히는 쪽이라 변별력이 낮다.

**추론 유형 분포 (claim 기준, 중복 집계)**

| | `numeric_threshold` | `temporal_scope` | `any_of` | `all_of` | `mutually_exclusive` | `exception` | 기타 |
|---|---|---|---|---|---|---|---|
| Pilot | 9 | 5 | 9 | 4 | 6 | 5 | – |
| Test | 13 | 18 | 4 | 8 | 2 | 4 | 1 |

## 8. 이 평가셋으로 실제로 판별한 것

데이터셋이 제 역할을 했는지가 결국 중요하다. 네 가지를 판별해냈다.

1. **모델 선정** — Pilot 64건에서 Qwen3.5-4B(FAR 0.1111)와 Kanana-2-3B(FAR 0.2222)가 갈렸다.
   핵심 지표인 False Accept Rate에서 2배 차이라 판단이 명확했다.
2. **프롬프트 개선안 기각** — INSUFFICIENT 인식을 고치려던 프롬프트 두 버전(Langfuse version 3, 5)을
   이 셋으로 검증해 **둘 다 기각**했다. v3는 기존 정답까지 흔들었고, v5는 목표 지표가 오히려
   나빠졌다. 평가셋이 없었으면 "그럴듯해 보이는 프롬프트"를 그냥 채택했을 것이다.
3. **약점 2종 식별** — Test에서 ① INSUFFICIENT↔UNSUPPORTED 경계 혼동, ② 복합조건 일부만 인용된
   claim을 승인하는 실패(`condition_omission`)가 재현됐다. 특히 ②는 Pilot에서 "어려운 문항"으로
   관찰된 게 **새로운 상품·새로운 문항에서 2/2 모두 그대로 재현**되며 우연이 아님이 확인됐다.
4. **모델 종속성 검증 (cross-model)** — 같은 Test 53건을 다른 모델에 돌려 실패가 Qwen 고유인지
   확인했다. Nemotron Ultra 550B의 지표가 Qwen3.5-4B와 완전히 동일하게 나와, 처음에는 이를
   "체급과 무관한 태스크 구조적 한계"의 근거로 읽었다.

| 모델 | Test FAR | UNSUPPORTED Recall | Macro F1 (eager 경로) |
|---|---|---|---|
| Qwen3.5-4B-int4 (선정 모델) | 0.1071 | 0.8846 | 0.8434 |
| Nemotron Ultra 550B (참고) | 0.1071 | 0.8846 | 0.8434 |
| Claude Haiku 4.5 (참고) | 0.0714 | 0.8462 | 0.8098 |

> **⚠️ 그 해석은 이후 뒤집혔다.** [#28 감사](../eval/precondition_audit.md)에서 4개 모델로 범위를
> 넓혀 보니 ①은 **모델별 특성**이었고(Kanana 3B·Haiku·Sonnet은 INSUFFICIENT를 4/4 맞힌다),
> ②도 절차형 프롬프트로 **잡히긴 잡혔다**(다만 채택 모델에서 과잉거부를 유발해 기각). 즉 위 두
> 모델의 지표가 같았던 건 "태스크 한계"의 증거가 아니라 **표본이 두 모델뿐이었던 탓**이다.
> 평가셋 관점에서 남는 교훈은 분명하다 — **대조군을 2개만 두면 "모델 탓"과 "태스크 탓"을 가를 수
> 없다.** 위 Macro F1도 채택 서빙 경로에서는 0.6151이다(같은 문서 §③·④ 참고).

## 9. 알려진 한계

정직하게 남긴다.

- **규모가 작다.** Pilot 64 + Test 53으로는 오류 유형별 셀이 1~6건 수준이라, 유형별 수치는
  경향으로만 읽어야 한다. "condition_omission 2/2 실패"는 방향은 분명하지만 통계적 강도는 약하다.
- **INSUFFICIENT 표본이 적다.** Pilot 4건, Test 2건. 원본 공시에서 자연 발생한 결측만 쓴다는
  원칙 때문인데, 그만큼 이 라벨의 지표는 흔들린다.
- **`claim_id`가 split 간 전역 고유하지 않다.** `p018_c03`이 Pilot과 Test 양쪽에 있다(내용은
  완전히 다른 문항). eval은 split별로 돌기 때문에 결과에 영향은 없지만, 두 split을 한 테이블로
  합칠 때는 `dataset_split`을 키에 포함해야 한다.
- **`error_match` 키워드 매칭은 재현율 한계가 있다.** 답변 합성이 결정적이지 않아, 재생성하면
  문구가 달라져 재검증이 필요하다.
- **필요조건/충분조건 같은 논리적 뉘앙스**는 키워드 규칙으로 못 잡는다. 이번엔 self-containment
  수정으로 해당 케이스가 해소됐지만 일반적인 해법은 아니다.
- **Test 셋은 더 이상 held-out이 아니다.** [#28 감사](../eval/precondition_audit.md)의 검증 실험에서
  Test 53건을 프롬프트 변형 3종에 사용했다. 리포트에 보고된 수치는 노출 이전 측정값이라 유효하지만,
  앞으로 프롬프트를 더 손보려면 새 셋이 필요하다.
- **Dev split을 따로 두지 않았다.** 프롬프트 개선을 Pilot에서 했기 때문에 Pilot 지표는 선정
  과정에서 반복 사용된 값이다. Test는 그 영향에서 자유롭다.

## 10. 재현

상품 데이터셋 파일은 공개 repo에 포함하지 않는다(인증키 기반으로 제공되는 데이터의 재배포를
피하기 위해 — [`../../data/sample/README.md`](../../data/sample/README.md) 참고). 인증키를 직접
발급받으면 아래 순서로 재생성된다.

```bash
python -m src.ingest.fetch_finlife                       # 공시 스냅샷 수집
python -m src.ingest.normalize_products                  # canonical record 생성
python -m src.decomposition.generate_synthetic_answers   # 시나리오 → 답변 합성
python -m src.decomposition.claim_decomposer             # 답변 → atomic claim + gold label
python -m src.decomposition.build_test_claims            # Test split 구축
```

데이터셋 필드 구조는 [`../../data/sample/claim_dataset_sample.json`](../../data/sample/claim_dataset_sample.json)에
마스킹 샘플로 들어 있고, 답변 합성·분해에 쓴 프롬프트 전문은 [`../../prompts/`](../../prompts/)에 있다.

## 관련 문서

| 문서 | 내용 |
|---|---|
| [`../profiling/eval_design_review.md`](../profiling/eval_design_review.md) | 원본 데이터 프로파일링 → 어떤 필드가 어떤 오류 유형을 만들기 좋은지 |
| [`../normalization/canonical_products_review.md`](../normalization/canonical_products_review.md) | evidence로 쓸 canonical record 정규화 규칙 |
| [`../decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md) | Claim Decomposition 검증, self-containment 수정 경위 |
| [`../eval/smoke_eval_review.md`](../eval/smoke_eval_review.md) | Pilot 분석 — 모델 선정, 프롬프트 기각 판정 |
| [`../eval/test_eval_review.md`](../eval/test_eval_review.md) | Test(held-out) 최종 검증, cross-model 확인 |
| [`../eval/precondition_audit.md`](../eval/precondition_audit.md) | eval 전제 감사 + 검증 실험(#28) |
