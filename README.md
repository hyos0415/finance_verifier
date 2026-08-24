# finance_verifier

금융 답변 검증용 소형 LLM(3~4B) Verifier.

📊 **[프로젝트 소개 & 결과 발표](./results/final/report_dashboard.html)** — 무슨 문제를
풀었는지, 무엇을 발견했는지 누구나 이해하기 쉽게 정리한 발표용 자료 (다운로드 후
브라우저로 열어서 확인. GitHub은 HTML을 렌더링하지 않는다).

아래는 기술적으로 더 상세한 내용이다.

> ⚠️ 이 리포지토리와 산출물은 **연구/평가 목적**이다. 여기 담긴 상품 정보·판정 결과는 금융
> 상담이나 투자/예금 의사결정의 근거로 쓰기 위한 게 아니다. 정확한 상담은 반드시 해당
> 금융회사 영업점 방문 또는 전문 상담사와의 상담을 통해 확인해야 한다 (이유:
> [결과 리포트 §7](./results/final/report.md#7-알려진-한계--원본-데이터의-모호성)).

## 핵심 질문

대형 LLM이 생성한 금융 답변을 매번 대형 모델로 검증하는 건 비용·지연이 크다. 제한된
역할의 3~4B급 로컬 모델을 검증 전용 서브에이전트로 써서, 잘못되거나 근거 부족한 금융
답변을 얼마나 안정적으로 차단할 수 있는가?

```
금융 답변 → Claim Decomposer(Claude API) → Atomic Claim(s)
         → Verifier SLM(Qwen3.5-4B, 로컬 vLLM) → SUPPORTED | UNSUPPORTED | INSUFFICIENT
```

## 결과 요약 (Test, unseen 53건)

| 지표 | 값 | CLAUDE.md 우선순위 |
|---|---|---|
| False Accept Rate | **0.1071** | 1순위 — 틀린 답을 맞다고 승인하는 최악의 실패를 최소화 |
| UNSUPPORTED Recall | **0.8846** | 2순위 |
| Macro F1 | 0.8434 | 참고 |
| Schema Valid Rate | 100% (53/53) | — |
| Latency (batch=1 decode, #25 최적화 후) | 12.4 → **44.3 tok/s (×3.6)** | — |

- **모델**: Qwen3.5-4B-int4-AutoRound 단독 확정 (Kanana-2-3B-instruct는 Pilot 64건 비교 후
  FAR·Recall 열세로 탈락).
- **확정된 한계 2가지**: ① INSUFFICIENT↔UNSUPPORTED 경계 혼동, ② AND 복합조건 일부만
  인용됐을 때 나머지 조건 누락(`condition_omission`)을 못 잡음 — 둘 다 모델 체급과
  무관하게(참고용으로 돌려본 Nemotron Ultra 550B 등에서도 동일) 재현된, 태스크/경계
  정의 자체의 구조적 한계로 결론지었다.

**전체 내용**은 [`results/final/report.md`](./results/final/report.md)에 있다.

## 프로젝트 구조

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
    ├── final/           # ★ #15 최종 결과 리포트 + 발표용 대시보드
    ├── eval/            # Pilot·Test eval 분석 + eval/raw/ (원본 실행 로그 JSON)
    ├── model_selection/ # 모델 선정, latency 진단(source of truth)
    ├── latency/         # #25 latency 실험 상세 로그
    ├── decomposition/, verifier/, normalization/, profiling/  # 컴포넌트별 검증 기록
```

## 문서 지도

| 문서 | 내용 |
|---|---|
| [`results/final/report.md`](./results/final/report.md) | **최종 결과 리포트** — 모델 선정, Test 결과, latency, 한계, 면책 |
| [`results/final/report_dashboard.html`](./results/final/report_dashboard.html) | 프로젝트 소개 & 결과 발표용 대시보드 (다운로드 후 브라우저로 열기) |
| [`results/eval/smoke_eval_review.md`](./results/eval/smoke_eval_review.md) | Pilot(64건) 분석 — 모델/프롬프트 선정 과정 |
| [`results/eval/test_eval_review.md`](./results/eval/test_eval_review.md) | Test(unseen 53건) 최종 검증 + 크로스모델 체크 |
| [`results/model_selection/qwen_latency_diagnosis.md`](./results/model_selection/qwen_latency_diagnosis.md) | Qwen latency 원인 진단 (source of truth) |
| [`results/latency/capability_and_results.md`](./results/latency/capability_and_results.md) | #25 latency 실험 상세 로그 |
| [`results/decomposition/claim_decomposer_smoke_review.md`](./results/decomposition/claim_decomposer_smoke_review.md) | Claim Decomposer 검증, self-containment 수정 경위 |
| [`results/verifier/verifier_smoke_review.md`](./results/verifier/verifier_smoke_review.md) | Verifier client 스모크 테스트 |
| [`results/normalization/canonical_products_review.md`](./results/normalization/canonical_products_review.md) | canonical product schema 정리 |
| [`results/profiling/eval_design_review.md`](./results/profiling/eval_design_review.md) | 데이터 프로파일링 → eval 설계 재검토 |
| [`results/profiling/deposit_products_dashboard.html`](./results/profiling/deposit_products_dashboard.html) | 원본 데이터 프로파일링 대시보드 |

설계·판정 기준·모델 후보·실행 환경·데이터 방침 등 프로젝트 전체 설계는
[CLAUDE.md](./CLAUDE.md) 참고.

## 실행

```bash
# vLLM 서버 (WSL2 + Docker Desktop, RTX 4070 Laptop 8GB 기준)
scripts/run_vllm_container.sh

# 최종 eval 재실행
python -m src.eval.run_eval --split test --model qwen --prompt-version production
```

`.env.example`을 `.env`로 복사해 `FINLIFE_API_KEY`(금융상품 API 수집용) 등 필요한
키를 채운다.
