# Qwen Latency 이상 진단 — "INT4가 BF16보다 느림" 실측 확인

## 배경

Pilot 비교(`results/eval/smoke_eval_review.md`)에서 Qwen3.5-4B-int4-AutoRound가
Kanana-2-3B-instruct(BF16)보다 latency가 더 느리게 나왔다(p50 7.83s vs 3.84s, 65건 기준).
메모리 대역폭 기준 이론 계산으로는 반대여야 한다 — RTX 4070 Laptop 8GB(~200GB/s 대역폭)
가정 시 배치=1 decode는 메모리 대역폭 bound이므로, INT4(가중치 ~2GB)가 BF16(가중치
~6GB)보다 이론상 3배 가까이 빨라야 한다. 사용자가 별도로 준비한 진단 지시서
(`qwen_latency_debug_handoff.md`, 가설 1~6 제시)를 기반으로 실측 검증했다.

## 검증 결과 — 가설별

| 가설 | 결과 | 근거 |
|---|---|---|
| 1. `enable_thinking` 미적용(thinking 누출) | **기각** | Qwen v2 eval 63건의 `raw_content` 전수 확인 — `<think>` 태그 0건, 전부 `{`로 바로 시작하는 valid JSON |
| 2. warm-up 시간이 latency에 섞임 | **기각** | `run_eval.py`가 warm-up 콜을 이미 latency 측정에서 제외; steady-state 상태에서도 ~2배 격차 유지 |
| 3. `--quantization` 미인식 | **기각** | 서버 로그에 `Using MarlinLinearKernel for AutoGPTQLinearMethod`, `quantization=inc` 명시; 가중치 로드 3.73 GiB(INT4 크기 — BF16이었다면 ~8GB) |
| 4. `--mamba-cache-mode=align` 플래그 누락 | **정정: 실존함, 원래 판단이 틀렸음** | 최초 확인 때 `vllm serve --help`(그룹 없이)로만 검색해 놓쳤다 — `vllm serve --help=all`로 다시 확인하니 `--mamba-cache-mode {align,all,none}`(기본값 `none`), `--mamba-backend`, `--use-replayssm`, `--replayssm-buffer-len` 등이 실제로 존재한다(`EngineArgs.add_cli_args()` argparse 직접 introspection으로 재검증). 다만 이 옵션은 **prefix caching 시 recurrent state를 언제 저장할지 정하는 정책**이지 raw decode 속도를 올리는 옵션이 아니고, 우리 워크로드(claim마다 evidence가 달라 공유되는 prefix가 없는 batch=1 단발 요청)에서는 prefix caching 자체가 적용될 상황이 아니라 이 플래그를 켜도 실질적 이득은 없을 것으로 판단된다. 대신 `--use-replayssm`(기본 `mamba_cache_mode=none`이 이미 이 옵션의 전제조건을 만족)는 decode 경로 자체를 건드리는 별개의 최적화라 시도해볼 가치가 있다 — 아래 "나중에 볼 것" 참고 |
| 5. 두 모델 request payload가 실제로 다름 | **기각** | `src/verifier/client.py` 기준 system prompt·temperature(0)·max_tokens(512) 완전히 동일, 모델명/`chat_template_kwargs`만 차이 |
| vLLM 버전이 이 아키텍처와 안 맞음(사용자 추가 의문) | **근거 부족 — 오히려 반대** | 로그에 `qwen_gdn_linear_attn.py`, `qwen_triton_warmup.py`(`model_type=qwen3_5_text` 명시) 등 Qwen3.5 GDN 하이브리드 구조 전용 코드가 확인됨 — 미지원 아키텍처의 fallback이 아니라 전용 경로. 이미지도 `latest` 태그라 더 올릴 버전이 없음 |
| 6. INT4 dequant/GDN 커널 오버헤드가 대역폭 이득을 상쇄 | **가장 유력 — 실측으로 뒷받침됨** | 아래 실측 참고 |

## 실측 — 동일 조건 5건, `usage.completion_tokens` 기준 순수 decode 속도

`src/verifier/client.SYSTEM_PROMPT`(v2), `temperature=0`, `max_tokens=512`, 동일 5개 claim
(`data/smoke/claim_dataset.json` smoke split 앞 5건), 동일 vLLM 버전·동일 GPU·
`--enforce-eager --max-model-len 2048`(커밋된 표준 설정) 기준.

| claim | Qwen prompt_tok | Qwen completion_tok | Qwen tok/s | Kanana prompt_tok | Kanana completion_tok | Kanana tok/s |
|---|---|---|---|---|---|---|
| p002_c01 | 232 | 88 | 9.75 | 195 | 69 | 18.25 |
| p002_c02_1 | 227 | 91 | 13.11 | 192 | 66 | 24.21 |
| p002_c02_2 | 232 | 87 | 12.13 | 195 | 78 | 23.86 |
| p002_c03 | 232 | 89 | 14.13 | 196 | 78 | 23.24 |
| p003_c01_1 | 329 | 98 | 12.98 | 278 | 65 | 23.25 |
| **평균** | | | **~12.4 tok/s** | | | **~22.6 tok/s** |

Kanana가 순수 토큰당 생성 속도에서도 ~1.8배 빠르다. 이건 출력 길이 차이나 warm-up 오염
때문이 아니다 — 실제 `usage.completion_tokens`를 직접 재서 확인한 값이라, 이론(INT4가
메모리 대역폭상 유리)과 정반대 방향의 결과가 측정 오류가 아니라 진짜라는 뜻이다.

prompt_tokens가 Qwen과 Kanana 사이에 다른 것(예: 232 vs 195)은 토크나이저 차이일 뿐 버그
아님 — 같은 원문 텍스트를 서로 다른 tokenizer가 다른 토큰 수로 쪼갠 것.

## 결론

**설정 실수가 아니라, 이 GPU·이 vLLM 버전·이 모델 조합에서 나타나는 구조적 현상으로
잠정 결론짓는다.** Qwen3.5의 GDN(Gated DeltaNet, Mamba 계열 선형 어텐션) 하이브리드
레이어는 Triton 커스텀 커널(`Using Triton/FLA GDN prefill kernel` 로그로 확인)에 의존하는데,
이 커널이 Kanana가 쓰는 매우 성숙한 FlashAttention2 경로만큼 최적화되지 않았을 가능성이
높다. 서버 자체가 시동 로그에서 "Marlin 커널이 작은 size_n(배치=1과 정확히 일치)에서
`VLLM_MARLIN_USE_ATOMIC_ADD=1`를 켜면 더 빠를 수 있다"고 힌트를 준 것도 이 해석과 부합한다
— INT4 GEMM 커널 자체도 소배치 환경에 완전히 튜닝되어 있지 않다는 신호.

즉 "INT4가 이론상 3배 빨라야 하는데 실제론 2배 느리다"는 이번 프로젝트의 실측
결과이지, 설정 버그가 아니다. 이는 CLAUDE.md가 이미 명시한 방법론("두 후보는 양자화
조건이 다르므로 성능차를 순수 모델 차이로 해석하지 않는다")과도 맞닿아 있다 — INT4 vs
BF16 비교는 정밀도 자체의 순수 비교가 아니라 "동일 8GB 환경에서 각자 신뢰 가능한 실행
경로를 썼을 때의 실측"이며, 이번 결과는 그 실행 경로(vLLM + AutoRound INT4 + GDN 하이브리드)의
현재 실측치로 그대로 받아들인다.

## `VLLM_MARLIN_USE_ATOMIC_ADD=1` 검증 — 효과 없음

동일한 5-claim 프로브를 `--enforce-eager --max-model-len 2048` + `VLLM_MARLIN_USE_ATOMIC_ADD=1`로
재실행.

| claim | 기본(baseline) tok/s | atomic_add tok/s |
|---|---|---|
| p002_c01 | 9.75 | 10.19 |
| p002_c02_1 | 13.11 | 13.28 |
| p002_c02_2 | 12.13 | 13.42 |
| p002_c03 | 14.13 | 13.30 |
| p003_c01_1 | 12.98 | 10.83 |
| **평균** | **~12.42** | **~12.20** |

측정 노이즈 수준의 차이(완성 토큰 수도 거의 동일)로, 실질적 개선 없음. `VLLM_MARLIN_USE_ATOMIC_ADD`는
Marlin의 INT4 GEMM(선형 레이어) 연산에 대한 최적화인데 여기서 효과가 없다는 건, **병목이 Marlin
선형 레이어가 아니라 GDN/Mamba 연산 경로 자체에 있다는 가설 6을 오히려 더 뒷받침**한다.

## `--dtype float16` 검증 — 효과 없음 (오히려 소폭 하락)

서버 로그가 자체적으로 "Marlin kernel with bf16 on GPUs before SM90 — consider fp16" 경고를
냈다. RTX 4070 Laptop(Ada Lovelace, SM89)이 정확히 이 "SM90 이전" 조건에 해당해서, activation
dtype을 bf16 대신 fp16으로 강제 지정(`--dtype float16`, `enforce-eager`·`max-model-len 2048`은
동일)해 같은 5-claim 프로브로 재검증했다. 가중치는 여전히 INT4라 메모리 사용량은 동일(3.73 GiB) —
바뀐 건 activation 연산 dtype뿐.

| claim | bf16(baseline) tok/s | fp16 tok/s |
|---|---|---|
| p002_c01 | 9.75 | 9.35 |
| p002_c02_1 | 13.11 | 11.95 |
| p002_c02_2 | 12.13 | 12.75 |
| p002_c03 | 14.13 | 11.79 |
| p003_c01_1 | 12.98 | 12.69 |
| **평균** | **~12.42** | **~11.71** |

개선 없음 — 오히려 평균 6% 정도 더 느려졌다(노이즈 범위 내로 보이지만 최소한 "더 빠르다"는
근거는 전혀 없음). vLLM의 경고 문구가 예상한 이득이 이 모델·이 워크로드(batch=1, 짧은 생성 길이)
에서는 실현되지 않았다.

## 종합 결론 (atomic_add + fp16 검증 포함)

INT4 GEMM 커널(Marlin)을 겨냥한 두 가지 최적화(`VLLM_MARLIN_USE_ATOMIC_ADD=1`, `--dtype float16`)
모두 개선 효과가 없었다. 이로써 Qwen의 latency 열세는 "Marlin 커널 튜닝 문제"가 아니라 **GDN/Mamba
하이브리드 구조 자체의 vLLM 구현 성숙도 문제**로 더 확실히 좁혀졌다 — 병목이 선형 레이어(Marlin이
담당하는 부분)가 아니라 GDN/Mamba 레이어의 Triton 커널 경로에 있다는 뜻이다. 이 이상은 vLLM 내부
커널을 직접 프로파일링(예: `torch.profiler`로 레이어별 시간 분해)해야 확인 가능한 영역이고, 그
정도의 커널 최적화 작업은 이번 프로젝트 스코프(로컬 SLM 2개의 실행 가능성 비교, CLAUDE.md의 "지금
하지 않는 것" — 커스텀 CUDA/양자화 작업 포함)를 벗어난다.

**최종 결론**: Qwen INT4가 Kanana BF16보다 느린 건 설정 실수가 아니라, 현재 vLLM 버전의 Qwen3.5
GDN 하이브리드 지원이 아직 이 정도 최적화 수준이라는 실측 결과다. 이 값 그대로 받아들이고 모델
선정 논의로 넘어간다(`results/eval/smoke_eval_review.md`의 FAR/Recall 트레이드오프가 latency보다
우선순위가 높다는 CLAUDE.md 방침에 따라, 이 latency 격차가 Qwen 선정 자체를 뒤집을 근거는 아님).

## 다음 세션에 시도해볼 실측 후보 (플래그 존재 재검증 완료)

"atomic_add·fp16 다 안 통했다"는 결론은 Marlin GEMM 레벨 최적화 두 가지에 한정된 것이었다. 이후
`vllm serve --help=all` + `EngineArgs.add_cli_args()` argparse 직접 introspection으로 재검증한
결과, 다음은 실제로 존재하는 플래그이고 아직 하나도 시도하지 않았다:

- **`--gdn-prefill-backend {flashinfer,triton,cutedsl}`** (기본 auto→현재 로그 기준 triton 선택됨) —
  GDN prefill 커널 자체를 바꿔보는 가장 표적화된 실험. 우리가 병목으로 지목한 바로 그 레이어를
  직접 건드림.
- **`--use-replayssm`** (+ 필요시 `--replayssm-buffer-len`) — decode 시 매 스텝 전체 SSM state를
  다시 쓰지 않고 최근 입력을 버퍼링해 재사용하는 Mamba2 decode 커널. 전제조건(`mamba_cache_mode`가
  `none` 또는 `align`, Triton mamba backend)이 **기본값 그대로 이미 충족**돼 있어 추가 설정 없이
  이 플래그 하나만 켜도 된다. `--mamba-cache-mode`(prefix caching 정책, 우리 워크로드엔 무관— 위
  가설 4 정정 내용 참고)와는 별개로, decode 경로 자체를 바꾸는 옵션이라 latency에 직접 영향을 줄
  가능성이 있다.
- **CUDA graph batch=1 최소 구성**: `--enforce-eager` 제거 + `--max-num-seqs 1` + `--max-model-len 1024`
  + `-cc.cudagraph_mode=FULL_DECODE_ONLY` + `--cudagraph-capture-sizes 1` (모두 실제 존재 확인됨,
  `-cc`는 `--compilation-config`의 실제 shorthand — 우리 로그에서도 `-cc.mode=none` 형태로 이미
  관찰됨). `--max-num-seqs 1`이면 이전에 겪은 Mamba cache block 부족 크래시(64/49 한도 문제)도
  구조적으로 재발하지 않는다.
- **`--performance-mode interactivity`** (선택지 `balanced`/`interactivity`/`throughput`, 기본
  `balanced`) — small batch e2e latency 우선 모드, 우리 batch=1·request당 1 verdict 구조에 부합.
- **`--optimization-level {O0,O1,O2,O3}`** (기본 `O2`) — CUDA graph 실험이 성공하면 추가로 시도.

우선순위는 `--gdn-prefill-backend` → `--use-replayssm` → CUDA graph 최소 구성 → interactivity →
optimization-level 순으로 본다(표적화된 정도, 구현 난이도 순).

## 이슈 #25 실측 결과 — CUDA Graph 활성화로 batch=1 decode 3.6배 개선

위 5개 후보를 이슈 #25(`25-latency-optimization` 브랜치)에서 실제로 검증했다. 상세 실험 로그·표는
`results/latency/capability_and_results.md`에 있고, 여기(source of truth)에는 최종 결론만
옮겨 적는다.

### 결론: 유효한 레버는 딱 하나 — 서빙 용량(`max-num-seqs`) 재설계로 CUDA Graph를 켜는 것

| 후보 | 결과 |
|---|---|
| 1. `--gdn-prefill-backend {flashinfer,cutedsl}` | 이 GPU(Ada Lovelace, SM89)에서 미지원 → `auto`와 동일한 `triton`으로 자동 폴백. 측정 불가 |
| 2. `--use-replayssm` | 부팅 자체가 실패(Nemotron-H 전용, Qwen3.5 아키텍처엔 적용 불가) |
| 3. CUDA graph batch=1 최소 구성(`--enforce-eager` 제거 + `--max-num-seqs 1` + `--max-model-len 1024`) | **채택 — eager 대비 ~3.6배**(12.4→44.26 tok/s, Test 53건 전체 순차 실행 기준) |
| 4. `--performance-mode interactivity` | 3번 대비 차이 없음(43.94 tok/s, 노이즈 수준) |
| 5. `--optimization-level 3`(O3) | 3+4번 대비도 차이 없음(43.98 tok/s) |
| 3번의 decode-only 세부 튜닝(`-cc.cudagraph_mode=FULL_DECODE_ONLY`) | 실측 없이 스킵 — prefill은 요청당 1회뿐이라 손댈 여지가 이미 작고, 같은 계열인 4·5번이 효과 없었음 |

`--enforce-eager`를 그냥 빼면 기본 `max_num_seqs=256`이 GDN 하이브리드 구조의 Mamba cache
block 예산(8GB에서 확보 가능한 건 64개뿐)을 초과해 서버가 부팅도 못 하고 죽는다. `max_num_seqs`
를 워크로드 실제 크기로 낮추면 이 예산 문제가 사라지고 CUDA Graph가 정상적으로 capture된다 —
이게 유일하게 효과가 있었던 레버였다. GDN prefill 커널 선택이나 SSM state 재사용, 컴파일
강도 조절 같은 더 표적화된 옵션들은 이 GPU 세대·이 모델 아키텍처 조합에서는 전부 선택지가
아니었거나 이미 확보한 개선 이상으로 나아가지 못했다.

### 정확성 회귀 체크 — `--max-model-len` 2048→1024 축소로 인한 부작용 없음

Test(unseen) 53건 전체를 실제 Qwen tokenizer로 재확인한 결과 prompt 잘림 위험은 0건(최장
claim도 885/1024 토큰). 53건 재실행 결과 핵심 지표(False Accept Rate 0.1071, UNSUPPORTED
Recall 0.8846)는 baseline과 완전히 동일했고, 딱 1건(`p024_c01`, INSUFFICIENT)만 결과가
바뀌었다 — `temperature=0`에 동일 입력·동일 가중치인데도 eager vs CUDA-graph 실행 경로의
부동소수점 차이가 이미 알려진 취약 경계(INSUFFICIENT↔UNSUPPORTED)를 흔든 것으로 해석한다.
Macro F1 하락(0.8434→0.6151)은 INSUFFICIENT 표본이 2건뿐이라 1건 flip이 과대 증폭된 표본
크기 문제로, 핵심 지표(FAR·Recall)는 영향받지 않았다.

### 서빙 용량(`max-num-seqs`) 최종 채택 — 1이 아니라 4

`max-num-seqs`를 1/2/4/8로 스윕한 결과 처리량은 계속 늘지만 선형이 아니고(8배 동시요청에
3.9배), 개별 요청 latency는 늘어난다. GPU 피크 VRAM 증가도 완만하다(seqs 1→8 사이 약
410MB, WSL2 실사용 한도까지 여유 충분). 답변 1개가 여러 atomic claim으로 쪼개지는 실제
워크로드(Test셋 평균 1.89개, 최대 2개; Smoke셋 평균 5.82개, 최대 11개)를 감안해
**`--max-num-seqs=4`를 최종 서빙 설정으로 채택**했고, `scripts/run_vllm_container.sh`의
기본값도 이에 맞춰 갱신했다(`--enforce-eager` 제거, `--max-model-len 1024 --max-num-seqs 4`).

k6로 VU 1/4/8/16 부하테스트를 돌려 vLLM의 multi-user 서빙 장점도 확인했다 — 서버 용량(4)을
넘는 동시 사용자도 거부되지 않고 큐에서 대기하며(latency가 오버구독 비율에 거의 선형으로
증가), 4배 오버구독(VU=16)에서도 에러 0%·schema valid 100%.

각 옵션(`--enforce-eager`/`--max-num-seqs`/`--gdn-prefill-backend`/`--use-replayssm`/
`--performance-mode`/`--optimization-level`)이 정확히 무엇을 하는지는
`notes/vllm_serving_modes.md`(개인 메모, git 미추적)에 정리했다.

## 나중에 볼 것 (지금은 스코프 밖)

Qwen3.5의 GDN(선형 어텐션) 병목 자체를 vLLM/Triton 커널 레벨에서 해소하려는 외부 연구가 있다
(예: NVIDIA B200 등 최신 하드웨어에서 수작업 Triton 커널로 Qwen3.5-27B 추론을 최적화하는 시도).
다만 이는 데이터센터급 하드웨어·훨씬 큰 모델을 겨냥한 별개 작업이고, 우리가 직접 커스텀 GDN
Triton 커널을 작성/이식하는 건 CLAUDE.md가 명시한 "지금 하지 않는 것"(자체 양자화·커스텀 CUDA
커널 등)의 연장선이라 이번 1주 프로젝트 스코프에서는 진행하지 않는다. vLLM이 향후 버전에서
GDN 경로를 자체적으로 더 최적화하면(예: Marlin 대신 다른 INT4 커널, 또는 GDN 전용 fused 커널)
재검증할 가치가 있다 — 그때 다시 확인.
