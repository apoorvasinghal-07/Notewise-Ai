import io
import os
import re
import json
import time
import random
import logging
from pathlib import Path

from pypdf import PdfReader
from google import genai

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate, login, logout, get_user_model,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from .models import StudyDocument

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
except Exception:
    pass

logger = logging.getLogger(__name__)
User = get_user_model()

MAX_PDF_SIZE = 10 * 1024 * 1024  # 10 MB

# Verified currently supported Gemini models in Google GenAI SDK:
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
VERIFIED_GEMINI_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def get_configured_model():
    """Return the configured Gemini model name from environment or default."""
    env_model = os.getenv("GEMINI_MODEL", "").strip()
    return env_model or DEFAULT_GEMINI_MODEL


def get_model_candidates():
    """
    Return an ordered list of model candidates to try.
    Prefers the configured GEMINI_MODEL, followed by any GEMINI_FALLBACK_MODELS,
    followed by the standard verified models.
    """
    preferred = get_configured_model()
    candidates = [preferred]

    fallback_env = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
    if fallback_env:
        for m in fallback_env.split(","):
            m = m.strip()
            if m and m not in candidates:
                candidates.append(m)

    for m in VERIFIED_GEMINI_MODELS:
        if m not in candidates:
            candidates.append(m)

    return candidates


def is_transient_error(exc):
    """
    Check if an exception is a transient error suitable for retry.
    Handles HTTP 503 (UNAVAILABLE), 429 (RESOURCE_EXHAUSTED / RATE LIMIT),
    500 (INTERNAL), 502 (BAD_GATEWAY), 504 (DEADLINE_EXCEEDED),
    as well as socket timeouts and temporary network drops.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is None and hasattr(exc, "response"):
        code = getattr(exc.response, "status_code", None)

    if code in (503, 429, 500, 502, 504):
        return True
    status = str(getattr(exc, "status", "")).upper()
    if status in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE_EXCEEDED"):
        return True

    err_str = f"{type(exc).__name__} {exc}".lower()
    transient_patterns = [
        "503", "unavailable", "overloaded",
        "429", "resource_exhausted", "resource exhausted", "rate limit", "quota",
        "500", "internal server error",
        "502", "bad gateway",
        "504", "gateway timeout", "deadline exceeded", "deadline expired",
        "timeout", "timed out", "connecttimeout", "readtimeout",
        "connection reset", "broken pipe",
    ]
    return any(p in err_str for p in transient_patterns)


def is_auth_error(exc):
    """Check if exception is an authentication / authorization error."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is None and hasattr(exc, "response"):
        code = getattr(exc.response, "status_code", None)
    if code in (401, 403):
        return True
    err_str = f"{type(exc).__name__} {exc}".lower()
    return any(p in err_str for p in ("401", "403", "api_key", "unauthenticated", "permission_denied", "forbidden", "api key not valid"))


def is_not_found_error(exc):
    """Check if exception indicates model not found (404) or retired."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is None and hasattr(exc, "response"):
        code = getattr(exc.response, "status_code", None)
    if code == 404:
        return True
    err_str = f"{type(exc).__name__} {exc}".lower()
    return any(p in err_str for p in ("not found", "404", "no longer available", "not supported for generatecontent"))


def clean_and_parse_json(text):
    """
    Safely extract and parse JSON from AI responses.
    Handles responses enclosed in markdown code fences or surrounded by commentary.
    """
    if not text:
        raise ValueError("AI returned an empty response.")

    cleaned = text.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if "```" in cleaned:
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Extract JSON object or array by finding outermost brackets
    first_bracket = cleaned.find('[')
    first_brace = cleaned.find('{')

    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        last_bracket = cleaned.rfind(']')
        if last_bracket != -1 and last_bracket > first_bracket:
            try:
                return json.loads(cleaned[first_bracket:last_bracket + 1])
            except json.JSONDecodeError:
                pass
    elif first_brace != -1:
        last_brace = cleaned.rfind('}')
        if last_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(cleaned[first_brace:last_brace + 1])
            except json.JSONDecodeError:
                pass

    raise ValueError("The AI response did not contain valid JSON.")


def generate_ai_text(prompt):
    """
    Generate text using Google GenAI SDK.
    Features:
    - Safe retries with exponential backoff and jitter for transient 503, 429, and 5xx errors.
    - Automatic graceful fallback to verified models if the primary model remains overloaded or unavailable.
    - Configurable model selection via GEMINI_MODEL.
    - Helpful user-facing error messages without exposing API keys.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Please add your key to the .env file."
        )

    try:
        max_retries = max(1, int(os.getenv("GEMINI_MAX_RETRIES", "3")))
    except ValueError:
        max_retries = 3

    try:
        initial_backoff = max(0.2, float(os.getenv("GEMINI_INITIAL_BACKOFF", "1.5")))
    except ValueError:
        initial_backoff = 1.5

    try:
        backoff_factor = max(1.2, float(os.getenv("GEMINI_BACKOFF_FACTOR", "2.0")))
    except ValueError:
        backoff_factor = 2.0

    try:
        max_backoff = max(initial_backoff, float(os.getenv("GEMINI_MAX_BACKOFF", "8.0")))
    except ValueError:
        max_backoff = 8.0

    try:
        timeout_sec = max(5.0, float(os.getenv("GEMINI_TIMEOUT_SECONDS", "20.0")))
    except ValueError:
        timeout_sec = 20.0

    models_to_try = get_model_candidates()

    try:
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": int(timeout_sec * 1000)}
        )
    except Exception:
        try:
            client = genai.Client(api_key=api_key)
        except Exception as init_err:
            logger.error("Failed to initialize Google GenAI client: %s", init_err)
            raise RuntimeError(f"Failed to initialize Gemini client: {init_err}")

    last_error = None
    last_error_was_503 = False
    last_error_was_429 = False

    for model_index, model_name in enumerate(models_to_try):
        logger.info(
            "Attempting AI generation with model '%s' (candidate %d of %d)",
            model_name, model_index + 1, len(models_to_try)
        )

        for attempt in range(max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                if response and response.text:
                    if attempt > 0:
                        logger.info(
                            "Model '%s' succeeded on retry attempt %d/%d.",
                            model_name, attempt, max_retries
                        )
                    return response.text.strip()
                raise RuntimeError("Gemini returned an empty response.")

            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()

                # Fatal: Auth/Permission errors must fail fast without wasteful retries
                if is_auth_error(exc):
                    logger.error("Authentication failed with Gemini API: %s", exc)
                    raise RuntimeError(
                        "Your GEMINI_API_KEY appears to be invalid or unauthenticated. "
                        "Please verify your key in the .env file or generate a new key at https://aistudio.google.com/app/apikey"
                    )

                # Model not found / 404: skip to next candidate immediately without retries
                if is_not_found_error(exc):
                    logger.warning(
                        "Model '%s' is not supported or not found (404). Trying next fallback model...",
                        model_name
                    )
                    break

                # Transient errors: 503 UNAVAILABLE, 429 RATE LIMIT, 5xx
                if is_transient_error(exc):
                    if "503" in err_str or "unavailable" in err_str or getattr(exc, "code", None) == 503:
                        last_error_was_503 = True
                    if "429" in err_str or "resource_exhausted" in err_str or getattr(exc, "code", None) == 429:
                        last_error_was_429 = True

                    if attempt < max_retries:
                        # Compute exponential backoff with jitter
                        delay = min(
                            initial_backoff * (backoff_factor ** attempt) + random.uniform(0.1, 0.4),
                            max_backoff
                        )
                        logger.warning(
                            "Transient error (%s) on model '%s' (attempt %d/%d). Retrying in %.2fs...",
                            type(exc).__name__, model_name, attempt + 1, max_retries + 1, delay
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.warning(
                            "Model '%s' exhausted all %d retry attempts due to transient error. Trying fallback model...",
                            model_name, max_retries + 1
                        )
                        break

                # Network / DNS errors
                if "getaddrinfo" in err_str or "cannot connect" in err_str or "connecterror" in err_str:
                    logger.error("Network connection error to Gemini API: %s", exc)
                    raise RuntimeError(
                        "Cannot connect to Google Gemini API servers. Please check your internet connection."
                    )

                # Other unexpected error: log and try next model
                logger.warning("Unexpected error on model '%s': %s", model_name, exc)
                break

    # If all candidate models and retries failed:
    logger.error("All Gemini models and retry attempts failed. Last error: %s", last_error, exc_info=True)

    if last_error_was_503:
        configured_model = get_configured_model()
        alt_model = "gemini-3.5-flash" if configured_model != "gemini-3.5-flash" else "gemini-3.5-flash-lite"
        raise RuntimeError(
            f"Google Gemini service is temporarily overloaded (503 UNAVAILABLE). "
            f"NoteWise AI automatically retried {max_retries} times across multiple backup models. "
            f"Please try again in a few moments, or configure GEMINI_MODEL={alt_model} in your .env file."
        )

    if last_error_was_429:
        raise RuntimeError(
            "Gemini API rate limit or quota exceeded (429). "
            "Please wait a moment before sending another request, or check your quota at Google AI Studio."
        )

    err_str = str(last_error).lower()
    if "getaddrinfo" in err_str or "connecterror" in err_str:
        raise RuntimeError("Cannot connect to Google Gemini API servers. Please check your internet connection.")

    raise RuntimeError(f"AI generation failed: {last_error}")


def extract_pdf_text(pdf_file):
    """
    Extract selectable text from an uploaded PDF.
    Returns (extracted_text, error_message).
    """
    try:
        pdf_file.seek(0)
        reader = PdfReader(io.BytesIO(pdf_file.read()))

        if reader.is_encrypted:
            return None, "This PDF is password-protected or encrypted. Please provide an unencrypted PDF."

        pages_text = []
        for index, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text and page_text.strip():
                pages_text.append(page_text.strip())

        full_text = "\n\n".join(pages_text).strip()

        if not full_text:
            return None, (
                "No selectable text found in the PDF. Scanned PDFs or images require OCR. "
                "Please upload a text-based PDF or paste your notes directly."
            )

        return full_text, None

    except Exception as exc:
        logger.error("PDF extraction error: %s", exc, exc_info=True)
        return None, "Could not read this PDF. The file may be corrupt or formatted improperly."


def home(request):
    return render(request, "home.html", {
        "title": "NoteWise AI | Home",
    })


@login_required
@require_http_methods(["GET", "POST"])
def features(request):
    if request.method == "POST":
        action = request.POST.get("action", "analyze")

        # ---------------------------------------------------------------------
        # 1. Select Active Document
        # ---------------------------------------------------------------------
        if action == "select_document":
            document = StudyDocument.objects.filter(
                id=request.POST.get("document_id"),
                user=request.user,
            ).first()

            if not document:
                messages.error(request, "Document not found.")
                return redirect("features")

            request.session["active_document_id"] = document.id
            request.session.pop("study_result", None)
            request.session.pop("active_quiz", None)
            messages.success(request, f"Active document set to: {document.title}")
            return redirect("features")

        # ---------------------------------------------------------------------
        # 2. Delete Document from Library
        # ---------------------------------------------------------------------
        if action == "delete_document":
            document = StudyDocument.objects.filter(
                id=request.POST.get("document_id"),
                user=request.user,
            ).first()

            if document:
                if request.session.get("active_document_id") == document.id:
                    request.session.pop("active_document_id", None)
                    request.session.pop("study_result", None)
                    request.session.pop("active_quiz", None)
                document.delete()
                messages.success(request, "Document removed from your library.")
            else:
                messages.error(request, "Document not found.")

            return redirect("features")

        # ---------------------------------------------------------------------
        # 3. Clear Current Result
        # ---------------------------------------------------------------------
        if action == "clear_result":
            request.session.pop("study_result", None)
            request.session.pop("active_quiz", None)
            return redirect("features")

        # ---------------------------------------------------------------------
        # 4. Clear Chat History
        # ---------------------------------------------------------------------
        if action == "clear_chat":
            active_id = request.session.get("active_document_id")
            if active_id:
                request.session.pop(f"chat_history_{active_id}", None)
            request.session.pop("study_result", None)
            messages.success(request, "Chat conversation history cleared.")
            return redirect("features")

        # ---------------------------------------------------------------------
        # 5. Score Quiz
        # ---------------------------------------------------------------------
        if action == "score_quiz":
            quiz = request.session.get("active_quiz", [])
            if not quiz:
                messages.error(request, "No active quiz to score. Please generate a quiz first.")
                return redirect("features")

            score = 0
            reviewed = []

            for index, question in enumerate(quiz):
                selected = request.POST.get(f"answer_{index}", "").strip().upper()
                correct = str(question.get("answer", "")).strip().upper()
                is_correct = bool(selected and selected == correct)

                if is_correct:
                    score += 1

                reviewed.append({
                    "question": question.get("question", ""),
                    "options": question.get("options", {}),
                    "answer": correct,
                    "selected": selected,
                    "is_correct": is_correct,
                    "explanation": question.get("explanation", ""),
                    "topic": question.get("topic", "General"),
                })

            total = len(quiz)
            percentage = round(score * 100 / total) if total else 0

            # Record in user's quiz history for personalized learning
            doc_id = request.session.get("active_document_id")
            history = request.session.get("quiz_history", [])
            history.append({
                "document_id": doc_id,
                "score": score,
                "total": total,
                "percentage": percentage,
                "weak_topics": [
                    q.get("topic", "General") for q in reviewed if not q["is_correct"]
                ],
            })
            request.session["quiz_history"] = history[-20:]
            request.session["study_result"] = {
                "tool": "quiz_score",
                "title": "Your Quiz Results",
                "score": score,
                "total": total,
                "percentage": percentage,
                "quiz_review": reviewed,
            }
            request.session.pop("active_quiz", None)
            return redirect("features")

        # ---------------------------------------------------------------------
        # 6. Execute AI Study Tools
        # ---------------------------------------------------------------------
        if action == "use_tool":
            tool = request.POST.get("tool", "").strip()
            allowed_tools = {
                "chat", "quiz", "flashcards",
                "summary", "exam_answers", "personalized",
            }

            if tool not in allowed_tools:
                messages.error(request, "Unknown study tool requested.")
                return redirect("features")

            document_id = request.session.get("active_document_id")
            document = StudyDocument.objects.filter(
                id=document_id,
                user=request.user,
            ).first()

            if not document or not document.source_text.strip():
                messages.error(
                    request,
                    "Please select or upload a document containing readable notes first.",
                )
                return redirect("features")

            question = request.POST.get("question", "").strip()
            topic = request.POST.get("topic", "").strip()
            marks = request.POST.get("marks", "5").strip()

            if tool == "chat" and not question:
                messages.error(request, "Please enter a question to ask about your notes.")
                return redirect("features")

            if tool == "exam_answers":
                if not topic:
                    messages.error(request, "Please enter an exam question or topic.")
                    return redirect("features")
                if marks not in {"2", "5", "10"}:
                    marks = "5"

            # Use up to 45,000 characters from the document source
            source = document.source_text[:45000]

            # Build tool-specific prompts
            if tool == "chat":
                chat_key = f"chat_history_{document.id}"
                history = request.session.get(chat_key, [])
                history_text = ""
                if history:
                    history_lines = []
                    for h in history[-4:]:
                        history_lines.append(f"Student: {h.get('question', '')}")
                        history_lines.append(f"Assistant: {h.get('answer', '')}")
                    history_text = "PREVIOUS CONVERSATION CONTEXT:\n" + "\n".join(history_lines) + "\n\n"

                prompt = f"""You are NoteWise AI, an expert academic study assistant.
Answer the student's question accurately using ONLY the provided study notes.
If the notes do not contain the answer or sufficient context, clearly state:
"This topic is not covered in your uploaded notes."
Do not invent facts or cite external information as if it came from the notes.

STUDY NOTES:
{source}

{history_text}CURRENT QUESTION:
{question}
"""

            elif tool == "quiz":
                prompt = f"""You are NoteWise AI. Based strictly on the provided study notes, generate exactly 5 multiple choice questions (MCQs) for revision.
Return ONLY a valid JSON object without any commentary or markdown code fences, in this exact format:
{{
  "questions": [
    {{
      "question": "Question text here?",
      "options": {{
        "A": "First option",
        "B": "Second option",
        "C": "Third option",
        "D": "Fourth option"
      }},
      "answer": "A",
      "explanation": "Clear explanation grounded in the notes",
      "topic": "Specific topic name"
    }}
  ]
}}
Rules:
- Exactly 5 questions.
- Each question must have 4 options labeled A, B, C, D.
- The answer field must be exactly one letter: A, B, C, or D.
- Ground all questions, options, and explanations solely in the study notes.

STUDY NOTES:
{source}
"""

            elif tool == "flashcards":
                prompt = f"""You are NoteWise AI. Based strictly on the provided study notes, create 8 to 10 high-value revision flashcards.
Return ONLY a valid JSON object without markdown fences or extra commentary, in this format:
{{
  "cards": [
    {{
      "front": "Key term, concept, or question",
      "back": "Clear definition, key formula, or explanation from notes"
    }}
  ]
}}
Ensure the flashcards cover the most important concepts and definitions in the notes.

STUDY NOTES:
{source}
"""

            elif tool == "summary":
                prompt = f"""You are NoteWise AI, an expert revision summarizer.
Create a well-structured, clear revision summary of these study notes.
Structure your summary with:
1. Executive Overview (2-3 sentences summarizing the core theme)
2. Key Concepts & Definitions (important terms with clear definitions)
3. Core Topics & Key Details (organized bullet points with explanations)
4. Important Rules, Formulas, or Frameworks (if present in the notes)
5. Quick Revision Takeaways (key exam points to remember)

Preserve critical terminology. Do not fabricate information missing from the notes.

STUDY NOTES:
{source}
"""

            elif tool == "exam_answers":
                prompt = f"""You are NoteWise AI, an academic exam coach.
Write a structured, high-scoring exam answer worth {marks} marks for this question:
QUESTION / TOPIC: {topic}

Target Marks: {marks} Marks.

Answer structure guidelines:
- If 2 Marks: Provide a precise 2-3 sentence definition/statement and the essential distinction or key point.
- If 5 Marks: Provide a clear formal definition, followed by 3-4 structured bullet points with brief explanations and examples from the notes.
- If 10 Marks: Provide a comprehensive exam answer with an Introduction, Key Principles/Themes, Detailed In-depth Analysis (with subheadings and bullet points), Practical Examples/Nuances from the notes, and a Brief Conclusion.

Rely strictly on the uploaded study notes. If the notes do not have enough information to fully cover {marks} marks, state the limitation honestly.

STUDY NOTES:
{source}
"""

            elif tool == "personalized":
                doc_history = [
                    item for item in request.session.get("quiz_history", [])
                    if str(item.get("document_id")) == str(document.id)
                ]
                if doc_history:
                    weak_set = []
                    for h in doc_history:
                        weak_set.extend(h.get("weak_topics", []))
                    weak_summary = f"Student has completed {len(doc_history)} quiz(es) on this document. Average score: {round(sum(h.get('percentage', 0) for h in doc_history)/len(doc_history))}%. Weak areas: {', '.join(set(weak_set)) if weak_set else 'None identified'}."
                else:
                    weak_summary = "Student has not yet taken a quiz on this document. Create a diagnostic revision plan covering the key topics found in the notes."

                prompt = f"""You are NoteWise AI, a personalized learning coach.
Create an actionable, realistic revision roadmap based on the student's study notes and recent performance.

QUIZ PERFORMANCE SUMMARY:
{weak_summary}

STUDY NOTES:
{source}

Your study plan should include:
1. Performance Diagnosis: Highlight priority topics needing attention.
2. Topic Revision Order: Rank topics from highest priority to lower priority.
3. Realistic Study Schedule: A structured step-by-step revision routine.
4. Active Recall & Practice Tasks: Specific practice tasks, quiz topics, and self-test prompts.
"""

            try:
                output = generate_ai_text(prompt)

                if tool == "quiz":
                    data = clean_and_parse_json(output)
                    questions = data.get("questions", [])
                    if not questions:
                        raise ValueError("No questions found in AI response.")

                    validated_quiz = []
                    for q in questions[:10]:
                        ans = str(q.get("answer", "")).strip().upper()
                        if ans not in {"A", "B", "C", "D"}:
                            ans = "A"
                        options = q.get("options", {})
                        if not isinstance(options, dict) or len(options) < 2:
                            continue
                        validated_quiz.append({
                            "question": q.get("question", "Question"),
                            "options": {
                                "A": str(options.get("A", "")),
                                "B": str(options.get("B", "")),
                                "C": str(options.get("C", "")),
                                "D": str(options.get("D", "")),
                            },
                            "answer": ans,
                            "explanation": q.get("explanation", ""),
                            "topic": q.get("topic", "General"),
                        })

                    if not validated_quiz:
                        raise ValueError("Unable to generate valid quiz questions from the document.")

                    request.session["active_quiz"] = validated_quiz
                    result = {
                        "tool": "quiz",
                        "title": "Smart Quiz",
                        "quiz": validated_quiz,
                    }

                elif tool == "flashcards":
                    data = clean_and_parse_json(output)
                    raw_cards = data.get("cards", [])
                    if not raw_cards:
                        raise ValueError("No flashcards found in AI response.")

                    validated_cards = []
                    for c in raw_cards[:15]:
                        front = str(c.get("front", "")).strip()
                        back = str(c.get("back", "")).strip()
                        if front and back:
                            validated_cards.append({"front": front, "back": back})

                    if not validated_cards:
                        raise ValueError("Unable to generate valid flashcards from the document.")

                    result = {
                        "tool": "flashcards",
                        "title": "AI Flashcards",
                        "cards": validated_cards,
                    }

                elif tool == "chat":
                    chat_key = f"chat_history_{document.id}"
                    history = request.session.get(chat_key, [])
                    history.append({
                        "question": question,
                        "answer": output,
                    })
                    request.session[chat_key] = history[-15:]
                    result = {
                        "tool": "chat",
                        "title": "PDF Chat Answer",
                        "question": question,
                        "text": output,
                        "chat_history": history[-15:],
                    }

                elif tool == "exam_answers":
                    result = {
                        "tool": "exam_answers",
                        "title": f"Exam Answer ({marks} Marks)",
                        "topic": topic,
                        "marks": marks,
                        "text": output,
                    }

                elif tool == "summary":
                    result = {
                        "tool": "summary",
                        "title": "Your Notes Summary",
                        "text": output,
                    }

                elif tool == "personalized":
                    result = {
                        "tool": "personalized",
                        "title": "Personalized Revision Plan",
                        "text": output,
                    }

                request.session["study_result"] = result
                request.session["active_document_id"] = document.id

            except Exception as exc:
                logger.error("NoteWise AI tool '%s' error: %s", tool, exc, exc_info=True)
                messages.error(
                    request,
                    f"{exc}"
                )

            return redirect("features")

        # ---------------------------------------------------------------------
        # 7. Upload and Analyze a New Document
        # ---------------------------------------------------------------------
        pdf_file = request.FILES.get("pdf_file")
        notes_text = request.POST.get("notes_text", "").strip()
        title = request.POST.get("title", "").strip()

        if not pdf_file and not notes_text:
            messages.error(request, "Please upload a PDF file or paste your study notes.")
            return redirect("features")

        if pdf_file:
            if not pdf_file.name.lower().endswith(".pdf"):
                messages.error(request, "Only PDF files are supported.")
                return redirect("features")

            if pdf_file.size > MAX_PDF_SIZE:
                messages.error(request, "PDF size must be 10 MB or less.")
                return redirect("features")

            header_bytes = pdf_file.read(5)
            if not header_bytes.startswith(b"%PDF-"):
                messages.error(request, "This file does not appear to be a valid PDF.")
                return redirect("features")

            pdf_file.seek(0)
            source_text, err = extract_pdf_text(pdf_file)

            if err:
                messages.error(request, err)
                return redirect("features")

            title = title or pdf_file.name.rsplit(".", 1)[0]
        else:
            source_text = notes_text
            title = title or "My Study Notes"

        document = StudyDocument(
            user=request.user,
            title=title[:200],
            source_text=source_text,
            status="ready",
        )

        if pdf_file:
            document.pdf_file = pdf_file

        document.save()
        request.session["active_document_id"] = document.pk
        request.session.pop("study_result", None)
        request.session.pop("active_quiz", None)

        messages.success(request, f"'{document.title}' extracted and saved successfully!")
        return redirect("features")

    # -------------------------------------------------------------------------
    # GET Request: Render Features Page
    # -------------------------------------------------------------------------
    documents = StudyDocument.objects.filter(user=request.user).order_by("-created_at")
    active_id = request.session.get("active_document_id")
    active_document = documents.filter(pk=active_id).first() if active_id else None

    # Default to the most recent document if none is explicitly selected
    if not active_document and documents.exists():
        active_document = documents.first()
        request.session["active_document_id"] = active_document.id

    result = request.session.get("study_result")
    if result and not active_document:
        result = None

    active_chat_history = []
    if active_document:
        active_chat_history = request.session.get(f"chat_history_{active_document.id}", [])

    api_configured = bool(
        os.getenv("GEMINI_API_KEY") and
        os.getenv("GEMINI_API_KEY").strip() != "your_gemini_api_key_here"
    )

    return render(request, "features.html", {
        "title": "NoteWise AI | Study Workspace",
        "documents": documents,
        "active_document": active_document,
        "study_result": result,
        "chat_history": active_chat_history,
        "ai_configured": api_configured,
        "configured_model": get_configured_model(),
    })


def how_it_works(request):
    return render(request, "how-it-works.html", {
        "title": "NoteWise AI | How It Works",
    })


def about(request):
    return render(request, "about.html", {
        "title": "NoteWise AI | About",
    })


def signin(request):
    context = {}

    if request.method == "POST":
        login_id = request.POST.get("login_id", "").strip()
        password = request.POST.get("password", "")
        context["login_id"] = login_id
        username = login_id

        if "@" in login_id:
            existing_user = User.objects.filter(email__iexact=login_id).first()
            if existing_user:
                username = existing_user.get_username()

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            if not request.POST.get("remember_me"):
                request.session.set_expiry(0)

            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)

            return redirect("features")

        context["form_errors"] = ["Invalid username/email or password."]

    return render(request, "signin.html", context)


def signup(request):
    context = {"form_data": {}}

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")

        context["form_data"] = {"username": username, "email": email}
        errors = []

        if not username or not email or not password1:
            errors.append("Please fill in all required fields.")

        if User.objects.filter(username__iexact=username).exists():
            errors.append("This username is already taken.")

        if email and User.objects.filter(email__iexact=email).exists():
            errors.append("This email is already registered.")

        if password1 != password2:
            errors.append("Passwords do not match.")

        if password1:
            try:
                validate_password(password1)
            except ValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            context["form_errors"] = errors
            return render(request, "signup.html", context)

        User.objects.create_user(
            username=username,
            email=email,
            password=password1,
        )
        messages.success(request, "Account created successfully. Please log in.")
        return redirect("signin")

    return render(request, "signup.html", context)


@require_http_methods(["POST"])
def signout(request):
    logout(request)
    return redirect("home")
