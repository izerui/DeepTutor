"""Japanese and Korean interface locales: registration, catalogs and server-side copy."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from deeptutor.api.routers.quiz_judge import SUPPORTED_JUDGE_LANGUAGES
from deeptutor.book.export import _LABELS as EXPORT_LABELS
from deeptutor.book.overview_copy import _COPY as OVERVIEW_COPY
from deeptutor.catalog.taxonomy import grades
from deeptutor.catalog.taxonomy_i18n import LANGUAGES, NAMES, NAMES_JA_KO, localized
from deeptutor.runtime.banner import LABELS as BANNER_LABELS
from deeptutor.runtime.banner import _pick_language
from deeptutor.services.config.launch_settings import (
    _normalize_language as _launch_language,
)
from deeptutor.services.config.settings_spec import _LANGUAGE_CHOICES
from deeptutor.services.i18n import _MESSAGES, _parse_language
from deeptutor.services.prompt.language import language_directive, language_label
from deeptutor.services.settings.interface_settings import _normalize_language

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

LOCALES = {
    "ja": ("日本語", ["ja", "JA", " ja ", "jp", "japanese", "Japanese", "ja-JP", "ja_JP", "日本語"]),
    "ko": ("한국어", ["ko", "KO", " ko ", "kr", "korean", "Korean", "ko-KR", "ko_KR", "한국어"]),
}


def _catalog(locale: str) -> dict[str, str]:
    return json.loads((WEB / f"locales/{locale}/app.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_every_normalizer_accepts_the_language(code: str) -> None:
    for raw in LOCALES[code][1]:
        assert _normalize_language(raw) == code, raw
    assert _launch_language(f"{code}-{code.upper()}") == code
    assert _parse_language(code) == code
    assert _pick_language(code) == code


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_settings_offer_the_language_by_its_own_name(code: str) -> None:
    labels = {choice: label for choice, label, _desc in _LANGUAGE_CHOICES}
    assert labels[code] == LOCALES[code][0]
    assert language_label(code) == LOCALES[code][0]
    assert LOCALES[code][0] in language_directive(code)


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_web_catalog_is_complete_with_a_single_plural_form(code: str) -> None:
    en = _catalog("en")
    catalog = _catalog(code)
    # Japanese and Korean have only the "other" plural category, so `_one` is not needed.
    expected = {key for key in en if not key.endswith("_one")}
    assert set(catalog) == expected
    assert all(value.strip() for value in catalog.values())


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_web_catalog_keeps_interpolation_placeholders(code: str) -> None:
    en = _catalog("en")
    catalog = _catalog(code)
    mismatches = [
        key for key in catalog if set(PLACEHOLDER.findall(en[key])) != set(PLACEHOLDER.findall(catalog[key]))
    ]
    assert mismatches == []


def test_korean_catalog_is_written_in_hangul() -> None:
    korean = _catalog("ko")
    kana_or_han = re.compile(r"[぀-ヿ一-鿿]")
    assert [key for key, value in korean.items() if kana_or_han.search(value) and not key.startswith("language.")] == []


def test_every_locale_names_japanese_and_korean_by_endonym() -> None:
    for locale in ("en", "zh", "fr", "de", "uk", "pl", "ja", "ko"):
        catalog = _catalog(locale)
        assert catalog["language.japanese"] == "日本語"
        assert catalog["language.korean"] == "한국어"


def test_frontend_registers_both_languages() -> None:
    languages = (WEB / "i18n/languages.ts").read_text(encoding="utf-8")
    init = (WEB / "i18n/init.ts").read_text(encoding="utf-8")
    datetime = (WEB / "lib/datetime.ts").read_text(encoding="utf-8")
    for code, name in (("ja", "japanese"), ("ko", "korean")):
        assert f'{{ code: "{code}", labelKey: "language.{name}" }}' in languages
        assert f"@/locales/{code}/app.json" in init
    assert '"ja-JP"' in datetime and '"ko-KR"' in datetime


@pytest.mark.parametrize("table", [BANNER_LABELS, _MESSAGES, EXPORT_LABELS, OVERVIEW_COPY])
def test_server_copy_has_every_english_entry(table: dict[str, dict]) -> None:
    for code in LOCALES:
        assert set(table[code]) == set(table["en"]), code


def test_quiz_feedback_speaks_both_languages() -> None:
    assert {"ja", "ko"} <= SUPPORTED_JUDGE_LANGUAGES


def test_built_in_classification_is_named_in_both_languages() -> None:
    assert set(NAMES_JA_KO) == set(NAMES)
    assert LANGUAGES[-2:] == ("ja", "ko")
    names = localized({"zh": "数学", "en": "Mathematics"})
    assert names["ja"] == "数学" and names["ko"] == "수학"
    seventh = next(g for g in grades() if g["id"] == "grade.7")
    assert seventh["names"]["ja"] == "中学1年"
    assert seventh["names"]["ko"] == "중학교 1학년"
