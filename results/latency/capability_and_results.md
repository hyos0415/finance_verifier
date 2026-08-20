# Qwen Latency 최적화 — 현재까지 가능한 것 / 불가능한 것 / 성과

이슈 #25(`25-latency-optimization` 브랜치) 진행 중 세션 단위 기록. 상세 진단 서사는
`results/model_selection/qwen_latency_diagnosis.md`(source of truth)에 있고, 이 문서는
"이 환경에서 실제로 되는 레버 vs 안 되는 레버"만 빠르게 훑어볼 수 있도록 분리한 요약이다.
최종적으로는 이 내용을 `qwen_latency_diagnosis.md`에 합류시킨다.

대상 체크포인트: `Intel/Qwen3.5-4B-int4-AutoRound` (INT4 AutoRound + Marlin 커널, vLLM
OpenAI-compatible 서버, RTX 4070 Laptop 8GB + WSL2 + Docker).

Baseline: `--enforce-eager --max-model-len 2048` (커밋된 표준 설정) 기준 decode
**~12.4-13.1 tok/s**.

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

## 아직 안 한 것 / 다음 단계

- Test(unseen) 53건 **전체**에 대해 이 설정으로 재실행 (지금은 앞 10건만 확인 — p50/p95는
  아직 없음, 10건 평균만 있음)
- `results/model_selection/qwen_latency_diagnosis.md`에 이 결과 정식 반영(위 정정 사항 포함)
- Experiment 3(decode-only CUDA graph 세부 튜닝) / 4(`--performance-mode interactivity`) /
  5(`--optimization-level O3`) 추가 진행 여부 결정 — 이미 3.6배 개선을 확인했으므로 추가
  레버의 한계효용을 판단한 뒤 진행할지 여기서 마무리할지 정한다
- git commit/push 전 확정 필요 (아직 커밋 없음)
