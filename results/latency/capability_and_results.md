# Qwen Latency 최적화 — 현재까지 가능한 것 / 불가능한 것 / 성과

이슈 #25(`25-latency-optimization` 브랜치) 진행 중 세션 단위 기록. 상세 진단 서사는
`results/model_selection/qwen_latency_diagnosis.md`(source of truth)에 있고, 이 문서는
"이 환경에서 실제로 되는 레버 vs 안 되는 레버"를 실험 단위로 자세히 남긴 원본 로그다.
**최종 결론은 `qwen_latency_diagnosis.md`의 "이슈 #25 실측 결과" 절에 합류 완료** —
거기서는 결론만 간결하게 보고, 여기서는 각 실험의 로그/표/재현 방법까지 자세히 본다.

대상 체크포인트: `Intel/Qwen3.5-4B-int4-AutoRound` (INT4 AutoRound + Marlin 커널, vLLM
OpenAI-compatible 서버, RTX 4070 Laptop 8GB + WSL2 + Docker).

Baseline: `--enforce-eager --max-model-len 2048` (커밋된 표준 설정) 기준 decode
**~12.4-13.1 tok/s**.

> **설정 갱신(같은 세션 후반)**: 아래 "가능한 것" 절은 최초 CUDA graph 실험 당시의
> `--max-num-seqs 1` 기준 서술이다. 이후 정확성 회귀 체크(아래 "정확성 회귀 체크" 절)와
> `--max-num-seqs` 스윕·k6 부하테스트 결과를 반영해 **서빙 설정을 `--max-num-seqs 4`로
> 최종 채택**했다 — 아래로 내려가서 "해석 및 권장값" 절과 "vLLM 멀티유저 서빙 검증" 절 참고.

---

## 가능한 것 (실제로 효과 있었던 레버)

### ✅ CUDA Graph 활성화 — `--enforce-eager` 제거 + `--max-model-len 1024` + `--max-num-seqs 1`

`--enforce-eager`를 그냥 빼면 기본 `max_num_seqs=256`이 Mamba cache block 예산(64개)을
초과해 서버가 뜨지도 못한다(아래 "불가능한 것" 참고). `max_num_seqs`를 batch=1 워크로드
실제 크기에 맞게 **1**로, context도 실사용 범위에 맞게 **1024**로 줄이면 KV/Mamba cache
예산 문제가 구조적으로 사라지고, 그 여유로 CUDA Graph를 정상적으로 capture할 수 있다.

로그로 실제 capture 확인됨:
```
Capturing CUDA graphs (mixed prefill-decode, PIECEWISE): 100%|██████████| 2/2
Capturing CUDA graphs (decode, FULL): 100%|██████████| 1/1
Graph capturing finished in 4 secs, took 0.00 GiB
```
(`max_num_seqs=1`이라 capture size가 `[1, 2]`로 자동으로 좁혀짐 — 정확히 우리가 원하는
batch=1 decode 경로만 그래프로 굽는 셈)

**결과 — Test(unseen) split 앞 10개 claim, 동일 client 페이로드 기준:**

| claim | completion tokens | latency (s) | tok/s |
|---|---|---|---|
| p004_c01 | 158 | 4.51 | 35.01 |
| p004_c02 | 72 | 1.73 | 41.59 |
| p008_c01 | 127 | 2.79 | 45.56 |
| p008_c02 | 119 | 2.56 | 46.51 |
| p010_c01 | 96 | 2.06 | 46.72 |
| p010_c02 | 99 | 2.12 | 46.75 |
| p017_c01 | 81 | 1.74 | 46.57 |
| p017_c02 | 107 | 2.27 | 47.19 |
| p019_c01 | 185 | 3.86 | 47.87 |
| p019_c02 | 202 | 4.21 | 48.03 |
| **평균** | | **2.78** | **45.18** |

**baseline 12.4~13.1 tok/s → 45.18 tok/s, 약 3.6배 개선.** VRAM 사용량 5.37/8.19 GiB
(`nvidia-smi`) — 8GB 예산 안에서 여유 있게 들어간다. 측정 노이즈 수준이 아니라 매 claim이
일관되게 35 tok/s 이상으로, baseline 범위(9.75~14.13 tok/s)와 겹치지 않는다.

---

## 불가능한 것 (이 환경/모델 조합에서 시도했지만 안 됨)

### ❌ `--enforce-eager` 제거만 하고 나머지 기본값 유지

```
ValueError: max_num_seqs (256) exceeds available Mamba cache blocks (64).
Please lower max_num_seqs to at most 64 or increase gpu_memory_utilization.
```
서버가 아예 기동하지 못하고 종료(exit code 1). GDN 하이브리드 구조상 decode sequence 하나당
Mamba cache block 하나가 필요한데, 8GB VRAM에서 확보 가능한 블록이 64개뿐이라 기본
`max_num_seqs=256`과 맞지 않는다. → `--max-num-seqs`를 낮춰야만 해결(위 "가능한 것" 참고).

### ❌ `--gdn-prefill-backend flashinfer` / `--gdn-prefill-backend cutedsl`

둘 다 동일한 경고 후 자동 폴백:
```
GDN prefill backend 'X' is selected but cannot use this kernel on the current platform.
Falling back to Triton/FLA.
```
RTX 4070 Laptop(Ada Lovelace, SM89)에서는 `flashinfer`·`cutedsl` GDN prefill 커널 자체가
지원 대상이 아니다. 결국 `auto`가 이미 고르는 것과 동일한 `triton` 커널로 강제 귀결되므로,
이 환경에서는 조정 가능한 레버가 아니다(하드웨어 세대 문제 — 이후 GPU 세대에서 재검증 가치는
있음).

### ❌ `--use-replayssm`

기동 자체가 pydantic validation error로 즉시 실패:
```
--use-replayssm is only supported for Nemotron-H models
(got architecture 'Qwen3_5ForConditionalGeneration')
```
**이전 세션 진단 문서의 판단 정정**: "전제조건(`mamba_cache_mode`)이 기본값 그대로 이미
충족돼 있어 추가 설정 없이 켜도 된다"고 적혀 있었으나, 실제로는 모델 아키텍처 자체가
Nemotron-H 전용 최적화 대상이라 Qwen3.5에는 애초에 적용 불가능하다. 재시도할 필요 없음.

### ❌ `VLLM_MARLIN_USE_ATOMIC_ADD=1`

5-claim 프로브 기준 baseline ~12.42 tok/s → 12.20 tok/s로 오히려 미세 하락(노이즈 범위).
Marlin INT4 GEMM(선형 레이어) 최적화인데 효과가 없다는 건 병목이 선형 레이어가 아니라
GDN/Mamba 커널 경로에 있다는 근거를 보강할 뿐, 그 자체로는 채택할 이유가 없다.

### ❌ `--dtype float16` (activation dtype만 bf16→fp16)

5-claim 프로브 기준 baseline ~12.42 tok/s → 11.71 tok/s로 약 6% 하락. vLLM이 로그에서
"SM90 이전 GPU는 fp16을 고려하라"고 권고했지만, 이 워크로드(batch=1, 짧은 생성)에서는 그
이득이 실현되지 않았다.

---

## 성과 요약

| 설정 | decode tok/s | 비고 |
|---|---|---|
| Baseline (`--enforce-eager --max-model-len 2048`) | ~12.4–13.1 | 커밋된 표준 설정, Qwen v2 eval 기준값 |
| + `VLLM_MARLIN_USE_ATOMIC_ADD=1` | ~12.20 | 개선 없음 |
| + `--dtype float16` | ~11.71 | 개선 없음(소폭 하락) |
| **`--max-model-len 1024 --max-num-seqs 1` (eager 제거, CUDA Graph 활성)** | **~45.18** | **채택 후보 — baseline 대비 약 3.6배** |

가장 유효한 레버는 "커널 옵션을 바꾸는 것"이 아니라 **워크로드 실제 크기(batch=1, 짧은
context)에 맞게 serving capacity를 줄여서 CUDA Graph가 켜질 여유를 만드는 것**이었다.
GDN prefill 커널 선택이나 Mamba decode 커널 교체 같은 더 표적화된 레버는 이 GPU 세대·이
모델 아키텍처 조합에서는 애초에 선택지가 아니었다.

---

## 정확성 회귀 체크 — `--max-model-len 1024` 축소로 인한 잘림/품질 저하 여부

Experiment 2가 `--max-model-len`을 2048→1024로 줄였기 때문에, Test(unseen) 53건 전체에 대해
① 컨텍스트 예산이 실제로 부족한 claim이 있는지, ② 있다면/없다면 verdict 품질이 baseline과
달라지는지를 순서대로 확인했다.

### ① 토큰 예산 체크 — 잘림 위험 없음

실제 Qwen tokenizer(`enable_thinking=False` chat template 적용)로 53건 전부의
`system_prompt + "Evidence: ... Claim: ..."` prompt 토큰 수를 계산하고, `max_tokens=512`를
항상 전부 소진한다고 가정한 최악의 경우로 `prompt_tokens + 512`가 1024를 넘는지 확인했다.

- 최장 claim: `p012_c02`(spcl_cnd) — prompt 373 토큰, `373 + 512 = 885` (여유 139 토큰)
- 53건 중 1024 초과 **0건**

즉 `--max-model-len 1024`로 인한 프롬프트 잘림은 이 데이터셋에서는 발생하지 않는다.

### ② 실제 53건 재실행 — baseline(2048, eager) vs Exp2(1024, cudagraph, seqs=1) 비교

동일 53건을 Exp2 서버(재개된 컨테이너, `docker start vllm-server`)에 재실행하고
baseline 결과(`results/eval/test_qwen_prompt-v6.json`, git에 커밋된 버전)와 claim 단위로 비교했다.

| 지표 | baseline (2048, eager) | Exp2 (1024, cudagraph, seqs=1) |
|---|---|---|
| False Accept Rate | 0.1071 | **0.1071 (동일)** |
| UNSUPPORTED Recall | 0.8846 | **0.8846 (동일)** |
| Macro F1 | 0.8434 | 0.6151 |
| Schema Valid Rate | 1.0 | 1.0 |
| Latency p50/p95 | 9.08s/14.27s | 2.47s/3.89s |

53건 중 **verdict가 다른 건 1건뿐**: `p024_c01`(gold=INSUFFICIENT, KDB 정기예금 — spcl_cnd 정보
없음). baseline은 `INSUFFICIENT`(정답), Exp2는 `UNSUPPORTED`(오답)로 뒤집혔다. 두 응답의
`reason` 텍스트는 "우대조건 정보가 존재하지 않는다"는 근거 인식까지는 거의 동일한 문장이고,
마지막 결론 토큰만 갈렸다 — 즉 **컨텍스트 잘림(위 ①에서 배제됨)이 아니라, 이미 확정된 약점인
INSUFFICIENT↔UNSUPPORTED 경계에서 eager vs CUDA-graph 실행 경로 간의 부동소수점 연산 차이가
경계를 흔든 것**으로 해석한다. `temperature=0`이라 완전 동일 입력·완전 동일 가중치인데도 결과가
달라졌다는 게 그 근거다.

**핵심 지표(FAR·UNSUPPORTED Recall)는 이 flip에 영향받지 않았다** — 둘 다 INSUFFICIENT를
직접 다루지 않는 지표이기 때문. Macro F1만 0.8434→0.6151로 크게 떨어졌는데, INSUFFICIENT
클래스 표본이 원래 2건뿐이라 그중 1건이 뒤집히면 macro 평균이 과대하게 흔들리는 표본 크기
문제로 본다(`test_eval_review.md`가 이미 "53건도 통계적 정밀도보다는 정성적 신호로 읽는다"고
명시한 것과 같은 맥락).

**결론**: latency 3.6배 개선은 정확성 트레이드오프 없이 얻은 게 맞지만(핵심 지표 불변),
CUDA graph 활성화가 이미 알려진 경계 취약점의 출력 안정성에 미세한 흔들림을 준다는 신호는
있다. 표본이 1건뿐이라 확정적 결론은 아니고, 최종 리포트(#15)에서 "known limitation" 항목에
이 관찰을 짧게 덧붙일 가치가 있다. (전체 재실행 결과 JSON은 위 표로 이미 요약됐으므로 git에
별도로 남기지 않았다 — baseline 파일 `results/eval/test_qwen_prompt-v6.json`만 유지)

---

## `--max-num-seqs` 스윕 — 동시 처리량 확장성

`max-num-seqs`를 1(현재 채택 설정)에서 2/4/8로 올렸을 때 동시 요청 처리량이 어떻게 늘어나는지
확인했다. 매 설정마다 컨테이너를 재기동하고(`--max-model-len 1024` 고정), 정확히
`max-num-seqs`와 같은 수의 동시 요청을 `ThreadPoolExecutor`로 보내 총 벽시계 시간과
`usage.completion_tokens` 합으로 aggregate tok/s를 측정했다.

각 요청은 `ThreadPoolExecutor(max_workers=N)`로 **동시에** 발사됐다 — 순차 호출이 아니라
실제로 여러 개의 서로 다른 claim이 vLLM의 continuous batching에 의해 한 batch로 묶여 GPU에서
같이 decode된다는 뜻이다(로그에서도 `Capturing CUDA graphs ... [1, 2, 4]`처럼 capture size가
동시 처리 개수에 맞춰 잡히는 걸 확인).

| max-num-seqs | 동시 요청 수 | 총 처리시간 | aggregate tok/s | 평균 latency | 최대 latency | 처리량 배수(1 대비) | idle VRAM | peak VRAM |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | - | 45.18 | 2.78s | - | 1.0x | 5353 MiB | 5373 MiB |
| 2 | 2 | 3.81s | 60.60 | 2.90s | 3.81s | 1.34x | 5357 MiB | 5377 MiB |
| 4 | 4 | 4.44s | 107.92 | 3.56s | 4.43s | 2.39x | 5399 MiB | 5567 MiB |
| 8 | 8 | 4.99s | 176.10 | 3.86s | 4.98s | 3.90x | 5365 MiB | 5783 MiB |

(각 설정은 컨테이너를 완전히 재기동해 독립 측정, `nvidia-smi --query-gpu=memory.used`로
부하 중 0.3초 간격 폴링한 최댓값이 "peak VRAM")

**전체 처리량(aggregate tok/s)은 seqs를 늘릴수록 계속 개선된다** — 여러 claim을 동시에
넣으면 GPU가 한 번에 더 많은 토큰을 생성하므로 "여러 문서(claim)를 한 배치로 처리"하는 게
맞고, 총 처리 속도도 실제로 좋아진다. 다만 **선형은 아니다** — 8배 동시 요청에 처리량은
3.9배만 늘고, 개별 요청의 latency가 늘어난다(평균 2.78s→3.86s, 최대 4.98s). GPU가 batch로
여러 decode step을 묶어 처리하다 보니 한 요청이 다른 요청들의 처리를 일부 기다리는 형태로
보인다.

**GPU 피크는 완만하게만 늘어난다** — idle 상태는 seqs 값과 무관하게 ~5.35-5.4GB로 거의
동일한데(이건 `--gpu-memory-utilization 0.85` 기준으로 KV cache 예약량이 시동 시 고정되기
때문), 부하 중 peak는 seqs=1→8 사이에 5373→5783 MiB로 **약 410MB만 증가**한다. WSL2 환경의
실사용 가능 VRAM 한도(~6.89GB, CLAUDE.md 기록)까지는 seqs=8에서도 여전히 1GB 이상 여유가
있어, 이 범위에서는 OOM 걱정 없이 seqs를 더 올릴 수 있는 헤드룸이 남아있다.

**해석 및 권장값**: 하나의 답변이 여러 개의 독립적인 atomic claim으로 쪼개지는 구조(CLAUDE.md)
에서, 실제 Test셋의 답변당 claim 수는 평균 1.89개(최대 2개), Smoke셋(복잡한 조건이 몰린
케이스)은 평균 5.82개(최대 11개)였다. 이를 고려하면:

- **`max-num-seqs=4`**가 균형점으로 보인다 — 답변 대부분(2개 claim)을 한 배치로 다 처리하고도
  여유가 있고, latency 증가(2.78s→3.56s, +28%)가 크지 않으면서 처리량은 2.4배 늘어난다. VRAM도
  +200MB 수준으로 저렴하다.
- `seqs=8`은 흔치 않은 다중 조건 답변(스모크셋의 5~11개 claim 케이스)까지 한 배치로 처리할 수
  있지만, 추가 처리량 이득(2.4x→3.9x)에 비해 개별 latency 증가(최대 4.98s)가 더 커서 "일반적인
  경우"엔 과한 설정일 수 있다.

**→ 최종 채택: `--max-num-seqs=4`.** `seqs=1`(순수 batch=1 실행)에서 전환한다. CLAUDE.md가
정의한 핵심 latency 지표(p50/p95, batch=1 기준)는 이 전환과 별개로 유지된다 — 그 지표는
"GPU가 완전히 유휴 상태에서 claim 1건을 처리하는 데 걸리는 시간"을 재는 정의이고, `seqs=4`는
"여러 claim/여러 사용자가 동시에 몰릴 때 서버가 어떻게 동작하는가"를 다루는 서빙 계층의 별개
설정이다. 두 숫자를 혼동하지 않게 #15 최종 리포트에서 구분해서 기술한다. `scripts/run_vllm_container.sh`
의 기본값도 이 설정으로 갱신했다.

---

## vLLM 멀티유저 서빙 검증 — k6 부하테스트

`max-num-seqs=4`를 최종 채택 설정으로 가져가기로 하면서(위 스윕에서의 균형점), vLLM의 핵심
장점인 "여러 사용자가 동시에 하나의 서빙 인스턴스를 쓸 수 있다"는 걸 실제로 검증했다.
`max-num-seqs`(GPU가 한 번에 처리하는 배치 크기 상한)와 k6의 VU(동시에 요청을 보내는
클라이언트 수)는 다른 개념이라, VU를 서버 용량(4)과 같게/2배(8)/4배(16)로 걸어 오버구독
상황에서 서버가 어떻게 반응하는지 확인했다.

`src/verifier/client.py`와 동일한 payload(system prompt, `chat_template_kwargs.enable_thinking:
false`, `temperature=0`, `max_tokens=512`, JSON schema response_format)로 k6 스크립트를
작성하고, `data/test/claim_dataset.json`의 실제 53개 claim을 순환시켜 각 VU 레벨마다 고정된
총 요청 수(`shared-iterations` executor)를 처리했다.

| VU(동시 사용자) | 서버 용량(seqs=4) 대비 | 요청 수 | throughput | p50 | p90 | p95 | p99 | 에러율 |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.25x | 24 | 0.367 req/s | 2.60s | 3.90s | 4.10s | 4.33s | 0% |
| 4 | 1.0x | 24 | 1.184 req/s | 3.26s | 5.08s | 5.08s | 5.08s | 0% |
| 8 | 2.0x | 32 | 1.239 req/s | 6.23s | 8.44s | 8.78s | 8.78s | 0% |
| 16 | 4.0x | 48 | 1.251 req/s | 11.34s | 14.33s | 16.88s | 16.93s | 0% |

**핵심 관찰**:
- **throughput은 VU=4(서버 용량과 일치)에서 이미 거의 포화**(1.184 req/s)되고, VU를 8/16으로
  늘려도 총 처리량은 거의 그대로다(1.24~1.25 req/s). GPU가 한 번에 처리 가능한 양은
  `max-num-seqs`로 이미 캡이 걸려 있기 때문.
- 초과 사용자(용량을 넘는 VU)는 거부되지 않고 **큐에서 대기하며, latency가 오버구독 비율에
  거의 선형으로 늘어난다**(1x→2x→4x 오버구독에 p50이 3.26s→6.23s→11.34s로, 대략 그 비율만큼
  증가).
- **4배 오버구독(VU=16)에서도 에러/타임아웃 0건, schema valid 100%** — 서버가 죽거나 요청을
  실패시키는 대신 늦게라도 전부 처리한다는 게 확인됐다.

**해석**: 이게 vLLM continuous batching의 실질적 장점이다 — 순수 단일 요청 서빙(예: 별도
동시성 제어 없는 naive Flask 서버)이었다면 서버가 GPU 메모리 부족으로 죽거나 요청을 거부할
상황에서도, vLLM은 초과 요청을 큐잉해 latency만 우아하게 늘리며 전부 처리한다. 다만 이는
"동시 사용자를 무한히 받아도 괜찮다"는 뜻은 아니고, `max-num-seqs=4`를 넘는 동시 사용자가
지속적으로 발생하는 서비스라면 latency SLA를 위해 `max-num-seqs`를 더 올리거나(위 스윕 참고,
VRAM 여유는 충분) 요청 큐 앞단에 별도 rate limiting/timeout 정책이 필요하다는 걸 시사한다.

---

## 이슈 #25 실측 후보 4·5 — `--performance-mode interactivity` / `--optimization-level O3`

CUDA graph batch=1(`--max-num-seqs 1`) 구성 위에 두 옵션을 각각 추가로 얹어, 동일한 방법론
(Test 53건 전체를 순차 호출, `usage.completion_tokens` 기준 tok/s + latency percentile)으로
측정했다.

| Config | aggregate tok/s | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| CUDA graph batch=1 (기준) | 44.26 | 2.60s | 3.98s | 4.42s |
| + `--performance-mode interactivity` | 43.94 | 2.59s | 3.96s | 4.02s |
| + interactivity + `--optimization-level 3` | 43.98 | 2.53s | 3.89s | 4.36s |

**둘 다 유의미한 차이 없음(노이즈 수준).** `max-num-seqs=1`로 이미 CUDA graph capture size가
`[1, 2]`로 최소화돼 있어서, "작은 배치 latency 우선"(interactivity)이나 "더 공격적인 컴파일"
(O3)이 추가로 개선할 여지가 거의 없었던 것으로 보인다. 각 옵션이 정확히 뭘 하는지와 이 실측
근거는 `notes/vllm_serving_modes.md`에 정리했다.

`--gdn-prefill-backend`(1번)와 `--use-replayssm`(2번)은 각각 이 GPU 세대에서 미지원 커널로
폴백되거나(triton으로 자동 귀결, 측정 불가), 모델 아키텍처 전제조건 불충족으로 부팅 자체가
실패해 애초에 측정 대상이 아니었다(이전 세션에 이미 확인, 위 "불가능한 것" 절 참고).

이슈 #25의 실측 후보 표(1~5번)가 이걸로 전부 채워졌다 — 결론: **latency 개선은 커널 선택이나
컴파일 강도 조절이 아니라, 워크로드 실제 크기에 맞게 서빙 용량(`max-num-seqs`)을 재설계해
CUDA Graph를 켤 여유를 만드는 것에서만 나왔다.**

---

## 아직 안 한 것 / 다음 단계

- `results/model_selection/qwen_latency_diagnosis.md`에 이 문서 전체 결과를 정식 반영(아직 안 함)
- **Experiment 3(decode-only CUDA graph 세부 튜닝, `-cc.cudagraph_mode=FULL_DECODE_ONLY` +
  `--cudagraph-capture-sizes 1`)은 스킵하기로 결정.** 이 실험이 없애려는 건 prefill+decode
  전환 구간의 PIECEWISE 그래프인데, prefill은 요청당 1회(전체 evidence+claim을 한 번에 처리)인
  반면 decode는 요청당 70~200스텝 반복되어 latency의 대부분을 차지한다 — 손댈 수 있는 부분
  자체가 이미 전체 시간의 극히 일부다. 게다가 같은 계열(CUDA graph/컴파일 세부 조정)인
  interactivity·O3가 이미 둘 다 유의미한 차이가 없었다는 결과가 나왔어서, 3번도 같은 결과가
  나올 가능성이 높다고 판단해 재기동 비용을 들이지 않기로 함.
