# #12 Claim Decomposer — Smoke 결과

`src/decomposition/generate_synthetic_answers.py`(Claude Sonnet 5로 canonical product 기반 synthetic
답변 10개 생성) → `src/decomposition/claim_decomposer.py`(Claude Sonnet 5로 atomic claim 분해 + 규칙
기반 Atomicity/Coverage sanity check + metadata 부착)까지 파이프라인이 한 바퀴 도는 것을 확인했다.

## 산출물

- `data/smoke/synthetic_answers.json` — 10개 synthetic 답변 (SUPPORTED 3, UNSUPPORTED 5, INSUFFICIENT 1, 혼합 답변 1)
- `data/smoke/claim_dataset.json` — 29개 atomic claim (decomposition 후)

## Smoke 시나리오 (10개)

| claim_id | product | source_field | 의도한 label | error_type |
|---|---|---|---|---|
| p002_c01 | e-그린세이브예금 | 12개월 base_rate | SUPPORTED | – |
| p002_c02 | e-그린세이브예금 | 12개월 base_rate | UNSUPPORTED | base_vs_max_rate |
| p002_c03 | e-그린세이브예금 | 12개월 base_rate | UNSUPPORTED | numeric_error |
| p003_c01 | iM함께예금 | spcl_cnd (OR 조건) | SUPPORTED | – |
| p003_c02 | iM함께예금 | spcl_cnd (OR→AND 반전) | UNSUPPORTED | condition_reversal |
| p002_c04 | e-그린세이브예금 | spcl_cnd (AND 조건 일부 누락) | UNSUPPORTED | condition_omission |
| p001_c01 | WON플러스예금 | mtrt_int | SUPPORTED | – |
| p021_c01 | IBK평생한가족통장 | mtrt_int (구간 혼동) | UNSUPPORTED | boundary_condition_error |
| p022_c01 | IBK더굴리기통장 | spcl_cnd (자연 결측) | INSUFFICIENT | missing_information (natural_missing) |
| p002_c05 | e-그린세이브예금 | base_rate + spcl_cnd (혼합) | UNSUPPORTED | conditional_benefit_generalization |

## 첫 실행에서 나온 문제: metadata 상속이 compound 답변에서 깨진다

최초 구현은 "시나리오 1개 = claim 1개 = gold label 1개"를 가정했다. 단일 사실 답변(숫자 claim 3개)은
문제없었지만, 조건문이 들어간 답변은 Decomposer가 여러 개의 독립적인 사실로 정확하게 쪼갰다. 예:
p003_c01("A 또는 B를 충족하면 0.10%p 우대") 한 문장이 "A 조건", "B 조건", "OR 관계", "보상 수치" 4~5개의
atomic claim으로 분해됐다 — Decomposer가 잘못 동작한 게 아니라 원래 의도대로 동작한 것이다.

**이건 예외 케이스가 아니라 정상 케이스였다.** CLAUDE.md가 "Level 2(조건문)가 더 중요한 난이도 축"이라고
지정한 것과 정확히 일치한다 — 프로젝트가 정말 신경 쓰는 케이스일수록 compound decomposition이 기본값에
가깝다. 그래서 "시나리오 1개 = claim 1개"라는 초기 가정 자체를 폐기했다.

## 수정한 스키마: "정답 답변에 오류 하나만 주입한다"는 전제를 명시적으로 인코딩

각 시나리오는 오류가 없거나(faithful) 오류 하나만(`error_type`) 주입하도록 설계한다 — 원래 그렇게
만들었으니, decompose 결과 중 그 오류에 해당하는 claim(들)만 시나리오의 label/error_type을 받고,
**나머지는 전부 evidence를 충실히 반영한 SUPPORTED가 기본값**이다.

- `error_type`이 없는 시나리오(순수 SUPPORTED) → decompose된 claim 전부 SUPPORTED
- `error_type`이 있는 시나리오 → `error_match`(대체 표현 목록, 하나라도 일치하면 매치)에 걸리는 claim만
  시나리오의 label/error_type을 받고, 나머지는 기본값(SUPPORTED)
- `error_match`가 decompose 결과 전체에서 단 하나도 안 걸리면(주입한 오류가 decomposition 과정에서
  인식 불가능한 형태로 사라졌다는 뜻) — 추측하지 않고 전체 claim을 `needs_manual_review: true`로 플래그

이전에는 MIXED 시나리오 하나에만 특수 처리로 keyword 매칭을 썼는데, 이제 모든 시나리오가 같은 규칙을
쓴다 — 단일 사실 시나리오는 그냥 `error_match`가 필요 없는(또는 전체가 SUPPORTED인) 특수 케이스일 뿐이다.

## 결과

29개 claim 전부 자동 라벨링됐고(`needs_manual_review: false` 29/29), 사람이 직접 하나씩 대조 확인한 결과
28/29는 명확히 맞았다. 나머지 1건(`p003_c02_5`, "두 조건을 동시에 충족하면 우대금리가 적용된다")은
경계 사례다 — 실제로 둘 다 만족하면 우대금리가 적용되는 건 참이라 필요조건(반전된 부분)과 충분조건
서술을 keyword 매칭만으로 구분하기 어렵다. Coverage 체크는 10/10 answer 모두 통과(원문의 모든 숫자가
decompose 결과 어딘가에 살아있음).

## 한계 (정직하게 남겨둠)

- **키워드 매칭은 재현율(recall) 문제가 있다.** 처음 버전에서 `error_match=["보너스"]`만 썼을 때
  "자동으로 0.1%의 추가 이율이 제공된다"(보너스라는 단어 없이 같은 오류를 다르게 표현) claim이
  SUPPORTED로 잘못 라벨링됐다. `error_match`를 대체 표현 여러 개를 허용하는 방식(any-of)으로 바꾸고
  `"0.1%"`를 추가해서 잡았지만, 이런 재현율 이슈는 표현이 또 달라지면 다시 생길 수 있다.
- **필요조건 vs 충분조건 같은 논리적 뉘앙스는 keyword 매칭으로 못 잡는다** (`p003_c02_5` 참고). 이건
  결국 LLM-as-judge가 필요한 영역인데 CLAUDE.md가 명시적으로 스코프 밖으로 뺐다 — 그래서 Pilot/Dev
  단계에서는 이런 애매한 케이스를 사람이 스팟체크하는 걸 전제로 한다.
- 답변 텍스트는 Claude API 호출 결과라 재생성할 때마다 문구가 달라질 수 있다(temperature 기본값이라
  결정적이지 않음) — `error_match` 키워드는 이번에 실제로 나온 문구에 맞춰 튜닝한 것이라, 다시
  생성하면 재검증이 필요하다.

## 종료 조건 재확인 (이슈 체크리스트 대비)

- [x] `generate_synthetic_answers.py` — canonical 기반 synthetic 답변 생성 (정상 3 + 오류 주입 5 + natural INSUFFICIENT 1 + 혼합 1)
- [x] `claim_decomposer.py` — Claude API로 atomic claim 분해
- [x] Atomicity 규칙 기반 체크 — 문장 종결 표지(`다./음./니다.`) 2개 이상이면 비atomic으로 판정 (이번 표본은 모두 이미 문장 단위로 잘 나뉘어 있어 발동 안 함)
- [x] Coverage 규칙 기반 체크 — 원문 숫자가 decompose 결과에 다 남아있는지 확인, 10/10 통과
- [x] Claim Dataset metadata 부착 — "오류 하나만 주입" 전제를 스키마로 일반화, 29/29 자동 라벨링(28/29 확인 결과 정확)
- [x] Smoke 규모(10개 answer → 29개 claim)로 파이프라인 확인

## 다음 단계로 넘길 것 (#13/#14에서 다룰 화두)

1. Pilot 규모로 확장할 때 `error_match`를 시나리오별로 더 폭넓게(대체 표현 여러 개) 정의하는 관행을 유지.
2. `p003_c02_5` 같은 필요/충분조건 뉘앙스 케이스는 자동 라벨을 무조건 신뢰하지 말고 표본을 뽑아 사람이
   확인.
3. Atomicity 체크가 이번 표본에서 한 번도 안 걸렸으므로, 실제로 비atomic한 사례를 Pilot 단계에서 최소
   1개는 포함해 규칙이 실제로 작동하는지 확인 필요.
