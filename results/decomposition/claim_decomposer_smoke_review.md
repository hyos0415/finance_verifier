# #12 Claim Decomposer — Smoke 결과

`src/decomposition/generate_synthetic_answers.py`(Claude Sonnet 5로 canonical product 기반 synthetic
답변 10개 생성) → `src/decomposition/claim_decomposer.py`(Claude Sonnet 5로 atomic claim 분해 + 규칙
기반 Atomicity/Coverage sanity check + metadata 부착)까지 파이프라인이 한 바퀴 도는 것을 확인했다.

## 산출물

- `data/smoke/synthetic_answers.json` — 10개 synthetic 답변 (SUPPORTED 3, UNSUPPORTED 5, INSUFFICIENT 1, MIXED 1)
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
| p002_c05 | e-그린세이브예금 | base_rate + spcl_cnd (혼합) | MIXED | conditional_benefit_generalization |

## 핵심 발견 — metadata 상속이 compound 답변에서 깨진다

**단일 사실 답변(숫자 claim 3개: p002_c01/02/03)은 그대로 1:1로 decompose되어 metadata가 깔끔하게
붙었다** (`needs_manual_review: false`).

**하지만 조건문이 들어간 답변(spcl_cnd의 AND/OR, mtrt_int의 구간)은 Decomposer가 진짜로 여러 개의
독립적인 사실로 쪼갰다.** 예를 들어 p003_c01("A 또는 B를 충족하면 0.10%p 우대") 한 문장이:

```
1. iM함께예금은 우대조건 중 하나로 전월 총수신 평잔이 30만원 이상인 경우를 포함한다.
2. iM함께예금은 우대조건 중 하나로 상품 가입 전에 첫만남플러스통장을 보유한 경우를 포함한다.
3. 전월 총수신 평잔 30만원 이상 조건과 첫만남플러스통장 보유 조건 중 하나만 충족해도 우대금리를 받을 수 있다.
4. 해당 우대조건을 충족하면 연 0.10%p의 우대금리를 받을 수 있다.
```

4개의 atomic claim으로 분해됐다. 이건 Decomposer가 잘못 동작한 게 아니라 **원래 의도대로 동작한 것**이다
— 문제는 내 초기 설계 가정("시나리오 1개 = claim 1개 = gold label 1개")이 compound 답변에서는 성립하지
않는다는 점이다.

더 심각한 예: `condition_reversal` 시나리오(p003_c02, "A와 B를 동시에 충족해야 한다" — 반전됨)도 4개로
쪼개졌는데, 그중 "우대조건 중 하나는 전월 총수신 평잔이 30만원 이상인 것이다"(claim 2) 자체는 evidence와
**독립적으로 봐도 참**이다. 오직 "동시에 충족해야 한다"는 AND 프레이밍(claim 4)만 실제로 잘못됐다.
그런데 내 코드는 시나리오 전체를 `UNSUPPORTED`로 알고 있어서, 이 metadata를 그대로 물려주면 claim 2에
잘못된 골드 라벨을 붙이게 된다.

**그래서 `claim_decomposer.py`는 이 경우를 자동으로 라벨링하지 않고 `needs_manual_review: true`로
플래그만 남긴다** (29개 중 24개). 임의로 답을 추정해서 틀린 골드 라벨을 심는 것보다, "이건 사람이 봐야
한다"고 정직하게 표시하는 쪽을 택했다 — Coverage 체크는 전부 통과했다(원문의 모든 숫자가 decompose된
claim 어딘가에 살아있음, 10/10 answer 기준 `coverage_ok: true`), 즉 정보가 유실되지는 않았다.

## 종료 조건 재확인 (이슈 체크리스트 대비)

- [x] `generate_synthetic_answers.py` — canonical 기반 synthetic 답변 생성 (정상 3 + 오류 주입 5 + natural INSUFFICIENT 1 + 혼합 1)
- [x] `claim_decomposer.py` — Claude API로 atomic claim 분해
- [x] Atomicity 규칙 기반 체크 — 문장 종결 표지(`다./음./니다.`) 2개 이상이면 비atomic으로 판정 (Smoke 10개 answer 모두 이미 문장 단위로 잘 나뉘어 있어 이번엔 걸린 게 없음 — 규칙 자체는 동작하지만 이번 표본에서 발동 안 함)
- [x] Coverage 규칙 기반 체크 — 원문 숫자가 decompose 결과에 다 남아있는지 확인, 10/10 통과
- [x] Claim Dataset metadata 부착 — 단일 사실 답변은 깔끔히 부착(5/29 claim), compound 답변은 `needs_manual_review`로 정직하게 플래그(24/29 claim)
- [x] Smoke 규모(10개 answer → 29개 claim)로 파이프라인 확인

## 다음 단계로 넘길 것 (#13/#14에서 다룰 화두)

1. **Pilot 규모 Claim Dataset을 만들 때는 기본적으로 "답변 하나 = 검증 대상 사실 하나"가 되도록 synthetic
   answer 생성 자체를 단일 사실 지향으로 설계**하고, compound 답변은 "decomposition 자체가 잘 되는지"를
   보는 별도 스트레스 테스트 카테고리로 명확히 분리하는 걸 검토.
2. `needs_manual_review: true`로 남은 24개 claim의 골드 라벨은 **이번 이슈에서 임의로 채우지 않았다** —
   Component Eval(#13/#14)에 투입하기 전에 사람이 evidence 대비 하나씩 확인해서 채워야 한다.
3. Atomicity 체크가 이번 표본에서 한 번도 안 걸렸으므로, 실제로 비atomic한 사례(문장 종결 표지가 여러 개인
   답변)를 Pilot 단계에서 최소 1개는 포함해 규칙이 실제로 작동하는지 확인 필요.
