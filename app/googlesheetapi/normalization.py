"""Canonicalize UTM strings without altering source documents in MongoDB."""
import re
import unicodedata

# Invisible formatting characters commonly copied into campaign values.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\ufeff\u2060\u061c\u034f"), None)
_PERSIAN_ARABIC_NUMERALS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
)
_LETTER_VARIANTS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})


def normalize_utm(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return ""  # no implicit matching against unexpected BSON types
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(_INVISIBLE).translate(_PERSIAN_ARABIC_NUMERALS)
    value = value.translate(_LETTER_VARIANTS)
    return re.sub(r"\s+", " ", value).strip().casefold()
