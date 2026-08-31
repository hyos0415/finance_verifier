# 최종 결과 정리 — 금융 답변 검증용 Verifier SLM (#15)

> ⚠️ **면책**: 이 프로젝트(및 산출물)는 소형 LLM이 금융 답변의 근거 관계를 얼마나 잘 판정하는지
> 검증하는 **연구/평가 목적의 산출물**이다. 여기서 다루는 상품 정보·판정 결과는 실제 금융 상담이나
> 투자/예금 의사결정의 근거로 쓰기 위한 것이 아니다. **정확한 상담은 반드시 해당 금융회사 영업점
> 방문 또는 전문 상담사와의 상담을 통해 확인해야 한다.** (근거: 원본 공시 데이터 자체가 조건 서술을
> 다의적으로 남겨두는 경우가 있어, Verifier가 아무리 정확해도 100% 확신 있는 판정이 불가능한
> 사례가 존재한다 — §9 참고.)

## 0. 핵심 질문 (재확인)

대형 LLM이 생성한 금융 답변을 매번 대형 모델로 검증하는 건 비용·지연이 크다. 제한된 역할의
3~4B급 로컬 모델을 검증 전용 서브에이전트로 써서, 잘못되거나 근거 부족한 금융 답변을 얼마나
안정적으로 차단할 수 있는가 — 그리고 그 성능·한계가 정확히 어디에 있는가.

## 1. 시스템 설계

```
금융 답변 → Claim Decomposer(Claude API) → Atomic Claim(s) → Verifier SLM(Qwen, 로컬 vLLM)
          → SUPPORTED | UNSUPPORTED | INSUFFICIENT
```

### 1.1 Claim Decomposer — 무엇을 판정할지 정하는 단계

**왜 문장 단위가 아니라 atomic claim인가.** 금융 답변 한 문장에는 사실이 여러 개 섞여 있다
("12개월 기본금리는 3.2%이고, 조건 두 개를 모두 채우면 3.5%까지 올라간다"). 이걸 통째로
판정하면 verdict 하나에 "숫자는 맞는데 조건은 틀렸다"를 담을 수 없다. 그래서 답변을 먼저
**독립적으로 검증 가능한 사실 단위**로 쪼갠 뒤 각각을 판정한다.

**이 단계는 upstream 설계다.** 분해 단위가 잘못되면 Verifier 성능 측정 자체가 달라진다.
실제로 Pilot 단계에서 이 문제를 겪었다.

| | 내용 |
|---|---|
| 초기 문제 | 분해된 claim이 앞선 문장을 가리키는 지시어("두 조건", "해당")를 남기거나, 숫자에 붙어 있던 시점 조건("1개월 이내")을 빠뜨렸다 |
| 왜 치명적인가 | Verifier는 claim을 하나씩 독립적으로 본다. claim 안에 없는 정보를 참조하는 문장은 **어떤 Verifier도 원리적으로 판정할 수 없다** — 프롬프트를 아무리 고쳐도 못 푼다 |
| 수정 | Decomposer 프롬프트에 self-containment를 금지 규칙으로 명시 — 지시어 사용 금지(지칭 대상을 풀어쓸 것), 숫자에 붙은 조건(시점·자격·상품 종류)을 분리하지 말 것 |
| 영향 | 같은 답변에서 claim 29개 → 27개로 병합. **평가셋의 규모와 구성 자체가 바뀌었고, 그 전에 뽑은 eval 결과는 전부 무효 처리했다** |

여기에 규칙 기반 sanity check 두 가지를 붙였다 — **Atomicity**(claim 하나에 사실 판단이
하나인가), **Coverage**(원본 답변의 숫자가 분해 결과에 다 살아있는가).

Decomposer는 로컬 SLM이 아니라 **Claude Sonnet 5 API**로 돈다. 8GB GPU에 Verifier와 분해기를
동시에 올릴 여유가 없고, Verifier 후보 모델의 분해 성능은 검증된 바가 없어 역할을 분리했다
(`src/decomposition/claim_decomposer.py`).

### 1.2 Verifier

`(Evidence, Atomic Claim)` 쌍을 받아 **새 답변을 생성하지 않고** 근거 관계만
`{verdict, evidence, reason}` JSON으로 판정한다. 판정 경계는 세 가지로 고정했다 —
SUPPORTED(직접 뒷받침), UNSUPPORTED(명시적 충돌), INSUFFICIENT(판단 정보 자체가 없음:
충돌이 아니라 부재). vLLM의 guided decoding으로 스키마를 강제하고, 같은 스키마의 Pydantic
모델로 다시 검증해 Schema Valid Rate를 측정한다
(`src/verifier/client.py`, `src/verifier/schemas.py`).

### 1.3 서빙

WSL2 + Docker Desktop(WSL2 backend) + `vllm/vllm-openai` 공식 이미지 → OpenAI 호환
엔드포인트. RTX 4070 Laptop 8GB 단일 GPU.

## 2. Evaluation Dataset 구축

이 프로젝트에서 가장 손이 많이 간 작업이다. Verifier를 **무엇으로 판별할지**를 정하는 일이라,
결과의 신뢰도가 여기서 결정된다. 전체 과정은
[`results/dataset/eval_dataset_construction.md`](../dataset/eval_dataset_construction.md)에
별도로 정리했고, 여기서는 설계 결정만 요약한다.

```
금감원 Finlife API (은행권 정기예금)
  → canonical product schema 정규화
  → 상품 데이터 profiling (어떤 필드가 어떤 오류를 만들기 좋은가)
  → 실제 발생 가능한 failure type 정의 (taxonomy)
  → 오류 주입 시나리오 설계 → synthetic 답변 생성
  → Claim Decomposition → self-containment 검토
  → gold label 확정 → 사람 검수
  → Pilot / held-out Test 분리 (선정 단계에 미사용)
```

### 2.1 원천 데이터와 정규화

소스는 금융감독원 "금융상품 한눈에" Open API, **은행권 정기예금 단일 상품군**이다.
`baseList`/`optionList`를 상품 식별자로 join해 canonical record로 정규화하고, 자연 결측
표기(`"해당사항 없음"`/`"없음"`/`"해당없음"`)를 `null`로 통일했다. 이 정규화가 중요한 이유는
**결측 처리 방식이 곧 INSUFFICIENT 라벨의 공급원**이기 때문이다 — placeholder 문자열을 값으로
두면 "정보 없음"을 "정보 있음"으로 착각한다
([`normalization/canonical_products_review.md`](../normalization/canonical_products_review.md)).

이어서 프로파일링으로 "어떤 필드가 어떤 오류 유형을 만들기 좋은가"를 확인했다. 우대조건
(`spcl_cnd`)은 AND/OR 표지와 문장당 평균 3.53개의 숫자 조건을 담고 있어 논리 조건 오류의 주
소스, 만기후이자(`mtrt_int`)는 구간·경계값 추론의 별도 축이라는 결론이 나왔다
([`profiling/eval_design_review.md`](../profiling/eval_design_review.md)).

### 2.2 합성 Claim 생성 원칙

**왜 합성인가.** 한국어 금융 공시를 근거로 3분류(특히 INSUFFICIENT) 판정을 요구하는 공개
평가셋이 없다. 그리고 "정확도 몇 %"가 아니라 *어떤 오류를 못 잡는가*를 알아야 Verifier로
쓸지 판단할 수 있는데, 그러려면 오류 유형이 메타데이터로 붙어 있어야 한다.

원칙 네 가지로 만들었다.

1. **근거는 실제 데이터, 답변만 합성.** evidence는 실제 공시 필드에서 그대로 가져오고, 그
   근거에 대해 "대형 LLM이 답했을 법한" 답변을 합성한다. 근거까지 지어내면 평가가 현실과
   분리된다.
2. **랜덤 변형이 아니라 오류 유형을 정의해서 주입한다.** 초기 8종으로 시작해 공시 텍스트를
   읽으며 **13종으로 확장**했다.

   | 초기 설계 8종 | 구축 중 추가 5종 |
   |---|---|
   | `numeric_error`, `term_error`, `eligibility_error`, `condition_reversal`, `condition_omission`, `base_vs_max_rate`, `conditional_benefit_generalization`, `missing_information` | `boundary_condition_error`(구간 경계 오적용), `mutually_exclusive_ignored`(중복적용 불가 무시), `exception_omission`("단, ~제외" 삭제), `evidence_mismatch`(근거가 다른 항목), `fabricated_condition`(없는 조건 생성) |

   추론 성격(`reasoning_type`: `any_of`/`all_of`/`numeric_threshold`/`temporal_scope`/
   `mutually_exclusive`/`exception`/`cross_field_mismatch`)도 별도 축으로 붙여, 실패가 특정
   오류 유형이 아니라 특정 *추론 방식*에 몰리는지 볼 수 있게 했다.
3. **시나리오 하나에 오류 하나만.** 답변 하나가 claim 여러 개로 쪼개지므로, 주입한 오류가
   실린 claim만 오답 라벨을 받고 나머지는 evidence를 충실히 반영한 SUPPORTED가 된다.
4. **INSUFFICIENT는 지어내지 않는다.** 원본 공시에 실제로 우대조건 정보가 없는 상품
   (`natural_missing`)만 쓴다. 인위적으로 근거를 지워 만든 결측은 실제 분포를 왜곡한다.

### 2.3 Gold label과 품질 검수

라벨 부여는 `error_match`(주입 오류를 식별하는 대체 표현 목록)로 자동화하되, **하나도 안
걸리면 추측하지 않고 `needs_manual_review`로 표시**해 사람에게 넘긴다. 최종본에서 이 플래그는
Pilot 64건·Test 53건 모두 0건이다.

자동 체크(Atomicity/Coverage)는 최종본에서 100% 통과지만, **정작 결과를 바꾼 결함 3건은 사람이
대조하다 발견했다.**

| 발견한 문제 | 조치 |
|---|---|
| **self-containment 결함** — 판정 자체가 불가능한 claim | Decomposer 프롬프트 수정 후 재분해(§1.1), 기존 eval 결과 무효 처리 |
| **단위 불일치** — 근거는 "20백만원", 시나리오 지시문은 "2천만원"(같은 금액, 다른 표기) | 데이터 버그로 분리. 안 잡았으면 두 모델 공통 오답이 "모델 실패"로 집계될 뻔했다 |
| **오라벨 의심 1건** — Pilot에서 두 모델이 함께 틀린 문항 재검토 | 실제로 라벨이 맞았던 건은 재검토 대상에서 제외, 라벨이 틀린 1건은 제거해 **65 → 64건 확정** |

교훈은 단순하다 — **모델이 틀린 문항은 먼저 데이터를 의심한다.**

### 2.4 Pilot / held-out Test 분리와 leakage 방지

| split | 용도 | claim 수 | 시나리오 | 상품 | 저자 |
|---|---|---|---|---|---|
| Smoke | 파이프라인 1바퀴 확인 (모델 우열 판단 X) | 10 answers → 29 claims | – | – | Claude Code |
| **Pilot** (저장 위치: `data/smoke/claim_dataset.json`) | 모델·프롬프트 선정 | **64** | 23 | 11 | Claude Code |
| **Test (held-out)** | 최종 성능 확인 (선정 후 최초 1회 실행) | **53** | 53 | 28 | Claude Code 28 + Codex 25 |

leakage를 막기 위한 장치가 두 가지다.

- **(상품, 근거 필드) 쌍이 완전히 분리돼 있다.** 상품 5개가 양쪽에 등장하지만 참조 필드가
  하나도 겹치지 않는다 — Pilot에서 우대조건을 쓴 상품은 Test에서 만기후이자를 쓰는 식이다.
  근거 텍스트 기준으로 중복 문항이 없고, 나머지 23개 상품은 Test에서 처음 등장한다.
- **저자를 분리했다.** Test 53건 중 25건은 다른 에이전트(Codex)가 작성했다. 한 사람이 양쪽을
  다 쓰면 같은 문체·같은 함정 패턴이 반복돼 held-out의 의미가 약해진다. 라벨 검수는 사람이
  양쪽 모두 전수 확인했다.

라벨 분포는 Pilot(SUPPORTED 46 / UNSUPPORTED 14 / INSUFFICIENT 4), Test(25 / 26 / 2)다.

## 3. 모델·프롬프트 선정 — Qwen3.5-4B-int4-AutoRound 단독 확정

아래는 **선정 당시 측정값**이다 — CUDA Graph 적용 전(eager) 경로 기준이며, §4의 최종 수치와
경로가 다르다.

| 후보 | 정밀도/경로 | Pilot FAR | Pilot UNSUPPORTED Recall | Pilot Macro F1 | Pilot latency p50/p95 |
|---|---|---|---|---|---|
| **Qwen3.5-4B-int4-AutoRound** (확정) | INT4 AutoRound → vLLM | **0.1111** | **0.8571** | 0.6656 | 7.83s / 17.05s |
| Kanana-2-3B-instruct (비교 후 탈락) | BF16 → vLLM | 0.2222 | 0.7143 | **0.7652** | **3.90s / 7.81s** |

> 문항 수가 정확히 같지는 않았다 — Qwen은 오라벨 1건을 제거하기 전인 **65건**, Kanana는 정리된
> **64건**에서 측정됐다. #28에서 동일한 64건으로 Qwen을 다시 돌린 결과 FAR이 0.1111로 같아
> 선정 근거는 그대로 유지된다.

**결정 근거**: CLAUDE.md 지표 우선순위 1순위인 False Accept Rate(FAR)·2순위 UNSUPPORTED
Recall 모두 Qwen이 우세. Kanana가 Macro F1과 latency에서 우세하지만, "틀린 걸 맞다고
승인"하는 실패가 "맞는 걸 틀렸다고 거부"하는 것보다 훨씬 위험한 금융 Verifier 맥락에서는
FAR을 우선한다.

> **이 선택에는 지표가 보여주지 않은 교환이 있었다(#28에서 확인).** Kanana는 Pilot의
> INSUFFICIENT 4건을 **4/4** 맞혔고, **Qwen은 선정 당시 1/4·현재 채택 설정에서는 0/4**다. FAR 한 지표에서 이기고 판정 클래스 하나를
> 통째로 잃은 셈인데, 당시 우선순위 지표에는 그 손실이 드러나지 않았다. Macro F1이 사실상 그
> 신호였지만(Kanana 0.7652 vs Qwen 0.6656) "보조 지표"로 밀려 있었다 — §6·§9 참고.

**그럼 Qwen 선택을 유지할 수 있나 — 이 프로젝트의 위험 우선순위에서는 유지한다.**

먼저 엄격하게 짚으면, 채택 설정의 Qwen은 **완전한 3분류 Verifier라고 부르기 어렵다**(SUPPORTED
Recall 1.000 / UNSUPPORTED 0.8846 / **INSUFFICIENT 0.000**, §5.1). 요구사항이 "세 판정을 정확히
구별한다"였다면 탈락시키는 게 맞다.

그런데 이 프로젝트가 명시적으로 우선한 위험은 다르다 — **잘못된 금융 정보를 승인하는 False Accept의
최소화**다. 그 기준 아래에서는 유지할 근거가 있다.

1. **Qwen이 INSUFFICIENT를 놓칠 때 100% UNSUPPORTED로 간다**(Pilot 4/4, Test 2/2). SUPPORTED로
   간 적이 한 번도 없다. 즉 잃은 클래스의 실패 양상은 "위험한 승인"이 아니라 **보수적 차단**이다 —
   안전을 해치지 않고 utility만 깎는다.
2. **빈도가 다르다.** 부정 라벨은 Pilot 64건 중 18건(28%)인데 INSUFFICIENT는 4건(6%)이다. Kanana는
   자주 등장하는 위험 케이스에서 2배 더 승인하고(FAR 0.2222), Qwen은 드문 케이스에서 보수적으로
   틀린다. 정확도도 Qwen이 높다(0.8438 vs 0.7969).

**다만 당시 근거는 불완전했다.** Macro F1을 "보조 지표"로 넘긴 판단이 결과적으로 나쁘지 않았던 건
그 지표를 *대표 지표*로 쓰기 어려워서였지(§9), 거기 담긴 신호가 무의미해서가 아니었다. 실제로 그
신호는 "Qwen이 클래스 하나를 버렸다"를 정확히 가리키고 있었다. 지금이라면 클래스별 recall을 함께
보고 **"INSUFFICIENT를 포기하는 교환"을 명시적으로 승인**했을 것이다 — 결과는 같아도 그게 정직한
절차다.

Qwen의 latency 열세는 설정 실수가 아니라 실측으로 확인된 구조적 현상이다 — Qwen3.5의
GDN(Gated DeltaNet) 하이브리드 레이어가 vLLM/Triton에서 아직 Kanana의 FlashAttention2
경로만큼 성숙하게 최적화되어 있지 않다(`results/model_selection/qwen_latency_diagnosis.md`).
이후 #25에서 이 열세를 서빙 설정만으로 3.6배 줄였다 (§7).

프롬프트는 v1(baseline) → v2(reason 길이 제약 추가)를 채택하고, INSUFFICIENT 경계를 겨냥한
두 개선안은 모두 기각했다. 채택/기각 판단 기준과 근거는 §6에 표로 정리했다. **v2로 Test
단계까지 고정**했다(Langfuse `verifier-system-prompt` production label).

## 4. 최종 결과 — Test(held-out, 53건)

Test는 **모델·프롬프트 선정에 사용하지 않은 held-out 셋**이다 — 선정을 확정한 뒤 1회 평가했고,
이후 #28의 감사 실험에 재사용됐다(§9 참고).

**아래 수치는 그 셋을 최종 채택 서빙 경로(CUDA graph, `--max-num-seqs 4`)에서 측정한 값이다.**
선정 당시의 최초 평가는 CUDA Graph 적용 전(eager) 경로에서 이뤄졌으므로, 두 경로가 다른 지표는
아래 "서빙 경로에 따른 차이"에서 따로 비교한다. 선정에 쓰이지 않았다는 성질 자체는 그대로다.

| 지표 (채택 경로 기준) | Pilot(64) | **Test(53, held-out)** |
|---|---|---|
| False Accept Rate ↓ | 0.1111 | **0.1071** |
| SUPPORTED Recall ↑ (정상 claim 인식) | 0.9130 | **1.0000** |
| UNSUPPORTED Recall ↑ | 0.8571 | **0.8846** |
| INSUFFICIENT Recall ↑ | 0.0000 | **0.0000** |
| Macro F1 (참고) | 0.5464 | **0.6151** |
| Accuracy (참고) | 0.8438 | **0.9057** |
| Schema Valid Rate | 1.0 | **1.0** |
| Latency p50 / p95 | 2.23s / 4.69s | **2.55s / 3.97s** |

Pilot 튜닝에 쓰이지 않은 데이터에서도 핵심 지표(FAR·UNSUPPORTED Recall)가 유지되거나 소폭
개선됐다 — 모델/프롬프트 선정이 Pilot 64건에 과적합된 결과가 아니라는 근거다.

(참고: 최적화 전 eager 경로의 latency는 Pilot 7.83s / 17.05s, Test 9.08s / 14.27s였다. 개선 경위는 §7.)

### 서빙 경로에 따른 차이 — 이전 판본의 Macro F1 정정

이전 판본은 Test Macro F1을 **0.8434**로 적었는데, 그건 CUDA Graph 적용 전(eager,
`max-model-len 2048`) 값이고 **채택 설정에서는 측정된 적이 없었다**(#28에서 확인·정정).

차이는 verdict **한 건**에서 온다. `p024_c01`(gold=INSUFFICIENT)이 eager에서는 정답이었는데 CUDA
graph에서는 UNSUPPORTED로 뒤집혔다. 클래스별로 분해하면 왜 한 건이 macro 평균을 0.22나 움직이는지
보인다.

| 경로 | SUPPORTED F1 | UNSUPPORTED F1 | INSUFFICIENT F1 | **Macro** | Accuracy |
|---|---|---|---|---|---|
| eager (이전 판본 기준) | 0.9434 | 0.9200 | **0.6667** | 0.8434 | 0.9245 |
| CUDA graph (**채택**) | 0.9434 | 0.9020 | **0.0000** | 0.6151 | 0.9057 |

Test의 INSUFFICIENT는 2건뿐이라 그 클래스 F1이 0.6667 → 0으로 무너지면 3클래스 macro 평균이 그대로
끌려간다. 정확도로는 1/53건(1.9%p) 차이다. **희소 클래스가 있는 3분류에서 Macro F1은 단일 verdict에
지배당한다** — 이 프로젝트가 Macro F1을 보조 지표로 둔 게 결과적으로 나쁘지 않았지만, 이유는 "보조라서"가
아니라 **표본이 이 지표를 지탱하지 못해서**였다.

이 차이는 실행 노이즈가 아니다. 같은 설정으로 두 번 돌리면 verdict도 reason 텍스트도 완전히
동일하다(#28에서 확인).

## 5. Failure Analysis — 두 가지 약점, 그리고 감사로 뒤집힌 해석

이 절은 #28 자체 감사에서 **결론이 두 번 바뀐** 부분이다. 처음 판본은 두 약점을 모두 "태스크
자체의 구조적 한계"로 결론지었는데, 4개 모델에 같은 실험을 돌려보니 **둘 다 그렇게 부를 수 없었다.**

### 5.1 약점 ① — INSUFFICIENT를 사실상 출력하지 않는다 (모델별 특성)

채택 설정의 Qwen은 **Pilot·Test 6개 실행 전부에서 INSUFFICIENT verdict를 한 번도 내지 않았다.**
경계를 헷갈리는 게 아니라 세 번째 클래스에 도달하지 않는, 사실상 2분류기다.

처음 판본은 이걸 "모델 체급 무관한 태스크 구조적 한계"로 결론지었다. **틀렸다.** 같은 프롬프트·같은
데이터로 전체 모델을 보면 이렇다.

| INSUFFICIENT 정답률 | Pilot (4건) | Test (2건) |
|---|---|---|
| **Kanana-2-3B (로컬 3B — 탈락시킨 후보)** | **4/4** | – |
| Claude Haiku 4.5 | **4/4** | **2/2** |
| Claude Sonnet 5 | **4/4** | – |
| Nemotron Ultra 550B | 0/4 | 1/2 |
| Gemma-4-31B | 1/4 | – |
| **Qwen3.5-4B (채택)** | **0/4** | **0/2** |

**3B 로컬 모델이 만점을 받는 태스크를 "태스크 구조적 한계"라고 부를 수 없다.** 정확한 서술은
**"Qwen(및 Nemotron·Gemma)의 모델별 실패"**다. 원래 판본은 실패한 두 모델만 인용해 일반화했다.

부수적으로, INSUFFICIENT gold를 UNSUPPORTED로 판정하면 FAR 정의상 "오승인 아님"으로 집계된다 —
**이 실패는 1순위 지표에 전혀 잡히지 않는다.**

### 5.2 약점 ② — 조건의 논리 구조와 적용 범위를 잡지 못한다

AND로 묶인 조건 중 일부만 인용한 claim을 "언급된 부분은 evidence와 일치한다"며 승인한다. Test에서
신규 주입 2건을 모두 놓쳤다(기존 `condition_omission`).

처음 판본은 "프롬프트로 해결되지 않는다"고 적었다. 그런데 **그 문장은 INSUFFICIENT 경계에서만 두 번
검증된 것**이었고, 완전성 규칙은 시도된 적이 없었다. #28에서 실제로 넣어봤다.

- **규칙 1문장(v7)**: Qwen Test에서 **verdict 변화 0건.**
- **절차형 규칙(v8)**: Qwen Test에서 **`condition_omission` 2/2 교정, FAR 0.** 목표는 달성됐다.

**그런데 효과가 모델 능력에 종속된다.**

| 모델 | v2 → v8 정확도 | 판정 |
|---|---|---|
| Sonnet 5 | 0.9062 → **0.9344** | 개선 (FAR 0, UNSUP Recall 1.0). 단 Schema Valid 1.0 → 0.953 |
| Nemotron 550B | 0.9245 → **0.9623** (Test) | 개선 |
| Haiku 4.5 | 0.9057 → **0.8491** (Test, FAR도 악화) | 악화 |
| **Qwen3.5-4B (채택 모델)** | 0.8438 → **0.6875** (Pilot) | **붕괴** |

Qwen이 Pilot에서 정상 claim **11건**을 새로 거부했다(정상 claim 인식률 0.913 → 0.674). 그 케이스를
보면 원인이 분명하다.

| 거부된 정상 claim | 모델이 든 이유 | 실제 구조 |
|---|---|---|
| `p004_c01` | 다른 우대조항(0.05%p)을 안 썼다 | **별개 혜택**에 속한 조건 — 대조 대상이 아님 |
| `p020_c01` | 조건을 일부만 언급했다 | claim은 두 조건을 **모두** 언급했다(오독) |
| `p034_c01` | 조건을 다 안 썼다 | **ANY_OF**(택일)를 ALL_OF로 간주 |

**정정된 결론**: 약점은 "누락을 못 본다"가 아니라 **"evidence에 나열된 조건이 ALL_OF인지 ANY_OF인지,
어느 혜택에 속하는지 범위(scope)를 잡지 못한다"**이다. 절차형 규칙은 그 판별 능력을 **요구**할 뿐
제공하지 않는다 — 그래서 판별할 수 있는 모델에서는 개선이 되고, 못 하는 모델에서는 무차별 적용돼
과잉거부가 된다.

실패 방향도 모델마다 다르다. **Qwen은 UNSUPPORTED로**, **Haiku는 INSUFFICIENT로** 쏠린다(Test 변화
4건 중 3건, Pilot 악화 5건 전부). 즉 이 규칙은 "누락 탐지 기능"이 아니라 **방향이 모델에 종속된
엄격함 교란**이다.

정확한 요약은 **prompt-solvable이 아니라 "프롬프트로 통제는 되지만 안정적이지 않다"**이다.
production 프롬프트는 v2로 유지했다(§6).

**→ 후속 과제로 이어지는 지점**: 판별 능력을 *요구*하는 대신 조건의 논리 구조(ALL_OF/ANY_OF/적용 범위)를
**입력으로 제공**하면, 작은 모델에서도 과잉거부 없이 누락을 잡을 수 있는가? (§11)

케이스별 기록은 [`results/eval/test_eval_review.md`](../eval/test_eval_review.md), 감사 실험 전체는
[`results/eval/precondition_audit.md`](../eval/precondition_audit.md)에 있다.

## 6. Regression Evaluation — 변경을 어떤 기준으로 채택했나

이 프로젝트에서 변경(프롬프트·파이프라인·서빙 설정)은 감으로 채택하지 않았다. 원칙은 하나다.

> **변경 → 같은 Eval Set 재실행 → 목표 지표가 개선됐는가 + 기존 정답이 훼손되지 않았는가 →
> 채택/기각**

두 번째 조건이 핵심이다. "목표 지표만 좋아졌다"로 채택하면 다른 곳이 깨진 걸 못 본다.

| 변경 | 목표 | 검증 셋 | 결과 | 결정 |
|---|---|---|---|---|
| **Decomposer self-containment 수정** | 판정 불가능한 claim 제거 | Smoke | claim 29 → 27로 병합, 지시어 잔존 0건. 기존 eval 결과는 전부 무효 처리 | **채택** |
| **프롬프트 v2** (Langfuse v2) | `reason` 장문 출력으로 스키마가 깨지는 문제 해결 | Pilot | latency 개선 + Qwen 정확도 개선, Kanana 예측 불변 | **채택** |
| **프롬프트 v3** (Langfuse v3) — 판정 순서 규칙 + 예시 1개 | INSUFFICIENT 인식 개선 | Pilot 64 | INSUFFICIENT 4/4로 목표는 달성. 그러나 **기존에 맞히던 UNSUPPORTED가 흔들림** (Kanana Macro F1 0.7652→0.5714, Qwen FAR 0.111→**0.1667**) | **기각** |
| **프롬프트 v4** (Langfuse v5) — 짧은 부정 규칙만 추가 | 같은 목표를 과적합 없이 | Pilot 64 | FAR·Recall은 동일하게 유지됐지만 **목표 지표가 오히려 악화** (Qwen INSUFFICIENT 1/4→0/4, Macro F1 0.6656→0.5464) | **기각** |
| **CUDA Graph 활성화** (`--max-num-seqs 4`) | batch=1 decode 처리량 | Test 53 전체 재실행 | 12.4 → **44.3 tok/s(×3.6)**, 핵심 지표 동일(FAR 0.1071 / Recall 0.8846), verdict 변화 1건 | **채택** |
| **완전성 규칙 1문장** (#28, v7) | `condition_omission` 탐지 | Test 53 | **verdict 변화 0건.** 판정이 하나도 안 움직임 | **기각**(무효과) |
| **절차형 완전성 규칙** (#28, v8) | 같은 목표를 더 명시적으로 | Pilot 64 + Test 53, **4개 모델** | 채택 모델 Qwen에서 `condition_omission` 2/2 교정·FAR 0이지만 **정상 claim 인식률 0.913 → 0.674**(정확도 0.844 → 0.688). Sonnet·Nemotron에서는 오히려 개선 — **효과가 모델 능력에 종속**(§8.2) | **기각**(채택 모델에서 utility 붕괴) |

INSUFFICIENT 경계를 노린 기각 두 건(v3·v5)에서 얻은 결론이 §5 약점 ①의 근거가 된다 —
**접근이 정반대인 두 시도(절차+예시 추가 vs 짧은 부정 규칙만)가 모두 실패했다면, 그건 프롬프트
문구 문제가 아니라 태스크 자체의 문제다.**

약점 ②는 다르다. #28에서 처음으로 완전성 규칙을 시도했더니 **잡히긴 잡혔다**(Test 2/2). 기각 사유가
"불가능"이 아니라 **"utility 비용이 크다"**로 바뀌었다는 뜻이다.

### 이 회귀 표가 못 잡는 것 — 지표에 utility 제약이 없다

v8은 1·2순위 지표(FAR·UNSUPPORTED Recall)가 **양쪽 split 모두에서 개선**됐는데, 실제로는 정상 claim
인식률을 0.913 → 0.674로 떨어뜨린 변경이다. 우선순위 지표만 보고 판단했다면 명백한 악화를 "개선"으로
승인했을 것이다. 같은 이유로 `"UNSUPPORTED"`만 반환하는 상수 스텁도 1·2·3순위를 전부 이긴다.

문제는 FAR을 1순위로 둔 것 자체가 아니라 **그 짝이 되는 지표가 우선순위 안에 없다는 것**이다.
순위제 대신 제약식이 맞다.

> **FAR을 목표치 이하로 낮추되, 정상 claim 거부율(FRR)을 기준선 대비 악화시키지 않는다.**

같이 보는 scorecard: FAR · UNSUPPORTED Recall · **SUPPORTED Recall(FRR)** · INSUFFICIENT Recall ·
Macro F1 · Accuracy · 95% CI. 틀린 답변을 전부 막으려고 정상 답변까지 차단하는 검증기는 안전하지만
쓸 수 없다 — 이번 실험이 그걸 실제 수치로 보여줬다.

이번 프로젝트에서는 **지표 정의를 사후에 바꾸지 않았다.** 이미 보고된 수치의 기준을 소급 변경하면
그게 더 나쁘기 때문이고, 대신 §9에 한계로 기록하고 후속 프로젝트의 설계 원칙으로 넘긴다.

회귀 판정을 가능하게 한 조건은 두 가지였다. (1) Pilot 셋이 고정돼 있어 변경 전후를 같은 기준
으로 비교할 수 있었고, (2) 모든 호출이 Langfuse에 trace로 남아 어떤 프롬프트 버전이 어떤 결과를
냈는지 사후에 되짚을 수 있었다(§10).

## 7. Inference 최적화 (#25) — CUDA Graph로 3.6배

| 설정 | batch=1 decode 처리량 |
|---|---|
| 기존(`--enforce-eager`) | ~12.4 tok/s |
| CUDA Graph 활성화(`--max-num-seqs 4 --max-model-len 1024`, eager 제거) | **~44.26 tok/s (+3.6배)** |

기본 `max_num_seqs=256`는 GDN 하이브리드의 Mamba cache block 예산(8GB에서 64개뿐)을 초과해
서버가 부팅조차 못 한다 — 서빙 용량을 워크로드 실제 크기로 낮춰서 CUDA Graph capture를
가능하게 한 것이 유일하게 효과가 있었던 레버였다(`--gdn-prefill-backend`/`--use-replayssm`/
`--performance-mode`/`--optimization-level`은 이 GPU 세대·아키텍처 조합에서 선택지가
아니었거나 측정 가능한 효과가 없었음).

- **정확성 회귀 없음**: Test 53건 전체 재실행 결과 핵심 지표(FAR 0.1071, Recall 0.8846) 동일,
  1건(`p024_c01`)만 eager↔CUDA-graph 부동소수점 차이로 verdict가 바뀌었으나 이미 알려진
  INSUFFICIENT/UNSUPPORTED 경계 문제와 같은 케이스.
- **최종 서빙 설정**: `max-num-seqs=4` 채택 — 답변 1개가 여러 atomic claim으로 쪼개지는 실제
  워크로드(Test 평균 1.89개/answer, Smoke 평균 5.82개)를 감안. k6 부하테스트(VU 1/4/8/16)로
  용량 초과 시에도 에러 없이 큐잉만 늘어남을 확인.

상세: `results/latency/capability_and_results.md`(실험 로그), `results/model_selection/qwen_latency_diagnosis.md`(source of truth).

## 8. 크로스모델 대조 — 무엇이 모델 탓이고 무엇이 태스크 탓인가

Verifier 후보는 CLAUDE.md 방침상 로컬 3~4B급 SLM 2개로 제한했다. 대형 모델 실행은 **후보 선정에
영향을 주지 않고**, 두 가지 다른 질문에 답하기 위한 것이다 — (1) 이 태스크·프롬프트가 잘 짜여
있는가(상한선), (2) 관찰된 실패가 모델 탓인가 태스크 탓인가.

### 8.1 상한선 — 같은 프롬프트, 같은 Pilot 64건

| 지표 | Qwen(로컬) | Kanana(로컬) | Haiku 4.5 | Sonnet 5 | Nemotron 550B | Gemma-4-31B |
|---|---|---|---|---|---|---|
| FAR ↓ | 0.1111 | 0.2222 | **0.0000** | 0.0556 | **0.0000** | 0.0556 |
| SUPPORTED Recall ↑ | 0.9130 | 0.8043 | 0.8261 | **0.9348** | **0.9348** | **0.9348** |
| UNSUPPORTED Recall ↑ | 0.8571 | 0.7143 | 0.8571 | 0.7857 | 0.7857 | 0.7143 |
| INSUFFICIENT Recall ↑ | 0.0000 | **1.0000** | **1.0000** | **1.0000** | 0.0000 | 0.2500 |
| Accuracy | 0.8438 | 0.7969 | 0.8438 | **0.9062** | 0.8438 | 0.8438 |

**시사점**: 더 큰 모델은 같은 프롬프트로도 더 낮은 FAR을 뽑아낸다 — 로컬 후보의 성능은 "태스크
자체의 상한"이 아니라 "이 체급에서 감수하는 트레이드오프"다. 다만 **INSUFFICIENT 열은 체급 순서를
따르지 않는다** — 3B Kanana가 만점이고 550B Nemotron이 0점이다. §5.1의 근거가 여기 있다.

(latency는 로컬 GPU 서빙 vs hosted API 왕복이라 직접 비교 대상이 아니다.)

### 8.2 절차형 프롬프트(v8)를 4개 모델에 동일 적용

§5.2에서 쓴 실험이다. 같은 규칙이 모델에 따라 정반대 결과를 낸다.

| 모델 | split | FAR (v2 → v8) | SUPPORTED Recall | Accuracy | Schema |
|---|---|---|---|---|---|
| Sonnet 5 | Pilot | 0.0556 → **0.0000** | 0.9348 → 0.9070 | 0.9062 → **0.9344** | 1.000 → **0.953** |
| Nemotron 550B | Pilot | 0.0000 → 0.0000 | 0.9348 → 0.9130 | 0.8438 → **0.8750** | 1.000 |
| Nemotron 550B | Test | 0.1071 → **0.0357** | 1.0000 → 1.0000 | 0.9245 → **0.9623** | 1.000 |
| Haiku 4.5 | Test | 0.0714 → **0.1071** | 0.9600 → 0.8800 | 0.9057 → **0.8491** | 1.000 |
| **Qwen3.5-4B** | Pilot | 0.1111 → 0.0556 | 0.9130 → **0.6739** | 0.8438 → **0.6875** | 1.000 |

세 가지가 한 번에 보인다.

1. **능력 종속**: 조건의 논리 구조를 판별할 수 있는 모델(Sonnet·Nemotron)에서는 개선, 못 하는
   모델(Qwen)에서는 붕괴.
2. **실패 방향의 모델 종속**: Qwen은 UNSUPPORTED로, Haiku는 INSUFFICIENT로 쏠린다.
3. **스키마 비용**: Sonnet은 절차를 충실히 수행하다 출력이 길어져 3/64에서 JSON이 잘렸다
   (Schema Valid 0.953). 이 실패는 원래 하니스에서 잡히지 않고 실행이 중단됐는데, #28에서
   `schema_valid=False`로 집계하도록 고쳐서 측정됐다.

**이 절이 없었으면 "Qwen에서 안 되니 태스크가 어렵다"로 잘못 결론지었을 것이다.**

## 9. 알려진 한계 — 원본 데이터의 모호성

Verifier 파이프라인의 정확도와 별개로, **원본 공시 데이터 자체가 조건 서술을 다의적으로
남겨두는 경우가 있다.** 예: 한 상품(`p003`)의 우대조건은 두 요건 중 하나만 충족해도 "각 연
0.10%p"를 준다고 적혀 있는데, 이 "각"이 대시(-) 항목 단위인지 그 안의 하위 조건(OR) 단위인지
원문만으로 완전히 명확하지 않다
([`decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md) 참고).
Verifier가 아무리 정확하게 판정해도 원문 자체가 모호하면 100% 확신 있는 결론은 낼 수 없다 —
이건 파이프라인의 실패가 아니라 **소스 데이터의 한계**이며, 그래서 이 문서 최상단과
데모/서비스 단계 모두에 면책 문구를 명시한다.

### 지표 우선순위에 utility 제약이 없다

CLAUDE.md가 정한 우선순위(FAR > UNSUPPORTED Recall > Schema Valid Rate)에는 **정상 claim을 거부하는
실패를 재는 자리가 없다.** 그래서 상수 거부 스텁도, 실제 과잉거부 프롬프트(v8)도 상위 지표에서는
"개선"으로 읽힌다. 대안이 되는 제약식과 scorecard는 §6에 정리했다. **이번 프로젝트에서는 지표 정의를
사후에 바꾸지 않고 한계로만 기록한다.**

### Macro F1 — 진단 지표로는 유용하고, 대표 선택 지표로는 부적절하다

Macro F1이 잘못된 신호를 준 게 아니다. 오히려 **Qwen이 세 클래스 중 하나를 통째로 버렸다는 사실을
강하게 벌점으로 줬다** — 그 문제 제기는 타당했고 #28에서 §5.1을 찾는 실마리가 됐다.

| 모델 (Pilot) | FAR | Accuracy | **Macro F1** |
|---|---|---|---|
| Qwen3.5-4B | 0.1111 | 0.8438 | **0.5464** |
| Kanana-2-3B | 0.2222 | 0.7969 | **0.7652** |
(Qwen은 채택 서빙 경로, Kanana는 선정 당시 경로에서 잰 값이다. Qwen을 선정 당시 경로로 재도
FAR 0.1111 / Accuracy 0.8571 / Macro F1 0.6656으로 **역전은 그대로**다.)


**문제는 가중치다.** INSUFFICIENT는 Pilot 64건 중 4건(6%)인데 3클래스 macro 평균에서는 33.3%를
차지한다. 비용 구조도 대칭이 아니다 — "정보 부재를 충돌로 오판"과 "틀린 답을 승인"은 이 도메인에서
비용이 전혀 다른데 macro 평균은 같은 무게로 센다. Test에서는 표본이 더 작아(n=2) 왜곡이 커진다(§4).

**결론**: 클래스별 실패를 드러내는 **진단 지표로는 계속 유용하다.** 다만 클래스 불균형이 크고 비용
구조가 비대칭인 이 태스크에서 **모델 선정의 대표 지표로 쓰기에는 부적절하다.** 이 리포트의 §4·§8
표를 클래스별 recall + support 방식으로 바꾼 이유다.

### 표본이 작아 신뢰구간이 넓다

Wilson 95% 신뢰구간(Test 53건): FAR 0.1071 → [0.037, 0.272](폭 23.5%p), UNSUPPORTED Recall
0.8846 → [0.710, 0.960], INSUFFICIENT 정답률 1/2 → [0.095, 0.905]. **FAR을 0.107에서 0.07로
줄이는 개선은 이 표본으로는 노이즈와 구분되지 않는다.** §5 약점 ①의 Test 근거도 2건뿐이다.

### Test 셋은 더 이상 held-out이 아니다

#28의 검증 실험에서 Test 53건을 프롬프트 변형 3종에 사용했다. 리포트에 보고된 수치는 노출 이전
측정값이라 유효하지만, **앞으로 프롬프트를 더 손보려면 새 평가셋이 필요하다.**

### 그 밖의 평가셋 한계

규모 64+53으로 오류 유형별 셀이 작다는 점, INSUFFICIENT 표본이 Pilot 4·Test
2건뿐이라는 점, Dev split을 따로 두지 않아 Pilot 지표는 선정 과정에서 반복 사용된 값이라는 점
등)는 [`dataset/eval_dataset_construction.md` §9](../dataset/eval_dataset_construction.md)에
정리했다.

## 10. 재현 / 관측

```bash
# vLLM 서버 (WSL2 + Docker, RTX 4070 Laptop 8GB)
scripts/run_vllm_container.sh   # 기본값: Qwen3.5-4B-int4-AutoRound, --max-num-seqs 4 --max-model-len 1024

# 최종 eval 재실행
python -m src.eval.run_eval --split test --model qwen --prompt-version production
```

Langfuse Cloud(US 리전)에 모든 Verifier 호출이 generation trace로 기록되며, 프롬프트 버전은
Langfuse Prompt Management로 자동 추적된다 — 실행 시점의 `production` 버전을 끌어오고 그 번호가
observation에 링크되므로, eval 결과 파일명(`{split}_{model}_prompt-v{N}.json`)의 `N`이 곧
Langfuse 버전 번호다. 프롬프트 전문과 버전 이력은 [`prompts/`](../../prompts/) 참고.

상품 데이터셋 파일은 인증키 기반으로 제공되는 데이터라 공개 repo에 포함하지 않는다. 재현
절차와 마스킹 샘플은 [`data/sample/README.md`](../../data/sample/README.md)에 있다. eval 실행 로그도 같은 이유로
텍스트를 제거한 통합 요약본만 추적한다 — [`results/eval/runs_summary.json`](../eval/runs_summary.json)에
본 실험 13건과 #28 감사 11건, 총 24개 실행의 지표·클래스별 recall·문항별 판정이 들어 있다.

### 재현 계약 — 무엇까지 고정해야 같은 실험인가

#28에서 확인된 사실: **동일 설정 2회 실행은 verdict도 reason 텍스트도 완전히 동일**하다. 반면
serving 실행 경로가 바뀌면 경계 케이스가 흔들린다(§4의 `p024_c01`). 따라서 "같은 모델·같은
프롬프트"는 같은 실험의 조건으로 부족하고, 아래가 전부 고정돼야 한다.

| 축 | 이번 실험 값 |
|---|---|
| 모델 / 양자화 | `Intel/Qwen3.5-4B-int4-AutoRound`, quantization `inc`, dtype `bfloat16` |
| 추론 엔진 | vLLM **v0.27.1** (`vllm/vllm-openai@sha256:0a51ea5b…bfd967`) |
| 실행 경로 | CUDA graph (`FULL_AND_PIECEWISE`, capture `[1,2,4,8]`) |
| 서빙 파라미터 | `--max-model-len 1024 --max-num-seqs 4 --gpu-memory-utilization 0.85`, `seed=0` |
| 생성 파라미터 | `temperature=0`, `max_tokens=512`, `response_format=json_schema` |
| chat template | `chat_template_kwargs={"enable_thinking": false}` |
| 프롬프트 버전 | Langfuse `verifier-system-prompt` production=**v6** |

## 11. 지금 하지 않은 것 / 스코프 밖

전체 Agent orchestration, 실제 서비스 완성, Fine-tuning, LLM-as-a-Judge, 대출/적금 등
상품군 확대, 대규모 RAG, GDN/Mamba 커널 레벨 직접 최적화 — CLAUDE.md "지금 하지 않는 것"
절 참고. 다음에 손댈 가치가 있는 후보:

- **과잉거부를 재는 지표를 우선순위 안에 넣기** — FAR과 짝이 되는 False Reject Rate 계열.
  §9에서 확인했듯 현재 우선순위는 상수 스텁도, 실제 과잉거부 프롬프트도 "개선"으로 읽는다.
- **조건의 논리 구조를 입력으로 제공하기.** §5.2·§8.2가 보여준 건 "절차형 규칙이 판별 능력을
  *요구*할 뿐 제공하지 않는다"는 것이다. 우대조건을 `ALL_OF / ANY_OF / NOT / CAP / temporal` 같은
  구조로 미리 정리해 evidence와 함께 주면, 작은 모델에서도 과잉거부 없이 누락을 잡을 수 있는가 —
  이게 가장 직접적인 후속 실험이다. 잘 되면 완전성 판정의 상당 부분을 모델 추론에서 결정론 채점으로
  옮길 수 있다.
- **v8을 강한 프롬프트 baseline으로 보존.** 구조화 접근을 평가할 때 "그냥 프롬프트를 길게 쓰면
  되는 것 아닌가"라는 반문에 답하려면 이 비교군이 필요하다.
- `condition_omission`에 대한 프롬프트 개선을 **과잉거부 없이** 달성하는 문구 탐색 — #28의 v8은
  잡아내긴 했으나 대가가 컸다. 단, Test가 이미 노출됐으므로 새 평가셋이 먼저 필요하다.
- INSUFFICIENT 표본을 늘린 평가셋 확장 — 현재 6건으로는 이 라벨의 지표가 흔들린다.
- 안전 케이스에 `pass^k` 집계 적용 (현재는 전부 k=1 비율).
- vLLM이 GDN 경로를 더 최적화하는 향후 버전이 나오면 latency 재검증.

## 12. 이슈 트래킹

#1~#14, #23, #25 완료, 본 문서로 **#15(최종 결과 정리)** 를 닫았다. 이후 **#28(eval 전제 감사 +
크로스모델 검증)** 에서 §3·§4·§5·§8·§9를 정정했다. 전체 경위는 `CLAUDE.md`의 "다음 작업" 절과
각 이슈 링크 참고.

## 부록 A. 프로젝트 구조

```
finance_verifier/
├── src/
│   ├── ingest/         # Finlife API 수집, canonical 정규화, 데이터 프로파일링
│   ├── decomposition/  # Claim Decomposer (Claude API)
│   ├── verifier/       # Verifier client, JSON schema, Langfuse 연동
│   └── eval/           # Eval harness, metrics, failure analysis
├── prompts/             # Verifier / Decomposer 프롬프트 전문 (읽기용 스냅샷)
├── scripts/             # vLLM 컨테이너 기동 스크립트
├── data/
│   └── sample/          # 구조 확인용 마스킹 샘플 (실제 상품 데이터는 repo 미포함)
└── results/
    ├── final/           # ★ #15 최종 결과 리포트 + 대시보드
    ├── dataset/         # 평가 데이터셋 설계·구축 과정
    ├── eval/            # eval 분석 + 감사 기록 + runs_summary.json (실행 로그 원본은 로컬 전용)
    ├── model_selection/ # 모델 선정, latency 진단 (source of truth)
    ├── latency/         # #25 latency 실험 상세 로그
    ├── decomposition/, verifier/, normalization/, profiling/  # 컴포넌트별 검증 기록
```

## 부록 B. 문서 지도

| 문서 | 내용 |
|---|---|
| [`results/final/retrospective.md`](./retrospective.md) | **프로젝트 회고(KPT)** — 판단 과정에 대한 기록. 무엇을 계속할지·무엇이 틀렸는지·다음에 뭘 다르게 할지 |
| [`results/dataset/eval_dataset_construction.md`](../dataset/eval_dataset_construction.md) | **평가 데이터셋 설계·구축** — taxonomy, gold label 규칙, 검수, Pilot/Test 분리 |
| [`results/eval/smoke_eval_review.md`](../eval/smoke_eval_review.md) | Pilot(64건) 분석 — 모델/프롬프트 선정 과정 |
| [`results/eval/test_eval_review.md`](../eval/test_eval_review.md) | Test(held-out 53건) 최종 검증 + 크로스모델 체크 |
| [`results/eval/precondition_audit.md`](../eval/precondition_audit.md) | **eval 전제 감사 + 검증 실험(#28)** — 약점 재규명, 지표 우선순위 취약성 |
| [`results/model_selection/qwen_latency_diagnosis.md`](../model_selection/qwen_latency_diagnosis.md) | Qwen latency 원인 진단 (source of truth) |
| [`results/latency/capability_and_results.md`](../latency/capability_and_results.md) | #25 latency 실험 상세 로그 |
| [`results/decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md) | Claim Decomposer 검증, self-containment 수정 경위 |
| [`results/verifier/verifier_smoke_review.md`](../verifier/verifier_smoke_review.md) | Verifier client 스모크 테스트 |
| [`results/normalization/canonical_products_review.md`](../normalization/canonical_products_review.md) | canonical product schema 정리 |
| [`results/profiling/eval_design_review.md`](../profiling/eval_design_review.md) | 데이터 프로파일링 → eval 설계 재검토 |
| [`results/profiling/deposit_products_dashboard.html`](../profiling/deposit_products_dashboard.html) | 원본 데이터 프로파일링 대시보드 |
| [`prompts/README.md`](../../prompts/README.md) | 프롬프트 전문 + Verifier 프롬프트 버전 이력 |
