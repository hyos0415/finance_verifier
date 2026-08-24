# 최종 결과 정리 — 금융 답변 검증용 Verifier SLM (#15)

> ⚠️ **면책**: 이 프로젝트(및 산출물)는 소형 LLM이 금융 답변의 근거 관계를 얼마나 잘 판정하는지
> 검증하는 **연구/평가 목적의 산출물**이다. 여기서 다루는 상품 정보·판정 결과는 실제 금융 상담이나
> 투자/예금 의사결정의 근거로 쓰기 위한 것이 아니다. **정확한 상담은 반드시 해당 금융회사 영업점
> 방문 또는 전문 상담사와의 상담을 통해 확인해야 한다.** (근거: 원본 공시 데이터 자체가 조건 서술을
> 다의적으로 남겨두는 경우가 있어, Verifier가 아무리 정확해도 100% 확신 있는 판정이 불가능한
> 사례가 존재한다 — §7 참고.)

## 0. 핵심 질문 (재확인)

대형 LLM이 생성한 금융 답변을 매번 대형 모델로 검증하는 건 비용·지연이 크다. 제한된 역할의
3~4B급 로컬 모델을 검증 전용 서브에이전트로 써서, 잘못되거나 근거 부족한 금융 답변을 얼마나
안정적으로 차단할 수 있는가 — 그리고 그 성능·한계가 정확히 어디에 있는가.

## 1. 파이프라인

```
금융 답변 → Claim Decomposer(Claude API) → Atomic Claim(s) → Verifier SLM(Qwen, 로컬 vLLM)
          → SUPPORTED | UNSUPPORTED | INSUFFICIENT
```

- **Claim Decomposer**: Claude Sonnet 5 API. 답변 1개를 self-contained atomic claim으로 분해하고,
  규칙 기반 Atomicity/Coverage sanity check을 거친다 (`src/decomposition/claim_decomposer.py`).
- **Verifier**: `(Evidence, Atomic Claim)` 쌍을 받아 새 답변을 생성하지 않고 근거 관계만
  `{verdict, evidence, reason}` JSON으로 판정한다 (`src/verifier/client.py`, `src/verifier/schemas.py`).
- **서빙**: WSL2 + Docker Desktop(WSL2 backend) + `vllm/vllm-openai` 공식 이미지 → OpenAI 호환
  엔드포인트. RTX 4070 Laptop 8GB 단일 GPU.

## 2. 데이터 규모

| split | 용도 | claim 수 | 비고 |
|---|---|---|---|
| Smoke | 파이프라인 1바퀴 확인 | 10 answers → 29 claims | 모델 우열 판단 X |
| "Pilot" (실제 저장 위치: `data/smoke/claim_dataset.json`) | 모델/프롬프트 선정 | 64 claims | self-containment 수정 후 27→65건으로 확장, 오라벨 1건 제거해 64건 확정 |
| Test (unseen) | 최종 성능 확인 | 53 claims | Claude 28 + Codex 25 공동 생성, Pilot 프롬프트 튜닝에 전혀 관여하지 않은 신규 claim |

소스는 금융감독원 "금융상품 한눈에" Open API, 은행권 정기예금 단일 상품군
(`results/normalization/canonical_products_review.md`, `results/profiling/deposit_products_profile.json`).

## 3. 모델 선정 — Qwen3.5-4B-int4-AutoRound 단독 확정

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
이후 #25에서 이 열세를 서빙 설정만으로 3.6배 줄였다 (§5).

**프롬프트**: v1(baseline) → v2(reason 길이 제약 추가, latency 절반 개선 + 정확도 개선) 채택.
v3(절차+예시로 INSUFFICIENT 인식 유도)·v4(짧은 부정 규칙만 추가) 두 방식 모두 Qwen에서
기존 정답을 흔들거나 개선 효과가 없어 기각. **v2로 Test 단계까지 고정**
(Langfuse `verifier-system-prompt` production label).

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

### 확정된 두 가지 약점

1. **INSUFFICIENT ↔ UNSUPPORTED 경계 혼동.** "정보 부재"(evidence에 판단거리 자체가 없음)와
   "명시적 충돌"을 구분하지 못한다. Qwen뿐 아니라 **Nemotron Ultra 550B(4건 중 0건 정답)**,
   Gemma-4-31B(1/4)도 유사하게 실패했고, v3/v4 두 가지 프롬프트 엔지니어링 시도 모두 해결에
   실패했다 — 모델 체급과 무관한, **이 태스크/경계 정의 자체의 구조적 한계**로 결론짓는다.
2. **`condition_omission`** — AND로 묶인 복합 조건 중 일부만 인용해서 claim을 만들면, "언급된
   부분은 evidence와 일치한다"는 데 꽂혀서 **명시되지 않은 나머지 조건이 생략됐다는 사실 자체를
   못 잡는다.** Test에서 신규 주입 2건 전부 놓쳤고, 크로스모델 체크(Qwen 0/2, Claude Haiku 4.5
   1/2, Nemotron Ultra 550B 0/2)에서도 130배 파라미터 차이가 나는 두 모델이 정확히 같은 지점에서
   같은 실수를 했다 — 체급 문제가 아니라 **"부분 인용 뒤에 숨은 조건을 evidence 전체와 대조하는"
   이 추론 유형 자체가 구조적으로 어렵다.**

두 약점 모두 `results/eval/test_eval_review.md`에 케이스별로 기록되어 있다.

## 5. Latency 최적화 (#25) — CUDA Graph로 3.6배

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

## 6. 참고용 — 대형 모델 대비 상한선

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
동시에 Nemotron Ultra(550B)도 INSUFFICIENT 4건을 전부 놓쳤다는 사실은, §4의 경계 혼동이
모델 체급으로 해소되는 문제가 아니라는 근거를 더한다. (latency는 로컬 GPU 서빙 vs hosted
API 왕복이라 직접 비교 대상이 아니며 참고 수치로만 본다.)

## 7. 알려진 한계 — 원본 데이터의 모호성

Verifier 파이프라인의 정확도와 별개로, **원본 공시 데이터 자체가 조건 서술을 다의적으로
남겨두는 경우가 있다.** 예: iM함께예금의 우대조건 서술("전월 총수신 평잔 30만원 이상 또는
첫만남플러스통장 보유 시 각 연 0.10%p")은 "각 0.10%p"가 대시(-) 항목 단위인지, 그 안의
하위 조건(OR) 단위인지 원문만으로 완전히 명확하지 않다(`results/decomposition/claim_decomposer_smoke_review.md`
참고). Verifier가 아무리 정확하게 판정해도 원문 자체가 모호하면 100% 확신 있는 결론은
낼 수 없다 — 이건 파이프라인의 실패가 아니라 **소스 데이터의 한계**이며, 그래서 이 문서
최상단과 데모/서비스 단계 모두에 면책 문구를 명시한다.

## 8. 재현

```bash
# vLLM 서버 (WSL2 + Docker, RTX 4070 Laptop 8GB)
scripts/run_vllm_container.sh   # 기본값: Qwen3.5-4B-int4-AutoRound, --max-num-seqs 4 --max-model-len 1024

# 최종 eval 재실행
python -m src.eval.run_eval --split test --model qwen --prompt-version production
```

Langfuse Cloud(US 리전)에 모든 Verifier 호출이 generation trace로 기록되며, 프롬프트
버전은 Langfuse Prompt Management로 자동 추적된다.

## 9. 지금 하지 않은 것 / 스코프 밖

전체 Agent orchestration, 실제 서비스 완성, Fine-tuning, LLM-as-a-Judge, 대출/적금 등
상품군 확대, 대규모 RAG, GDN/Mamba 커널 레벨 직접 최적화 — CLAUDE.md "지금 하지 않는 것"
절 참고. 다음에 손댈 가치가 있는 후보:

- `condition_omission`을 노리는 명시적 few-shot이나 evidence 전체 대조를 강제하는 프롬프트
  구조 변경(이번 프로젝트에서는 INSUFFICIENT 경계 프롬프트 튜닝에 리소스를 썼고 여기까지는
  못 갔음).
- vLLM이 GDN 경로를 더 최적화하는 향후 버전이 나오면 latency 재검증.

## 10. 이슈 트래킹

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
├── prompts/             # Verifier / Decomposer 프롬프트
├── scripts/             # vLLM 컨테이너 기동 스크립트
├── data/
│   ├── raw/             # Finlife API snapshot (은행권 정기예금)
│   ├── normalized/      # canonical product record
│   ├── smoke/, test/    # claim dataset + synthetic answers (Pilot 규모는 smoke/에 있음)
└── results/
    ├── final/           # ★ #15 최종 결과 리포트 + 대시보드
    ├── eval/            # Pilot·Test eval 분석 + eval/raw/ (원본 실행 로그 JSON)
    ├── model_selection/ # 모델 선정, latency 진단 (source of truth)
    ├── latency/         # #25 latency 실험 상세 로그
    ├── decomposition/, verifier/, normalization/, profiling/  # 컴포넌트별 검증 기록
```

## 부록 B. 문서 지도

| 문서 | 내용 |
|---|---|
| [`results/eval/smoke_eval_review.md`](../eval/smoke_eval_review.md) | Pilot(64건) 분석 — 모델/프롬프트 선정 과정 |
| [`results/eval/test_eval_review.md`](../eval/test_eval_review.md) | Test(unseen 53건) 최종 검증 + 크로스모델 체크 |
| [`results/model_selection/qwen_latency_diagnosis.md`](../model_selection/qwen_latency_diagnosis.md) | Qwen latency 원인 진단 (source of truth) |
| [`results/latency/capability_and_results.md`](../latency/capability_and_results.md) | #25 latency 실험 상세 로그 |
| [`results/decomposition/claim_decomposer_smoke_review.md`](../decomposition/claim_decomposer_smoke_review.md) | Claim Decomposer 검증, self-containment 수정 경위 |
| [`results/verifier/verifier_smoke_review.md`](../verifier/verifier_smoke_review.md) | Verifier client 스모크 테스트 |
| [`results/normalization/canonical_products_review.md`](../normalization/canonical_products_review.md) | canonical product schema 정리 |
| [`results/profiling/eval_design_review.md`](../profiling/eval_design_review.md) | 데이터 프로파일링 → eval 설계 재검토 |
| [`results/profiling/deposit_products_dashboard.html`](../profiling/deposit_products_dashboard.html) | 원본 데이터 프로파일링 대시보드 |
