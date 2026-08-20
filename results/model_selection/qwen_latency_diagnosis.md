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
| 4. `--mamba-cache-mode=align` 플래그 누락 | **해당 없음 — 플래그 자체가 존재하지 않음** | `docker exec vllm-server vllm serve --help`로 전수 확인, `--mamba*`/`--ssm*` 계열 CLI 플래그가 이 vLLM 버전(0.27.1, `vllm/vllm-openai:latest`)에 하나도 없음. 진단 지시서의 이 항목은 검증 없이 작성된 것으로 보임(가상의 플래그) |
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

## 나중에 볼 것 (지금은 스코프 밖)

Qwen3.5의 GDN(선형 어텐션) 병목 자체를 vLLM/Triton 커널 레벨에서 해소하려는 외부 연구가 있다
(예: NVIDIA B200 등 최신 하드웨어에서 수작업 Triton 커널로 Qwen3.5-27B 추론을 최적화하는 시도).
다만 이는 데이터센터급 하드웨어·훨씬 큰 모델을 겨냥한 별개 작업이고, 우리가 직접 커스텀 GDN
Triton 커널을 작성/이식하는 건 CLAUDE.md가 명시한 "지금 하지 않는 것"(자체 양자화·커스텀 CUDA
커널 등)의 연장선이라 이번 1주 프로젝트 스코프에서는 진행하지 않는다. vLLM이 향후 버전에서
GDN 경로를 자체적으로 더 최적화하면(예: Marlin 대신 다른 INT4 커널, 또는 GDN 전용 fused 커널)
재검증할 가치가 있다 — 그때 다시 확인.
