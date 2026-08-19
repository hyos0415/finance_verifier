"""Shared helpers for Finlife free-text fields (spcl_cnd/mtrt_int/etc_note).

Finlife spells "no info" several ways ("해당사항 없음" / "없음" / "해당없음") —
exact-matching only one of them silently overcounts natural-missing samples
as populated text (see issue #5 eval-design review).
"""

NO_INFO_PLACEHOLDERS = {"해당사항 없음", "없음", "해당없음"}


def is_no_info(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in NO_INFO_PLACEHOLDERS | {""})
