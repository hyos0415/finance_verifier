# data/sample/ — 구조 확인용 마스킹 샘플

이 디렉토리의 두 파일은 **실제 공시 데이터가 아니다.** 파이프라인의 입출력 구조를 보여주기 위해
직접 지어낸 합성 데이터다. 은행명·상품명·금리·우대조건 문구 전부 가상이며, 실제 금융상품과
대응 관계가 없다.

| 파일 | 대응하는 실제 산출물 | 내용 |
|---|---|---|
| `deposit_products_sample.json` | `data/normalized/deposit_products_canonical.json` | canonical product record 4건 (`s001`~`s004`) |
| `claim_dataset_sample.json` | `data/{smoke,pilot,dev,test}/claim_dataset.json` | Claim + gold label 6건 (`s002_c01` 등) |

## 왜 실제 데이터가 repo에 없나

이 프로젝트의 데이터 소스는 금융감독원 "금융상품 한눈에" Open API이고, 인증키를 발급받아야
호출할 수 있다. 수집한 스냅샷 파일을 공개 repo에 그대로 올리는 건 인증키로 접근을 관리하는
제공 방식을 우회하는 재배포에 가깝다고 판단해, **`data/` 아래 데이터셋 파일은 git 추적에서
제외했다** (`.gitignore` 참고). 수집·정규화 코드와 평가 지표·분석 리포트는 repo에 그대로 있다.

## 4건이 커버하는 구조

샘플 4건은 데이터 난이도 축(Level 1 정형 필드 / Level 2 자유서술 조건문)과 자연 결측을 모두
포함하도록 골랐다.

| product_id | 특징 | 왜 넣었나 |
|---|---|---|
| `s001` | `spcl_cnd`가 `null`, `mtrt_int` 3구간 | 자연 결측(natural missing) → INSUFFICIENT gold label의 소스 |
| `s002` | 번호 매긴 우대조건, `max_limit` 있음, 옵션 4구간 | 기본금리/최고금리 혼동(`base_vs_max_rate`) 케이스 |
| `s003` | `any_of`(1가지 이상 요건 충족) 조건 | AND/OR 반전(`condition_reversal`) 케이스 |
| `s004` | `all_of`(A이면서 동시에 B) + 예외 단서 | 복합조건 일부 누락(`condition_omission`) 케이스 — Test에서 확인된 약점 |

`claim_dataset_sample.json` 6건은 위 4개 상품에 대해 SUPPORTED / `base_vs_max_rate` /
`condition_reversal` / `condition_omission` / 자연 결측 INSUFFICIENT를 각각 하나씩 담고 있다.

## 실제 데이터로 재현하려면

```bash
# 1. 인증키를 직접 발급받아 .env에 넣는다 (FINLIFE_API_KEY=...)
python -m src.ingest.fetch_finlife        # data/raw/ 스냅샷 생성
python -m src.ingest.normalize_products   # data/normalized/ canonical record 생성
python -m src.ingest.profile_deposit_products   # results/profiling/ 프로파일 재생성

# 2. 이후 Claim Dataset 구축 → Verifier eval은 ANTHROPIC_API_KEY와 vLLM 서버가 필요하다
```

인증키 발급: 금융감독원 금융상품통합비교공시 "금융상품 한눈에" 개발자 페이지.
