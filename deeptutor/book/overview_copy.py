"""Reader-facing copy for the engine-injected Overview chapter.

This one page is built **deterministically** — no LLM call — so its wording
cannot come from the model the way every other block's does. It used to live as
``"中文" if book.language == "zh" else "English"`` conditionals scattered through
``engine.py``, which meant the language picker could offer eleven languages
while the book's own front page only ever spoke two.

Collecting it here does not by itself translate the page, but it turns "add a
language" from an edit across two functions into one entry in this table, and
it makes the gap visible instead of implicit: anything not listed falls back to
English, and :func:`missing_languages` reports what is still owed.
"""

from __future__ import annotations

from typing import Any

# Keys every entry must define. Kept explicit so a partial translation fails
# loudly in tests rather than rendering a stray English string mid-page.
REQUIRED_KEYS = frozenset(
    {
        "chapter_title",
        "chapter_summary",
        "objectives",
        "intro_title",
        "intro_body",
        "untitled_book",
        "concept_map_title",
        "chapter_index_title",
        "chapter_index_heading",
    }
)

_COPY: dict[str, dict[str, Any]] = {
    "en": {
        "chapter_title": "How to read this book",
        "chapter_summary": (
            "Auto-generated overview of the book's concept graph and chapter index."
        ),
        "objectives": [
            "See the full chapter map at a glance",
            "Understand how concepts depend on each other",
            "Pick the reading path that fits your goals",
        ],
        "intro_title": "How to read this book",
        "intro_body": (
            "The diagram below maps the {concepts} core concepts in this book and "
            "how they depend on each other. The chapter index that follows lists "
            "all {chapters} chapters — read top-to-bottom for the recommended "
            "path, or jump straight to whatever you're most curious about."
        ),
        "untitled_book": "This book",
        "concept_map_title": "Concept map",
        "chapter_index_title": "Chapter index",
        "chapter_index_heading": "## Chapter index",
    },
    "zh": {
        "chapter_title": "本书导览",
        "chapter_summary": "自动生成的概念图与章节索引，作为本书的入口。",
        "objectives": [
            "了解整本书的章节脉络",
            "掌握各章之间的概念依赖关系",
            "选择最合适的阅读顺序",
        ],
        "intro_title": "如何阅读这本书",
        "intro_body": (
            "下方的概念图展示了本书 {concepts} 个核心概念以及它们之间的依赖关系；"
            "再下方是 {chapters} 个章节的入口。"
            "你可以按从上到下的顺序阅读，也可以根据自己的兴趣或先验知识选择切入点。"
        ),
        "untitled_book": "本书",
        "concept_map_title": "概念图",
        "chapter_index_title": "章节索引",
        "chapter_index_heading": "## 章节索引",
    },
    "fr": {
        "chapter_title": "Comment lire ce livre",
        "chapter_summary": (
            "Vue d’ensemble générée automatiquement à partir du graphe de concepts "
            "et de l’index des chapitres."
        ),
        "objectives": [
            "Voir la carte complète des chapitres en un coup d’œil",
            "Comprendre les dépendances entre les concepts",
            "Choisir le parcours de lecture adapté à vos objectifs",
        ],
        "intro_title": "Comment lire ce livre",
        "intro_body": (
            "Le schéma ci-dessous cartographie les {concepts} concepts clés de ce livre "
            "et leurs dépendances. L’index des chapitres qui suit liste les {chapters} "
            "chapitres — lisez de haut en bas pour suivre le parcours recommandé, ou "
            "allez directement au sujet qui vous intéresse le plus."
        ),
        "untitled_book": "Ce livre",
        "concept_map_title": "Carte des concepts",
        "chapter_index_title": "Index des chapitres",
        "chapter_index_heading": "## Index des chapitres",
    },
    "ja": {
        "chapter_title": "この本の読み方",
        "chapter_summary": "本書の概念グラフと章の索引から自動生成された概要です。",
        "objectives": [
            "章の全体像をひと目で把握する",
            "概念同士の依存関係を理解する",
            "目的に合った読み進め方を選ぶ",
        ],
        "intro_title": "この本の読み方",
        "intro_body": (
            "下の図は、本書の {concepts} 個の主要な概念とそれらの依存関係を示しています。"
            "続く章の索引には {chapters} 個の章がすべて並んでいます。"
            "上から順に読めばおすすめの順序で学べますし、"
            "興味のある章から直接読み始めることもできます。"
        ),
        "untitled_book": "本書",
        "concept_map_title": "概念マップ",
        "chapter_index_title": "章の索引",
        "chapter_index_heading": "## 章の索引",
    },
    "ko": {
        "chapter_title": "이 책을 읽는 방법",
        "chapter_summary": "이 책의 개념 그래프와 챕터 목차로 자동 생성된 개요입니다.",
        "objectives": [
            "전체 챕터 구성을 한눈에 파악하기",
            "개념 간의 의존 관계 이해하기",
            "목표에 맞는 읽기 순서 선택하기",
        ],
        "intro_title": "이 책을 읽는 방법",
        "intro_body": (
            "아래 다이어그램은 이 책의 핵심 개념 {concepts}개와 그 사이의 의존 관계를 보여 줍니다. "
            "이어지는 챕터 목차에는 {chapters}개 챕터가 모두 나열되어 있습니다. "
            "위에서 아래로 읽으면 권장 순서를 따르게 되고, "
            "가장 궁금한 내용으로 바로 이동할 수도 있습니다."
        ),
        "untitled_book": "이 책",
        "concept_map_title": "개념 맵",
        "chapter_index_title": "챕터 목차",
        "chapter_index_heading": "## 챕터 목차",
    },
    "vi": {
        "chapter_title": "Cách đọc cuốn sách này",
        "chapter_summary": "Tổng quan được tạo tự động về sơ đồ khái niệm và mục lục các chương của sách.",
        "objectives": [
            "Nắm toàn bộ bản đồ các chương chỉ trong một cái nhìn",
            "Hiểu các khái niệm phụ thuộc lẫn nhau như thế nào",
            "Chọn lộ trình đọc phù hợp với mục tiêu của bạn",
        ],
        "intro_title": "Cách đọc cuốn sách này",
        "intro_body": (
            "Sơ đồ bên dưới thể hiện {concepts} khái niệm cốt lõi của cuốn sách và mối quan hệ phụ "
            "thuộc giữa chúng. Mục lục chương ngay sau đó liệt kê cả {chapters} chương — bạn có thể "
            "đọc từ trên xuống dưới theo lộ trình được đề xuất, hoặc chuyển thẳng đến phần mình "
            "quan tâm nhất."
        ),
        "untitled_book": "Cuốn sách này",
        "concept_map_title": "Sơ đồ khái niệm",
        "chapter_index_title": "Mục lục chương",
        "chapter_index_heading": "## Mục lục chương",
    },
    "tr": {
        "chapter_title": "Bu kitap nasıl okunur",
        "chapter_summary": "Kitabın kavram grafiğinin ve bölüm dizininin otomatik oluşturulmuş özeti.",
        "objectives": [
            "Tüm bölüm haritasını tek bakışta görün",
            "Kavramların birbirine nasıl bağlı olduğunu anlayın",
            "Hedeflerinize uygun okuma yolunu seçin",
        ],
        "intro_title": "Bu kitap nasıl okunur",
        "intro_body": (
            "Aşağıdaki diyagram, bu kitaptaki {concepts} temel kavramı ve bunların birbirine nasıl "
            "bağlı olduğunu gösterir. Ardından gelen bölüm dizini {chapters} bölümün tamamını "
            "listeler; önerilen yol için yukarıdan aşağıya okuyabilir ya da en çok merak ettiğiniz "
            "bölüme doğrudan geçebilirsiniz."
        ),
        "untitled_book": "Bu kitap",
        "concept_map_title": "Kavram haritası",
        "chapter_index_title": "Bölüm dizini",
        "chapter_index_heading": "## Bölüm dizini",
    },
    "pt": {
        "chapter_title": "Como ler este livro",
        "chapter_summary": (
            "Visão geral gerada automaticamente do grafo de conceitos e do índice de capítulos do "
            "livro."
        ),
        "objectives": [
            "Ter uma visão rápida do mapa completo de capítulos",
            "Entender como os conceitos dependem uns dos outros",
            "Escolher o caminho de leitura que melhor atende aos seus objetivos",
        ],
        "intro_title": "Como ler este livro",
        "intro_body": (
            "O diagrama abaixo mapeia os {concepts} conceitos principais deste livro e como eles "
            "dependem uns dos outros. O índice a seguir lista todos os {chapters} capítulos — leia "
            "de cima para baixo para seguir a ordem recomendada, ou vá direto ao capítulo que mais "
            "despertar sua curiosidade."
        ),
        "untitled_book": "Este livro",
        "concept_map_title": "Mapa de conceitos",
        "chapter_index_title": "Índice de capítulos",
        "chapter_index_heading": "## Índice de capítulos",
    },
    "it": {
        "chapter_title": "Come leggere questo libro",
        "chapter_summary": (
            "Panoramica generata automaticamente della mappa concettuale e dell'indice dei capitoli "
            "del libro."
        ),
        "objectives": [
            "Vedere a colpo d'occhio la mappa completa dei capitoli",
            "Comprendere le relazioni tra i concetti chiave",
            "Scegliere il percorso di lettura più adatto ai tuoi obiettivi",
        ],
        "intro_title": "Come leggere questo libro",
        "intro_body": (
            "Il diagramma seguente mostra i {concepts} concetti fondamentali di questo libro e le "
            "loro interconnessioni. L'indice successivo elenca tutti i {chapters} capitoli; puoi "
            "leggerli in ordine sequenziale oppure passare direttamente al capitolo di tuo "
            "interesse."
        ),
        "untitled_book": "Questo libro",
        "concept_map_title": "Mappa concettuale",
        "chapter_index_title": "Indice dei capitoli",
        "chapter_index_heading": "## Indice dei capitoli",
    },
    "hu": {
        "chapter_title": "Így olvassa ezt a könyvet",
        "chapter_summary": "A könyv fogalomgráfjából és fejezetjegyzékéből automatikusan készült áttekintés.",
        "objectives": [
            "Egy pillantással lássa a teljes fejezettérképet",
            "Értse meg, hogyan függenek össze a fogalmak",
            "Válassza ki a céljaihoz illő olvasási útvonalat",
        ],
        "intro_title": "Így olvassa ezt a könyvet",
        "intro_body": (
            "Az alábbi ábra a könyv {concepts} fő fogalmát és azok egymásra épülését mutatja. A "
            "következő fejezetjegyzék mind a {chapters} fejezetet felsorolja — haladjon fentről "
            "lefelé az ajánlott sorrendhez, vagy ugorjon egyenesen arra, ami a leginkább érdekli."
        ),
        "untitled_book": "Ez a könyv",
        "concept_map_title": "Fogalomtérkép",
        "chapter_index_title": "Fejezetjegyzék",
        "chapter_index_heading": "## Fejezetjegyzék",
    },
}


def overview_copy(language: str | None) -> dict[str, Any]:
    """Copy for *language*, falling back to English (then to the base tag).

    ``zh-tw`` finds no exact entry, tries ``zh``, and only then falls back —
    matching how the prompt layer resolves language tags.
    """
    code = (language or "en").strip().lower()
    return _COPY.get(code) or _COPY.get(code.split("-", 1)[0]) or _COPY["en"]


def missing_languages(supported: list[str]) -> list[str]:
    """Which of *supported* still render the Overview page in English.

    Not a failure — the page stays readable — but a book generated in a
    language listed here has an English front page in front of prose that is
    not English. Used by tests to keep the gap honest and countable.
    """
    return [
        code
        for code in supported
        if code.strip().lower() not in _COPY and code.strip().lower().split("-", 1)[0] not in _COPY
    ]


__all__ = ["REQUIRED_KEYS", "missing_languages", "overview_copy"]
