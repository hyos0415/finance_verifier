# Test(Unseen) 결과 — Qwen + v2 프롬프트 최종 확인

`data/test/claim_dataset.json`(53건, #23에서 구축 — Claude 28 + Codex 25, v1~v4 프롬프트 튜닝에
전혀 관여하지 않은 신규 claim)에 Qwen3.5-4B-int4-AutoRound + v2 프롬프트(Langfuse production,
version 6)로 최종 eval을 실행한 결과.

**1차(33건) → 2차(53건) 확장 경위**: 1차 결과를 검토하던 중 "Pilot에서 발견된 진짜 어려운
케이스(엣지 케이스)들을 Test 설계에 다 반영했는가"라는 질문이 나왔다. 확인해보니 Pilot의
failure taxonomy 12종 중 `condition_omission`, `exception_omission`, `mutually_exclusive_ignored`
3종이 1차 Test(33건)엔 아예 없었고, "24개월 vs 36개월처럼 아주 미세한 숫자 차이" 유형의
term_error도 없었다. 이 4가지 빈틈을 메우는 시나리오 5개씩(Claude/Codex)을 추가해 53건으로
확장한 뒤 재실행한 게 아래 결과다. 1차(33건) 결과는 이 문서에 남기지 않고 2차(53건, 최종)로
덮어썼다 — git 히스토리에 1차 커밋이 남아있어 필요하면 대조 가능하다.

## 헤드라인 비교 — Pilot(64건) vs Test(53건, unseen)

| 지표 | Pilot | Test | 비고 |
|---|---|---|---|
| False Accept Rate | 0.1111 | 0.1071 | 거의 동일 |
| UNSUPPORTED Recall | 0.8571 | **0.8846** | 비슷/소폭 개선 |
| Macro F1 | 0.6656 | **0.8434** | 개선 |
| Schema Valid Rate | 1.0 | 1.0 | 동일 |
| Latency p50/p95 | 7.83s/17.05s | 9.08s/14.27s | 비슷 |

**엣지 케이스를 채운 뒤(53건)에는 FAR이 Pilot 수준으로 다시 올라왔다** — 1차(33건)에서
FAR 0.0556로 더 좋게 나왔던 건, 그 33건에 Qwen이 취약한 유형(condition_omission 등)이
우연히 빠져 있었기 때문이라는 게 이번에 명확해졌다. 즉 **"Test가 Pilot보다 근본적으로 쉬웠다"는
착시였고, 엣지 케이스를 제대로 채우자 정직한 난이도로 수렴했다** — 이 자체가 "테스트셋 설계가
Pilot의 발견을 충실히 반영해야 신빙성이 생긴다"는 걸 보여주는 좋은 사례다. Macro F1과 Recall은
여전히 Pilot보다 좋은 편인데, 이는 v2가 Pilot 64건에 과적합되지 않았다는 신호로 계속 읽을 수
있다 — 다만 53건도 여전히 작은 표본(INSUFFICIENT 2건 등)이라 통계적 정밀도보다는 정성적
신호로 읽는다.

## 오답 5건

### condition_omission 2건 전부 오답 — Pilot이 짚었던 "가장 어려운 유형"이 그대로 재현

| claim_id | 상품 | 실제 라벨 | 예측 | 비고 |
|---|---|---|---|---|
| p020_c02 | The파트너예금 | UNSUPPORTED | **SUPPORTED (오답)** | AND 조건 중 '마케팅동의' 요건 누락 |
| p034_c02 | Sh해양플라스틱Zero!예금 | UNSUPPORTED | **SUPPORTED (오답)** | '특정 상품을 통해야' 제한 누락 |

**이번 확장에서 가장 중요한 발견이다.** `smoke_eval_review.md`가 Pilot에서 짚었던 "`p002_c04_3`
(조건 누락)은 두 모델 다 놓친 진짜 어려운 케이스"라는 관찰이 우연이 아니었다 — 완전히 새로운
상품 2개에 새로 주입한 condition_omission이 **2건 모두** 그대로 통과됐다(false accept). AND로
묶인 복합 조건 중 일부만 인용해서 claim을 만들면, Qwen은 "claim이 말한 부분은 evidence와
일치한다"에 꽂혀서 **명시되지 않은 나머지 조건이 생략됐다는 사실 자체를 못 잡는 것**으로 보인다.
INSUFFICIENT↔UNSUPPORTED 경계 혼동과 별개로, **`condition_omission`은 Qwen의 두 번째 확정된
구조적 약점**으로 문서화한다.

### 같은 갭필 라운드에서 오히려 잘 잡은 유형들

| claim_id | 오류유형 | 결과 |
|---|---|---|
| p009_c02 | term_error (24개월 3.09%→3.06%, 0.03%p 차이) | **정답** |
| p006_c02 | exception_omission (만기별 예외 조항 누락) | **정답** |
| p026_c02 | exception_omission (최저금리 floor 조항 누락) | **정답** |
| p012_c02 | mutually_exclusive_ignored (스택 안 되는 걸 합산 주장) | **정답** |

흥미롭게도 "0.03%p 차이"처럼 숫자만 미세하게 다른 term_error는 오히려 정확히 잡았다 —
`condition_omission`이 어려운 이유는 숫자 비교의 정밀도 문제가 아니라, **"명시된 조건 외에
다른 조건이 더 있는지"를 evidence 전체와 대조해야 하는 다른 종류의 추론**이기 때문으로 보인다.

### false accept 1건 (기존 Pilot 패턴)

`p008_c02`(boundary_condition_error) — 요구불평잔 구간(300만원=0.1%, 500만원=0.2%)에서 낮은
구간에 높은 구간의 우대율을 붙인 claim을 SUPPORTED로 잘못 승인. Pilot에서도 반복 관찰된
"체급 문제"(구간/숫자 미세 비교에 약함) 패턴과 일치.

### INSUFFICIENT 2건 중 1건 오답 — 확정 약점 재확인

| claim_id | 실제 라벨 | 예측 | 비고 |
|---|---|---|---|
| p024_c01 | INSUFFICIENT | **INSUFFICIENT (정답)** | spcl_cnd가 `None`(진짜 데이터 없음) |
| p017_c02 | INSUFFICIENT | UNSUPPORTED (오답) | evidence_mismatch(cross-field) — claim은 mtrt_int 얘기인데 evidence는 spcl_cnd만 줌 |

`p017_c02`는 완전히 새로운 claim 유형(cross-field mismatch)인데도 Qwen의 기존
INSUFFICIENT↔UNSUPPORTED 경계 혼동이 그대로 나타났다 — 이 약점이 특정 claim 문구에 국한된
우연이 아니라는 근거가 하나 더 늘었다.

### false reject: 0건

Pilot에서 관찰됐던 과잉 거부 패턴은 Test에서 재현되지 않았다.

## 참고용 크로스모델 체크 — condition_omission도 INSUFFICIENT처럼 보편적인 약점인가

INSUFFICIENT↔UNSUPPORTED 혼동이 모델 체급과 무관하게 나타났던 것처럼, `condition_omission`도
그런지 확인하기 위해 같은 53건 Test 셋을 Claude Haiku 4.5·Nemotron Ultra 550B에도 그대로
돌렸다(Sonnet은 이번엔 비용 대비 실익이 적다고 판단해 제외). `src/eval/run_eval_claude.py`,
`src/eval/run_eval_nvidia.py`도 `--split test`를 읽도록 같이 파라미터화했다.

| claim_id | 오류유형 | Qwen | Haiku | Nemotron Ultra |
|---|---|---|---|---|
| p020_c02 | condition_omission | MISS | **OK** | MISS |
| p034_c02 | condition_omission | MISS | MISS | MISS |
| p017_c02 | evidence_mismatch(INSUFFICIENT) | MISS | OK | OK |
| p024_c01 | missing_information(INSUFFICIENT) | OK | OK | MISS |
| p025_c01 | fabricated_condition | OK | MISS | OK |
| p008_c02 | boundary_condition_error | MISS | OK | OK |

| 지표 | Qwen | Haiku | Nemotron Ultra |
|---|---|---|---|
| False Accept Rate | 0.1071 | 0.0714 | 0.1071 |
| UNSUPPORTED Recall | 0.8846 | 0.8462 | 0.8846 |
| Macro F1 | 0.8434 | 0.8098 | 0.8434 |

**`condition_omission`은 3개 모델·6회 시도 중 단 1건(Haiku의 `p020_c02`)만 맞혔다** —
INSUFFICIENT 혼동보다도 더 보편적으로 나타나는 약점이다. Qwen(4B)과 Nemotron Ultra(550B)는
이 유형에서 완전히 동일하게 0/2를 기록했다 — 파라미터 130배 차이가 나는 두 모델이 정확히 같은
지점에서 같은 실수를 한다는 건, 이게 "작은 모델이라 놓친다"는 체급 문제가 아니라 **"AND로 묶인
복합조건 중 일부만 언급됐을 때, 언급 안 된 나머지 조건이 있는지"를 evidence 전체와 대조하는
이 특정 추론 자체가 구조적으로 어렵다**는 뜻으로 읽는다. Haiku만 유일하게 `p020_c02` 하나를
잡았는데, 이것만으로 "Haiku는 이 유형을 안다"고 결론짓기엔 표본(2건)이 너무 작다.

전체 지표는 Qwen과 Nemotron Ultra가 공교롭게도 완전히 동일하게 나왔다(FAR·Recall·F1 전부
일치) — condition_omission 2건을 똑같이 놓치고 다른 몇 건에서 갈린 게 서로 상쇄된 우연으로,
"두 모델의 전반적 성능이 동급"이라는 의미로 확대 해석하지는 않는다.

## 결론

- **모델/프롬프트 선정은 그대로 유지한다** — Qwen3.5-4B-int4-AutoRound + v2 프롬프트가
  엣지 케이스를 제대로 채운 unseen 데이터에서도 Pilot과 비슷하거나 나은 지표를 유지했다.
- **확정 약점이 두 가지로 늘었다**: ① INSUFFICIENT↔UNSUPPORTED 경계 혼동(Qwen뿐 아니라
  Nemotron Ultra·Gemma-4-31B도 유사 — `smoke_eval_review.md` 참고, 모델 체급과 무관한 태스크
  고유의 한계로 판단), ② **`condition_omission`(AND 복합조건 중 일부만 인용하면 못 잡음)** —
  이번 크로스모델 체크(Qwen 0/2, Haiku 1/2, Nemotron Ultra 550B 0/2)로 이것도 모델 체급과
  무관한 태스크 고유의 한계임이 확인됐다. 둘 다 #15 최종 리포트에 "알려진 한계"로 명시한다.
- **issue #23 완료** — Test(unseen) eval까지 끝났으니 #15(최종 결과 정리)로 넘어간다.
