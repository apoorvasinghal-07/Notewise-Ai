import io
import json
from unittest.mock import patch

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import StudyDocument
from core.views import (
    clean_and_parse_json,
    extract_pdf_text,
    generate_ai_text,
)

User = get_user_model()


class JSONParsingTests(TestCase):
    def test_clean_json(self):
        raw = '{"questions": [{"question": "What is AI?", "answer": "A"}]}'
        parsed = clean_and_parse_json(raw)
        self.assertIn("questions", parsed)
        self.assertEqual(parsed["questions"][0]["answer"], "A")

    def test_markdown_fenced_json(self):
        raw = '```json\n{"cards": [{"front": "CPU", "back": "Central Processing Unit"}]}\n```'
        parsed = clean_and_parse_json(raw)
        self.assertIn("cards", parsed)
        self.assertEqual(parsed["cards"][0]["front"], "CPU")

    def test_fenced_without_language_tag(self):
        raw = '```\n{"cards": [{"front": "RAM", "back": "Random Access Memory"}]}\n```'
        parsed = clean_and_parse_json(raw)
        self.assertEqual(parsed["cards"][0]["front"], "RAM")

    def test_json_with_surrounding_text(self):
        raw = 'Here is the requested quiz:\n{"questions": []}\nHope this helps!'
        parsed = clean_and_parse_json(raw)
        self.assertIn("questions", parsed)

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            clean_and_parse_json("Not a json at all")


class ViewRoutingTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_public_pages_load(self):
        for route_name in ["home", "how_it_works", "about"]:
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)

    def test_features_requires_login(self):
        response = self.client.get(reverse("features"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/signin/", response.url)


class AuthenticationFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_signup_and_signin(self):
        # 1. Sign up
        signup_data = {
            "username": "teststudent",
            "email": "student@example.com",
            "password1": "StrongP@ssw0rd123!",
            "password2": "StrongP@ssw0rd123!",
        }
        response = self.client.post(reverse("signup"), signup_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="teststudent").exists())

        # 2. Sign in with username
        signin_data = {
            "login_id": "teststudent",
            "password": "StrongP@ssw0rd123!",
        }
        signin_resp = self.client.post(reverse("signin"), signin_data)
        self.assertEqual(signin_resp.status_code, 302)
        self.assertEqual(signin_resp.url, reverse("features"))

        # 3. Sign out
        signout_resp = self.client.post(reverse("signout"))
        self.assertEqual(signout_resp.status_code, 302)

    def test_signin_with_email(self):
        User.objects.create_user(
            username="emailuser",
            email="user@notewise.ai",
            password="SecurePass999#",
        )
        response = self.client.post(reverse("signin"), {
            "login_id": "user@notewise.ai",
            "password": "SecurePass999#",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("features"))


class DocumentAndToolsFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="studious_user",
            password="StrongPassword123!",
        )
        self.other_user = User.objects.create_user(
            username="other_student",
            password="StrongPassword123!",
        )
        self.client.login(username="studious_user", password="StrongPassword123!")

    def test_upload_pasted_notes(self):
        response = self.client.post(reverse("features"), {
            "action": "analyze",
            "title": "Data Structures Notes",
            "notes_text": "Stacks use LIFO. Queues use FIFO. Binary Trees have at most two children.",
        })
        self.assertEqual(response.status_code, 302)
        doc = StudyDocument.objects.filter(user=self.user, title="Data Structures Notes").first()
        self.assertIsNotNone(doc)
        self.assertIn("LIFO", doc.source_text)
        # Verify document is set as active
        self.assertEqual(self.client.session.get("active_document_id"), doc.id)

    def test_user_document_isolation(self):
        # Create doc for other user
        other_doc = StudyDocument.objects.create(
            user=self.other_user,
            title="Private Notes",
            source_text="Secret content.",
        )
        # Current user tries to select other user's document
        response = self.client.post(reverse("features"), {
            "action": "select_document",
            "document_id": other_doc.id,
        })
        self.assertEqual(response.status_code, 302)
        # Active document must not be set to other_doc
        self.assertNotEqual(self.client.session.get("active_document_id"), other_doc.id)

    @patch("core.views.generate_ai_text")
    def test_all_six_study_tools(self, mock_ai):
        doc = StudyDocument.objects.create(
            user=self.user,
            title="Python Fundamentals",
            source_text="Python is a high-level interpreted programming language. It uses indentation for code blocks.",
        )
        session = self.client.session
        session["active_document_id"] = doc.id
        session.save()

        # Tool 1: AI Chat
        mock_ai.return_value = "Python uses indentation to define code blocks."
        resp = self.client.post(reverse("features"), {
            "action": "use_tool",
            "tool": "chat",
            "question": "How does Python define code blocks?",
        })
        self.assertEqual(resp.status_code, 302)
        res = self.client.session.get("study_result")
        self.assertEqual(res["tool"], "chat")
        self.assertIn("indentation", res["text"])

        # Tool 2: Smart Quiz
        mock_ai.return_value = json.dumps({
            "questions": [
                {
                    "question": "What does Python use for blocks?",
                    "options": {"A": "Curly braces", "B": "Indentation", "C": "Begin/End", "D": "Tags"},
                    "answer": "B",
                    "explanation": "Python uses whitespace indentation.",
                    "topic": "Syntax"
                }
            ]
        })
        resp = self.client.post(reverse("features"), {
            "action": "use_tool",
            "tool": "quiz",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIsNotNone(self.client.session.get("active_quiz"))

        # Score Quiz
        score_resp = self.client.post(reverse("features"), {
            "action": "score_quiz",
            "answer_0": "B",
        })
        self.assertEqual(score_resp.status_code, 302)
        quiz_res = self.client.session.get("study_result")
        self.assertEqual(quiz_res["tool"], "quiz_score")
        self.assertEqual(quiz_res["score"], 1)
        self.assertEqual(quiz_res["percentage"], 100)

        # Tool 3: Flashcards
        mock_ai.return_value = json.dumps({
            "cards": [
                {"front": "Interpreted Language", "back": "Code is executed line by line."}
            ]
        })
        resp = self.client.post(reverse("features"), {
            "action": "use_tool",
            "tool": "flashcards",
        })
        self.assertEqual(resp.status_code, 302)
        cards_res = self.client.session.get("study_result")
        self.assertEqual(cards_res["tool"], "flashcards")
        self.assertEqual(len(cards_res["cards"]), 1)

        # Tool 4: Summary
        mock_ai.return_value = "Executive Summary: Python is interpreted and uses indentation."
        resp = self.client.post(reverse("features"), {
            "action": "use_tool",
            "tool": "summary",
        })
        self.assertEqual(resp.status_code, 302)
        summary_res = self.client.session.get("study_result")
        self.assertEqual(summary_res["tool"], "summary")

        # Tool 5: Exam Answers
        mock_ai.return_value = "5-Mark Answer: 1. Definition 2. Syntax 3. Semantics 4. Conclusion"
        resp = self.client.post(reverse("features"), {
            "action": "use_tool",
            "tool": "exam_answers",
            "topic": "Explain Python block structure",
            "marks": "5",
        })
        self.assertEqual(resp.status_code, 302)
        exam_res = self.client.session.get("study_result")
        self.assertEqual(exam_res["tool"], "exam_answers")
        self.assertEqual(exam_res["marks"], "5")

        # Tool 6: Personalized Learning
        mock_ai.return_value = "Revision Plan: Focus on Python syntax and practice exercises."
        resp = self.client.post(reverse("features"), {
            "action": "use_tool",
            "tool": "personalized",
        })
        self.assertEqual(resp.status_code, 302)
        plan_res = self.client.session.get("study_result")
        self.assertEqual(plan_res["tool"], "personalized")

    def test_delete_document(self):
        doc = StudyDocument.objects.create(
            user=self.user,
            title="Temporary Notes",
            source_text="Delete me.",
        )
        response = self.client.post(reverse("features"), {
            "action": "delete_document",
            "document_id": doc.id,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StudyDocument.objects.filter(id=doc.id).exists())



class GeminiRetryAndErrorHandlingTests(TestCase):
    def setUp(self):
        import os
        os.environ["GEMINI_API_KEY"] = "test-api-key-12345"
        os.environ["GEMINI_MAX_RETRIES"] = "2"
        os.environ["GEMINI_INITIAL_BACKOFF"] = "0.01"

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_503_transient_error_retries_and_succeeds(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ServerError
        mock_client = mock_client_cls.return_value
        
        # 1st call: 503 UNAVAILABLE, 2nd call: success
        error_503 = ServerError(503, {"error": {"message": "Model is overloaded", "status": "UNAVAILABLE"}})
        success_resp = type("Response", (), {"text": "AI generated successfully after retry"})()
        mock_client.models.generate_content.side_effect = [error_503, success_resp]

        result = generate_ai_text("Test prompt")
        self.assertEqual(result, "AI generated successfully after retry")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        mock_sleep.assert_called_once()

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_503_exhaustion_switches_to_fallback_model(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ServerError
        mock_client = mock_client_cls.return_value

        # Primary model fails 3 times with 503 (initial + 2 retries)
        error_503 = ServerError(503, {"error": {"message": "Capacity exceeded", "status": "UNAVAILABLE"}})
        # Fallback model succeeds
        fallback_resp = type("Response", (), {"text": "Fallback model succeeded"})()
        mock_client.models.generate_content.side_effect = [error_503, error_503, error_503, fallback_resp]

        result = generate_ai_text("Test prompt")
        self.assertEqual(result, "Fallback model succeeded")
        self.assertEqual(mock_client.models.generate_content.call_count, 4)

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_503_persistent_failure_gives_user_friendly_message(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ServerError
        mock_client = mock_client_cls.return_value

        error_503 = ServerError(503, {"error": {"message": "Service unavailable", "status": "UNAVAILABLE"}})
        mock_client.models.generate_content.side_effect = error_503

        with self.assertRaises(RuntimeError) as ctx:
            generate_ai_text("Test prompt")

        error_message = str(ctx.exception)
        self.assertIn("503 UNAVAILABLE", error_message)
        self.assertIn("GEMINI_MODEL", error_message)

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_429_rate_limit_retries_and_succeeds(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ClientError
        mock_client = mock_client_cls.return_value

        error_429 = ClientError(429, {"error": {"message": "Rate limit exceeded", "status": "RESOURCE_EXHAUSTED"}})
        success_resp = type("Response", (), {"text": "Success after 429"})()
        mock_client.models.generate_content.side_effect = [error_429, success_resp]

        result = generate_ai_text("Test prompt")
        self.assertEqual(result, "Success after 429")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_401_auth_error_fails_fast_without_retrying(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ClientError
        mock_client = mock_client_cls.return_value

        error_401 = ClientError(401, {"error": {"message": "API key invalid", "status": "UNAUTHENTICATED"}})
        mock_client.models.generate_content.side_effect = error_401

        with self.assertRaises(RuntimeError) as ctx:
            generate_ai_text("Test prompt")

        self.assertIn("GEMINI_API_KEY", str(ctx.exception))
        # Should not retry
        self.assertEqual(mock_client.models.generate_content.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_404_model_not_found_skips_immediately_to_fallback(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ClientError
        mock_client = mock_client_cls.return_value

        error_404 = ClientError(404, {"error": {"message": "Model not found", "status": "NOT_FOUND"}})
        fallback_resp = type("Response", (), {"text": "Fallback after 404"})()
        mock_client.models.generate_content.side_effect = [error_404, fallback_resp]

        result = generate_ai_text("Test prompt")
        self.assertEqual(result, "Fallback after 404")
        # 1 call to 404 model (no retry), 1 call to fallback model
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        mock_sleep.assert_not_called()

    def test_model_candidate_selection_env_override(self):
        import os
        from core.views import get_configured_model, get_model_candidates
        
        os.environ["GEMINI_MODEL"] = "gemini-3.5-flash"
        self.assertEqual(get_configured_model(), "gemini-3.5-flash")
        candidates = get_model_candidates()
        self.assertEqual(candidates[0], "gemini-3.5-flash")
        self.assertIn("gemini-3.5-flash-lite", candidates)
        self.assertIn("gemini-3.8-flash", candidates)

    @patch("time.sleep")
    @patch("google.genai.Client")
    def test_504_gateway_timeout_retries_and_succeeds(self, mock_client_cls, mock_sleep):
        from google.genai.errors import ServerError
        mock_client = mock_client_cls.return_value

        error_504 = ServerError(504, {"error": {"message": "Deadline expired before operation could complete", "status": "DEADLINE_EXCEEDED"}})
        success_resp = type("Response", (), {"text": "Success after 504"})()
        mock_client.models.generate_content.side_effect = [error_504, success_resp]

        result = generate_ai_text("Test prompt")
        self.assertEqual(result, "Success after 504")
        self.assertEqual(mock_client.models.generate_content.call_count, 2)
        mock_sleep.assert_called_once()

    def test_custom_fallback_models_env_var(self):
        import os
        from core.views import get_model_candidates
        os.environ["GEMINI_MODEL"] = "gemini-3.8-flash"
        os.environ["GEMINI_FALLBACK_MODELS"] = "gemini-3.5-flash-lite,gemini-3-flash-preview"
        candidates = get_model_candidates()
        self.assertEqual(candidates[0], "gemini-3.8-flash")
        self.assertEqual(candidates[1], "gemini-3.5-flash-lite")
        self.assertEqual(candidates[2], "gemini-3-flash-preview")
