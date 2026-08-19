# #7 모델 Serving Smoke (심화)

`vllm/vllm-openai:latest`(vLLM 0.27.1) 공식 이미지로 두 후보를 각각 컨테이너로 띄워 실제 OpenAI
호환 엔드포인트(`/v1/chat/completions`)에 요청을 보내 확인했다. 공통 실행 옵션:
`--enforce-eager --gpu-memory-utilization 0.85 --max-model-len 2048`,
`VLLM_WSL2_ENABLE_PIN_MEMORY=1`, HF 캐시는 `scripts/run_vllm_container.sh`가 마운트하는
Windows 경로(`C:\Users\user\.cache\huggingface`)를 그대로 사용 — 두 모델 모두 이미 캐시돼 있어
재다운로드 없이 진행함 (Qwen 4.3GB, Kanana 6.6GB).

## 체크리스트

- [x] GPU memory 실측 (Peak VRAM)
- [x] structured output(JSON 강제) 가능 여부
- [x] vLLM 호환성 확인
- [x] 문제 발생 시 Transformers fallback으로 확정 → **불필요** (둘 다 vLLM에서 정상 서빙)

## GPU Memory 실측 (Peak VRAM)

컨테이너 내부에서 vLLM이 시동 시 자체 보고하는 프로파일링 로그 기준 (WSL2 가상화 오버헤드로 실제
가용 VRAM은 8GB가 아니라 6.89GB — CLAUDE.md에 이미 기록된 값과 동일하게 재확인됨).

| 후보 | 가중치 로드 | 소요시간 | 가중치+non-torch | peak activation | KV cache | 합계(≈) | KV cache 크기 |
|---|---|---|---|---|---|---|---|
| Kanana-2-3B (BF16) | 6.19 GiB | 53.96s | 6.19 GiB | 0.13 GiB | 0.48 GiB | **6.67 GiB** | 3,888 tokens |
| Qwen3.5-4B-int4-AutoRound | 3.73 GiB | 49.15s | 4.1 GiB | 1.66 GiB | 1.04 GiB | **6.8 GiB** | 18,724 tokens |

INT4 양자화로 가중치 자체는 더 작지만(weight load 3.73 GiB vs 6.19 GiB), Marlin 커널의 활성값
버퍼(peak activation 1.66 GiB)가 더 커서 `gpu_memory_utilization 0.85` 캡 아래 합계는 두 모델이
비슷하게 수렴한다. 대신 Qwen 쪽이 KV cache 여유가 훨씬 크다(18,724 vs 3,888 tokens) — 같은 8GB
카드에서 더 긴 컨텍스트/더 많은 동시 요청을 감당할 여유가 있다는 뜻.

## Structured Output (JSON 강제)

Verifier 출력 스키마(`verdict`/`evidence`/`reason`, `verdict` enum)를 그대로
`response_format: {"type": "json_schema", "json_schema": {...}}`로 넘겨 테스트.

- **Kanana**: 스키마 그대로 valid JSON 반환 (`verdict: INSUFFICIENT`)
- **Qwen (enable_thinking=False + json_schema 동시 적용)**: 스키마 그대로 valid JSON 반환 (`verdict: UNSUPPORTED`)

두 모델 모두 vLLM의 guided-decoding으로 스키마를 강제할 수 있음을 확인했다. (verdict 값 자체의
정확도는 이번 스모크의 목적이 아니고 Pilot/Dev 단계에서 별도로 평가한다 — 다만 이 예시 하나만 보면
Qwen이 "명시적 충돌 → UNSUPPORTED"를 CLAUDE.md 판정 기준대로 정확히 골랐고, Kanana는 INSUFFICIENT로
헷갈렸다는 점은 참고삼아 기록해둔다.)

## Qwen `enable_thinking=False` — vLLM 호출 방식

CLAUDE.md는 Transformers 네이티브 호출 기준으로 `apply_chat_template(..., enable_thinking=False)`를
명시하고 있는데, vLLM의 OpenAI 호환 서버에서는 요청 body에 `chat_template_kwargs`를 추가해야 동일하게
적용된다:

```json
{
  "messages": [...],
  "chat_template_kwargs": {"enable_thinking": false}
}
```

이 파라미터 없이 호출하면 CLAUDE.md가 경고한 대로 "Thinking Process:" 영어 장문 추론이 먼저 나오고,
80 토큰으로는 답변에 도달하지 못하고 잘렸다(`finish_reason: length`, 재현 확인됨). `chat_template_kwargs`를
추가하자 바로 한국어 최종 답변으로 끝났다(`finish_reason: stop`). **Verifier Client(#13) 구현 시 이
파라미터를 빠뜨리지 않아야 한다.**

## vLLM 호환성

- 두 모델 모두 별도 에러 없이 정상 기동·서빙 (`/v1/models`, `/v1/chat/completions` 200 응답)
- Qwen 로그에 `Qwen3VLVideoProcessorInitKwargs` 관련 `[ERROR]` 라인이 있으나, 이는 Transformers의
  video processor kwarg 문서화 누락 경고일 뿐 서빙에는 영향 없음 (무시 가능)
- Qwen은 Marlin 커널 선택·준비 단계 때문에 컨테이너 기동이 Kanana보다 느림(~4.5분 vs ~2분, cold start
  기준) — 런타임 latency가 아니라 1회성 로드 비용이라 서빙 성능 비교에는 포함하지 않음

## 결론

두 후보 모두 이 8GB 환경에서 vLLM으로 안정적으로 서빙 가능하고, Verifier가 요구하는 JSON 스키마
강제도 둘 다 지원한다. Transformers fallback으로 갈 이유가 없다. 실제 latency/VRAM 정식 비교(배치=1,
temperature=0, 고정 max_tokens, warm-up 제외, p50/p95)는 Pilot 단계(#14)에서 Eval Harness와 함께 진행한다.
