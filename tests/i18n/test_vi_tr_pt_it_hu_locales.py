"""Vietnamese, Turkish, Brazilian Portuguese, Italian and Hungarian interface locales."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from deeptutor.api.routers.quiz_judge import SUPPORTED_JUDGE_LANGUAGES, _build_judge_user_prompt
from deeptutor.book.export import _LABELS as EXPORT_LABELS
from deeptutor.book.overview_copy import _COPY as OVERVIEW_COPY
from deeptutor.catalog.taxonomy import OTHER_LANGUAGES, grades
from deeptutor.catalog.taxonomy_i18n import LANGUAGES, NAMES, NAMES_VI_TR_PT_IT_HU, localized
from deeptutor.core.response_languages import SUPPORTED_RESPONSE_LANGUAGES
from deeptutor.runtime.banner import LABELS as BANNER_LABELS
from deeptutor.runtime.banner import _pick_language
from deeptutor.services.config.launch_settings import (
    _normalize_language as _launch_language,
)
from deeptutor.services.config.settings_spec import _LANGUAGE_CHOICES, _RESPONSE_LANGUAGE_CHOICES
from deeptutor.services.i18n import _MESSAGES, _parse_language
from deeptutor.services.prompt.language import language_label
from deeptutor.services.settings.interface_settings import (
    _normalize_language,
    _normalize_response_language,
)

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")

LOCALES = {
    "vi": ("Tiếng Việt", ["vi", "VI", " vi ", "vi-VN", "vi_VN", "vietnamese", "Tiếng Việt"]),
    "tr": ("Türkçe", ["tr", "TR", "tr-TR", "tr_TR", "turkish", "Turkish", "Türkçe"]),
    "pt": ("Português (Brasil)", ["pt", "pt-BR", "pt_BR", "pt-br", "portuguese", "Português"]),
    "it": ("Italiano", ["it", "IT", "it-IT", "it_IT", "italian", "Italiano"]),
    "hu": ("Magyar", ["hu", "HU", "hu-HU", "hu_HU", "hungarian", "Magyar"]),
}
#: The plural categories i18next asks for (Intl.PluralRules) besides the plain key.
PLURAL_FORMS = {"vi": {"other"}, "tr": {"one", "other"}, "hu": {"one", "other"},
                "pt": {"one", "many", "other"}, "it": {"one", "many", "other"}}


def _catalog(locale: str) -> dict[str, str]:
    return json.loads((WEB / f"locales/{locale}/app.json").read_text(encoding="utf-8"))


def _expected_keys(code: str) -> set[str]:
    keys: set[str] = set()
    for key in _catalog("en"):
        stem, _, form = key.rpartition("_")
        if form in {"one", "other"} and stem:
            keys |= {f"{stem}_{f}" for f in PLURAL_FORMS[code]}
        else:
            keys.add(key)
    return keys


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_every_normalizer_accepts_the_language(code: str) -> None:
    for raw in LOCALES[code][1]:
        assert _normalize_language(raw) == code, raw
        assert _launch_language(raw) == code, raw
        assert _parse_language(raw) == code, raw
        assert _pick_language(raw) == code, raw


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_settings_offer_the_language_by_its_own_name(code: str) -> None:
    labels = {choice: label for choice, label, _desc in _LANGUAGE_CHOICES}
    assert labels[code] == LOCALES[code][0]
    assert code in OTHER_LANGUAGES and code in LANGUAGES


@pytest.mark.parametrize("code", ["vi", "tr", "hu"])
def test_replies_can_be_asked_for_in_the_new_reply_languages(code: str) -> None:
    assert code in SUPPORTED_RESPONSE_LANGUAGES
    assert code in {choice for choice, _label, _desc in _RESPONSE_LANGUAGE_CHOICES}
    assert language_label(code) == LOCALES[code][0]
    assert _normalize_response_language(LOCALES[code][0]) == code


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_web_catalog_is_complete_with_the_plural_forms_of_the_language(code: str) -> None:
    catalog = _catalog(code)
    assert set(catalog) == _expected_keys(code)
    assert all(value.strip() for value in catalog.values())


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_web_catalog_keeps_interpolation_placeholders(code: str) -> None:
    en = _catalog("en")
    catalog = _catalog(code)
    mismatches = []
    for key, value in catalog.items():
        source = en.get(key) or en[key.rsplit("_", 1)[0] + "_other"]
        expected, actual = set(PLACEHOLDER.findall(source)), set(PLACEHOLDER.findall(value))
        if key.endswith(("_one", "_many", "_other")):
            expected.add("count")
            actual.add("count")
        if expected != actual:
            mismatches.append(key)
    assert mismatches == []


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_web_catalog_has_no_cjk_outside_language_names(code: str) -> None:
    en = _catalog("en")
    catalog = _catalog(code)
    leaks = [
        key for key, value in catalog.items()
        if CJK.search(value) and not key.startswith("language.") and not CJK.search(en.get(key, ""))
    ]
    assert leaks == []


def test_every_locale_names_the_five_languages_by_endonym() -> None:
    names = {"vietnamese": "Tiếng Việt", "turkish": "Türkçe", "portuguese": "Português (Brasil)",
             "italian": "Italiano", "hungarian": "Magyar"}
    for locale in ("en", "zh", "fr", "de", "uk", "pl", "ja", "ko", *LOCALES):
        catalog = _catalog(locale)
        for name, endonym in names.items():
            assert catalog[f"language.{name}"] == endonym, (locale, name)


def test_frontend_registers_all_five_languages() -> None:
    languages = (WEB / "i18n/languages.ts").read_text(encoding="utf-8")
    init = (WEB / "i18n/init.ts").read_text(encoding="utf-8")
    datetime = (WEB / "lib/datetime.ts").read_text(encoding="utf-8")
    names = {"vi": "vietnamese", "tr": "turkish", "pt": "portuguese", "it": "italian", "hu": "hungarian"}
    for code, name in names.items():
        assert f'{{ code: "{code}", labelKey: "language.{name}" }}' in languages
        assert f"@/locales/{code}/app.json" in init
    for tag in ("vi-VN", "tr-TR", "pt-BR", "it-IT", "hu-HU"):
        assert f'"{tag}"' in datetime


@pytest.mark.parametrize("table", [BANNER_LABELS, _MESSAGES, EXPORT_LABELS, OVERVIEW_COPY])
def test_server_copy_has_every_english_entry(table: dict[str, dict]) -> None:
    for code in LOCALES:
        assert set(table[code]) == set(table["en"]), code


def test_server_messages_keep_their_format_fields() -> None:
    fields = re.compile(r"\{([^{}]*)\}")
    for code in LOCALES:
        for key, text in _MESSAGES["en"].items():
            assert set(fields.findall(_MESSAGES[code][key])) == set(fields.findall(text)), (code, key)
        for key, text in BANNER_LABELS["en"].items():
            assert set(fields.findall(BANNER_LABELS[code][key])) == set(fields.findall(text)), (code, key)


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_quiz_feedback_speaks_the_language(code: str) -> None:
    assert code in SUPPORTED_JUDGE_LANGUAGES
    prompt = _build_judge_user_prompt(
        language=code, question="2 + 2 = ?", question_type="", options=None, correct_answer="4",
        explanation="", user_answer="", has_image=True, image_count=2,
    )
    assert "Question type" not in prompt and "Learner's answer" not in prompt
    assert "2" in prompt


def test_built_in_classification_is_named_in_the_five_languages() -> None:
    assert set(NAMES_VI_TR_PT_IT_HU) == set(NAMES)
    assert LANGUAGES[-5:] == ("vi", "tr", "pt", "it", "hu")
    names = localized({"zh": "数学", "en": "Mathematics"})
    assert (names["vi"], names["tr"], names["pt"], names["it"], names["hu"]) == (
        "Toán học", "Matematik", "Matemática", "Matematica", "Matematika",
    )
    for code in LOCALES:
        values = [entry[list(LOCALES).index(code)] for entry in NAMES_VI_TR_PT_IT_HU.values()]
        assert all(value.strip() and len(value) <= 40 and not CJK.search(value) for value in values)
    by_id = {grade["id"]: grade["names"] for grade in grades()}
    assert by_id["grade.7"]["pt"] == "7º ano" and by_id["grade.10"]["pt"] == "1º ano do ensino médio"
    assert by_id["grade.7"]["it"] == "1ª media" and by_id["grade.12"]["it"] == "3ª superiore"
    assert by_id["grade.9"]["vi"] == "Lớp 9" and by_id["grade.9"]["tr"] == "9. Sınıf"
    assert by_id["grade.9"]["hu"] == "9. osztály"
