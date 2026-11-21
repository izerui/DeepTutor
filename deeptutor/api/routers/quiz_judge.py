"""AI judge WebSocket — grades a learner's quiz answer.

Mounted on its own (without router-level HTTP auth dependencies) because
WebSocket upgrades cannot use FastAPI's HTTP dependency injection, so we
rely on ``ws_require_auth`` inside the handler — mirroring the pattern
used by ``unified_ws``.
"""

from __future__ import annotations

import asyncio
import base64 as _b64
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from deeptutor.services.config import PROJECT_ROOT, load_config_with_main
from deeptutor.services.llm import stream as llm_stream
from deeptutor.services.settings.interface_settings import get_response_language
from deeptutor.utils.error_utils import format_exception_message

logger = logging.getLogger(__name__)
_config = load_config_with_main("main.yaml", PROJECT_ROOT)

router = APIRouter()


_JUDGE_SYSTEM_PROMPTS = {
    "zh": (
        "你是一名严谨且鼓励学习者的助教，正在批改一道测验题。"
        "请基于题目、参考答案与解析，对学习者的作答给出针对性的判定与反馈。\n\n"
        "回答要求：\n"
        "- 先用一行明确结论：✅ 正确 / ⚠️ 部分正确 / ❌ 不正确，并简短点明关键判定依据。\n"
        "- 然后分条列出：哪里做对了、哪里出错或缺漏、应该如何改正。\n"
        "- 若题目本身有多种合理答案，请承认学习者的合理之处。\n"
        "- 直接以学习者的作答为对象，不要泛泛而谈。\n"
        "- 全程使用中文。"
    ),
    "en": (
        "You are a rigorous yet encouraging teaching assistant grading a learner's quiz answer. "
        "Use the question, reference answer, and explanation to deliver a targeted assessment.\n\n"
        "Requirements:\n"
        "- Open with one line that states the verdict: ✅ Correct / ⚠️ Partially correct / ❌ Incorrect, "
        "and the key reason.\n"
        "- Then list: what the learner got right, what is wrong or missing, and how to fix it.\n"
        "- If multiple reasonable answers exist, acknowledge what the learner did well.\n"
        "- Speak directly to the learner's submission — do not give a generic lecture.\n"
        "- Reply in English."
    ),
    "de": (
        "Du bist eine gründliche, aber ermutigende Lehrkraft und bewertest die Antwort "
        "einer lernenden Person auf eine Quizfrage. Stütze dich auf Frage, Musterlösung "
        "und Erklärung.\n\n"
        "Anforderungen:\n"
        "- Beginne mit einer Zeile, die das Ergebnis nennt: ✅ Richtig / ⚠️ Teilweise richtig "
        "/ ❌ Falsch, und kurz den entscheidenden Grund.\n"
        "- Liste danach auf: was richtig ist, was falsch ist oder fehlt und wie es sich verbessern lässt.\n"
        "- Wenn mehrere sinnvolle Antworten möglich sind, erkenne an, was gut gelöst ist.\n"
        "- Sprich direkt über diese Antwort, ohne allgemeinen Vortrag.\n"
        "- Antworte auf Deutsch und duze die lernende Person."
    ),
    "uk": (
        "Ти вимогливий, але доброзичливий асистент учителя, який перевіряє відповідь учня "
        "на тестове завдання. Спирайся на умову, еталонну відповідь і пояснення.\n\n"
        "Вимоги до відповіді:\n"
        "- Почни одним рядком із вердиктом: ✅ Правильно / ⚠️ Частково правильно / ❌ Неправильно "
        "і коротко назви головну підставу.\n"
        "- Далі по пунктах: що учень зробив правильно, де помилка або чого бракує, як це виправити.\n"
        "- Якщо можливі кілька слушних відповідей, визнай те, що учень зробив добре.\n"
        "- Звертайся саме до цієї відповіді, без загальних лекцій.\n"
        "- Відповідай українською."
    ),
    "fr": (
        "Tu es un assistant pédagogique rigoureux et encourageant qui corrige la réponse "
        "d’un apprenant à un quiz. Appuie-toi sur la question, la réponse de référence et "
        "l’explication pour fournir une évaluation ciblée.\n\n"
        "Exigences :\n"
        "- Commence par une ligne indiquant le verdict : ✅ Correct / ⚠️ Partiellement correct / "
        "❌ Incorrect, avec la raison principale.\n"
        "- Liste ensuite : ce que l’apprenant a juste, ce qui est faux ou manquant, et comment corriger.\n"
        "- Si plusieurs réponses raisonnables existent, reconnais ce que l’apprenant a bien fait.\n"
        "- Adresse-toi directement à la réponse de l’apprenant, sans discours générique.\n"
        "- Réponds en français et tutoie l’apprenant."
    ),
    "ja": (
        "あなたは厳格でありながら学習者を励ます教育アシスタントで、学習者のクイズの解答を採点しています。"
        "問題、模範解答、解説をもとに、的を絞った評価を行ってください。\n\n"
        "回答の要件:\n"
        "- 最初の 1 行で判定を明示してください: ✅ 正解 / ⚠️ 部分的に正解 / ❌ 不正解。"
        "あわせて判定の主な理由を簡潔に述べてください。\n"
        "- 続けて、正しくできている点、誤りや不足している点、その直し方を箇条書きで示してください。\n"
        "- 妥当な答えが複数ある場合は、学習者のよくできている点を認めてください。\n"
        "- 一般論の講義ではなく、学習者の解答そのものについて述べてください。\n"
        "- 日本語で、です・ます調で回答してください。"
    ),
    "ko": (
        "당신은 엄격하면서도 학습자를 격려하는 조교로서 학습자의 퀴즈 답안을 채점하고 있습니다. "
        "문제, 모범 답안, 해설을 바탕으로 학습자에게 맞춘 평가를 제공하세요.\n\n"
        "응답 요구 사항:\n"
        "- 첫 줄에서 판정을 밝히세요: ✅ 정답 / ⚠️ 부분 정답 / ❌ 오답, "
        "그리고 핵심 판단 근거를 간단히 덧붙이세요.\n"
        "- 이어서 잘한 점, 틀리거나 빠진 점, 고치는 방법을 항목별로 나열하세요.\n"
        "- 합리적인 답이 여러 개 있을 수 있다면 학습자가 잘한 부분을 인정하세요.\n"
        "- 일반적인 강의가 아니라 학습자의 답안 자체에 대해 직접 이야기하세요.\n"
        "- 한국어로 답하세요."
    ),
    "vi": (
        "Bạn là trợ giảng nghiêm túc nhưng luôn khích lệ, đang chấm câu trả lời của người học "
        "cho một câu hỏi kiểm tra. Hãy dựa vào câu hỏi, đáp án tham khảo và phần giải thích "
        "để đưa ra nhận xét sát với bài làm.\n\n"
        "Yêu cầu:\n"
        "- Mở đầu bằng một dòng nêu kết luận: ✅ Đúng / ⚠️ Đúng một phần / ❌ Sai, "
        "kèm lý do chính.\n"
        "- Sau đó nêu: người học đã làm đúng gì, chỗ nào sai hoặc còn thiếu, và cách sửa.\n"
        "- Nếu có nhiều đáp án hợp lý, hãy ghi nhận những gì người học đã làm tốt.\n"
        "- Nhận xét trực tiếp vào bài làm này, không giảng lý thuyết chung chung.\n"
        "- Trả lời bằng tiếng Việt."
    ),
    "tr": (
        "Titiz ama cesaretlendirici bir öğretim asistanısınız ve bir öğrencinin test sorusuna "
        "verdiği yanıtı değerlendiriyorsunuz. Soruyu, örnek yanıtı ve açıklamayı temel alarak "
        "yanıta özel bir değerlendirme yapın.\n\n"
        "Yanıt gereksinimleri:\n"
        "- Sonucu belirten tek bir satırla başlayın: ✅ Doğru / ⚠️ Kısmen doğru / ❌ Yanlış "
        "ve temel gerekçeyi kısaca yazın.\n"
        "- Ardından madde madde yazın: öğrencinin neyi doğru yaptığı, neyin yanlış ya da eksik "
        "olduğu ve nasıl düzeltileceği.\n"
        "- Birden fazla makul yanıt mümkünse öğrencinin iyi yaptığı yönleri belirtin.\n"
        "- Doğrudan bu yanıta odaklanın, genel bir ders vermeyin.\n"
        "- Türkçe yanıt verin ve öğrenciye \"siz\" diye hitap edin."
    ),
    "pt": (
        "Você é um assistente de ensino rigoroso e encorajador que avalia a resposta de um "
        "estudante a uma questão de teste. Use a questão, a resposta de referência e a "
        "explicação para fazer uma avaliação direcionada.\n\n"
        "Requisitos:\n"
        "- Comece com uma linha informando o veredito: ✅ Correto / ⚠️ Parcialmente correto / "
        "❌ Incorreto, e o motivo principal.\n"
        "- Em seguida, liste: o que o estudante acertou, o que está errado ou faltando e como "
        "corrigir.\n"
        "- Se houver várias respostas válidas, reconheça os acertos do estudante.\n"
        "- Comente diretamente a resposta enviada — não dê uma explicação genérica.\n"
        "- Responda em português do Brasil e trate o estudante por \"você\"."
    ),
    "it": (
        "Sei un assistente didattico rigoroso ma incoraggiante che corregge la risposta "
        "di uno studente a una domanda di un quiz. Basati sulla domanda, sulla risposta di "
        "riferimento e sulla spiegazione per fornire una valutazione mirata.\n\n"
        "Requisiti:\n"
        "- Inizia con una riga che esprime il verdetto: ✅ Corretto / ⚠️ Parzialmente corretto / "
        "❌ Non corretto, con la motivazione principale.\n"
        "- Quindi elenca: cosa lo studente ha fatto bene, cosa è errato o mancante e come "
        "correggerlo.\n"
        "- Se esistono più risposte ragionevoli, riconosci i punti validi dello studente.\n"
        "- Rivolgiti direttamente alla risposta dello studente, senza lezioni generiche.\n"
        "- Rispondi in italiano e dai del tu allo studente."
    ),
    "hu": (
        "Ön szigorú, de bátorító tanársegéd, aki egy tanuló kvízkérdésre adott válaszát "
        "értékeli. A kérdés, a mintaválasz és a magyarázat alapján adjon célzott értékelést.\n\n"
        "Követelmények:\n"
        "- Egy sorral kezdje az ítélettel: ✅ Helyes / ⚠️ Részben helyes / ❌ Helytelen, "
        "és a döntő indokkal.\n"
        "- Ezután sorolja fel: mit tudott jól a tanuló, mi hibás vagy hiányzik, és hogyan "
        "javítható.\n"
        "- Ha több elfogadható válasz is létezik, ismerje el, amit a tanuló jól tett.\n"
        "- Közvetlenül a tanuló megoldására reagáljon — ne tartson általános előadást.\n"
        "- Magyarul válaszoljon."
    ),
}

# The set of languages the judge can speak IS the set of system prompts it has.
# Deriving it here means adding a language is one edit (add a prompt), not three
# — the previous ("zh", "en") literals were repeated in the whitelist, the
# fallback and the frontend, and a Ukrainian UI silently got English feedback.
SUPPORTED_JUDGE_LANGUAGES: frozenset[str] = frozenset(_JUDGE_SYSTEM_PROMPTS)


def _build_judge_user_prompt(
    *,
    language: str,
    question: str,
    question_type: str,
    options: dict | None,
    correct_answer: str,
    explanation: str,
    user_answer: str,
    has_image: bool,
    image_count: int = 0,
) -> str:
    options_block = ""
    if options:
        try:
            options_block = "\n".join(f"  {k}. {v}" for k, v in options.items())
        except Exception:
            options_block = ""
    if language == "zh":
        parts = [
            f"题目类型：{question_type or 'unknown'}",
            f"题干：\n{question}",
        ]
        if options_block:
            parts.append(f"选项：\n{options_block}")
        if correct_answer:
            parts.append(f"参考答案：\n{correct_answer}")
        if explanation:
            parts.append(f"参考解析：\n{explanation}")
        parts.append(
            "学习者作答：\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "（仅提交了图片，无文字作答）"
            )
        )
        if has_image:
            count_text = (
                f"学习者另附了 {image_count} 张图片作为作答内容"
                if image_count > 1
                else "学习者另附了一张图片作为作答内容"
            )
            parts.append(f"{count_text}，请结合图片中的文字/公式/草图一并判定。")
        parts.append("请针对该学习者的具体作答给出 AI 评判。")
    elif language == "fr":
        parts = [
            f"Type de question : {question_type or 'inconnu'}",
            f"Question :\n{question}",
        ]
        if options_block:
            parts.append(f"Options :\n{options_block}")
        if correct_answer:
            parts.append(f"Réponse de référence :\n{correct_answer}")
        if explanation:
            parts.append(f"Explication de référence :\n{explanation}")
        parts.append(
            "Réponse de l’apprenant :\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(seule une image a été soumise, sans réponse écrite)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"L’apprenant a joint {image_count} images à sa réponse. "
                    "Lis leur texte, leurs formules et leurs croquis, et prends-les en compte "
                    "dans l’évaluation."
                )
            else:
                parts.append(
                    "L’apprenant a joint une image à sa réponse. Lis son texte, ses formules "
                    "et ses croquis, et prends-la en compte dans l’évaluation."
                )
        parts.append("Produis une évaluation qui porte sur la réponse précise de cet apprenant.")
    elif language == "ja":
        parts = [
            f"問題の種類: {question_type or '不明'}",
            f"問題文:\n{question}",
        ]
        if options_block:
            parts.append(f"選択肢:\n{options_block}")
        if correct_answer:
            parts.append(f"模範解答:\n{correct_answer}")
        if explanation:
            parts.append(f"解説:\n{explanation}")
        parts.append(
            "学習者の解答:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "（画像のみが提出され、文字での解答はありません）"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"学習者は解答として {image_count} 枚の画像を添付しています。"
                    "画像内の文字・数式・スケッチを読み取り、判定に反映してください。"
                )
            else:
                parts.append(
                    "学習者は解答として 1 枚の画像を添付しています。"
                    "画像内の文字・数式・スケッチを読み取り、判定に反映してください。"
                )
        parts.append("この学習者の具体的な解答に即した AI 評価を行ってください。")
    elif language == "ko":
        parts = [
            f"문제 유형: {question_type or '알 수 없음'}",
            f"문제:\n{question}",
        ]
        if options_block:
            parts.append(f"선택지:\n{options_block}")
        if correct_answer:
            parts.append(f"모범 답안:\n{correct_answer}")
        if explanation:
            parts.append(f"해설:\n{explanation}")
        parts.append(
            "학습자 답안:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(이미지만 제출되었으며 입력한 답안은 없습니다)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"학습자가 답안으로 이미지 {image_count}장을 첨부했습니다. "
                    "이미지 속 글자, 수식, 스케치를 읽고 판정에 반영하세요."
                )
            else:
                parts.append(
                    "학습자가 답안으로 이미지 한 장을 첨부했습니다. "
                    "이미지 속 글자, 수식, 스케치를 읽고 판정에 반영하세요."
                )
        parts.append("이 학습자의 구체적인 답안에 맞춘 AI 평가를 작성하세요.")
    elif language == "vi":
        parts = [
            f"Dạng câu hỏi: {question_type or 'không rõ'}",
            f"Câu hỏi:\n{question}",
        ]
        if options_block:
            parts.append(f"Các lựa chọn:\n{options_block}")
        if correct_answer:
            parts.append(f"Đáp án tham khảo:\n{correct_answer}")
        if explanation:
            parts.append(f"Giải thích tham khảo:\n{explanation}")
        parts.append(
            "Bài làm của người học:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(chỉ nộp hình ảnh, không có câu trả lời dạng văn bản)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"Người học đã đính kèm {image_count} hình ảnh trong bài làm. "
                    "Hãy đọc chữ, công thức và hình vẽ trong ảnh rồi đưa vào đánh giá."
                )
            else:
                parts.append(
                    "Người học đã đính kèm một hình ảnh trong bài làm. "
                    "Hãy đọc chữ, công thức và hình vẽ trong ảnh rồi đưa vào đánh giá."
                )
        parts.append("Hãy đưa ra đánh giá AI dựa trên chính bài làm cụ thể của người học này.")
    elif language == "tr":
        parts = [
            f"Soru türü: {question_type or 'bilinmiyor'}",
            f"Soru:\n{question}",
        ]
        if options_block:
            parts.append(f"Seçenekler:\n{options_block}")
        if correct_answer:
            parts.append(f"Örnek yanıt:\n{correct_answer}")
        if explanation:
            parts.append(f"Örnek açıklama:\n{explanation}")
        parts.append(
            "Öğrencinin yanıtı:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(yalnızca görsel gönderildi, yazılı yanıt yok)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"Öğrenci yanıtına {image_count} görsel ekledi. "
                    "Görsellerdeki metni, formülleri ve çizimleri okuyun ve değerlendirmeye katın."
                )
            else:
                parts.append(
                    "Öğrenci yanıtına bir görsel ekledi. "
                    "Görseldeki metni, formülleri ve çizimleri okuyun ve değerlendirmeye katın."
                )
        parts.append("Bu öğrencinin verdiği yanıta özel bir AI değerlendirmesi yazın.")
    elif language == "pt":
        parts = [
            f"Tipo de questão: {question_type or 'desconhecido'}",
            f"Questão:\n{question}",
        ]
        if options_block:
            parts.append(f"Alternativas:\n{options_block}")
        if correct_answer:
            parts.append(f"Resposta de referência:\n{correct_answer}")
        if explanation:
            parts.append(f"Explicação de referência:\n{explanation}")
        parts.append(
            "Resposta do estudante:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(apenas uma imagem foi enviada, sem resposta digitada)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"O estudante anexou {image_count} imagens à resposta. "
                    "Leia o texto, as fórmulas e os esboços delas e leve-os em conta na avaliação."
                )
            else:
                parts.append(
                    "O estudante anexou uma imagem à resposta. "
                    "Leia o texto, as fórmulas e os esboços dela e leve-os em conta na avaliação."
                )
        parts.append("Faça uma avaliação de IA voltada para a resposta específica deste estudante.")
    elif language == "it":
        parts = [
            f"Tipo di domanda: {question_type or 'sconosciuto'}",
            f"Domanda:\n{question}",
        ]
        if options_block:
            parts.append(f"Opzioni:\n{options_block}")
        if correct_answer:
            parts.append(f"Risposta di riferimento:\n{correct_answer}")
        if explanation:
            parts.append(f"Spiegazione di riferimento:\n{explanation}")
        parts.append(
            "Risposta dello studente:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(è stata inviata solo un’immagine, senza risposta scritta)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"Lo studente ha allegato {image_count} immagini alla risposta. "
                    "Leggi testo, formule e schizzi presenti nelle immagini e tienine conto nella valutazione."
                )
            else:
                parts.append(
                    "Lo studente ha allegato un’immagine alla risposta. "
                    "Leggi testo, formule e schizzi presenti nell’immagine e tienine conto nella valutazione."
                )
        parts.append("Fornisci una valutazione IA centrata sulla risposta specifica di questo studente.")
    elif language == "hu":
        parts = [
            f"Kérdéstípus: {question_type or 'ismeretlen'}",
            f"Kérdés:\n{question}",
        ]
        if options_block:
            parts.append(f"Válaszlehetőségek:\n{options_block}")
        if correct_answer:
            parts.append(f"Mintaválasz:\n{correct_answer}")
        if explanation:
            parts.append(f"Magyarázat:\n{explanation}")
        parts.append(
            "A tanuló válasza:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(csak kép érkezett, gépelt válasz nincs)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"A tanuló {image_count} képet csatolt a válaszához. "
                    "Olvassa el a képeken lévő szöveget, képleteket és vázlatokat, és vegye figyelembe őket az értékelésben."
                )
            else:
                parts.append(
                    "A tanuló egy képet csatolt a válaszához. "
                    "Olvassa el a képen lévő szöveget, képleteket és vázlatokat, és vegye figyelembe az értékelésben."
                )
        parts.append("Készítsen AI-értékelést, amely ennek a tanulónak a konkrét válaszára reagál.")
    else:
        parts = [
            f"Question type: {question_type or 'unknown'}",
            f"Question:\n{question}",
        ]
        if options_block:
            parts.append(f"Options:\n{options_block}")
        if correct_answer:
            parts.append(f"Reference answer:\n{correct_answer}")
        if explanation:
            parts.append(f"Reference explanation:\n{explanation}")
        parts.append(
            "Learner's answer:\n"
            + (
                user_answer.strip()
                if user_answer and user_answer.strip()
                else "(only an image was submitted, no typed answer)"
            )
        )
        if has_image:
            if image_count > 1:
                parts.append(
                    f"The learner attached {image_count} images as part of the answer. "
                    "Read their text/formulas/sketches and factor them into the judgment."
                )
            else:
                parts.append(
                    "The learner attached an image as part of the answer. "
                    "Read its text/formulas/sketches and factor it into the judgment."
                )
        parts.append("Produce an AI judgment that addresses this learner's specific answer.")
    return "\n\n".join(parts)


async def _build_multimodal_user_content(
    *,
    text: str,
    image_records: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Compose an OpenAI-style content-parts array with text + image blocks.

    For ``url``-only records we resolve local AttachmentStore paths to
    base64 here (most providers can fetch external URLs themselves, but
    locally-hosted ``/files/attachments/...`` is only reachable from the
    browser). Falls back to passing the URL through when resolution is
    not possible.
    """
    from urllib.parse import unquote, urlparse

    from deeptutor.services.storage import get_attachment_store

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    attachment_store = get_attachment_store()
    resolve = getattr(attachment_store, "resolve_path", None)

    for record in image_records:
        b64 = record.get("base64") or ""
        url = record.get("url") or ""
        filename = record.get("filename") or "answer.png"
        mime_type = record.get("mime_type") or _guess_image_mime(filename)

        if not b64 and url and resolve is not None:
            try:
                parsed = urlparse(url)
                parts = (parsed.path or url).strip("/").split("/")
                # Current store URLs use files/attachments; keep older saved
                # api/attachments references readable as well.
                if len(parts) >= 5 and parts[:2] in (
                    ["files", "attachments"],
                    ["api", "attachments"],
                ):
                    sid = unquote(parts[2])
                    aid = unquote(parts[3])
                    name = unquote("/".join(parts[4:]))
                    target = resolve(session_id=sid, attachment_id=aid, filename=name)
                    if target is not None and target.exists():
                        b64 = _b64.b64encode(target.read_bytes()).decode("ascii")
            except Exception as exc:
                logger.debug("Could not resolve %s to bytes: %s", url, exc)

        if b64:
            data_url = f"data:{mime_type};base64,{b64}"
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        elif url:
            content.append({"type": "image_url", "image_url": {"url": url}})

    return content


def _data_url_mime(value: str) -> str | None:
    """Return the media type declared in a ``data:`` URL, or ``None``.

    Args:
        value: A string beginning with ``data:``, e.g.
            ``data:image/jpeg;base64,...``.

    Returns:
        The declared media type (``image/jpeg``), or ``None`` when the URL
        declares no media type.
    """
    header = value[len("data:") : value.find(",")] if "," in value else ""
    mime = header.split(";", 1)[0].strip()
    return mime or None


def _guess_image_mime(filename: str | None) -> str:
    if not filename:
        return "image/png"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/png")


@router.websocket("/questions/judge")
async def websocket_quiz_judge(websocket: WebSocket):
    """Stream an AI judgment for a single quiz answer.

    Auth is enforced via ``ws_require_auth`` rather than a router-level
    HTTP dependency — see module docstring.

    Client → Server (initial JSON):
        {
            "question": str,
            "question_type": str,
            "options": dict | null,
            "correct_answer": str,
            "explanation": str,
            "user_answer": str,
            # New: list of image entries. Each entry has either ``base64``
            # (no ``data:`` prefix) or ``url`` (already hosted via the
            # AttachmentStore). ``user_answer_image`` (single, base64) is
            # still accepted for backward compatibility.
            "user_answer_images": [
                {"base64": str, "url": str, "filename": str, "mime_type": str},
                ...
            ] | null,
            "user_answer_image": str | null,  # legacy single-image form
            "image_filename": str | null,     # legacy filename for the above
            "language": "zh" | "en" | "de" | "uk" | "fr" | "ja" | "ko" | "vi" | "tr" | "pt" | "it" | "hu",
        }

    Server → Client (streaming):
        {"type": "started"}
        {"type": "text", "content": "..."}        # zero or more
        {"type": "done"}
        {"type": "error", "content": "..."}
    """
    from deeptutor.api.routers.auth import ws_auth_failed, ws_require_auth
    from deeptutor.multi_user.context import reset_current_user

    user_token = await ws_require_auth(websocket)
    if user_token is ws_auth_failed:
        return

    await websocket.accept()

    async def safe_send(payload: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return False

    try:
        data = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await safe_send({"type": "error", "content": f"Invalid request: {exc}"})
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                reset_current_user(user_token)
            except Exception:
                pass
        return

    question_text = (data.get("question") or "").strip()
    if not question_text:
        await safe_send({"type": "error", "content": "Question is required"})
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                reset_current_user(user_token)
            except Exception:
                pass
        return

    requested_language = (data.get("language") or "").strip().lower()
    if requested_language not in SUPPORTED_JUDGE_LANGUAGES:
        requested_language = get_response_language(
            default=_config.get("system", {}).get("language", "en")
        )
        if requested_language not in SUPPORTED_JUDGE_LANGUAGES:
            requested_language = "en"

    user_answer = data.get("user_answer") or ""

    # Resolve the image set. New clients send ``user_answer_images`` (list);
    # legacy clients send the single ``user_answer_image`` + ``image_filename``
    # pair. Build a uniform list of ``{base64, url, filename, mime_type}`` so
    # the downstream multimodal-message builder doesn't care which form
    # arrived.
    raw_images = data.get("user_answer_images")
    image_records: list[dict[str, str]] = []
    if isinstance(raw_images, list):
        for entry in raw_images:
            if not isinstance(entry, dict):
                continue
            b64 = entry.get("base64") or ""
            url = entry.get("url") or ""
            declared_mime: str | None = None
            if isinstance(b64, str) and b64.startswith("data:"):
                declared_mime = _data_url_mime(b64)
                try:
                    b64 = b64.split(",", 1)[1]
                except IndexError:
                    b64 = ""
            if not b64 and not url:
                continue
            filename = entry.get("filename") or "answer.png"
            mime_type = entry.get("mime_type") or declared_mime or _guess_image_mime(filename)
            image_records.append(
                {
                    "base64": b64,
                    "url": url,
                    "filename": filename,
                    "mime_type": mime_type,
                }
            )
    else:
        legacy_b64 = data.get("user_answer_image") or ""
        legacy_mime: str | None = None
        if isinstance(legacy_b64, str) and legacy_b64.startswith("data:"):
            legacy_mime = _data_url_mime(legacy_b64)
            try:
                legacy_b64 = legacy_b64.split(",", 1)[1]
            except IndexError:
                legacy_b64 = ""
        if legacy_b64:
            legacy_filename = data.get("image_filename") or "answer.png"
            image_records.append(
                {
                    "base64": legacy_b64,
                    "url": "",
                    "filename": legacy_filename,
                    "mime_type": legacy_mime or _guess_image_mime(legacy_filename),
                }
            )

    has_image = bool(image_records)

    options_value = data.get("options") if isinstance(data.get("options"), dict) else None
    system_prompt = _JUDGE_SYSTEM_PROMPTS.get(requested_language, _JUDGE_SYSTEM_PROMPTS["en"])
    user_prompt = _build_judge_user_prompt(
        language=requested_language,
        question=question_text,
        question_type=data.get("question_type") or "",
        options=options_value,
        correct_answer=data.get("correct_answer") or "",
        explanation=data.get("explanation") or "",
        user_answer=user_answer,
        has_image=has_image,
        image_count=len(image_records),
    )

    if not (user_answer.strip() or has_image):
        await safe_send(
            {
                "type": "error",
                "content": ("No answer to judge — submit a typed answer or attach an image."),
            }
        )
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                reset_current_user(user_token)
            except Exception:
                pass
        return

    await safe_send({"type": "started"})

    # Build a multimodal user message when ≥1 image was attached. We pass
    # the full ``messages`` array to ``factory.stream`` so it forwards the
    # content-parts unchanged (the single-image ``image_data`` kwarg only
    # supports one image).
    stream_kwargs: dict[str, Any] = {}
    if has_image:
        from deeptutor.services.llm import config as _llm_config_mod
        from deeptutor.services.llm.capabilities import supports_vision

        llm_cfg = _llm_config_mod.get_llm_config()
        binding = getattr(llm_cfg, "binding", "openai") or "openai"
        model = getattr(llm_cfg, "model", "") or ""
        if supports_vision(binding, model):
            user_content = await _build_multimodal_user_content(
                text=user_prompt,
                image_records=image_records,
            )
            stream_kwargs["messages"] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
        else:
            # Vision-incapable model — fall back to text-only judge so the
            # learner still gets feedback on their typed answer.
            logger.info(
                "Judge: %s/%s does not support vision; dropping %d image(s)",
                binding,
                model,
                len(image_records),
            )

    try:
        async with asyncio.timeout(2 * 60):
            async for chunk in llm_stream(
                prompt=user_prompt,
                system_prompt=system_prompt,
                **stream_kwargs,
            ):
                if not chunk:
                    continue
                if not await safe_send({"type": "text", "content": chunk}):
                    break
        await safe_send({"type": "done"})
    except TimeoutError:
        await safe_send({"type": "error", "content": "AI judge timed out. Please try again."})
    except WebSocketDisconnect:
        logger.debug("AI judge client disconnected mid-stream")
    except Exception as exc:
        logger.exception("AI judge stream failed")
        await safe_send({"type": "error", "content": format_exception_message(exc)})
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        if user_token is not None:
            try:
                reset_current_user(user_token)
            except Exception:
                pass
