# #6 Canonical Product Schema — 정규화 규칙 & 결과

`src/ingest/normalize_products.py`가 `data/raw/finlife_deposit_2026-08-18_page1.json`의
`baseList`(38건)와 `optionList`(152건)를 join해 `data/normalized/deposit_products_canonical.json`
(38개 canonical record)을 만든다. 이 문서는 "무엇을 어떻게 바꿨는지"와 "그 결과가 실제로 어떻게
생겼는지"를 같이 보여준다.

## 정규화 규칙

| 규칙 | 내용 | 근거 |
|---|---|---|
| Join key | `(fin_co_no, fin_prdt_cd)` — `optionList`를 이 키로 그룹핑해 `baseList` 각 레코드에 붙인다 | 프로파일링 스크립트와 동일한 키 사용 |
| `product_id` | `baseList` 원본 순서대로 `p001`~`p038` 부여 | Claim Dataset의 `claim_id`(`p012_c03` 형태)가 참조할 안정적인 짧은 ID가 필요 |
| 자연 결측 정규화 | `spcl_cnd`/`mtrt_int`/`etc_note`가 `"해당사항 없음"`/`"없음"`/`"해당없음"` 중 하나면 값을 `null`로 치환 | [#5 Eval Design 재검토](../profiling/eval_design_review.md)에서 발견한 3가지 placeholder 표기. `src/ingest/text_utils.py`의 `is_no_info()`로 프로파일링 스크립트와 공유 |
| `join_deny_code` / `join_deny_label` | 원본 코드값(`"1"`/`"2"`/`"3"`)을 보존하면서 `제한없음`/`서민전용`/`일부제한` 라벨을 나란히 추가 | Finlife API의 고정 enum. 이번 스냅샷엔 `"1"`만 있지만 향후 재수집 시 다른 코드가 나올 수 있어 라벨을 미리 붙여둠 |
| `options` | `save_trm`(문자열) → `save_trm_months`(int) 변환, `intr_rate`/`intr_rate2`/`intr_rate_type_nm` → `base_rate`/`max_rate`/`rate_type`로 이름 정리, `save_trm_months` 오름차순 정렬 | 원본 옵션은 정렬 순서가 보장되지 않음 |
| `disclosure` | `dcls_month`/`dcls_strt_day`/`dcls_end_day`/`fin_co_subm_day`를 하위 객체로 분리 | 공시 메타데이터는 Claim 근거 텍스트가 아니므로 최상위 필드와 구분 |
| 나머지 필드 | `kor_co_nm`→`bank_name`, `fin_prdt_nm`→`product_name`처럼 이름만 정리, 값은 원본 그대로 | 아직 raw 구조를 다 보지 못한 다른 abstraction은 추가하지 않음 (CLAUDE.md 원칙) |

## Before → After 예시 (자연 결측)

정규화 전 `spcl_cnd` 원본 값 3가지 표기 모두 `null`로 통일된다:

| product_id | 은행 | 원본 값 (raw) | 정규화 후 |
|---|---|---|---|
| p001 | 우리은행 | `"해당사항 없음"` | `null` |
| p022 | 중소기업은행 | `"없음"` | `null` |
| p024 | 한국산업은행 | `"해당없음"` | `null` (`etc_note`도 동일 사유로 `null`) |

## 스키마 예시 (populated / null 케이스)

```json
{
  "product_id": "p002",
  "fin_co_no": "0010002",
  "fin_prdt_cd": "00320342",
  "bank_name": "한국스탠다드차타드은행",
  "product_name": "e-그린세이브예금",
  "join_way": "인터넷,스마트폰",
  "join_member": "개인(개인사업자 포함)",
  "join_deny_code": "1",
  "join_deny_label": "제한없음",
  "max_limit": 1000000000,
  "spcl_cnd": "1.SC제일은행 최초 거래 신규고객 우대이율 제공(보너스이율0.1%) ...",
  "mtrt_int": "만기 후 1개월: 약정이율의 50% ...",
  "etc_note": "디지털채널 전용상품 (인터넷, 모바일뱅킹)",
  "disclosure": {
    "dcls_month": "202607",
    "dcls_strt_day": "20260803",
    "dcls_end_day": "99991231",
    "fin_co_subm_day": "202608031118"
  },
  "options": [
    { "save_trm_months": 1, "base_rate": 2.2, "max_rate": 2.3, "rate_type": "단리" },
    { "save_trm_months": 3, "base_rate": 2.9, "max_rate": 3.0, "rate_type": "단리" },
    { "save_trm_months": 6, "base_rate": 3.2, "max_rate": 3.4, "rate_type": "단리" },
    { "save_trm_months": 12, "base_rate": 3.65, "max_rate": 3.85, "rate_type": "단리" }
  ]
}
```

```json
{
  "product_id": "p001",
  "bank_name": "우리은행",
  "product_name": "WON플러스예금",
  "spcl_cnd": null,
  "mtrt_int": "만기 후\n- 1개월이내 : 만기시점약정이율×50%\n...",
  "etc_note": "- 가입기간: 1~36개월\n..."
}
```

## 전체 38개 상품 (요약)

| product_id | 은행 | 상품명 | 옵션수 | 기간범위(개월) | spcl_cnd | mtrt_int | etc_note |
|---|---|---|---|---|---|---|---|
| p001 | 우리은행 | WON플러스예금 | 6 | 1~36 | null | populated | populated |
| p002 | 한국스탠다드차타드은행 | e-그린세이브예금 | 4 | 1~12 | populated | populated | populated |
| p003 | 아이엠뱅크 | iM함께예금 | 1 | 12~12 | populated | populated | populated |
| p004 | 아이엠뱅크 | iM스마트예금 | 6 | 1~36 | populated | populated | populated |
| p005 | 부산은행 | LIVE정기예금 | 6 | 1~36 | populated | populated | populated |
| p006 | 부산은행 | 더(The) 특판 정기예금 | 6 | 1~36 | populated | populated | populated |
| p007 | 부산은행 | 더(The) 레벨업 정기예금 | 2 | 6~12 | populated | populated | populated |
| p008 | 광주은행 | 미즈월복리정기예금 | 3 | 12~36 | populated | populated | populated |
| p009 | 광주은행 | 스마트모아Dream정기예금 | 6 | 1~36 | populated | populated | populated |
| p010 | 광주은행 | 굿스타트예금 | 1 | 12~12 | populated | populated | populated |
| p011 | 광주은행 | The플러스예금 | 3 | 3~12 | populated | populated | populated |
| p012 | 제주은행 | 제주Dream 정기예금 (개인/만기 지급식) | 6 | 1~36 | populated | populated | populated |
| p013 | 제주은행 | J정기예금 (만기지급식) | 6 | 1~36 | populated | populated | populated |
| p014 | 제주은행 | 스마일드림 정기예금 (개인/선이자 지급식) | 3 | 3~12 | populated | populated | populated |
| p015 | 전북은행 | JB 다이렉트예금통장 (만기일시지급식) | 3 | 3~12 | populated | populated | populated |
| p016 | 전북은행 | JB 123 정기예금 (만기일시지급식) | 1 | 12~12 | populated | populated | populated |
| p017 | 전북은행 | 내맘 쏙 정기예금 | 4 | 1~12 | populated | populated | populated |
| p018 | 경남은행 | BNK더조은정기예금 | 4 | 3~24 | populated | populated | populated |
| p019 | 경남은행 | The든든예금(시즌2) | 3 | 3~12 | populated | populated | populated |
| p020 | 경남은행 | The파트너예금 | 4 | 3~24 | populated | populated | populated |
| p021 | 중소기업은행 | IBK평생한가족통장(실세금리정기예금) | 3 | 12~36 | populated | populated | populated |
| p022 | 중소기업은행 | IBK더굴리기통장(실세금리정기예금) | 4 | 6~36 | null | populated | populated |
| p023 | 중소기업은행 | IBK굴리기통장(정기예금) | 4 | 1~12 | null | populated | populated |
| p024 | 한국산업은행 | KDB 정기예금 | 6 | 1~36 | null | populated | null |
| p025 | 국민은행 | KB Star 정기예금 | 6 | 1~36 | populated | populated | populated |
| p026 | 신한은행 | 신한My플러스 정기예금 | 4 | 1~12 | populated | populated | populated |
| p027 | 신한은행 | 쏠편한 정기예금 | 6 | 1~36 | populated | populated | populated |
| p028 | 농협은행주식회사 | NH왈츠회전예금 II | 4 | 1~12 | populated | populated | populated |
| p029 | 농협은행주식회사 | NH내가Green초록세상예금 | 3 | 12~36 | populated | populated | populated |
| p030 | 농협은행주식회사 | NH올원e예금 | 6 | 1~36 | null | populated | populated |
| p031 | 농협은행주식회사 | NH고향사랑기부예금 | 1 | 12~12 | populated | populated | populated |
| p032 | 주식회사 하나은행 | 하나의정기예금 | 6 | 1~36 | populated | populated | populated |
| p033 | 주식회사 케이뱅크 | 코드K 정기예금 | 6 | 1~36 | populated | populated | populated |
| p034 | 수협은행 | Sh해양플라스틱Zero!예금 (만기일시지급식) | 2 | 6~12 | populated | populated | populated |
| p035 | 수협은행 | 헤이(Hey)정기예금 | 3 | 3~12 | null | populated | populated |
| p036 | 수협은행 | Sh첫만남우대예금 | 1 | 12~12 | populated | populated | populated |
| p037 | 주식회사 카카오뱅크 | 카카오뱅크 정기예금 | 6 | 1~36 | populated | populated | populated |
| p038 | 토스뱅크 주식회사 | 토스뱅크 먼저 이자 받는 정기예금 | 3 | 3~12 | populated | populated | populated |

`spcl_cnd` null 6건(p001/p022/p023/p024/p030/p035)이 [#5 리뷰](../profiling/eval_design_review.md)에서
확인한 자연 발생 INSUFFICIENT 표본과 정확히 일치한다. `etc_note`는 p024 1건만 null.

## 검증

- `options` 개수 min/max/mean = 1/6/4.0 — 프로파일링 통계(`options_per_product`)와 일치
- `product_id` 38개 전부 unique
- `mtrt_int` null 0건 — 100% populated 통계와 일치

## 다음 단계

이 canonical record가 Claim Dataset Builder(#12, Claim Decomposer)의 입력이 된다. Claim metadata 스키마
초안(`claim_id`/`product_id`/`source_field`/`label`/`error_type`/`reasoning_type`/`insufficient_source`/
`dataset_split`)은 [#5 리뷰 문서](../profiling/eval_design_review.md)에 이미 정리돼 있고, `product_id`가
그 스키마의 join key가 된다.
