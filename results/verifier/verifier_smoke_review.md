# #13 Verifier Client — Smoke 결과

`src/verifier/client.py`가 Qwen(vLLM, INT4 AutoRound)과 Kanana(vLLM, BF16) 양쪽에서 완전히 동일한
prompt/스키마/생성 설정으로 동작하는지 확인했다. #12의 `claim_dataset.json`에서 label/error_type별로
하나씩 고른 6개 claim으로 Smoke 테스트를 돌렸다 — **모델 우열 판단이 목적이 아니라 파이프라인이 두
후보 모두에서 한 바퀴 도는지 확인하는 것**(CLAUDE.md Eval 단계 표).

## 산출물

- `src/verifier/schemas.py` — JSON Schema + Pydantic `VerifierOutput`
- `src/verifier/client.py` — `verify(evidence, claim, model_key)`, Qwen/Kanana 공용
- `src/verifier/smoke_test.py`, `data/smoke/verifier_smoke_results.json`

## 결과

| claim_id | gold | error_type | Kanana | Qwen |
|---|---|---|---|---|
| p002_c01 | SUPPORTED | – | SUPPORTED ✓ | SUPPORTED ✓ |
| p002_c02 | UNSUPPORTED | base_vs_max_rate | UNSUPPORTED ✓ | UNSUPPORTED ✓ |
| p003_c02_4 | UNSUPPORTED | condition_reversal | SUPPORTED ✗ | UNSUPPORTED ✓ |
| p021_c01_4 | UNSUPPORTED | boundary_condition_error | SUPPORTED ✗ | UNSUPPORTED ✓ |
| p022_c01 | INSUFFICIENT | missing_information | INSUFFICIENT ✓ | UNSUPPORTED ✗ |
| p002_c05_3 | UNSUPPORTED | conditional_benefit_generalization | UNSUPPORTED ✓ | UNSUPPORTED ✓ |

- **Schema Valid Rate**: 12/12 (양쪽 모델, 6개 claim 전부) — `response_format: json_schema`가 두 모델
  모두에서 안정적으로 강제됨.
- **Match**: Kanana 4/6, Qwen 5/6 (참고용 수치 — Pilot 규모로 정식 비교 전까지는 결론 내리지 않음).
- **Latency**: Kanana 2.6~6.7초, Qwen 8.4~17.4초 (batch=1, `--enforce-eager`, WSL2 컨테이너 기준. 정식
  p50/p95 비교는 Pilot의 몫).

## 파이프라인 중 발견한 버그: evidence 텍스트에 Python `None`이 그대로 노출

`generate_synthetic_answers.py`의 `load_product_evidence()`가 `spcl_cnd`를 `!r`(repr)로 포맷하고
있었다 — 그 결과:

- `spcl_cnd`가 있는 상품도 자연스러운 여러 줄 텍스트 대신 Python repr(작은따옴표로 감싸고 줄바꿈이
  `\n` 리터럴로 이스케이프된 한 줄짜리 문자열)로 LLM에 전달되고 있었다.
- `spcl_cnd`가 `None`인 자연 결측 상품(`p022_c01`)은 evidence에 **"우대조건(spcl_cnd): None"**이라는
  Python 값이 그대로 노출되고 있었다 — INSUFFICIENT 골드 샘플에게 사람이 읽기엔 이상한 입력을 주고
  있었던 셈.

`None`은 `"우대조건 정보 없음"`으로, 그 외 필드는 원본 멀티라인 텍스트를 그대로 넘기도록 고쳤다.
`data/smoke/synthetic_answers.json`/`claim_dataset.json`의 관련 evidence_text 15건을 패치하고
(claim_text/answer_text는 이미 생성된 값이라 유지, evidence 포맷만 정정) 재실행해서 결과를 갱신했다.

**수정 전/후 비교**: Kanana는 `p022_c01`에서 수정 전 UNSUPPORTED → 수정 후 INSUFFICIENT로 바뀌어
정답이 됐다 — evidence 포맷이 실제로 판정에 영향을 줬다는 뜻. 반면 Qwen은 수정 후에도 여전히
UNSUPPORTED를 냈다 (아래 참고).

## 흥미로운 관찰: Qwen의 reasoning은 맞고 verdict는 틀림

`p022_c01`(INSUFFICIENT)에 대한 Qwen의 실제 응답:

```json
{
  "verdict": "UNSUPPORTED",
  "reason": "주어진 증거는 우대조건 정보가 '없음'으로 명시되어 있으며, 급여이체 시 우대금리가 추가되는
             구체적인 조건이나 근거를 포함하지 않습니다. 따라서 증거는 주장을 뒷받침할 수 없으며,
             명시적으로 충돌하는 것도 아닙니다."
}
```

`reason` 텍스트 자체는 CLAUDE.md의 INSUFFICIENT 정의("충돌이 아니라 정보 부재")를 정확히 서술하고
있는데, `verdict` 필드는 그와 모순되게 UNSUPPORTED를 골랐다. **모델이 SUPPORTED/UNSUPPORTED/
INSUFFICIENT 경계를 이해하지 못한 게 아니라, 이해한 판단을 최종 라벨에 일관되게 반영하지 못하는
케이스**로 보인다 — Pilot/Dev 단계에서 이 라벨-근거 불일치(label-reasoning inconsistency) 자체를
하나의 실패 유형으로 추적할 가치가 있다.

## 다음 단계로 넘길 것 (#14)

1. Pilot 규모로 확장할 때 이 "reasoning은 맞는데 verdict가 틀리는" 패턴이 얼마나 자주 나오는지 추적.
2. Kanana의 condition_reversal/boundary_condition_error 오답 2건은 이번 6개 표본만으로 결론 낼 수
   없음 — Pilot에서 정식 비교.
3. Latency/VRAM 정식 비교(p50/p95, warm-up 제외)는 Eval Harness(#14)에서 진행.
