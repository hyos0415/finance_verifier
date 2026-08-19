# #5 Eval Design 관점 재검토

기존 프로파일링(38개 상품 / 152개 옵션, `results/profiling/deposit_products_profile.json`)을
"어떤 필드가 어떤 Claim/오류 유형을 만들기 좋은가"라는 관점으로 재해석한다. 은행별 평균금리·스프레드
등 시장 비교성 통계는 corpus sanity check로만 참고하고, 아래 5개 질문에 답하는 데 집중한다.

## 데이터 보정 사항 (작업 중 발견)

`src/ingest/profile_deposit_products.py`의 결측 판정이 `"해당사항 없음"` 정확 일치만 확인해
`"없음"` / `"해당없음"` 같은 다른 표기의 자연 결측을 놓치고 있었다. `is_no_info()`로 통합해
재실행한 결과:

- `spcl_cnd` non-null ratio: 0.9737(37/38) → **0.8421(32/38)** — 자연 결측 1건 → **6건**
- 재실행된 `condition_complexity.spcl_cnd`: n=37→32, and_marker_ratio 0.162→0.188, or_marker_ratio 0.135→0.156, avg_numeric_conditions 3.05→3.53
- `text_length_stats.spcl_cnd`도 같은 placeholder(`"없음"` 등 2~5자)를 실제 조건문처럼 세고 있어서 평균/중앙값이 낮게 잡혀 있었음 → 제외 후 mean 81.0→95.6, median 79.5→109.0로 보정 (실제 우대조건 문장은 생각보다 더 길고 복잡함)
- profile JSON에 `natural_missing_spcl_cnd` 필드 추가 (아래 Q5 참고)

## Q1. 어떤 필드가 어떤 종류의 Claim을 만들기에 적합한가

| 필드 | 실제 구성 | 적합한 reasoning |
|---|---|---|
| `spcl_cnd` (32/38 populated, 84.2%) | AND 표지 18.8%, OR 표지 15.6%, 둘 다 6.2%, 문장당 평균 숫자조건 3.53개(최대 11) | 논리 조건·우대조건·예외 추론 — `condition_reversal`/`condition_omission`/`conditional_benefit_generalization`/`eligibility_error`의 주 소스 |
| `mtrt_int` (38/38 populated, 100%) | AND/OR 표지 거의 0, 문장당 평균 숫자조건 5.21개(최대 9). 샘플 5건 전부 "만기 후 N개월 이내/초과: 약정이율×P%" 계단형 구조 | 숫자·구간·경계값 추론 — `term_error`류와 명확히 다른 별도 축 |
| `etc_note` (37/38 populated, 97.4%) | AND/OR 표지 0, 문장당 평균 숫자조건 1.5개 | 조건 복잡도가 낮아 Level 1 sanity claim이나 부가 유의사항 정도로만 사용, 우선순위 낮음 |

## Q2. Verifier에게 어려울 것으로 예상되는 reasoning type

`spcl_cnd` 32건에서 후보 마커 빈도를 직접 스캔(정규식 기반, 과소추정 가능성 있음 — 자연어 표현이
`①②③...` 나열처럼 마커 없이도 암묵적으로 조건을 나열하는 경우가 많다):

```
prerequisite_temporal_maturity ("만기일/만기시/만기해지"): 7/32 (21.9%)
numeric_threshold_cap ("최고 연/최대 우대"):               5/32 (15.6%)
exception_clause ("단,/다만/제외하고"):                    3/32 (9.4%)
all_of / any_of / mutually_exclusive / range_condition_fraction /
temporal_window:                                            각 1/32 (3.1%)
```

→ 실제 채택할 taxonomy: `all_of`, `any_of`, `mutually_exclusive`, `temporal_scope`(prerequisite 포함),
`numeric_threshold`, `exception`. `range_condition`은 표본이 1건뿐이라 독립 카테고리 대신
`numeric_threshold`의 하위 유형으로 흡수. 표본이 32건으로 작아 마커 빈도만으로는 부족하고,
Claim 생성 시점에 사람이 각 예시를 직접 라벨링하는 편이 현실적이다(전수 라벨링 가능한 규모).

## Q3. `mtrt_int`를 별도 난이도 축으로 분리할지

샘플 5건 전부 동일한 계단형 구조(`만기 후 N개월 이내: 약정이율×P%`, 구간 2~3단)를 보여 —
`spcl_cnd`(논리·예외 추론)와 확실히 다른 숫자·구간 매핑 추론을 요구한다. 별도 축으로 채택하고
failure taxonomy에 `boundary_condition_error`, `numeric_mapping_error`를 신규 추가한다
(`maturity_period_confusion`은 이 둘로 흡수, 별도 유지 불필요).

## Q4. Pilot baseline 통제

`rate_distribution_by_term_months` 기준:

- 12개월: 38/38 유일 공통 구간, spread_mean 0.434%p(전 구간 중 최대) → **Pilot/Model Selection baseline으로 채택**
- 36개월: spread_mean 0.278%p(낮음)이지만 spread_max 2.0%p(전 구간 중 최대) → 조건부 혜택을 크게 얹는 이례적 상품 존재, hard edge case로 활용
- 1/3/6/24/36개월은 상품마다 존재 여부가 다름(`save_trm` 세트가 상품별로 상이) → `term_error`(비공통 구간 값 착오) claim은 이 구간에서만 자연스럽게 만들 수 있음

## Q5. 자연 발생 INSUFFICIENT 사례

보정 후 `spcl_cnd` 자연 결측이 **1건 → 6건(15.8%)**으로 확인됨 (`natural_missing_spcl_cnd` 참고):

| fin_prdt_cd | 은행 | 상품명 | 원본 값 |
|---|---|---|---|
| WR0001B | 우리은행 | WON플러스예금 | `해당사항 없음` |
| 01211310130 | 중소기업은행 | IBK더굴리기통장(실세금리정기예금) | `없음` |
| 01211310142 | 중소기업은행 | IBK굴리기통장(정기예금) | `없음` |
| 06492 | 한국산업은행 | KDB 정기예금 | `해당없음` |
| 10-003-1384-0001 | 농협은행주식회사 | NH올원e예금 | `없음` |
| 10120114700011 | 수협은행 | 헤이(Hey)정기예금 | `없음` |

→ Claim metadata에 `insufficient_source: natural_missing | synthetic_missing` 구분 채택.
자연 샘플 6건이면 INSUFFICIENT 최소 커버리지를 synthetic 삭제 없이도 확보 가능하다.

## Q6 (원 질문 순서상 failure taxonomy 재정리)

기존 8종 대비 변경:

```
numeric_error                          유지
term_error                             유지 (비공통 구간 착오 중심으로 범위 좁힘)
eligibility_error                      유지
condition_reversal                     유지 (AND↔OR 반전)
condition_omission                     유지
base_vs_max_rate                       유지 (12개월 baseline 중심)
conditional_benefit_generalization     유지
missing_information                    insufficient_source(natural_missing/synthetic_missing) 하위분류 추가
boundary_condition_error               신규 (mtrt_int 경계값 오류)
numeric_mapping_error                  신규 (mtrt_int 구간→숫자 매핑 오류)
mutually_exclusive_ignored             신규 후보이나 표본 1건 — hard/rare로만 표시, 정식 채택은 보류
```

## Split 전략

Product-level split 채택 — 하나의 `fin_prdt_cd`에서 나온 Claim은 전부 동일 split에 배정한다.
상품 수가 38개로 작으므로, split 확정 후 class/error_type 분포가 한쪽으로 쏠리지 않는지
Claim Dataset Builder(#12) 단계에서 실제로 확인한다.

## Claim Dataset Metadata 스키마 (초안 — #12에서 실제 구현된 스키마로 대체됨)

아래는 이 문서 작성 시점의 초안이다. **실제로 #12(Claim Decomposer)에서 구현된 스키마는 이것과
다르다** — `difficulty_features`는 만들지 않았고, 대신 decompose 과정 자체를 검증하는 필드
(`claim_text`/`answer_text`/`evidence_text`/`atomicity_ok`/`coverage_ok`/`coverage_missing_numbers`/
`needs_manual_review`)가 추가됐다. 최신 스키마와 설계 근거는
[claim_decomposer_smoke_review.md](../decomposition/claim_decomposer_smoke_review.md)를 참고할 것
— 아래 블록은 역사적 초안으로만 남겨둔다.

```json
{
  "claim_id": "p012_c03",
  "product_id": "p012",
  "source_field": "spcl_cnd",
  "label": "UNSUPPORTED",
  "error_type": "condition_omission",
  "reasoning_type": ["all_of", "temporal_scope"],
  "insufficient_source": null,
  "difficulty_features": {
    "num_conditions": 3,
    "numeric_conditions": 2,
    "logical_operators": 1,
    "has_exception": false,
    "has_temporal_condition": true
  },
  "dataset_split": "dev"
}
```

`difficulty`는 임의 점수로 바로 정의하지 않고, 위 `difficulty_features`를 관찰 가능한 값으로
먼저 저장한 뒤 Pilot/Dev 단계에서 실제 모델 성능을 보고 버킷화한다는 아이디어 자체는 유효하다 —
다만 `difficulty_features` 필드를 실제로 채우는 건 #12에서 하지 않았으므로, Pilot 규모로 갈 때
다시 검토가 필요하다.

## 종료 조건 체크

1. 어떤 필드가 어떤 Claim에 적합한가 → Q1
2. Verifier에게 어려운 reasoning type → Q2, Q3
3. Pilot baseline 통제 조건 → Q4 (12개월)
4. Failure taxonomy → Q6
5. dev/test leakage 방지 단위 → Product-level split

5개 질문에 모두 답했으므로 #5는 여기서 닫고 **#6 (Canonical Schema / Claim Dataset Builder)**로 이관한다.
