"""Issue #4 — fetch bank time-deposit products from the FSS Finlife API
and save a raw snapshot (page 1 only; see CLAUDE.md for why).

Usage:
    python -m src.ingest.fetch_finlife
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

FINLIFE_URL = "https://finlife.fss.or.kr/finlifeapi/depositProductsSearch.json"
TOP_FIN_GRP_NO = "020000"  # 은행권
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "raw"


def fetch_page(api_key: str, page_no: int) -> dict:
    response = requests.get(
        FINLIFE_URL,
        params={
            "auth": api_key,
            "topFinGrpNo": TOP_FIN_GRP_NO,
            "pageNo": page_no,
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("FINLIFE_API_KEY")
    if not api_key:
        print("FINLIFE_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    print(f"[fetch] GET {FINLIFE_URL} (topFinGrpNo={TOP_FIN_GRP_NO}, pageNo=1, auth=***)")
    payload = fetch_page(api_key, page_no=1)

    result = payload.get("result", {})
    err_cd = result.get("err_cd")
    if err_cd != "000":
        print(f"[fetch] API error: err_cd={err_cd} err_msg={result.get('err_msg')}", file=sys.stderr)
        sys.exit(1)

    base_list = result.get("baseList", [])
    option_list = result.get("optionList", [])
    max_page_no = result.get("max_page_no")
    print(f"[fetch] ok: total_count={result.get('total_count')} max_page_no={max_page_no} "
          f"baseList={len(base_list)} optionList={len(option_list)}")
    if max_page_no not in (1, "1"):
        print(f"[fetch] warning: max_page_no={max_page_no} != 1 — later pages are not fetched by this script",
              file=sys.stderr)

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "source": "FSS Finlife",
        "endpoint": "depositProductsSearch",
        "top_fin_grp_no": TOP_FIN_GRP_NO,
        "page_no": 1,
        "fetched_at": fetched_at,
        "data": payload,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = OUT_DIR / f"finlife_deposit_{date_str}_page1.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch] saved snapshot to {out_path}")


if __name__ == "__main__":
    main()
