# Eval 전제 감사 + 크로스모델 검증 실험 (#28)

`eval-precondition` 스킬 모드 2(검수)로 이 프로젝트의 eval 설계를 감사하고, 거기서 제기된 주장을
**4개 모델에 직접 실행해 검증한** 기록이다. 이 문서가 감사 관련 사실관계의 source of truth이고,
리포트·README·발표 자료의 정정은 전부 여기서 파생된다.

## 결론 먼저

프로젝트 종료 후 자체 감사에서 **기존 리포트의 결론 두 개가 뒤집혔고, 평가 지표 설계의 결함이
두 가지 드러났다.**

| # | 기존 결론 | 검증 후 |
|---|---|---|
| 1 | INSUFFICIENT 혼동은 **모델 체급 무관한 태스크 구조적 한계** | **틀렸다.** 같은 프롬프트로 Kanana(로컬 3B)·Haiku·Sonnet은 INSUFFICIENT를 **전부** 맞힌다. Qwen·Nemotron·Gemma만 실패하는 **모델별 특성**이다 |
| 2 | `condition_omission`은 프롬프트로 **해결되지 않는다** | **부분적으로 틀렸다.** 절차형 규칙을 넣으면 Test에서 2/2 잡힌다. 다만 효과가 **모델 능력에 종속**된다 — Sonnet·Nemotron은 전 지표 개선, Qwen은 정상 claim 인식률 0.913 → 0.674로 붕괴 |
| 3 | (없음) | **지표 우선순위에 utility 제약이 없다.** 상수 거부 스텁이 1·2·3순위를 전부 이긴다 |
| 4 | (없음) | **Macro F1은 이 데이터셋에서 부적절하다.** 4~2건짜리 INSUFFICIENT 클래스가 순위를 지배해, 더 나쁜 모델을 더 좋게 매긴다 |

부수적으로 **재현성은 오히려 확인**됐다 — 동일 설정 2회 실행이 verdict·reason 텍스트까지 완전히 동일했다.

## 실행 조건 (재현 계약)

"같은 모델·같은 프롬프트"만으로는 같은 실험이 아니다. 이번에 실측으로 확인된 재현 단위 전체:

| 축 | 값 (로컬 Qwen 기준) |
|---|---|
| 모델 | `Intel/Qwen3.5-4B-int4-AutoRound` (quantization=`inc`, dtype `bfloat16`) |
| 추론 엔진 | vLLM **v0.27.1**, 이미지 `vllm/vllm-openai@sha256:0a51ea5b4ae2…bfd967` |
| 실행 경로 | CUDA graph 활성 (`cudagraph_mode=FULL_AND_PIECEWISE`, capture `[1,2,4,8]`) |
| 서빙 파라미터 | `--max-model-len 1024 --max-num-seqs 4 --gpu-memory-utilization 0.85`, `seed=0` |
| 생성 파라미터 | `temperature=0`, `max_tokens=512`, `response_format=json_schema` |
| chat template | `chat_template_kwargs={"enable_thinking": false}` |
| 프롬프트 | Langfuse `verifier-system-prompt` — production=v6, 실험=`experiment`(v7)·`experiment2`(v8) |

대조군은 API 모델이다 — Claude Haiku 4.5 / Sonnet 5(Anthropic), Nemotron Ultra 550B(NVIDIA-hosted).
API 모델은 temperature 고정값을 쓰므로 로컬만큼 엄밀한 결정성은 보장되지 않는다.

프롬프트 변형을 production을 이동시키지 않고 돌리기 위해 세 하니스(`run_eval`, `run_eval_claude`,
`run_eval_nvidia`) 전부에 `--prompt-label`을 추가했다. **production 라벨은 이번 작업 내내 v6에 고정돼
있었다.**

## 실험 설계

프롬프트 두 종을 같은 데이터에 돌려 비교했다.

**v7 — 완전성 규칙 1문장** (production v2 + 아래 한 문장)

```
evidence가 여러 조건을 모두 충족해야 한다고 명시한 경우, claim이 그중 일부만 인용해
그것만으로 혜택이 성립하는 것처럼 서술하면 UNSUPPORTED로 판정하라.
```

**v8 — 절차형 완전성 규칙** (production v2 + 아래 절차)

```
판정 전에 반드시 다음 절차를 수행하라:
1) evidence가 요구하는 조건을 빠짐없이 나열한다.
2) claim이 그 조건들을 몇 개나 언급했는지 센다.
3) evidence가 여러 조건을 '모두 충족'하도록 요구하는데 claim이 그중 하나라도 언급하지 않고
   혜택이 성립한다고 서술했다면, 언급된 부분이 evidence와 일치하더라도 UNSUPPORTED로 판정하라
   — 조건을 빠뜨린 서술은 evidence와 충돌하는 서술이다.
```

v7은 Qwen Test에서 **verdict 변화 0건**이었다(문구만 바뀜). 이후 실험은 v8로 진행했다.

## 크로스모델 결과 — Pilot 64건

| 모델 | 프롬프트 | FAR ↓ | SUPPORTED Recall ↑ | UNSUP. Recall ↑ | INSUF. Recall ↑ | Schema | Accuracy | Macro F1 |
|---|---|---|---|---|---|---|---|---|
| **Qwen3.5-4B** (채택) | v2 | 0.1111 | 0.9130 | 0.8571 | **0.000** | 1.000 | 0.8438 | 0.5464 |
| | **v8** | 0.0556 | **0.6739** | 0.9286 | 0.000 | 1.000 | **0.6875** | 0.4534 |
| Kanana-2-3B (탈락) | v2 | 0.2222 | 0.8043 | 0.7143 | **1.000** | 1.000 | 0.7969 | 0.7652 |
| Haiku 4.5 | v2 | 0.0000 | 0.8261 | 0.8571 | **1.000** | 1.000 | 0.8438 | 0.7552 |
| | v8 | 0.0000 | 0.7826 | 0.8571 | 1.000 | 1.000 | 0.8125 | 0.7265 |
| Sonnet 5 | v2 | 0.0556 | 0.9348 | 0.7857 | **1.000** | 1.000 | 0.9062 | 0.8228 |
| | **v8** | **0.0000** | 0.9070 | **1.0000** | 1.000 | **0.953** | **0.9344** | 0.8949 |
| Nemotron 550B | v2 | 0.0000 | 0.9348 | 0.7857 | **0.000** | 1.000 | 0.8438 | 0.5665 |
| | **v8** | 0.0000 | 0.9130 | **1.0000** | 0.000 | 1.000 | **0.8750** | 0.6010 |
| Gemma-4-31B | v2 | 0.0556 | 0.9348 | 0.7143 | 0.250 | 1.000 | 0.8438 | 0.6233 |

## 크로스모델 결과 — Test 53건

| 모델 | 프롬프트 | FAR ↓ | SUPPORTED Recall ↑ | UNSUP. Recall ↑ | INSUF. Recall ↑ | Accuracy | Macro F1 |
|---|---|---|---|---|---|---|---|
| **Qwen3.5-4B** | v6 | 0.1071 | 1.0000 | 0.8846 | **0.000** | 0.9057 | 0.6151 |
| | v8 | **0.0000** | 0.8800 | 1.0000 | 0.000 | 0.9057 | 0.6162 |
| Haiku 4.5 | v2 | 0.0714 | 0.9600 | 0.8462 | **1.000** | 0.9057 | 0.8098 |
| | v8 | **0.1071** | 0.8800 | 0.8077 | 1.000 | **0.8491** | 0.7394 |
| Nemotron 550B | v2 | 0.1071 | 1.0000 | 0.8846 | 0.500 | 0.9245 | 0.8434 |
| | **v8** | **0.0357** | 1.0000 | **0.9615** | 0.500 | **0.9623** | 0.8695 |

## 항목별 판정

### ① INSUFFICIENT 실패는 태스크가 아니라 모델의 문제였다

리포트는 Nemotron(0/4)과 Gemma(1/4)만 인용해 "모델 체급과 무관한 태스크/경계 정의 자체의 구조적
한계"라고 결론지었다. **같은 프롬프트·같은 데이터에서 전체 모델을 보면 결론이 성립하지 않는다.**

| INSUFFICIENT 정답률 | Pilot (4건) | Test (2건) |
|---|---|---|
| **Kanana-2-3B (로컬 3B, 탈락한 후보)** | **4/4** | – |
| Claude Haiku 4.5 | **4/4** | **2/2** |
| Claude Sonnet 5 | **4/4** | – |
| Nemotron Ultra 550B | 0/4 | 1/2 |
| Gemma-4-31B | 1/4 | – |
| **Qwen3.5-4B (채택)** | 1/4 (eager) → **0/4** (채택 설정) | **0/2** |

**3B 로컬 모델이 만점을 받는 태스크를 "태스크 구조적 한계"라고 부를 수 없다.** 정확한 서술은
**"Qwen(및 Nemotron·Gemma)의 모델별 실패"**다.

그리고 채택 설정의 Qwen은 더 강한 상태다 — **6개 실행 전부에서 INSUFFICIENT verdict를 단 한 번도
출력하지 않았다.** 사실상 2분류기다.

**모델 선정에 숨어 있던 교환**: 이 프로젝트는 FAR(0.1111 vs 0.2222)을 근거로 Qwen을 택하고 Kanana를
탈락시켰다. 그런데 Kanana는 INSUFFICIENT를 4/4 맞혔고 Qwen은 0/4다. **한 지표에서 이기고 다른 축을
통째로 잃은 교환인데, 지표 우선순위가 그걸 보여주지 않았다.** 아래 ③과 같은 구조의 문제다.

### ② 절차형 규칙의 효과는 모델 능력에 종속된다

목표였던 `condition_omission`은 **잡힌다** — Qwen Test에서 2/2, 미정의 유형 `p008_c02`까지 교정되며
FAR 0. 문제는 그 대가가 모델마다 정반대라는 점이다.

| 모델 | v2 → v8 정확도 | 판정 |
|---|---|---|
| Sonnet 5 | 0.9062 → **0.9344** | 개선 (FAR 0, UNSUP Recall 1.0). 단 **Schema Valid 1.0 → 0.953** |
| Nemotron 550B | 0.8438 → **0.8750** (Pilot), 0.9245 → **0.9623** (Test) | 개선 |
| Haiku 4.5 | 0.8438 → 0.8125 (Pilot), 0.9057 → **0.8491** (Test, FAR도 악화) | 악화 |
| **Qwen3.5-4B** | 0.8438 → **0.6875** (Pilot) | **붕괴** |

**실패 방향도 모델마다 다르다.**

- **Qwen**은 UNSUPPORTED로 쏠린다 — Pilot에서 정상 claim 11건을 새로 거부(SUPPORTED→UNSUPPORTED).
- **Haiku**는 INSUFFICIENT로 쏠린다 — Test 변화 4건 중 3건, Pilot 악화 5건 전부가 SUPPORTED→INSUFFICIENT.
  게다가 목표였던 `p034_c02`는 v8에서도 못 잡았다.
- **Sonnet**은 규칙을 생산적으로 쓰지만 **출력이 길어져 3/64에서 JSON이 잘렸다**(Schema 0.953).

Qwen이 새로 거부한 케이스를 보면 원인이 분명하다.

| 거부된 정상 claim | 모델이 든 이유 | 실제 구조 |
|---|---|---|
| `p004_c01` | 다른 우대조항(0.05%p)을 안 썼다 | **별개 혜택**에 속한 조건 — 대조 대상이 아님 |
| `p020_c01` | 조건을 일부만 언급했다 | claim은 두 조건을 **모두** 언급했다(오독) |
| `p034_c01` | 조건을 다 안 썼다 | **ANY_OF**(택일)를 ALL_OF로 간주 |

**정정된 결론**: 약점은 "누락을 못 본다"가 아니라 **"evidence에 나열된 조건이 ALL_OF인지 ANY_OF인지,
어느 혜택에 속하는지 범위(scope)를 잡지 못한다"**이다. 절차형 규칙은 그 판별 능력을 **요구**할 뿐
제공하지 않는다. 그래서 판별할 수 있는 모델(Sonnet·Nemotron)에서는 개선이 되고, 못 하는
모델(Qwen 4B)에서는 무차별 적용돼 과잉거부가 된다.

이건 후속 과제의 가설로 바로 연결된다 — **판별 능력을 요구하는 대신 구조를 입력으로 주면
어떻게 되는가.**

### ③ 지표 우선순위에 utility 제약이 없다

`"UNSUPPORTED"`만 반환하는 상수 스텁을 실제 `metrics.py`로 채점하면:

| | FAR (1순위) | UNSUP. Recall (2순위) | Schema (3순위) | Macro F1 (참고) |
|---|---|---|---|---|
| Qwen 실제 (Test) | 0.1071 | 0.8846 | 1.0 | 0.6151 |
| `"UNSUPPORTED"` 상수 스텁 | **0.0000** | **1.0000** | 1.0 | 0.2194 |

**아무 판정도 하지 않는 스텁이 1·2·3순위를 전부 이긴다.** 그리고 가상의 반례가 아니라 실제 프롬프트도
같은 방향으로 움직였다 — Qwen v8은 정확도를 15.6%p 떨어뜨리면서 FAR·UNSUPPORTED Recall을 둘 다
개선했다.

FAR을 1순위로 두는 판단 자체는 금융 맥락에서 타당하다. 빠진 건 **그 짝**이다.

**대안: 순위제가 아니라 제약식**

> FAR을 목표치 이하로 낮추되, **정상 claim 거부율(FRR)을 기준선 대비 악화시키지 않는다.**

같이 보는 scorecard: FAR · UNSUPPORTED Recall · **SUPPORTED Recall(FRR)** · INSUFFICIENT Recall ·
Schema Valid Rate · Accuracy · 95% CI.

### ④ Macro F1은 이 데이터셋에서 부적절하다

Pilot에서 Macro F1 순위와 실제 유용성 순위가 **뒤집힌다.**

| 모델 | FAR | Accuracy | **Macro F1** |
|---|---|---|---|
| Nemotron 550B | **0.0000** | **0.8438** | 0.5665 |
| Kanana-2-3B | 0.2222 | 0.7969 | **0.7652** |

FAR도 정확도도 Nemotron이 낫는데 Macro F1은 Kanana가 0.2 높다. 원인은 4건짜리 INSUFFICIENT
클래스다 — Kanana는 4/4, Nemotron은 0/4이고, 3클래스 macro 평균에서 이 한 클래스가 1/3의 가중치를
가진다.

Test에서도 같다. 클래스별로 뜯으면 이렇다.

| 모델 | SUPPORTED F1 (n=25) | UNSUPPORTED F1 (n=26) | INSUFFICIENT F1 (**n=2**) | → Macro |
|---|---|---|---|---|
| Qwen v6 | 0.943 | 0.902 | **0.000** | 0.6151 |
| Haiku v2 | 0.941 | 0.917 | **0.571** | 0.8098 |

**주요 두 클래스 F1은 사실상 동일한데(0.94 / 0.90~0.92), Macro F1 격차 0.19가 전부 2건짜리
클래스에서 나온다.** 여기서 Macro F1은 "전반적 품질"이 아니라 "2문항을 맞혔나"를 재는 지표다.

같은 이유로 서빙 경로만 바꿔도 Macro F1이 크게 흔들린다.

```
p024_c01  gold=INSUFFICIENT   eager: INSUFFICIENT(정답) → CUDA graph: UNSUPPORTED(오답)
```

| 경로 | SUPPORTED F1 | UNSUPPORTED F1 | INSUFFICIENT F1 | **Macro** | Accuracy |
|---|---|---|---|---|---|
| eager (기존 리포트 헤드라인) | 0.9434 | 0.9200 | **0.6667** | **0.8434** | 0.9245 |
| CUDA graph (**실제 채택**) | 0.9434 | 0.9020 | **0.0000** | **0.6151** | 0.9057 |

verdict **1건**이 macro를 0.22 움직이는데 정확도로는 1.9%p 차이다. **최종 수치의 기준은 실제 채택한
서빙 경로로 정정한다.**

**결론**: 클래스 불균형이 심한 소표본 3분류에서 Macro F1을 헤드라인으로 쓰면 안 된다. 클래스별 F1과
support를 함께 제시하거나, 클래스별 recall을 따로 보고하는 편이 정직하다.

### ⑤ 재현성 — 감사의 우려를 실측으로 좁힘

동일 설정·동일 데이터로 Test 53건을 **두 번** 돌린 결과 **verdict 0건 차이, reason 텍스트까지 0건 차이**.

> **동일 serving configuration에서는 결정적으로 재현된다. 다만 eager ↔ CUDA graph 같은 실행 경로
> 변경에는 경계 케이스가 민감하다.**

#25에서 관찰된 뒤집힘은 실행 노이즈가 아니라 실행 경로 변경의 결과였다.

### ⑥ 하니스 결함 하나 (실험 중 발견·수정)

Sonnet + v8에서 출력이 `max_tokens=512`를 넘겨 JSON이 잘리자 **하니스가 예외로 죽었다.** 이건 API
장애가 아니라 **모델의 스키마 준수 실패**이므로 `schema_valid=False`로 집계해야 한다 — 그게 Schema
Valid Rate 지표의 정의다. `run_eval_claude.verify_claude`에서 `JSONDecodeError`/`StopIteration`을
잡아 기록하도록 고쳤고, 그 결과 Sonnet v8의 Schema Valid Rate **0.953**이 측정됐다.

원래 하니스라면 이 실패는 지표에 안 잡히고 실행이 중단됐을 것이다.

## 실험 비용 — Test 셋의 지위

Test 53건을 서빙 회귀 + 프롬프트 변형 2종 + 크로스모델 대조에 사용했다. 따라서 표현을 제한한다.

- ~~unseen Test~~ → **모델·프롬프트 선정에 사용하지 않은 held-out Test를 최초 1회 평가**
- 그 최초 평가 결과(v6)는 **그 시점의 held-out 평가로 여전히 유효**하다.
- **앞으로 프롬프트를 바꾸거나 production을 변경하려면 새 holdout이 필요하다.** v8을 채택하지 않으므로
  지금 새 Test를 만들 이유는 없다.

결론 근거를 Pilot에서도 확인한 이유가 이것이다.

## 반영 / 보류

**반영함** — 리포트 §3(모델 선정의 숨은 교환)·§5(약점 2종 재정의)·§6(지표 제약식)·§8(크로스모델
실험)·§9(한계)·§10(재현 계약), README 결과 요약, 발표 자료 결론 1개 절.

**보류 — production 프롬프트는 v6(=v2 내용) 유지.** v8은 Sonnet·Nemotron에서는 개선이지만 **채택 모델인
Qwen에서 utility를 붕괴시킨다.** 그리고 Test에서 튜닝한 결과를 채택하는 건 이 프로젝트가 지켜온 분리
원칙을 깨는 일이다.

**후속(FINeprint)으로 넘김**

- utility 제약을 포함한 scorecard를 처음부터 설계 (FAR ↔ FRR 짝, 클래스별 recall)
- 소표본 3분류에서 Macro F1을 헤드라인에서 제외
- **우대조건의 논리 연산자·적용 범위를 구조로 명시**(ALL_OF/ANY_OF/NOT/CAP/temporal)해서, 모델의
  판별 능력을 요구하는 대신 **입력으로 제공**했을 때 과잉거부 없이 누락을 잡을 수 있는지 검증
- **v8을 강한 프롬프트 baseline으로 보존** — "그냥 프롬프트를 길게 쓰면 되지 않나"에 답하기 위해
- 안전 케이스 `pass^k` 집계, 층별 eval 분리, judge 분산 측정, 표본 크기·MDE 선행 설계

## 원본 로그

실행별 raw JSON(`results/eval/raw/`, `results/eval/audit/`)은 **repo에 포함하지 않는다**(`.gitignore`) —
`reason`/`raw_content`에 공시 원문이 그대로 인용되기 때문이다.

대신 **텍스트를 제거한 통합 요약본을 추적한다**: [`runs_summary.json`](./runs_summary.json) — 본 실험
13건(`group: baseline`)과 #28 감사 실험 11건(`group: audit_28`), 총 24개 실행의 서빙·생성 파라미터,
metrics와 확장 scorecard(클래스별 recall·support 포함), 문항별 `claim_id`/`gold_label`/`error_type`/
`predicted_verdict`/`schema_valid`/`latency`가 들어 있다. **이 repo의 모든 인용 수치는 그 파일 하나로
재확인할 수 있다.**

재현 명령:

```bash
python -m src.eval.run_eval qwen --split test                             # 로컬 production v6
python -m src.eval.run_eval qwen --split test --prompt-label experiment2  # 로컬 절차형 v8
python -m src.eval.run_eval_claude haiku --split smoke --prompt-label experiment2
python -m src.eval.run_eval_nvidia nemotron --split test --prompt-label experiment2
```
