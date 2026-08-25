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
  → Pilot / unseen Test 분리 (Test는 튜닝에 미사용)
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

### 2.4 Pilot / unseen Test 분리와 leakage 방지

| split | 용도 | claim 수 | 시나리오 | 상품 | 저자 |
|---|---|---|---|---|---|
| Smoke | 파이프라인 1바퀴 확인 (모델 우열 판단 X) | 10 answers → 29 claims | – | – | Claude Code |
| **Pilot** (저장 위치: `data/smoke/claim_dataset.json`) | 모델·프롬프트 선정 | **64** | 23 | 11 | Claude Code |
| **Test (unseen)** | 최종 성능 확인 (한 번만 실행) | **53** | 53 | 28 | Claude Code 28 + Codex 25 |

leakage를 막기 위한 장치가 두 가지다.

- **(상품, 근거 필드) 쌍이 완전히 분리돼 있다.** 상품 5개가 양쪽에 등장하지만 참조 필드가
  하나도 겹치지 않는다 — Pilot에서 우대조건을 쓴 상품은 Test에서 만기후이자를 쓰는 식이다.
  근거 텍스트 기준으로 중복 문항이 없고, 나머지 23개 상품은 Test에서 처음 등장한다.
- **저자를 분리했다.** Test 53건 중 25건은 다른 에이전트(Codex)가 작성했다. 한 사람이 양쪽을
  다 쓰면 같은 문체·같은 함정 패턴이 반복돼 "unseen"의 의미가 약해진다. 라벨 검수는 사람이
  양쪽 모두 전수 확인했다.

라벨 분포는 Pilot(SUPPORTED 46 / UNSUPPORTED 14 / INSUFFICIENT 4), Test(25 / 26 / 2)다.

## 3. 모델·프롬프트 선정 — Qwen3.5-4B-int4-AutoRound 단독 확정

| 후보 | 정밀도/경로 | Pilot(64) FAR | Pilot(64) UNSUPPORTED Recall | Pilot(64) Macro F1 | Pilot(64) latency p50/p95 |
|---|---|---|---|---|---|
| **Qwen3.5-4B-int4-AutoRound** (확정) | INT4 AutoRound → vLLM | **0.1111** | **0.8571** | 0.6656 | 7.83s / 17.05s |
| Kanana-2-3B-instruct (비교 후 탈락) | BF16 → vLLM | 0.2222 | 0.7143 | **0.7652** | **3.90s / 7.81s** |

**결정 근거**: CLAUDE.md 지표 우선순위 1순위인 False Accept Rate(FAR)·2순위 UNSUPPORTED
Recall 모두 Qwen이 우세. Kanana가 Macro F1과 latency에서 우세하지만, "틀린 걸 맞다고
승인"하는 실패가 "맞는 걸 틀렸다고 거부"하는 것보다 훨씬 위험한 금융 Verifier 맥락에서는
FAR을 우선한다.

Qwen의 latency 열세는 설정 실수가 아니라 실측으로 확인된 구조적 현상이다 — Qwen3.5의
GDN(Gated DeltaNet) 하이브리드 레이어가 vLLM/Triton에서 아직 Kanana의 FlashAttention2
경로만큼 성숙하게 최적화되어 있지 않다(`results/model_selection/qwen_latency_diagnosis.md`).
이후 #25에서 이 열세를 서빙 설정만으로 3.6배 줄였다 (§7).

프롬프트는 v1(baseline) → v2(reason 길이 제약 추가)를 채택하고, INSUFFICIENT 경계를 겨냥한
두 개선안은 모두 기각했다. 채택/기각 판단 기준과 근거는 §6에 표로 정리했다. **v2로 Test
단계까지 고정**했다(Langfuse `verifier-system-prompt` production label).

## 4. 최종 결과 — Test(Unseen, 53건)

| 지표 | Pilot(64) | **Test(53, unseen)** |
|---|---|---|
| False Accept Rate | 0.1111 | **0.1071** |
| UNSUPPORTED Recall | 0.8571 | **0.8846** |
| Macro F1 | 0.6656 | **0.8434** |
| Schema Valid Rate | 1.0 | **1.0** |
| Latency p50 / p95 (최적화 전) | 7.83s / 17.05s | 9.08s / 14.27s |

Pilot 튜닝에 전혀 쓰이지 않은 unseen 데이터에서도 핵심 지표(FAR·Recall)가 유지되거나
소폭 개선됐다 — 모델/프롬프트 선정이 Pilot 64건에 과적합된 결과가 아니라는 근거다.

## 5. Failure Analysis — 확정된 두 가지 약점

1. **INSUFFICIENT ↔ UNSUPPORTED 경계 혼동.** "정보 부재"(evidence에 판단거리 자체가 없음)와
   "명시적 충돌"을 구분하지 못한다. Qwen뿐 아니라 **Nemotron Ultra 550B(4건 중 0건 정답)**,
   Gemma-4-31B(1/4)도 유사하게 실패했고, 프롬프트 엔지니어링 두 가지 시도 모두 해결에
   실패했다(§6) — 모델 체급과 무관한, **이 태스크/경계 정의 자체의 구조적 한계**로 결론짓는다.
2. **`condition_omission`** — AND로 묶인 복합 조건 중 일부만 인용해서 claim을 만들면, "언급된
   부분은 evidence와 일치한다"는 데 꽂혀서 **명시되지 않은 나머지 조건이 생략됐다는 사실 자체를
   못 잡는다.** Test에서 신규 주입 2건 전부 놓쳤다.

**cross-model validation** — 이 실패가 Qwen 고유인지 확인하기 위해 같은 Test 53건을 훨씬 큰
모델에 그대로 돌렸다. Nemotron Ultra 550B의 지표는 Qwen3.5-4B와 **완전히 동일**했다(FAR
0.1071, Recall 0.8846, Macro F1 0.8434). 파라미터가 130배 차이 나는 두 모델이 같은 지점에서
같은 실수를 한 것이다. `condition_omission`도 Qwen 0/2, Claude Haiku 4.5 1/2, Nemotron
0/2로, **"부분 인용 뒤에 숨은 조건을 evidence 전체와 대조하는" 추론 유형 자체가 구조적으로
어렵다**는 해석을 뒷받침한다.

두 약점 모두 [`results/eval/test_eval_review.md`](../eval/test_eval_review.md)에 케이스별로
기록되어 있다.

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

기각 두 건에서 얻은 결론이 §5의 근거가 된다 — **접근이 정반대인 두 시도(절차+예시 추가 vs
짧은 부정 규칙만)가 모두 실패했다면, 그건 프롬프트 문구 문제가 아니라 태스크 자체의 문제다.**

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

## 8. 참고용 — 대형 모델 대비 상한선

Verifier 후보는 CLAUDE.md 방침상 로컬 3~4B급 SLM 2개로 제한했지만, "이 태스크·이 프롬프트가
잘 짜여 있는가"를 참고하기 위해 동일 v2 프롬프트·동일 Pilot(64) 데이터셋을 Claude
Haiku 4.5/Sonnet 5, NVIDIA-hosted Nemotron Ultra 550B, Gemma-4-31B에도 그대로 돌렸다
(이 결과는 모델 선정 로직에 영향을 주지 않는다).

| 지표 | Qwen(로컬) | Kanana(로컬) | Claude Haiku 4.5 | Claude Sonnet 5 | Nemotron Ultra 550B | Gemma-4-31B |
|---|---|---|---|---|---|---|
| FAR | 0.1111 | 0.2222 | **0.0** | 0.0556 | **0.0** | 0.0556 |
| UNSUPPORTED Recall | 0.8571 | 0.7143 | 0.8571 | 0.7857 | 0.7857 | 0.7143 |
| Macro F1 | 0.6656 | 0.7652 | 0.7552 | **0.8228** | 0.5665 | 0.6233 |

**시사점**: 더 큰 모델은 같은 프롬프트로도 더 낮은 FAR을 뽑아낼 여지가 있다 — 즉 로컬 두
후보의 현재 성능은 "이 태스크 자체의 상한"이 아니라 "이 체급에서 감수하는 트레이드오프"다.
동시에 Nemotron Ultra(550B)도 INSUFFICIENT 4건을 전부 놓쳤다는 사실은, §5의 경계 혼동이
모델 체급으로 해소되는 문제가 아니라는 근거를 더한다. (latency는 로컬 GPU 서빙 vs hosted
API 왕복이라 직접 비교 대상이 아니며 참고 수치로만 본다.)

## 9. 알려진 한계 — 원본 데이터의 모호성

Verifier 파이프라인의 정확도와 별개로, **원본 공시 데이터 자체가 조건 서술을 다의적으로
남겨두는 경우가 있다.** 예: 한 상품(`p003`)의 우대조건은 두 요건 중 하나만 충족해도 "각 연
0.10%p"를 준다고 적혀 있는데, 이 "각"이 대시(-) 항목 단위인지 그 안의 하위 조건(OR) 단위인지
원문만으로 완전히 명확하지 않다
([`decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md) 참고).
Verifier가 아무리 정확하게 판정해도 원문 자체가 모호하면 100% 확신 있는 결론은 낼 수 없다 —
이건 파이프라인의 실패가 아니라 **소스 데이터의 한계**이며, 그래서 이 문서 최상단과
데모/서비스 단계 모두에 면책 문구를 명시한다.

평가셋 쪽 한계(규모 64+53으로 오류 유형별 셀이 작다는 점, INSUFFICIENT 표본이 Pilot 4·Test
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
절차와 마스킹 샘플은 [`data/sample/README.md`](../../data/sample/README.md)에 있다.

## 11. 지금 하지 않은 것 / 스코프 밖

전체 Agent orchestration, 실제 서비스 완성, Fine-tuning, LLM-as-a-Judge, 대출/적금 등
상품군 확대, 대규모 RAG, GDN/Mamba 커널 레벨 직접 최적화 — CLAUDE.md "지금 하지 않는 것"
절 참고. 다음에 손댈 가치가 있는 후보:

- `condition_omission`을 노리는 명시적 few-shot이나 evidence 전체 대조를 강제하는 프롬프트
  구조 변경(이번 프로젝트에서는 INSUFFICIENT 경계 프롬프트 튜닝에 리소스를 썼고 여기까지는
  못 갔음).
- INSUFFICIENT 표본을 늘린 평가셋 확장 — 현재 6건으로는 이 라벨의 지표가 흔들린다.
- vLLM이 GDN 경로를 더 최적화하는 향후 버전이 나오면 latency 재검증.

## 12. 이슈 트래킹

#1~#14, #23, #25 완료. 본 문서로 **#15(최종 결과 정리)** 를 닫는다. 전체 경위는
`CLAUDE.md`의 "다음 작업" 절과 각 이슈 링크 참고.

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
    ├── eval/            # Pilot·Test eval 분석 + eval/raw/ (원본 실행 로그 JSON)
    ├── model_selection/ # 모델 선정, latency 진단 (source of truth)
    ├── latency/         # #25 latency 실험 상세 로그
    ├── decomposition/, verifier/, normalization/, profiling/  # 컴포넌트별 검증 기록
```

## 부록 B. 문서 지도

| 문서 | 내용 |
|---|---|
| [`results/dataset/eval_dataset_construction.md`](../dataset/eval_dataset_construction.md) | **평가 데이터셋 설계·구축** — taxonomy, gold label 규칙, 검수, Pilot/Test 분리 |
| [`results/eval/smoke_eval_review.md`](../eval/smoke_eval_review.md) | Pilot(64건) 분석 — 모델/프롬프트 선정 과정 |
| [`results/eval/test_eval_review.md`](../eval/test_eval_review.md) | Test(unseen 53건) 최종 검증 + 크로스모델 체크 |
| [`results/model_selection/qwen_latency_diagnosis.md`](../model_selection/qwen_latency_diagnosis.md) | Qwen latency 원인 진단 (source of truth) |
| [`results/latency/capability_and_results.md`](../latency/capability_and_results.md) | #25 latency 실험 상세 로그 |
| [`results/decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md) | Claim Decomposer 검증, self-containment 수정 경위 |
| [`results/verifier/verifier_smoke_review.md`](../verifier/verifier_smoke_review.md) | Verifier client 스모크 테스트 |
| [`results/normalization/canonical_products_review.md`](../normalization/canonical_products_review.md) | canonical product schema 정리 |
| [`results/profiling/eval_design_review.md`](../profiling/eval_design_review.md) | 데이터 프로파일링 → eval 설계 재검토 |
| [`results/profiling/deposit_products_dashboard.html`](../profiling/deposit_products_dashboard.html) | 원본 데이터 프로파일링 대시보드 |
| [`prompts/README.md`](../../prompts/README.md) | 프롬프트 전문 + Verifier 프롬프트 버전 이력 |
