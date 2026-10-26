"""
NoteWise AI - Gemini API Diagnostic & Verification Script
Run this script to verify your Gemini API key and model connectivity:
    python test_gemini.py
"""

import os
import time
import random
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass

from google import genai

api_key = os.getenv("GEMINI_API_KEY", "").strip()

if not api_key or api_key == "your_gemini_api_key_here":
    print("=" * 60)
    print("[ERROR] GEMINI_API_KEY is not set!")
    print("=" * 60)
    print("How to fix:")
    print("1. Create a file named '.env' in this directory.")
    print("2. Add this line:")
    print("   GEMINI_API_KEY=your_actual_api_key_from_google_ai_studio")
    print("3. Get your free key at: https://aistudio.google.com/app/apikey")
    print("=" * 60)
    raise SystemExit(1)

configured_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
model_candidates = [
    configured_model,
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]
# Remove duplicates preserving order
seen = set()
models_to_try = [m for m in model_candidates if not (m in seen or seen.add(m))]

max_retries = int(os.getenv("GEMINI_MAX_RETRIES", "3"))
initial_backoff = float(os.getenv("GEMINI_INITIAL_BACKOFF", "1.5"))
backoff_factor = float(os.getenv("GEMINI_BACKOFF_FACTOR", "2.0"))

print("=" * 60)
print("NoteWise AI - Gemini Connectivity & Diagnostic Check")
print(f"Configured Model: {configured_model}")
print(f"Candidate Models: {', '.join(models_to_try)}")
print("=" * 60)

try:
    client = genai.Client(api_key=api_key, http_options={"timeout": 15000})
except Exception:
    client = genai.Client(api_key=api_key)

last_error = None
success = False
used_model = None

for model_name in models_to_try:
    print(f"\n--> Testing model: '{model_name}'...")
    model_succeeded = False

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                print(f"    Retry attempt {attempt}/{max_retries} for '{model_name}'...")

            response = client.models.generate_content(
                model=model_name,
                contents="Reply with exactly: NoteWise AI connection successful",
            )
            if response and response.text:
                print("=" * 60)
                print("[SUCCESS] Gemini API is working properly!")
                print(f"Active Working Model: {model_name}")
                print(f"AI Response: {response.text.strip()}")
                print("=" * 60)
                success = True
                model_succeeded = True
                used_model = model_name
                break

        except Exception as error:
            last_error = error
            err_str = str(error).lower()
            code = getattr(error, "code", None)

            # Check if 401 / 403 (invalid key) -> fail immediately
            if code in (401, 403) or "api_key" in err_str or "unauthenticated" in err_str:
                print(f"    [!] Authentication error on model '{model_name}'.")
                break

            # Check if 404 (model not found / deprecated) -> try next model candidate immediately
            if code == 404 or "not found" in err_str or "no longer available" in err_str:
                print(f"    [!] Model '{model_name}' is not found or retired (404). Trying next fallback model...")
                break

            # Check if 503 (UNAVAILABLE), 429 (RATE LIMIT), or 5xx -> retry with exponential backoff
            if code in (503, 429, 500, 502, 504) or "503" in err_str or "unavailable" in err_str or "429" in err_str or "quota" in err_str or "deadline" in err_str:
                if attempt < max_retries:
                    delay = initial_backoff * (backoff_factor ** attempt) + random.uniform(0.1, 0.4)
                    print(f"    [!] Transient error ({type(error).__name__}). Retrying in {delay:.2f}s...")
                    time.sleep(delay)
                    continue
                else:
                    print(f"    [!] Model '{model_name}' exhausted {max_retries} retries. Switching to fallback model...")
                    break
            else:
                print(f"    [!] Non-transient error on model '{model_name}': {error}")
                break

    if model_succeeded:
        break

if not success:
    print("\n" + "=" * 60)
    print("[ERROR] Gemini API test failed.")
    print(f"Error details: {type(last_error).__name__}: {last_error}")
    print("=" * 60)
    err_str = str(last_error).lower()
    code = getattr(last_error, "code", None)

    if code in (401, 403) or "api_key" in err_str or "unauthenticated" in err_str:
        print("Diagnosis: Your GEMINI_API_KEY appears invalid or expired.")
        print("Please check your key at https://aistudio.google.com/app/apikey")
    elif code == 503 or "503" in err_str or "unavailable" in err_str:
        print("Diagnosis: Google Gemini servers are temporarily overloaded (503 UNAVAILABLE).")
        print("To fix this, switch to a high-availability fallback model in your .env file:")
        print("   GEMINI_MODEL=gemini-3.5-flash")
    elif code == 429 or "quota" in err_str or "429" in err_str:
        print("Diagnosis: Quota or rate limit exceeded (429). Please wait a moment or check your quota.")
    else:
        print("Diagnosis: Check your network connection and API configuration.")
    print("=" * 60)
    raise SystemExit(1)
