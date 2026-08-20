# Test(Unseen) 결과 — Qwen + v2 프롬프트 최종 확인

`data/test/claim_dataset.json`(33건, #23에서 구축 — Claude 18 + Codex 15, v1~v4 프롬프트 튜닝에
전혀 관여하지 않은 신규 claim)에 Qwen3.5-4B-int4-AutoRound + v2 프롬프트(Langfuse production,
version 6)로 최종 eval을 실행한 결과.

## 헤드라인 비교 — Pilot(64건) vs Test(33건, unseen)

| 지표 | Pilot | Test | 비고 |
|---|---|---|---|
| False Accept Rate | 0.1111 | **0.0556** | 개선 |
| UNSUPPORTED Recall | 0.8571 | **0.9375** | 개선 |
| Macro F1 | 0.6656 | **0.8573** | 큰 폭 개선 |
| Schema Valid Rate | 1.0 | 1.0 | 동일 |
| Latency p50/p95 | 7.83s/17.05s | 8.86s/13.84s | 비슷 |

**Pilot보다 Test 지표가 전반적으로 더 좋게 나왔다.** 이건 v2 프롬프트가 Pilot 64건에만
과적합된 게 아니라 실제로 일반화된다는 긍정적 신호로 해석한다 — Test 셋은 v1~v4 프롬프트
선택 과정에 전혀 관여하지 않은 진짜 unseen 데이터이기 때문이다. 다만 표본이 33건으로
작아서(특히 UNSUPPORTED 16건, INSUFFICIENT 2건) 개별 지표의 통계적 변동폭은 Pilot보다 크다는
점은 감안해야 한다 — 이 숫자를 "Qwen이 Pilot보다 33% 더 좋아졌다"처럼 정밀하게 읽기보다는
"v2 프롬프트가 unseen 데이터에서도 무너지지 않고 대체로 버틴다"는 정성적 결론으로 읽는 게 맞다.

## 오답 3건 — 전부 이미 알려진 패턴 그대로 재현

### INSUFFICIENT 2건 중 1건 오답 — 알려진 약점 재확인

| claim_id | 실제 라벨 | 예측 | 비고 |
|---|---|---|---|
| p024_c01 | INSUFFICIENT | **INSUFFICIENT (정답)** | spcl_cnd가 `None`(진짜 데이터 없음) |
| p017_c02 | INSUFFICIENT | UNSUPPORTED (오답) | evidence_mismatch(cross-field) — claim은 mtrt_int 얘기인데 evidence는 spcl_cnd만 줌 |

`p017_c02`는 #23에서 새로 설계한 evidence_mismatch(cross-field) 유형이다 — evidence가 claim의
주제 자체를 다루지 않는(RAG가 엉뚱한 필드를 가져온 상황) 케이스인데, Qwen은 이걸 "정보 없음"이
아니라 "충돌"로 오판했다. 이건 `results/eval/smoke_eval_review.md`와
`results/model_selection/qwen_latency_diagnosis.md`에서 이미 여러 번 확인한 Qwen의 확정된
약점(INSUFFICIENT↔UNSUPPORTED 경계 혼동)이 **완전히 새로운 claim 유형(cross-field mismatch)에도
동일하게 나타난다**는 걸 보여준다 — 이 약점이 특정 claim 문구에 국한된 우연이 아니라 정말
구조적이라는 근거가 하나 더 늘었다.

### false accept 1건

`p008_c02`(boundary_condition_error) — 미즈월복리정기예금의 요구불평잔 구간(300만원=0.1%,
500만원=0.2%)에서 낮은 구간에 높은 구간의 우대율을 붙인 claim을 SUPPORTED로 잘못 승인했다.
숫자 구간을 세밀하게 비교해야 잡히는 유형으로, Pilot에서도 반복적으로 관찰된 "체급 문제"
패턴(로컬 SLM이 구간/숫자 미세 비교에 약함)과 일치한다.

### false reject: 0건

Pilot에서 관찰됐던 과잉 거부 패턴은 Test에서 재현되지 않았다.

## 결론

- **모델/프롬프트 선정은 그대로 유지한다** — Qwen3.5-4B-int4-AutoRound + v2 프롬프트가
  unseen 데이터에서도 FAR·Recall·F1 전 지표에서 Pilot 수준 이상을 유지했다.
- **INSUFFICIENT↔UNSUPPORTED 경계 혼동은 최종 확정 약점으로 문서화한다** — Qwen뿐 아니라
  Nemotron Ultra(550B), Gemma-4-31B도 유사한 패턴을 보였다는 참고 체크 결과
  (`smoke_eval_review.md`)까지 종합하면, 이건 모델 체급의 문제가 아니라 이 태스크·이 프롬프트
  설계 자체의 알려진 한계로 #15 최종 리포트에 명시한다.
- **issue #23 완료** — Test(unseen) eval까지 끝났으니 #15(최종 결과 정리)로 넘어간다.
