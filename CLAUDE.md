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

### Docker 시크릿 전달 원칙

- `FINLIFE_API_KEY`는 금융상품 API 수집 코드에만 필요하다. **vLLM inference container에는 전달하지 않는다.**
- 컨테이너에 secret이 필요한 경우: image build 단계에 bake하지 않고, Dockerfile `ENV`로 직접 넣지 않는다. runtime env / env-file 방식으로만 전달.
- API key 값은 stdout, 파일, Git diff, **Docker image**에도 절대 출력/저장하지 않는다.

## 데이터

- 소스: 금융감독원 `금융상품 한눈에` Open API. 인증키는 이미 발급됨.
- 범위: **은행권 정기예금만** (적금·대출 등 이번 범위 제외).
- API 제한: 일일 조회 10,000건, 기본 1회 조회 100건. Live service가 아니라 snapshot dataset 구축이므로 충분.
- 첫 raw 데이터 확인 후 `baseList`/`optionList`를 상품 식별자 기준으로 join해 canonical record로 정규화한다. **raw 응답을 보기 전에 과도한 abstraction을 만들지 않는다.**

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
│   ├── raw/           # API snapshot, 인증키 미포함
│   ├── normalized/    # canonical product record
│   ├── smoke/ pilot/ dev/ test/
├── src/
│   ├── ingest/        # fetch_finlife.py, normalize_products.py
│   ├── decomposition/ # claim_decomposer.py
│   ├── verifier/      # client.py, schemas.py
│   └── eval/          # metrics.py, run_eval.py, failure_analysis.py
├── prompts/
│   ├── decomposer/
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

우선순위: **모델 로드 최우선 체크(Kanana/Qwen 분리, WSL2/Docker와 무관하게 병렬) → WSL2 GPU 확인 → Docker Desktop + WSL2 통합 → Docker GPU smoke → vLLM 공식 container smoke → 실제 후보 모델 vLLM serving → Finlife API 첫 호출 → 데이터 프로파일링 → canonical schema 초안**. 상세 체크리스트는 원본 핸드오프 문서 참조 (로컬 경로는 memory의 reference 항목 참고 — Docker 도입판 문서 포함).

각 작업 블록은 GitHub 이슈로 트래킹한다: [#1](../../issues/1)(완료, repo/secret) · [#2](../../issues/2)(모델 로드 체크) · [#3](../../issues/3)(WSL2/vLLM — Docker 단계 포함하도록 범위 확장됨, 착수 시 WSL2 GPU/Docker Desktop/Docker GPU smoke/vLLM container smoke로 세분화 예정) · [#4](../../issues/4)(API 첫 호출) · [#5](../../issues/5)(데이터 프로파일링) · [#6](../../issues/6)(canonical schema) · [#7](../../issues/7)(모델 serving smoke — 심화).

## Git / 이슈 관리 방침

1주 솔로 프로젝트 규모에 맞춘 경량 워크플로우. 무겁게 가지 않는다 (required review, CI 게이트, project board, milestone 등은 쓰지 않음).

- **이슈**: 핸드오프 문서 24번 섹션(A~F) 작업 블록 단위로 하나씩. 세부 항목은 이슈 본문 체크박스로 관리하고, 작업하다 새로 쪼개야 할 하위 작업이 생기면 그때 이슈를 추가한다.
- **브랜치**: `<이슈번호>-slug` (예: `4-finlife-api-fetch`).
- **PR**: 이슈 하나당 PR 하나. PR 본문에 `Closes #N`을 넣어 머지 시 이슈가 자동으로 닫히게 한다. 리뷰어는 없으므로 self-merge, **squash merge**로 히스토리를 깔끔하게 유지한다.
- **커밋 메시지**: `feat:` / `fix:` / `chore:` / `docs:` 정도의 최소 prefix만 사용.
- 이미 끝난 작업(예: repo/secret 초기 설정)을 뒤늦게 이슈로 추적할 때는, 이슈를 만들고 관련 커밋 SHA를 comment로 남긴 뒤 바로 닫아 히스토리만 남긴다.
