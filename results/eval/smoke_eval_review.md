# #14 Eval Harness — Smoke/Pilot 분석 (29건 → 65건, 프롬프트 v1 → v2)

`src/eval/run_eval.py`로 #12의 Smoke claim 29개 전체를 Qwen/Kanana 양쪽에 동일 조건(warm-up 제외,
`max_tokens=512`, `temperature=0`, Langfuse Prompt Management v1)으로 돌린 결과. **29건은 아직
Pilot이 아니라 Smoke 규모라 모델 선정 근거로 쓰지 않았음** — 패턴 파악용. 이후 65건으로 확장한 뒤부터는
Pilot 규모(CLAUDE.md 기준 30~50건 이상) 결과로 취급한다(맨 아래 "Pilot 확장" 절 참고).

## 프롬프트 버전 관리

Langfuse는 버전을 1부터 매긴다. 이 리포트가 쓰는 버전은 **v1("baseline", 처음 만든 판정 기준
프롬프트 — reason 길이 제약 없음)**. 이후 Qwen의 reason이 512토큰에서도 안 끝나 스키마가 깨지는
문제를 발견해 v2("reason 1문장·100자 이내" 제약 추가)를 이미 Langfuse에 push해뒀지만, 이 리포트의
결과는 전부 v1 기준이고 v2는 아직 검증 전이다.

| 버전 | 내용 | 상태 |
|---|---|---|
| v1 | 판정 기준만, reason 길이 제약 없음 | 검증 완료 (`*_prompt-v1.json`) |
| v2 | reason 1문장·100자 이내 제약 추가 | 검증 완료 (`*_prompt-v2.json`) — 아래 참고 |

## v2 검증 결과 — 29건 재실행

| 지표 | Kanana v1 | Kanana v2 | Qwen v1 | Qwen v2 |
|---|---|---|---|---|
| False Accept Rate | 0.4444 | 0.4444 | 0.2222 | **0.1111** |
| UNSUPPORTED Recall | 0.5 | 0.5 | 0.75 | **0.875** |
| Macro F1 | 0.8095 | 0.8095 | 0.4667 | 0.5 |
| Schema Valid Rate | 1.0 | 1.0 | 0.9655 | **1.0** |
| Latency p50 / p95 | 5.95s / 8.9s | 4.51s / 7.0s | 16.7s / 36.35s | **7.22s / 14.30s** |

**Kanana는 v1/v2 사이 예측이 완전히 동일하다** — 원래도 짧게 답해서 길이 제약의 영향을 안 받음, latency만
소폭 개선.

**Qwen은 전 지표가 개선됐다** — 이전에 512토큰에서도 못 끝내고 깨졌던 케이스(`p021_c01_1`)가 이번엔
스키마 그대로 통과했고(1.0), FAR·Recall도 더 좋아졌고, 무엇보다 **latency가 p50 기준 절반 이상
줄었다**(16.7s→7.22s) — 답변을 짧게 쓰라고 하니 생성 자체가 빨라진 것. reason 길이 제약이 "부작용
없이 다 좋아지는" 드문 케이스였다.

다만 Macro F1은 Qwen이 여전히 Kanana보다 낮다(0.5 vs 0.8095) — v1 분석에서 짚었던 "Qwen은 SUPPORTED를
과도하게 UNSUPPORTED로 거부한다"는 패턴 자체는 프롬프트 길이 제약으로 해결되는 문제가 아니었다(오답
패턴은 v1과 거의 동일하게 유지됨, 다만 재현성 문제인지 몇 건은 UNSUPPORTED↔SUPPORTED가 바뀌었다).

## 헤드라인 지표

| 지표 | Kanana | Qwen |
|---|---|---|
| False Accept Rate | **0.4444** | **0.2222** |
| UNSUPPORTED Recall | 0.5 | 0.75 |
| Macro F1 | **0.8095** | **0.4667** |
| Schema Valid Rate | 1.0 | 0.9655 (28/29) |
| Latency p50 / p95 | 5.95s / 8.9s | 16.7s / 36.35s |

골드 분포(29건 공통): SUPPORTED 20, UNSUPPORTED 8, INSUFFICIENT 1 — SUPPORTED에 크게 쏠려 있다.

## FAR는 낮은데 Macro F1은 왜 더 나쁜가 — Kanana="loose", Qwen="strict" 패턴

예측 분포:
- Kanana: SUPPORTED 22 / UNSUPPORTED 6 / INSUFFICIENT 1
- Qwen: SUPPORTED 16 / UNSUPPORTED 12 / (schema invalid) 1

**Kanana는 "관대한" 실패 패턴**이다 — 오답 6건 중 4건이 UNSUPPORTED를 SUPPORTED로 잘못 승인한
것(`condition_reversal` 2건, `condition_omission` 1건, `boundary_condition_error` 1건) — 전부
조건문·숫자 구간을 세밀하게 비교해야 잡히는 케이스다. "체급 문제일 수도 있다"는 가설과 일치한다.

**Qwen은 "엄격한" 실패 패턴**이다 — 골드 UNSUPPORTED 8건 중 6건(75%)을 정확히 잡아내지만, 그 대가로
실제로는 SUPPORTED인 claim 5건을 UNSUPPORTED로 잘못 거부한다. SUPPORTED가 20/29로 다수 클래스라
이 오탐이 Macro F1 전체를 크게 끌어내린다(SUPPORTED precision이 크게 깎임) — FAR/Recall만 보면
좋아 보이는데 F1은 나쁜 이유가 여기 있다.

**CLAUDE.md의 지표 우선순위(FAR가 "핵심", F1은 그 아래)로 보면** 지금 시점에는 오히려 Qwen 쪽이
더 안전한 실패 방향이다 — 금융 Verifier에서는 "틀린 걸 맞다고 승인"하는 게 "맞는 걸 틀렸다고 거부"하는
것보다 훨씬 위험하기 때문. 다만 Smoke 29건으로 결론 내릴 단계는 아니다.

## 두 모델이 똑같이 틀린 케이스 — 성격이 다른 두 건

**`p002_c04_4`("5천만원 이상 가입 시 만기일에 보너스이율 0.1%가 적용됩니다", 골드 UNSUPPORTED
`condition_omission`) — 둘 다 SUPPORTED로 오답.** Evidence는 "6~12개월제 5천만원 이상 가입"
**그리고** "펀드 3천만원 이상 보유"를 **모두** 충족해야 한다고 명시하는데, claim은 첫 번째 조건만
언급하고 두 번째(펀드 보유)를 빠뜨렸다. 두 모델 다 "claim이 말한 부분은 evidence와 일치한다"는 데
꽂혀서, **명시된 것 외의 조건이 생략됐다는 사실 자체를 못 잡는다.** 이건 진짜 Verifier의 한계로 보인다
— "이 프로젝트가 잡으려는" 실패 유형(조건 누락) 정중앙에 있는 케이스인데 둘 다 놓쳤다.

**`p001_c01_2`("만기후이자는 만기시점 약정이율의 50%이다", 골드 SUPPORTED) — 둘 다 UNSUPPORTED로
오답.** 근데 이건 **Verifier 잘못이 아니라 #12 decomposition 결함**으로 보인다. 원본 답변
(`p001_c01_1`)은 "만기 후 **1개월 이내**에 해지할 경우"라는 시점 조건을 달고 있는데, 이 조건이
`p001_c01_2`로 분해되면서 빠졌다. Evidence는 시점에 따라 50%/30%/20% 세 가지 다른 값을 제시하는데,
claim만 놓고 보면 "언제"인지 정보가 없어 세 값 중 어느 것과도 명확히 매칭이 안 된다 — **Verifier
입장에서는 evidence만으로 판단이 안 서는 게 오히려 합리적인 반응**이다. `claim_decomposer.py`의
system prompt가 "각 claim은 self-contained해야 한다"고 명시했는데도 이 케이스에서는 지켜지지 않았고,
숫자만 확인하는 규칙 기반 Coverage 체크(`check_coverage`)는 "50%라는 숫자가 살아있으니 통과"로 보고
이 결함을 못 잡았다 — **조건어(시점/조건부 표현)가 원문에서 살아남았는지까지 확인하는 체크가 없다는
뜻**.

## #12 self-containment fix 이후 재실행 (27건) + failure_analysis.py

`claim_decomposer.py`의 self-containment 결함(대명사/지시어, 시점조건 누락)을 고치고 나서 29→27개
claim으로 갱신, 같은 prompt v2로 재실행했다(#12 PR 코멘트에 헤드라인 지표 비교 있음). 그 위에
`src/eval/failure_analysis.py`를 만들어 오답을 false accept(error_type별)/false reject
(reasoning_type별)로 나눠 분석했다.

### Qwen — false accept 1건, false reject 1건으로 수렴

```
false_accepts: p002_c04_3 (condition_omission)
false_rejects: p022_c01_1 (reasoning_type 없음)
insufficient_labeled_unsupported: p022_c01_2
```

v1/v2 분석에서 짚었던 "Qwen이 SUPPORTED를 과도하게 UNSUPPORTED로 거부한다"는 패턴이 **self-containment
수정만으로 사실상 사라졌다** — 대명사/지시어 때문에 판단 불가능했던 claim들이 실제 원인이었다는 뜻.
남은 오답 2건은 성격이 다르다: `p002_c04_3`(조건 누락)은 두 모델 다 놓치는 진짜 어려운 케이스,
`p022_c01_1`은 애초에 evidence(spcl_cnd)에 없는 사실(상품 종류)을 묻는 골드 라벨 자체가 의심스러운
케이스(#12에서 이미 재검토 대상으로 표시함). **v3(reason 과잉거부 완화 프롬프트)는 지금 불필요해
보인다** — 겨냥할 구체적 대상이 사라졌다.

INSUFFICIENT를 UNSUPPORTED로 잘못 판정하는 패턴(`p022_c01_2`)만 여전히 남아 있다 — 이건 프롬프트
길이 문제도 self-containment 문제도 아닌, Qwen 고유의 SUPPORTED/UNSUPPORTED/INSUFFICIENT 경계
처리 특성으로 보인다.

### Kanana — false accept 3건이 정확히 error_type별로 갈림

```
false_accepts: p003_c02_4(condition_reversal), p002_c04_3(condition_omission), p021_c01_4(boundary_condition_error)
false_rejects: p003_c01_3(reasoning_type=any_of), p022_c01_1(reasoning_type 없음)
```

false accept가 조건 반전/누락/구간 오류 각각 정확히 1건씩 — "논리·숫자 구간을 세밀하게 비교해야
잡히는 케이스를 체급 때문에 놓친다"는 가설과 정확히 일치한다. false reject 중 하나(`p003_c01_3`)는
`any_of`(OR 조건) — self-containment 수정으로 문장이 길고 복잡해지면서 새로 놓치게 된 케이스.

## Pilot 확장 (65건) — 안 써본 상품/오류유형 13개 시나리오 추가

기존 27건에 6개 상품(경남/제주×2/전북/농협/수협)·4개 신규 오류유형(`mutually_exclusive_ignored`,
`exception_omission`, `term_error`, `eligibility_error`)을 더해 65건으로 확장했다(#12 파이프라인
재사용, `generate_synthetic_answers.py`/`claim_decomposer.py`를 증분 실행 가능하게 고쳐서 기존 27건은
API 재호출 없이 그대로 유지). 자동 라벨링 중 발견한 오라벨(`p018_c02_1`, "모두 충족해야"가 실제로는
독립 조건인데 필요조건처럼 서술됨)은 실행 전에 제거해 65건으로 확정.

| 지표 | Kanana (65) | Qwen (65) |
|---|---|---|
| False Accept Rate | 0.2222 | **0.1111** |
| UNSUPPORTED Recall | 0.7143 | **0.8571** |
| Macro F1 | **0.7445** | 0.6656 |
| Schema Valid Rate | 1.0 | 1.0 |
| Latency p50 / p95 | 3.90s / 7.81s | 7.83s / 17.05s |

27건 때의 "Kanana=loose(F1 좋음·FAR 나쁨) / Qwen=strict(FAR·Recall 좋음·F1 나쁨)" 패턴이 표본이 늘어난
65건에서도 그대로, 오히려 더 뚜렷하게 재현됐다.

### 가장 중요한 신규 발견 — INSUFFICIENT 처리가 모델별로 확실히 갈린다

자연 결측 INSUFFICIENT 표본이 이번에 3개(`p022`/`p030`/`p035`)로 늘었다. **Kanana는 3/3 전부 정확히
INSUFFICIENT로 판정했고, Qwen은 3/3 전부 UNSUPPORTED로 오판했다.** 이전엔 표본 1개(`p022`)뿐이라
우연일 수 있었는데, 이제 서로 다른 상품 3개에서 동일하게 재현됐다 — Qwen이 "정보 부재"와 "명시적 충돌"을
구분하지 못하는 경향이 있다는 걸 상당히 신뢰도 있게 확인한 셈이다. CLAUDE.md의 SUPPORTED/UNSUPPORTED/
INSUFFICIENT 판정 기준 정의와 직결되는 대목이다.

### 사람이 만든 버그로 밝혀진 "공유 오답" 하나 (+ 정정)

**정정**: 처음엔 `p013_c03_2`(제주은행 J정기예금 term_error 시나리오, 24개월 vs 36개월 최고금리)를
단위 불일치 버그로 착각해서 기록했는데, 다시 확인해보니 그건 **진짜 어려운 케이스**였다 — evidence가
24개월 3.8%/36개월 3.85%로 미세한 소수점 차이인데 claim이 이를 바꿔치기한 것뿐, 단위 문제는 없다.
두 모델 다 이 0.05%p 차이를 놓친 건 정당한 term_error 실패다.

**진짜 단위 불일치 버그는 `p018_c03`**이었다 — evidence는 "20백만원"인데 내가 시나리오 instruction을
쓸 때 "2천만원"으로 바꿔 썼다(수학적으론 같은 금액 20,000,000원인데 표기 단위가 달라 두 모델 다 "다른
숫자"로 인식, UNSUPPORTED로 오답). evidence의 원래 표기를 그대로 쓰도록 시나리오를 고쳐 재생성했고,
두 모델 다 이번엔 정확히 SUPPORTED로 맞혔다.

`p018` 상품 전체에서 Kanana만 유독 많이 틀린 것(mutex 관련 claim 7건 중 7건)을 보고 "①②③④⑤ 원문자
포맷이 문제 아닐까" 의심했었는데, Qwen은 같은 포맷의 claim 대부분(7건 중 5건)을 맞혀서 포맷 자체가
원인이라는 가설은 이 데이터로는 확인되지 않았다.

이 종류의 숫자 표기 불일치는 모델이 CoT/reasoning으로 풀 수도 있지만 그러면 답변이 다시 길어진다
(v2에서 어렵게 줄인 reason 길이 제약과 상충) — 대신 evidence/claim 생성 전에 숫자 표기를 통일하는
전처리도 검토할 수 있다. **지금 당장은 보류** — 시나리오 작성 규칙(원문 숫자 표기를 그대로 쓸 것)으로
충분히 예방 가능해서, 데이터 파이프라인에 정규화 로직을 넣을 정도의 재현 빈도는 아직 아니다.

## v3 프롬프트 — INSUFFICIENT 인식은 고쳤지만 전반적으로는 손해 (기각)

정리된 64건(위 단위불일치 fix 반영, `p022_c01_1`은 재검토 결과 evidence에 상품명이 포함돼 있어
정상 라벨로 확인됨 — 재검토 대상에서 제외)에 v3 프롬프트를 붙여 재검증했다. v3는 v2에 "판정 순서:
evidence에 claim이 다루는 항목이 전혀 언급 안 됐으면 반드시 INSUFFICIENT로 판정하라, 언급이 있고
내용이 다를 때만 UNSUPPORTED" 규칙 + 예시 1개를 추가한 버전(Langfuse version 3).

| 지표 | Kanana v2 | Kanana v3 | Qwen v2 | Qwen v3 |
|---|---|---|---|---|
| False Accept Rate | 0.2222 | 0.1667 | ~0.111 | 0.1667 |
| UNSUPPORTED Recall | 0.7143 | 0.5714 | 0.8571 | 0.6429 |
| Macro F1 | **0.7652** | 0.5714 | 0.6656 | **0.7699** |
| Latency p50/p95 | 3.84s/7.73s | 5.15s/8.46s | ~7.8s/17s | 7.95s/17.5s |

INSUFFICIENT 4건(`p022_c01_2`/`p030_c01`/`p035_c01_1`/`p035_c01_2`)은 양쪽 모델 다 정확히 맞혔다 —
목표했던 문제는 고쳤다. **근데 그 대가로 원래 잘 맞히던 SUPPORTED/UNSUPPORTED 판정이 흔들렸다.**
`p002_c05_3/4`(원래 정답 UNSUPPORTED)가 INSUFFICIENT로, `p003_c02_4`(원래 정답 UNSUPPORTED,
condition_reversal)가 SUPPORTED로 새로 틀렸다 — "애매하면 INSUFFICIENT로 도피"하는 경향이 생긴
것으로 보이고, Kanana의 Macro F1은 0.7652→0.5714로 크게 나빠졌다. CLAUDE.md가 FAR를 "핵심" 지표로
못박은 기준으로도 Qwen의 FAR가 오히려 나빠졌다(0.111→0.1667). **v3는 기각, "production" 라벨을 v2
내용으로 되돌렸다**(Langfuse version 4 = v2와 동일 내용).

**원인 추정**: (1) 예시 1개를 박아둔 게 과적합을 유발("애매해 보이면 이 예시처럼 INSUFFICIENT") —
`p002_c05_3/4`는 evidence에 관련 내용(보너스이율)이 있는데도 도피한 정황과 일치. (2) 지시문이 길어져
condition_reversal 같은 기존 판정 지침에 대한 주의가 희석됐을 가능성.

**v4 설계안(미검증)**: 절차+예시를 걷어내고 "INSUFFICIENT를 회피 수단으로 쓰지 마라"는 짧고 강한 부정
규칙 하나만 추가.

```
INSUFFICIENT는 evidence에 claim이 다루는 항목이 전혀 언급되지 않았을 때만 써라 — 애매하거나
확신이 안 선다는 이유로 INSUFFICIENT를 고르지 마라. evidence에 관련 내용이 하나라도 있으면
그 내용과 claim을 직접 비교해 SUPPORTED 또는 UNSUPPORTED 중 하나로만 판정하라.
```

예시를 빼고(과적합 방지) v2 대비 길이를 최소한으로 늘리는 게 핵심. **아직 실행 전** — 다음 세션에서
검증 예정.

## 다음 단계로 넘길 것

1. **v4 프롬프트 검증** (다음 세션) — 위 설계안을 Langfuse에 push하고 64건 전체로 Kanana/Qwen 재실행.
   v2 대비 FAR/Recall이 나빠지지 않으면서 INSUFFICIENT 4건을 유지하는지가 채택 기준. 안 되면 프롬프트
   엔지니어링은 그만 시도하고 "현재 두 모델의 알려진 공유 약점"으로만 문서화.
2. **`p002_c04_3`(조건 누락)은 두 모델 다 놓친 진짜 어려운 케이스** — Dev 단계에 이런 "부분 인용 +
   조건 생략" 유형을 더 포함해서 재현되는 패턴인지 확인할 가치 있음.
3. **`p013_c03_2`(24개월 vs 36개월 최고금리, 0.05%p 차이)도 두 모델 다 놓치는 진짜 어려운 케이스** —
   Dev 표본에 인접 기간 간 미세한 숫자 차이 유형을 더 포함해볼 가치 있음.
4. 숫자 표기 정규화 전처리는 보류 — 재현 빈도가 늘어나면 그때 규칙 기반으로 추가.
5. v4 검증 후 모델 선정 최종화 — 현재는 Qwen 쪽으로 기울어 있음(FAR/Recall 우위, latency는 감수 가능
   판단, 도메인 특성상 재질문보다 한 번에 정확한 판정이 더 중요하다는 방향).
