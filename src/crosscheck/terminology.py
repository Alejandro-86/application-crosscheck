"""Maps surface forms (abbreviations, brand names) to canonical terms.

This is a small stand-in for a real clinical terminology service (e.g. SNOMED CT for conditions,
RxNorm for drugs). Unknown terms return None so callers can flag them for review instead of
guessing.
"""

from crosscheck.synthetic import CONDITION_FORMS, MED_CATALOG

_CONDITIONS = {
    form.lower(): canonical for canonical, forms in CONDITION_FORMS.items() for form in forms
}
_DRUGS: dict[str, str] = {}
for _generic, (_brand, *_rest) in MED_CATALOG.items():
    _DRUGS[_generic] = _generic
    _DRUGS[_brand.lower()] = _generic


def _clean(text: str) -> str:
    return text.strip().strip(".;,").strip().lower()


def normalise_condition(text: str) -> str | None:
    return _CONDITIONS.get(_clean(text))


def normalise_drug(text: str) -> str | None:
    return _DRUGS.get(_clean(text))


def normalise_allergy(text: str) -> str:
    return _clean(text)
