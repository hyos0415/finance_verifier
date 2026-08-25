# finance_verifier

금융 답변 검증용 소형 LLM(3~4B) Verifier.

📊 **[프로젝트 소개 & 결과 발표](./results/final/report_dashboard.html)** — 무슨 문제를
풀었는지, 무엇을 발견했는지 누구나 이해하기 쉽게 정리한 발표용 자료 (다운로드 후
브라우저로 열어서 확인. GitHub은 HTML을 렌더링하지 않는다).

아래는 기술적으로 더 상세한 내용이다.

> ⚠️ 이 repo와 산출물은 **연구/평가 목적**이다. 여기 담긴 상품 정보·판정 결과는 금융
> 상담이나 투자/예금 의사결정의 근거로 쓰기 위한 게 아니다. 정확한 상담은 반드시 해당
> 금융회사 영업점 방문 또는 전문 상담사와의 상담을 통해 확인해야 한다 (이유:
> [결과 리포트 §9](./results/final/report.md#9-알려진-한계--원본-데이터의-모호성)).

## 핵심 질문

대형 LLM이 생성한 금융 답변을 매번 대형 모델로 검증하는 건 비용·지연이 크다. 제한된
역할의 3~4B급 로컬 모델을 검증 전용 서브에이전트로 써서, 잘못되거나 근거 부족한 금융
답변을 얼마나 안정적으로 차단할 수 있는가?

```
금융 답변 → Claim Decomposer(Claude API) → Atomic Claim(s)
         → Verifier SLM(Qwen3.5-4B, 로컬 vLLM) → SUPPORTED | UNSUPPORTED | INSUFFICIENT
```

## 결과 요약

Test는 **모델·프롬프트 선정에 사용하지 않은 held-out 셋을 최초 1회 평가**한 결과다. 수치는 최종
채택한 서빙 경로(CUDA graph, `--max-num-seqs 4`) 기준이다.

| 지표 | Test 53건 | 의미 |
|---|---|---|
| False Accept Rate ↓ | **0.1071** | 틀린 답을 맞다고 승인 — 금융 검증기에서 가장 위험한 실패 |
| SUPPORTED Recall ↑ | **1.0000** | 정상 답변을 거부하지 않았다 |
| UNSUPPORTED Recall ↑ | **0.8846** | 틀린 답을 잡아낸 비율 |
| INSUFFICIENT Recall ↑ | **0.0000** | 아래 한계 ① |
| Accuracy | 0.9057 | 참고 |
| Schema Valid Rate | 100% (53/53) | JSON 스키마 준수 |
| Latency (batch=1 decode, #25 후) | 12.4 → **44.3 tok/s (×3.6)** | 서빙 설정만으로 개선 |

**모델**: Qwen3.5-4B-int4-AutoRound 단독 확정 (Kanana-2-3B는 FAR 열세로 탈락).

## 끝내고 나서 자체 감사를 했고, 결론 두 개가 뒤집혔다

프로젝트 종료 후 eval 설계를 점검하는 도구를 만들어 **이 프로젝트에 되돌려 적용했다**
([#28](./results/eval/precondition_audit.md)). 4개 모델(Qwen·Haiku·Sonnet·Nemotron 550B)에 같은
실험을 다시 돌린 결과다.

| 처음 결론 | 검증 후 |
|---|---|
| INSUFFICIENT 혼동은 **태스크 구조적 한계** | **틀렸다.** 같은 프롬프트로 Kanana(로컬 **3B**)·Haiku·Sonnet은 INSUFFICIENT를 전부 맞힌다. Qwen·Nemotron·Gemma만 실패하는 **모델별 특성**이다 |
| 조건 누락은 **프롬프트로 해결되지 않는다** | **부분적으로 틀렸다.** 절차형 규칙을 넣으면 잡힌다. 다만 효과가 **모델 능력에 종속** — Sonnet·Nemotron은 개선, 채택 모델 Qwen은 정상 답변 인식률 0.913 → **0.674**로 붕괴 |

여기서 **평가 지표 설계의 결함 두 가지**도 함께 드러났다.

1. **지표 우선순위에 utility 제약이 없다.** `"UNSUPPORTED"`만 반환하는 상수 스텁이 1·2·3순위 지표를
   전부 이긴다(FAR 0.0 / Recall 1.0 / Schema 1.0). 가상의 반례가 아니라 실제 프롬프트도 같은 방향으로
   움직였다 — 정확도를 15.6%p 떨어뜨리면서 FAR·Recall은 개선. → 순위제가 아니라 **제약식**이 맞다
   ("FAR을 낮추되 정상 답변 거부율을 악화시키지 않는다").
2. **Macro F1이 순위를 뒤집는다.** 4건짜리 INSUFFICIENT 클래스가 3클래스 macro 평균의 1/3을 차지해,
   FAR·정확도가 모두 나은 모델을 더 낮게 매긴다. 클래스 불균형이 심한 소표본에서는 헤드라인 지표로
   쓰면 안 된다.

### 그래서 Qwen 선택은 옳았나 — 옳았다

같은 프롬프트·같은 Pilot 64건 정면 비교다.

| 지표 | Qwen3.5-4B (채택) | Kanana-2-3B (탈락) | 승 |
|---|---|---|---|
| False Accept Rate ↓ | **0.1111** | 0.2222 | Qwen |
| SUPPORTED Recall ↑ | **0.9130** | 0.8043 | Qwen |
| UNSUPPORTED Recall ↑ | **0.8571** | 0.7143 | Qwen |
| INSUFFICIENT Recall ↑ | 0.0000 | **1.0000** | Kanana |
| Accuracy ↑ | **0.8438** | 0.7969 | Qwen |

Kanana가 이긴 한 칸이 아쉬워 보이지만, 두 가지가 그 손실을 제한한다.

- **Qwen이 INSUFFICIENT를 놓칠 때 100% UNSUPPORTED로 간다**(Pilot 4/4, Test 2/2). SUPPORTED로 간 적이
  한 번도 없다 — 잃은 클래스의 실패는 "위험한 승인"이 아니라 **보수적 차단**이다.
- **빈도가 다르다.** 부정 라벨은 64건 중 18건(28%)인데 INSUFFICIENT는 4건(6%)이다. Kanana는 자주
  등장하는 위험 케이스에서 2배 더 승인하고, Qwen은 드문 케이스에서 안전한 방향으로 틀린다.

다만 **결론은 맞았어도 당시 근거는 불완전했다.** Macro F1을 "보조 지표"라며 넘긴 게 결과적으로
옳았던 건 그 지표를 신뢰할 수 없어서였지, 거기 담긴 신호가 무의미해서가 아니었다.

### 확인된 한계 2가지

① **INSUFFICIENT를 사실상 출력하지 않는다** — 채택 설정 6개 실행 전부에서 이 verdict가 0건. 모델
선정 때 FAR을 근거로 Kanana를 탈락시켰는데, **Kanana는 이 항목에서 4/4였고 Qwen은 0/4**다. 지표
우선순위가 그 교환을 보여주지 않았다.

② **조건의 논리 구조와 적용 범위를 잡지 못한다** — AND 조건 일부만 인용한 claim을 승인한다. 절차형
프롬프트로 잡아낼 수는 있지만, 그러면 ANY_OF(택일) 조건을 ALL_OF로 오인해 정상 claim까지 거부한다.
즉 병목은 "누락을 못 본다"가 아니라 **"조건들이 전부 필요한지, 하나만 충족하면 되는지 구분하지
못한다"**이다.

**전체 내용**은 [`results/final/report.md`](./results/final/report.md)에 있다.

## 프로젝트 구조

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
│   └── sample/          # 구조 확인용 마스킹 샘플 (실제 상품 데이터는 미포함 — 아래 참고)
└── results/
    ├── final/           # ★ #15 최종 결과 리포트 + 발표용 대시보드
    ├── eval/            # eval 분석 + 감사 기록 + runs_summary.json (실행 로그 원본은 로컬 전용)
    ├── model_selection/ # 모델 선정, latency 진단(source of truth)
    ├── latency/         # #25 latency 실험 상세 로그
    ├── decomposition/, verifier/, normalization/, profiling/  # 컴포넌트별 검증 기록
```

## 데이터 방침 — 수집한 상품 데이터셋은 repo에 없다

데이터 소스는 금융감독원 「금융상품 한눈에」 Open API(은행권 정기예금)이고, **인증키를
발급받아야 호출할 수 있다.** 수집한 스냅샷 파일을 공개 repo에 그대로 올리는 건 인증키로
접근을 관리하는 제공 방식을 우회하는 재배포에 가깝다고 보고, **`data/` 아래 상품 데이터셋
파일은 git 추적에서 제외했다** (`.gitignore` 참고).

| repo에 없는 것 (로컬 전용) | 대신 repo에 있는 것 |
|---|---|
| `data/raw/` API 원본 스냅샷 | 수집·정규화 코드 (`src/ingest/`) |
| `data/normalized/` canonical product record | 정규화 규칙 문서 (`results/normalization/`) |
| `data/{smoke,test}/` claim dataset · synthetic answers | 평가 지표·분석 리포트 (`results/`) |
| — | 구조 확인용 마스킹 샘플 ([`data/sample/`](./data/sample/)) |

`data/sample/`의 두 파일은 실제 공시 데이터가 아니라 **파이프라인 입출력 구조를 보여주기 위해
지어낸 합성 데이터**다(은행명·상품명·금리·조건 문구 전부 가상). 재현 절차는
[`data/sample/README.md`](./data/sample/README.md) 참고.

## 프롬프트 관리 — Langfuse

Verifier system prompt는 repo 파일이 아니라 **[Langfuse](https://langfuse.com) Prompt
Management로 버전 관리했다.** 실행 시점에 `production` 라벨이 붙은 버전을 끌어오고, 그 버전
번호가 Langfuse generation observation에 자동으로 링크된다 — eval 결과 파일명
(`{split}_{model}_prompt-v{N}.json`)의 `N`이 그 Langfuse 버전 번호다. Verifier 호출마다
`model`/`gold_label`/`error_type`/`dataset_split` 등의 metadata도 함께 trace로 남긴다.

프롬프트 전문과 버전 이력(v1~v6, v3·v5 기각 경위)은 [`prompts/`](./prompts/)에 읽기용
스냅샷으로 정리해 뒀다. 실제 SSOT는 Langfuse와 `src/verifier/client.py`이다.

## 문서 지도

| 문서 | 내용 |
|---|---|
| [`results/final/report.md`](./results/final/report.md) | **최종 결과 리포트** — 모델 선정, Test 결과, latency, 한계, 면책 |
| [`results/final/report_dashboard.html`](./results/final/report_dashboard.html) | 프로젝트 소개 & 결과 발표용 대시보드 (다운로드 후 브라우저로 열기) |
| [`results/dataset/eval_dataset_construction.md`](./results/dataset/eval_dataset_construction.md) | **평가 데이터셋 설계·구축** — 오류 taxonomy, gold label 규칙, Pilot/Test 분리, 검수 |
| [`results/eval/smoke_eval_review.md`](./results/eval/smoke_eval_review.md) | Pilot(64건) 분석 — 모델/프롬프트 선정 과정 |
| [`results/eval/test_eval_review.md`](./results/eval/test_eval_review.md) | Test(held-out 53건) 최종 검증 + 크로스모델 체크 |
| [`results/eval/precondition_audit.md`](./results/eval/precondition_audit.md) | **eval 전제 감사 + 검증 실험** — 약점 재규명, 지표 우선순위 취약성 |
| [`results/model_selection/qwen_latency_diagnosis.md`](./results/model_selection/qwen_latency_diagnosis.md) | Qwen latency 원인 진단 (source of truth) |
| [`results/latency/capability_and_results.md`](./results/latency/capability_and_results.md) | #25 latency 실험 상세 로그 |
| [`results/decomposition/claim_decomposer_smoke_review.md`](./results/decomposition/claim_decomposer_smoke_review.md) | Claim Decomposer 검증, self-containment 수정 경위 |
| [`results/verifier/verifier_smoke_review.md`](./results/verifier/verifier_smoke_review.md) | Verifier client 스모크 테스트 |
| [`results/normalization/canonical_products_review.md`](./results/normalization/canonical_products_review.md) | canonical product schema 정리 |
| [`results/profiling/eval_design_review.md`](./results/profiling/eval_design_review.md) | 데이터 프로파일링 → eval 설계 재검토 |
| [`results/profiling/deposit_products_dashboard.html`](./results/profiling/deposit_products_dashboard.html) | 원본 데이터 프로파일링 대시보드 |
| [`prompts/README.md`](./prompts/README.md) | 프롬프트 전문 + Verifier 프롬프트 버전 이력 (Langfuse 기준) |
| [`data/sample/README.md`](./data/sample/README.md) | 마스킹 샘플 설명 + 실제 데이터 재현 절차 |

설계·판정 기준·모델 후보·실행 환경·데이터 방침 등 프로젝트 전체 설계는
[CLAUDE.md](./CLAUDE.md) 참고.

## 실행

```bash
# vLLM 서버 (WSL2 + Docker Desktop, RTX 4070 Laptop 8GB 기준)
scripts/run_vllm_container.sh

# 최종 eval 재실행
python -m src.eval.run_eval --split test --model qwen --prompt-version production
```

`.env.example`을 `.env`로 복사해 `FINLIFE_API_KEY`(금융상품 API 수집용),
`ANTHROPIC_API_KEY`(Claim Decomposer), `LANGFUSE_*`(trace/프롬프트 관리) 등 필요한 키를 채운다.

eval을 직접 돌리려면 상품 데이터를 먼저 수집해야 한다 (repo에 포함되지 않음) —
[`data/sample/README.md`](./data/sample/README.md)의 재현 절차 참고.
