# finance_verifier

금융 답변 검증용 소형 LLM Verifier 프로젝트. AI 엔지니어 1주 과제.

## 핵심 질문

대형 LLM이 생성한 금융 답변을 매번 대형 모델로 검증하는 건 비용·지연이 크다. 제한된 역할의 3~4B급 로컬 모델을 검증 전용 서브에이전트로 써서, 잘못되거나 근거 부족한 금융 답변을 얼마나 안정적으로 차단할 수 있는가.

Retrieval correctness ≠ Answer correctness — 검색이 정확한 근거를 찾아도 생성 과정에서 조건이 누락/반전/일반화되며 의미가 바뀔 수 있다. 이걸 독립된 Verifier 단계로 잡아낸다.

## 파이프라인

```
금융 답변 → Claim Decomposer → Atomic Claim(s) → Verifier SLM → SUPPORTED | UNSUPPORTED | INSUFFICIENT
```

- Claim Decomposition은 나중으로 미루지 않고 현재 파이프라인에 포함한다. 최소 sanity check만: **Atomicity**(claim 하나에 사실판단 하나), **Coverage**(원 answer의 주요 주장 누락 여부).
- Verifier는 새 답변을 생성하지 않는다. `(Evidence, Atomic Claim)`을 받아 근거 관계만 판정한다.
- Component Eval(Gold Claim → Verifier)과 End-to-End Eval(Answer → Decomposer → Verifier)을 구분해서 본다.
- **Claim Decomposer는 로컬 SLM(Qwen/Kanana)이 아니라 Claude API(`claude-sonnet-5`)로 돈다.** 로컬 GPU는 RAM 여유가 없고, Verifier 후보 모델의 분해 태스크 성능도 검증되지 않아 별도 호출로 분리했다(#12). Claim Dataset용 synthetic 답변 증강도 동일하게 Claude API를 쓴다. `ANTHROPIC_API_KEY`를 `.env`에 `FINLIFE_API_KEY`와 같은 방식으로 저장한다(API Key 보안 원칙 동일 적용 — 로그/커밋에 노출 금지).
- **답변 1개가 여러 개의 독립적인 atomic claim으로 쪼개지는 건 예외가 아니라 정상이다.** 특히 Level 2(조건문 — spcl_cnd의 AND/OR, mtrt_int의 구간)는 실제로 이렇게 쪼개지는 게 기본값이다(#12에서 확인). 그래서 "답변 1개 = claim 1개 = gold label 1개"로 가정하면 안 되고, 답변에 주입한 오류가 어느 atomic claim에 해당하는지 식별해서 그 claim에만 label을 붙이고 나머지는 evidence를 충실히 반영한 SUPPORTED가 기본값이 되도록 설계한다.

## Verifier 출력 스키마

```json
{
  "verdict": "SUPPORTED | UNSUPPORTED | INSUFFICIENT",
  "evidence": "판정 근거가 되는 문장",
  "reason": "판정 이유"
}
```

`confidence` 필드는 핵심 태스크가 아니므로 우선 제외.

### 판정 기준 (경계를 일관되게 유지)

- **SUPPORTED**: evidence가 claim을 직접 뒷받침
- **UNSUPPORTED**: evidence가 claim과 **명시적으로 충돌**
- **INSUFFICIENT**: evidence에 판단 정보 **자체가 없음** (충돌이 아니라 정보 부재)

## 모델 후보 (2개로 제한)

| 후보 | 체크포인트 | 정밀도 / 실행경로 |
|---|---|---|
| A — Qwen3.5-4B | `Intel/Qwen3.5-4B-int4-AutoRound` | INT4 AutoRound → vLLM |
| B — Kanana-2-3B-Instruct | `kakaocorp/kanana-2-3b-instruct` | BF16 native → vLLM/Transformers |

두 후보는 양자화 조건이 다르므로(INT4 vs BF16) 성능차를 순수 모델 차이로 해석하지 않는다. 비교 목적은 "동일 8GB GPU 환경에서 각자 신뢰 가능한 실행 경로를 썼을 때 어떤 조합이 Verifier 역할에 더 적합한가". 가능한 한 inference engine / prompt / context length / generation config / temperature / max tokens / eval dataset은 통일한다.

**Qwen3.5는 기본적으로 thinking(장문 추론) 모드로 응답한다.** `apply_chat_template(..., enable_thinking=False)`를 넘기지 않으면 최종 답변 전에 영어 위주의 긴 "Thinking Process"를 생성하며(512 토큰으로도 안 끝남, 중간에 한자 혼입도 관찰됨), Verifier의 엄격한 JSON 스키마 출력과 맞지 않는다. Verifier client에서는 반드시 `enable_thinking=False`로 호출한다. **vLLM OpenAI 호환 엔드포인트로 호출할 때는** (Transformers 네이티브 `apply_chat_template` kwarg가 아니라) 요청 body에 `"chat_template_kwargs": {"enable_thinking": false}`를 넣어야 동일하게 적용된다 (#7에서 재현 확인 — 이거 없으면 80 토큰으로도 thinking 중간에 잘림).

**모델 로드 체크는 순서가 중요하다.** "체크포인트가 Transformers로 로드되는가"는 WSL2/Docker 세팅과 무관하게 최우선·병렬로 바로 확인한다(리포 세팅 직후). Kanana는 이 단계에서 실패하면 후보 자체를 재검토해야 하는 리스크가 크다. **Qwen AutoRound는 Windows native Transformers에서 실패해도 바로 탈락시키지 않는다** — 모델 아키텍처 문제 / AutoRound·quantization backend 문제 / Windows backend 문제를 분리해서 보고, 최종 판단은 WSL2 + Docker + vLLM 경로에서 실제 serving 가능한지까지 확인한 뒤 내린다. "vLLM에서 이 아키텍처가 도는가"는 WSL2+Docker+vLLM 세팅 이후에 확인해도 된다 — vLLM은 미지원 아키텍처를 Transformers backend로 fallback할 수 있어서 여기서 막혀도 회복 여지가 있다.

## 실행 환경

최종 아키텍처 (Docker 포함, 확정):

```
Windows 11 + RTX 4070 Laptop 8GB
    ↓
WSL2 (Ubuntu)
    ↓
Docker Desktop (WSL2 backend)
    ↓
Linux GPU Container
    ↓
vLLM (공식 vllm/vllm-openai 이미지)
    ↓
OpenAI-compatible endpoint
    ↓
Eval / Claim Decomposer / Verifier client (WSL2 또는 host Python, GPU 불필요)
```

Docker는 연구 대상이 아니라 지원 계층이다. 목적은 둘로 제한한다.

1. vLLM/CUDA/Python 의존성을 이미지로 고정해 재현 가능한 추론 서버 구성
2. Qwen/Kanana 두 후보를 동일한 serving interface로 평가

Eval harness / Claim Decomposer / 데이터 수집 코드는 처음부터 컨테이너화하지 않는다 — 이번 프로젝트에서는 **vLLM serving layer만** 컨테이너로 격리한다.

- WSL 내부에 NVIDIA Linux display driver, Docker Engine을 각각 중복 설치하지 않는다 (Windows driver + Docker Desktop WSL integration만 사용).
- 모델/프로젝트 파일과 Hugging Face cache(`~/.cache/huggingface`)는 WSL filesystem에 두고 container에 volume mount (재기동 시 재다운로드 방지).
- 직접 CUDA base image를 만들지 않고 vLLM 공식 이미지(`vllm/vllm-openai`)를 우선 사용한다. `docker run` 위주로 시작하고, Compose는 서비스가 여러 개로 늘어났을 때만 검토한다.
- 계층(모델 로드 → WSL2 GPU → Docker GPU → vLLM container)을 한 번에 묶어서 디버깅하지 않는다 — 각 단계가 독립적으로 성공하는지 순서대로 확인한다.
- WSL2/Docker/vLLM 세팅이 막히면 모델 검증은 Transformers 경로로 임시 진행.

### WSL2 + vLLM 컨테이너에서 실제로 겪은 이슈 (재현 시 참고)

- **UVA(pinned memory) 에러** (`RuntimeError: UVA is not available`): vLLM은 WSL2를 감지하면 기본적으로 pinned memory를 꺼두는데, 최신 V1 엔진 일부 버퍼가 이를 필수로 요구해 실패한다. `-e VLLM_WSL2_ENABLE_PIN_MEMORY=1`로 해결.
- **GPU 메모리 예산 부족** (`No available memory for the cache blocks`): WSL2 GPU 가상화 오버헤드로 실제 가용 VRAM이 8GB보다 적다 (컨테이너 내부 기준 관측치 ~6.89GB, Windows `nvidia-smi`는 이 오버헤드를 안 보여줌). `--enforce-eager`(CUDA graph/컴파일 비활성화)로 여유를 확보한다.
- **Git Bash `-v` 볼륨 마운트 경로 변환 버그**: `-v "/c/Users/.../huggingface:/root/.cache/huggingface"` 형태로 주면 MSYS가 경로를 잘못 변환해 마운트가 조용히 실패한다 (컨테이너 안에 디렉터리가 안 생겨서, 캐시가 있어도 매번 재다운로드하는 것처럼 보임). **Windows + Git Bash에서 `docker run -v`를 쓸 때는 항상** `MSYS_NO_PATHCONV=1` + Windows 스타일 호스트 경로(`C:\Users\...`)를 쓴다. `scripts/run_vllm_container.sh`에 반영됨.

두 후보 모두 vLLM 공식 이미지에서 실제 서빙 확인됨 (`docker exec`/curl로 `/v1/chat/completions` 응답 확인). **Qwen AutoRound는 vLLM에서 `MarlinLinearKernel`(AutoGPTQ 계열 INT4 전용 fused 커널)을 자동 채택해 Windows Transformers fallback(~1.6 tok/s)보다 훨씬 빠르게 돈다** — Windows에서 겪은 "INT4가 BF16보다 20배 느림" 문제는 vLLM 단계에서 해소됨. 실제 latency/VRAM 정식 비교는 이슈 #7(모델 serving smoke 심화)에서 진행.

### Docker 시크릿 전달 원칙

- `FINLIFE_API_KEY`는 금융상품 API 수집 코드에만 필요하다. **vLLM inference container에는 전달하지 않는다.**
- 컨테이너에 secret이 필요한 경우: image build 단계에 bake하지 않고, Dockerfile `ENV`로 직접 넣지 않는다. runtime env / env-file 방식으로만 전달.
- API key 값은 stdout, 파일, Git diff, **Docker image**에도 절대 출력/저장하지 않는다.

## 데이터

- 소스: 금융감독원 `금융상품 한눈에` Open API. 인증키는 이미 발급됨.
- 범위: **은행권 정기예금만** (적금·대출 등 이번 범위 제외).
- API 제한: 일일 조회 10,000건, 기본 1회 조회 100건. Live service가 아니라 snapshot dataset 구축이므로 충분.
- 첫 raw 데이터 확인 후 `baseList`/`optionList`를 상품 식별자 기준으로 join해 canonical record로 정규화한다. **raw 응답을 보기 전에 과도한 abstraction을 만들지 않는다.**
- **`data/` 아래 상품 데이터셋 파일은 공개 리포에 올리지 않는다.** 인증키 발급을 전제로 제공되는 데이터를 공개 리포에 그대로 올리는 건 그 제공 방식을 우회하는 재배포에 가깝다. `data/raw`·`data/normalized`·`data/{smoke,pilot,dev,test}`는 `.gitignore`로 로컬 전용이며, 구조 확인용 마스킹 샘플(`data/sample/`, 합성 데이터)만 추적한다.

### API Key 보안 — 반드시 지킬 것

- 인증키를 Git, README, Markdown, 로그, 스크린샷, 프롬프트에 절대 직접 기록하지 않는다.
- 루트 `.env`에 `FINLIFE_API_KEY=<key>`로 저장, 코드에서는 환경변수로 읽는다. `.gitignore`에 `.env` 포함 (설정됨).
- `.env.example`에는 `FINLIFE_API_KEY=` 만 기록한다.
- API 요청 URL을 그대로 print하지 않는다. `auth` 쿼리 파라미터가 포함된 URL을 로그 파일에 저장하지 않는다.
- 디버깅 로그에서는 인증키를 `***`로 마스킹한다. raw API response에는 인증키를 포함시키지 않는다.

## 데이터 난이도 / Failure Taxonomy

- Level 1 (정형 필드: 기간/기본금리/최고금리/가입대상/가입방법/한도) — 숫자·기간·가입대상 변경, 기본/최고금리 혼동.
- Level 2 (자유서술: `spcl_cnd`/`mtrt_int`/`etc_note`) — 우대조건 일부 누락, AND/OR 반전, 조건부 혜택 일반화, 만기/중도해지 조건 혼동, 예외조건 삭제, 적용 시점 변경. **모델 비교에서 더 중요한 난이도 축일 가능성이 높음.**

Taxonomy (dataset metadata + 실패 분석 축으로 유지):
`numeric_error`, `term_error`, `eligibility_error`, `condition_reversal`, `condition_omission`, `base_vs_max_rate`, `conditional_benefit_generalization`, `missing_information`

## Eval 단계

```
Smoke (6~12 samples)  → 파이프라인이 한 바퀴 도는지만 확인. 모델 우열 판단 X.
Pilot (30~50 samples) → Qwen vs Kanana 모델 선정. 동일 prompt/dataset. 선정 후 두 모델 비교는 그만둔다.
Dev                   → Prompt/판정 정책 개선.
Test (unseen)         → 최종 성능 확인. Dev에서 반복 사용한 셋 재사용 금지.
```

## 평가 지표 우선순위

```
False Accept Rate (핵심)         — 실제 UNSUPPORTED/INSUFFICIENT를 SUPPORTED로 승인한 비율
        ↓
UNSUPPORTED Recall / Macro F1
        ↓
Schema Valid Rate                — JSON parse, 필수 필드, verdict enum, field type (Pydantic)
        ↓
Latency / Peak VRAM              — batch=1, temperature=0, 고정 max_tokens, warm-up 제외, p50/p95
```

Accuracy는 보조 지표일 뿐 우선순위에 넣지 않는다.

## 지금 하지 않는 것

전체 Agent orchestration, LangGraph, 실제 서비스 완성, VLM/PDF 처리, 자체 AutoRound 양자화, Fine-tuning, LLM-as-a-Judge, 대출/적금 등 상품군 확대, 대규모 RAG, Kubernetes, custom CUDA base image 직접 구축, 복잡한 multi-service Docker Compose, CI/CD·registry/deployment pipeline, 데이터/eval 코드 전체를 무리하게 컨테이너화. 중심은 **Claim Decomposition + Verifier + 평가/회귀 관리**. Docker는 **재현 가능한 vLLM serving 환경을 제공하는 지원 계층**으로만 쓴다.

## 프로젝트 구조

```
finance_verifier/
├── .env               # 실제 키 (git 추적 안 됨)
├── .env.example
├── data/
│   ├── sample/        # 마스킹 샘플만 git 추적 (합성 데이터)
│   ├── raw/           # API snapshot (로컬 전용, .gitignore)
│   ├── normalized/    # canonical product record (로컬 전용)
│   ├── smoke/ pilot/ dev/ test/   # claim dataset (로컬 전용)
├── src/
│   ├── ingest/        # fetch_finlife.py, normalize_products.py
│   ├── decomposition/ # claim_decomposer.py
│   ├── verifier/      # client.py, schemas.py
│   └── eval/          # metrics.py, run_eval.py, failure_analysis.py
├── prompts/           # 실제 사용한 프롬프트 전문 스냅샷 (SSOT는 Langfuse + 코드)
│   ├── decomposer/
│   ├── dataset/
│   └── verifier/
├── scripts/
│   ├── docker_gpu_smoke.sh
│   └── run_vllm_container.sh
└── results/
    ├── model_selection/ prompt_versions/ final/
```

이 구조는 제안안이다. 구현 과정에서 불필요한 디렉터리는 줄여도 된다. 현재 단계에서 별도 Dockerfile/Compose 파일을 반드시 만들 필요는 없다 — vLLM 공식 이미지 + `docker run`으로 시작하고, 반복 실행 명령이 길어지면 `scripts/run_vllm_container.sh`로 고정한다.

## 완료 기준 (1단계)

```
1. Qwen / Kanana 두 후보 최소 inference 확인
2. WSL2 + GPU 정상
3. Docker Desktop WSL2 integration 정상
4. Docker container 내부 GPU 정상
5. vLLM 공식 container + 작은 모델 endpoint 정상
6. 실제 후보 모델의 vLLM serving 가능 여부 확인
7. 금융상품 한눈에 API 호출 정상
8. 은행권 정기예금 raw snapshot 확보
9. 실제 baseList / optionList 구조 확인
```

## 다음 작업 (핸드오프 문서 기준)

1단계(환경 구성 + raw snapshot)부터 #14(Eval Harness + Pilot 모델 선정)·#23(Test 데이터셋 구축 + 최종 eval)·#25(Qwen Latency 개선 실험)·#15(최종 결과 정리)까지 전부 완료 — **Qwen3.5-4B-int4-AutoRound를 유일한 Verifier 후보로 확정**했고(Kanana는 비교 대상에서 제외), 프롬프트는 v2로 Test 단계까지 고정했다(`results/eval/smoke_eval_review.md`의 "모델 선정 확정" 절, `results/eval/test_eval_review.md` 참고). Test(53건, unseen)에서 확정 약점이 두 가지로 정리됨: ①INSUFFICIENT↔UNSUPPORTED 경계 혼동, ②`condition_omission`(AND 복합조건 일부 인용 시 누락 못 잡음). #25에서 CUDA Graph 활성화로 batch=1 decode ~3.6배 개선을 확인하고 서빙 설정을 `--max-num-seqs=4`로 채택했다(`results/model_selection/qwen_latency_diagnosis.md`의 "이슈 #25 실측 결과" 절 참고). #15에서 위 내용을 `results/final/report.md`(기술 리포트)로 정리하고, 비전문가용 발표 자료(`results/final/report_dashboard.html`)와 `README.md` 개편까지 마쳤다. **1주 과제 스코프의 전체 작업이 완료됐다** — 이후 착수할 항목은 CLAUDE.md "지금 하지 않는 것" 절과 `results/final/report.md` §9(다음 실험 후보)에 후속 과제로 남겨둔다.

각 작업 블록은 GitHub 이슈로 트래킹한다: [#1](../../issues/1)(완료, repo/secret) · [#2](../../issues/2)(완료, 모델 로드 체크) · [#3](../../issues/3)(완료, WSL2/vLLM) · [#4](../../issues/4)(완료, API 첫 호출) · [#5](../../issues/5)(완료, 데이터 프로파일링을 Eval Design 관점으로 재검토) · [#6](../../issues/6)(완료, canonical schema / Claim metadata) · [#7](../../issues/7)(완료, 모델 serving smoke — 심화) · [#12](../../issues/12)(완료, Claim Decomposer 구현) · [#13](../../issues/13)(완료, Verifier Client 구현) · [#14](../../issues/14)(완료, Eval Harness 구현 + Pilot 모델 선정 — Qwen 확정) · [#23](../../issues/23)(완료, Test 데이터셋 구축 — unseen, Claude+Codex 공동 생성, 확정 약점 2종 발견) · [#25](../../issues/25)(완료, Qwen Latency 개선 실험 — CUDA graph 활성화로 3.6배 개선, max-num-seqs=4 채택) · [#15](../../issues/15)(완료, 최종 결과 정리 — 기술 리포트 + 발표용 대시보드 + README 개편).

### Langfuse

**#14 착수 직후 가장 먼저 붙였다** (metrics.py/run_eval.py보다 먼저) — 나중에 붙이면 그전에 돌린 Pilot/Dev 결과가 trace로 안 남기 때문. **Langfuse Cloud(US 리전, 기존 계정) 사용, self-host 안 함** — 데이터가 공개 Finlife 공시 데이터라 PII 이슈가 없고, self-host는 Postgres/ClickHouse/Redis가 묶인 멀티서비스 스택이라 "지금 하지 않는 것" 항목(복잡한 multi-service Docker Compose)에 걸린다.

Verifier 호출(`src/verifier/client.py`)마다 Langfuse generation observation으로 trace를 남기고, 아래 metadata를 붙인다:

```
model, prompt_version, product_id, source_field, gold_label, error_type, reasoning_type, dataset_split
```

- **SDK는 v4** — `get_client()`/`start_as_current_observation(as_type="generation", ...)` 패턴. metadata가 `dict[str, str]`로 제한되므로(200자, 리스트/비문자열은 깨짐) `reasoning_type` 같은 리스트는 문자열로 join해서 넣는다(`src/verifier/client.py`의 `_flatten_metadata`).
- **prompt_version은 수동 관리 안 함** — Verifier system prompt를 Langfuse Prompt Management로 옮기고(`get_or_create_prompt`, 최초 실행 시 로컬 기본값으로 자동 시딩) `prompt=` 인자로 generation에 링크하면 `prompt_version`이 자동으로 추적된다.
- **env**: `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_BASE_URL`(v4 권장 이름, 레거시 `LANGFUSE_HOST`도 동작은 함). `.env.example` 참고.

별도 이슈로 트래킹하지 않고 #14(Eval Harness) 작업 범위 안에서 붙인다 — 1주 솔로 프로젝트 규모에서 별도 인프라로 분리할 정도는 아님.

## Git / 이슈 관리 방침

1주 솔로 프로젝트 규모에 맞춘 경량 워크플로우. 무겁게 가지 않는다 (required review, CI 게이트, project board, milestone 등은 쓰지 않음).

- **이슈**: 핸드오프 문서 24번 섹션(A~F) 작업 블록 단위로 하나씩. 세부 항목은 이슈 본문 체크박스로 관리하고, 작업하다 새로 쪼개야 할 하위 작업이 생기면 그때 이슈를 추가한다.
- **브랜치**: `<이슈번호>-slug` (예: `4-finlife-api-fetch`).
- **PR**: 이슈 하나당 PR 하나. PR 본문에 `Closes #N`을 넣어 머지 시 이슈가 자동으로 닫히게 한다. 리뷰어는 없으므로 self-merge, **squash merge**로 히스토리를 깔끔하게 유지한다.
- **커밋 메시지**: `feat:` / `fix:` / `chore:` / `docs:` 정도의 최소 prefix만 사용.
- 이미 끝난 작업(예: repo/secret 초기 설정)을 뒤늦게 이슈로 추적할 때는, 이슈를 만들고 관련 커밋 SHA를 comment로 남긴 뒤 바로 닫아 히스토리만 남긴다.
