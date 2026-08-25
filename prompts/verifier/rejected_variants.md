# 기각된 Verifier 프롬프트 변형 (Langfuse version 3, 5)

두 변형 모두 **INSUFFICIENT↔UNSUPPORTED 경계 오분류**를 고치려는 시도였고, 둘 다 기각됐다.
헤드라인 지표와 케이스별 분석은 [`../../results/eval/smoke_eval_review.md`](../../results/eval/smoke_eval_review.md) 참고.

## version 3 — 판정 순서 규칙 + worked example (기각)

version 2에 아래 취지의 "판정 순서" 규칙과 예시 1개를 덧붙인 버전이다.

> 판정 순서: evidence에 claim이 다루는 항목이 전혀 언급 안 됐으면 반드시 INSUFFICIENT로 판정하라,
> 언급이 있고 내용이 다를 때만 UNSUPPORTED.

> **주의**: 이 문구는 당시 리뷰 문서에 요약된 형태다. 덧붙인 규칙 원문과 worked example 전문은
> Langfuse version 3에만 남아 있고 repo에는 보존되지 않았다.

**결과**: 목표했던 INSUFFICIENT 4건은 양쪽 모델 모두 정확히 맞혔지만, 원래 맞히던
SUPPORTED/UNSUPPORTED 판정이 흔들렸다("애매하면 INSUFFICIENT로 도피"). Kanana Macro F1
0.7652→0.5714, Qwen False Accept Rate 0.111→0.1667. **핵심 지표인 FAR가 나빠져 기각.**

원인 추정: (1) 예시 1개가 과적합을 유발, (2) 지시문이 길어져 condition_reversal 등 기존 판정
지침에 대한 주의가 희석.

## version 5 — INSUFFICIENT 회피 억제 규칙만 단독 추가 (기각)

version 3의 실패 원인(절차 규칙 + 예시)을 걷어내고, version 2에 짧고 강한 부정 규칙 한 문단만
추가했다. 길이 증가를 최소화해 과적합을 피하는 게 설계 의도였다.

```text
INSUFFICIENT는 evidence에 claim이 다루는 항목이 전혀 언급되지 않았을 때만 써라 — 애매하거나
확신이 안 선다는 이유로 INSUFFICIENT를 고르지 마라. evidence에 관련 내용이 하나라도 있으면
그 내용과 claim을 직접 비교해 SUPPORTED 또는 UNSUPPORTED 중 하나로만 판정하라.
```

**결과**: FAR와 UNSUPPORTED Recall은 양쪽 모델 다 version 2와 동일하게 유지됐지만, 목표였던
INSUFFICIENT 개선이 Qwen에서 실패했다 — version 2에서 1/4 맞히던 것이 **0/4**로 악화되고 Macro F1도
0.6656→0.5464로 나빠졌다. Kanana는 소폭 개선(F1 0.7652→0.7992)됐지만, 두 후보는 동일 프롬프트를
써야 공정 비교가 성립하므로 Qwen에서의 악화 하나만으로 채택 기준 미달. **기각.**
