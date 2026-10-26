import os
import sys
from pathlib import Path

# Setup Django environment
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["DJANGO_SETTINGS_MODULE"] = "NoteWise.settings"

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import django
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from core.models import StudyDocument

User = get_user_model()
username = "e2e_tester"
user, _ = User.objects.get_or_create(username=username, defaults={"email": "tester@example.com"})
user.set_password("Password123!")
user.save()

client = Client()
client.login(username=username, password="Password123!")

notes = """Operating Systems Fundamentals:
1. Process Scheduling:
   - FCFS (First Come First Serve): Non-preemptive, simple, can cause convoy effect.
   - SJF (Shortest Job First): Optimal average waiting time, burst time hard to predict.
   - Round Robin (RR): Preemptive, uses fixed time quantum, ideal for time-sharing.

2. Virtual Memory & Paging:
   - Virtual memory allows execution of processes not completely in physical memory.
   - Paging maps fixed-size pages to memory frames.
   - Page Fault occurs when accessing a page not present in RAM.
   - Page replacement algorithms: FIFO, LRU (Least Recently Used), Optimal.

3. Deadlocks:
   - 4 Coffman conditions: Mutual Exclusion, Hold and Wait, No Preemption, Circular Wait.
   - Deadlock prevention denies one condition.
   - Banker's Algorithm is used for deadlock avoidance."""

doc, _ = StudyDocument.objects.get_or_create(
    user=user,
    title="OS Fundamentals Test",
    defaults={"source_text": notes}
)
doc.source_text = notes
doc.save()

# Select document into session
session = client.session
session["active_document_id"] = doc.id
session.save()

print("=" * 65)
print("TESTING ALL 6 NOTEWISE AI FEATURES WITH LIVE GEMINI API")
print("=" * 65)

results = {}

# -------------------------------------------------------------
# 1. AI-Powered PDF Chat
# -------------------------------------------------------------
print("\n[1/6] Testing AI Chat...")
client.post(reverse("features"), {
    "action": "use_tool",
    "tool": "chat",
    "question": "What are the 4 conditions required for a deadlock to occur?"
})
r1 = client.session.get("study_result", {})
chat_text = r1.get("text", "")
passed1 = r1.get("tool") == "chat" and len(chat_text) > 20
results["1. AI Chat"] = {
    "passed": passed1,
    "details": f"Generated {len(chat_text)} chars. Answer preview: {chat_text[:120].strip()}..."
}
print(f"      Result: {'PASS' if passed1 else 'FAIL'}")

# -------------------------------------------------------------
# 2. Smart Quiz Generator & Quiz Scoring
# -------------------------------------------------------------
print("\n[2/6] Testing Smart Quiz Generator...")
client.post(reverse("features"), {
    "action": "use_tool",
    "tool": "quiz"
})
quiz = client.session.get("active_quiz", [])
passed2 = len(quiz) >= 1
results["2. Smart Quiz Generator"] = {
    "passed": passed2,
    "details": f"Generated {len(quiz)} multiple-choice questions. Sample Q1: '{quiz[0]['question'] if quiz else 'N/A'}'"
}
print(f"      Result: {'PASS' if passed2 else 'FAIL'}")

if quiz:
    print("      Testing Interactive Quiz Scoring...")
    client.post(reverse("features"), {
        "action": "score_quiz",
        "answer_0": quiz[0].get("answer", "A")
    })
    score_res = client.session.get("study_result", {})
    score_passed = score_res.get("tool") == "quiz_score"
    results["   - Quiz Scoring & Analytics"] = {
        "passed": score_passed,
        "details": f"Score: {score_res.get('score')}/{score_res.get('total')} ({score_res.get('percentage')}%)"
    }
    print(f"      Result: {'PASS' if score_passed else 'FAIL'}")

# -------------------------------------------------------------
# 3. AI Flashcards
# -------------------------------------------------------------
print("\n[3/6] Testing AI Flashcards...")
client.post(reverse("features"), {
    "action": "use_tool",
    "tool": "flashcards"
})
r3 = client.session.get("study_result", {})
cards = r3.get("cards", [])
passed3 = r3.get("tool") == "flashcards" and len(cards) >= 1
results["3. AI Flashcards"] = {
    "passed": passed3,
    "details": f"Generated {len(cards)} flashcards. Sample Card 1: Front: '{cards[0]['front'] if cards else ''}', Back: '{cards[0]['back'] if cards else ''}'"
}
print(f"      Result: {'PASS' if passed3 else 'FAIL'}")

# -------------------------------------------------------------
# 4. Notes Summary
# -------------------------------------------------------------
print("\n[4/6] Testing Notes Summary...")
client.post(reverse("features"), {
    "action": "use_tool",
    "tool": "summary"
})
r4 = client.session.get("study_result", {})
summary_text = r4.get("text", "")
passed4 = r4.get("tool") == "summary" and len(summary_text) > 50
results["4. Notes Summary"] = {
    "passed": passed4,
    "details": f"Generated {len(summary_text)} chars structured summary."
}
print(f"      Result: {'PASS' if passed4 else 'FAIL'}")

# -------------------------------------------------------------
# 5. Exam Answer Generator
# -------------------------------------------------------------
print("\n[5/6] Testing Exam Answer Generator (5 Marks)...")
client.post(reverse("features"), {
    "action": "use_tool",
    "tool": "exam_answers",
    "topic": "Explain Round Robin scheduling and time quantum",
    "marks": "5"
})
r5 = client.session.get("study_result", {})
exam_text = r5.get("text", "")
passed5 = r5.get("tool") == "exam_answers" and r5.get("marks") == "5" and len(exam_text) > 50
results["5. Exam Answer Generator"] = {
    "passed": passed5,
    "details": f"Generated {len(exam_text)} chars for target marks: 5 Marks."
}
print(f"      Result: {'PASS' if passed5 else 'FAIL'}")

# -------------------------------------------------------------
# 6. Personalized Learning Plan
# -------------------------------------------------------------
print("\n[6/6] Testing Personalized Learning Plan...")
client.post(reverse("features"), {
    "action": "use_tool",
    "tool": "personalized"
})
r6 = client.session.get("study_result", {})
plan_text = r6.get("text", "")
passed6 = r6.get("tool") == "personalized" and len(plan_text) > 50
results["6. Personalized Learning Plan"] = {
    "passed": passed6,
    "details": f"Generated {len(plan_text)} chars personalized revision roadmap."
}
print(f"      Result: {'PASS' if passed6 else 'FAIL'}")

print("\n" + "=" * 65)
print("FINAL VERIFICATION SUMMARY")
print("=" * 65)
all_pass = True
for name, info in results.items():
    tag = "[PASS]" if info["passed"] else "[FAIL]"
    if not info["passed"]:
        all_pass = False
    print(f"{tag} {name}")
    print(f"       {info['details']}")
print("=" * 65)

if all_pass:
    print("ALL 6 FEATURES ARE VERIFIED AND WORKING PROPERLY!")
else:
    print("SOME FEATURES FAILED VERIFICATION.")
