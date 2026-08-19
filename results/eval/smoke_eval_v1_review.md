# #14 Eval Harness — Smoke 29건 분석 (프롬프트 v1/"v0" 기준)

`src/eval/run_eval.py`로 #12의 Smoke claim 29개 전체를 Qwen/Kanana 양쪽에 동일 조건(warm-up 제외,
`max_tokens=512`, `temperature=0`, Langfuse Prompt Management v1)으로 돌린 결과. **아직 Pilot이
아니라 Smoke 규모라 모델 선정 근거로 쓰지 않는다** — 패턴 파악용.

## 프롬프트 버전 관리

Langfuse는 버전을 1부터 매긴다. 이 리포트가 쓰는 버전은 **v1("baseline", 처음 만든 판정 기준
프롬프트 — reason 길이 제약 없음)**. 이후 Qwen의 reason이 512토큰에서도 안 끝나 스키마가 깨지는
문제를 발견해 v2("reason 1문장·100자 이내" 제약 추가)를 이미 Langfuse에 push해뒀지만, 이 리포트의
결과는 전부 v1 기준이고 v2는 아직 검증 전이다.

| 버전 | 내용 | 상태 |
|---|---|---|
| v1 | 판정 기준만, reason 길이 제약 없음 | 이 리포트가 씀 |
| v2 | reason 1문장·100자 이내 제약 추가 | push만 함, 미검증 |

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

## 다음 단계로 넘길 것

1. **prompt v2(reason 길이 제약)를 아직 검증 안 함** — 다음 작업으로 실제 재실행해서 스키마 실패가
   줄어드는지 확인 필요.
2. **`p001_c01_2`는 Claim Dataset 결함**으로 재분류 후보 — Pilot 규모로 갈 때 #12의 Coverage 체크에
   "조건어 보존" 여부까지 추가하는 걸 검토 (숫자만 보는 지금 체크로는 이런 케이스를 못 잡음).
3. **`p002_c04_4`(조건 누락)는 두 모델 다 놓친 진짜 어려운 케이스** — Pilot 표본에 이런 "부분 인용 +
   조건 생략" 유형을 더 포함해서 재현되는 패턴인지 확인할 가치 있음.
4. Kanana="loose"/Qwen="strict" 패턴이 29건 밖에서도 재현되는지 Pilot(30~50개)에서 검증.
