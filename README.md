# 📚 NoteWise AI — AI-Powered Study Workspace

Welcome to **NoteWise AI**! NoteWise AI is an intelligent study assistant built with Django and Google Gemini AI that turns your PDFs and lecture notes into interactive summaries, practice quizzes, flashcards, exam-ready answers, and personalized revision plans.

---

## 🛠️ Summary of Issues Identified and Fixed

1. **Gemini API 503 UNAVAILABLE & Model Availability Handling**:
   - **Root Cause**: During peak usage, high-demand flagship models (such as `gemini-3.8-flash`) frequently experience transient server load spikes resulting in `503 UNAVAILABLE: This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.`. Previously, `core/views.py` halted immediately on 503 without retrying or falling back to alternate available models. Furthermore, earlier legacy models like `gemini-2.5-flash` were deprecated for new API keys with `404 NOT_FOUND`.
   - **Fix**:
     - **Safe Exponential Backoff Retries**: Transient errors (503 UNAVAILABLE, 429 RATE LIMIT, 500, 502, 504 DEADLINE_EXCEEDED, and socket timeouts) are automatically retried with exponential backoff and randomized jitter to prevent server synchronization.
     - **Automatic Multi-Model Fallback**: If all retries on the primary model are exhausted, NoteWise AI automatically cascades to verified backup models (`gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3-flash-preview`, etc.) until a working model responds.
     - **Configurable Model Selection via `.env`**: Users can configure `GEMINI_MODEL=gemini-3.5-flash` (or another model) to bypass high-demand models directly.
     - **Actionable User-Facing Error Messages**: If all retries and backup models fail, NoteWise AI displays a clear banner explaining the overload and giving immediate steps to switch models without leaking API keys.
     - **Fail-Fast for Non-Transient Errors**: Fatal authentication errors (401/403) fail immediately without wasteful retries, and missing/deprecated models (404) immediately skip to the next candidate model.

2. **Environment Variable & `.env` Loading**:
   - **Root Cause**: `manage.py` and `NoteWise/settings.py` were not loading `.env` files via `python-dotenv`. Even if an API key was placed in `.env`, `os.getenv("GEMINI_API_KEY")` returned `None`, triggering the error message: *"AI generation failed. Check your API configuration and server terminal."*
   - **Fix**: Integrated `load_dotenv()` at startup in `manage.py`, `NoteWise/settings.py`, and `core/views.py`. Added a `.env.example` template.

3. **Malformed AI JSON Handling**:
   - **Root Cause**: When generating quizzes and flashcards, Gemini often encloses JSON responses in Markdown code blocks (```` ```json ... ``` ````). Direct `json.loads()` threw `JSONDecodeError`, causing AI failure alerts.
   - **Fix**: Created `clean_and_parse_json()` to strip code fences and extract valid JSON payloads reliably.

4. **Missing UI & Missing Inputs for Features**:
   - **Root Cause**: `features.html` had empty forms without inputs for Chat questions, Exam Answer topics, and Marks selectors. Furthermore, `features.html` was missing the entire HTML/CSS markup to render generated results, quizzes, flashcards, and conversation threads!
   - **Fix**:
     - Added question and topic input fields and mark selector pills (2M, 5M, 10M).
     - Added the interactive study results section (`#study-result`) rendering:
       - **AI Chat**: Conversation history thread with follow-up question input.
       - **Smart Quiz**: 5 MCQ questions with selectable radio cards, auto-scoring, percentages, green/red review cards, and grounded explanations.
       - **AI Flashcards**: Interactive deck with 3D flip card, Next/Previous controls, card counter, and a toggleable grid view.
       - **Notes Summary**: Structured formatting with copy-to-clipboard button.
       - **Exam Answers**: Structured responses tailored to 2, 5, or 10 marks.
       - **Personalized Plan**: Diagnostic or quiz-performance-based revision roadmaps.

5. **CSS Syntax Error**:
   - **Root Cause**: `static/css/style.css` had stray Markdown backticks (```` ``` ````) pasted inside the file, breaking CSS parsing for subsequent styles.
   - **Fix**: Removed markdown fences and added styles for badges, flashcards, quiz options, and chat threads.

6. **PDF Validation & Error Handling**:
   - Added robust validation for PDF headers (`%PDF-`), size limit (10 MB), and encrypted files.
   - Added clear explanations when a PDF contains no selectable text (scanned documents requiring OCR).

---

## 🚀 Quick Setup & Installation Guide

### Prerequisites
- Python 3.10 to 3.14 installed on your computer.
- A free Gemini API key from Google AI Studio.

---

### Step 1: Open the Project Directory
Extract the zip file and open your terminal or command prompt in the project root:
```bash
cd "Notewise AI"
```

---

### Step 2: Create and Activate a Virtual Environment

**On Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
*(If PowerShell restricts script execution, run: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`)*

**On Windows (Command Prompt):**
```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
```

**On macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

### Step 3: Install Required Packages
Install all project dependencies with:
```bash
pip install -r requirements.txt
```

---

### Step 4: Configure Your Gemini API Key

1. Get a free API key at [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Copy the `.env.example` file to create your own `.env` file:
   - **On Windows (PowerShell):**
     ```powershell
     Copy-Item .env.example .env
     ```
   - **On macOS / Linux / Bash:**
     ```bash
     cp .env.example .env
     ```
3. Open `.env` in any text editor and paste your API key and preferred model:
   ```env
   GEMINI_API_KEY=AIzaSyYourActualApiKeyHere
   GEMINI_MODEL=gemini-3.5-flash
   ```

### ⚙️ How to Select & Configure Gemini Models

If you experience temporary `503 UNAVAILABLE` errors due to high traffic on a specific model, NoteWise AI automatically retries and falls back to alternate working models. You can also explicitly configure any verified model using the `GEMINI_MODEL` environment variable in your `.env` file:

| Model Identifier | Availability & Characteristics | Recommended Use Case |
|---|---|---|
| `gemini-3.5-flash` | **High Availability** · Fast TTFT · High stability | **Default & Recommended**: Balances speed, reasoning, and exceptional uptime against 503s. |
| `gemini-3.5-flash-lite` | **Ultra-High Availability** · Lowest latency | Best fallback for real-time quiz and flashcard generation under extreme server loads. |
| `gemini-3-flash-preview` | **High Availability** · Fast preview tier | Solid alternate candidate during multi-model failover. |
| `gemini-3.8-flash` | **Advanced Reasoning Tier** · Dynamic thinking | Deep reasoning; automatically backed up with retry/fallback if demand spikes occur. |
| `gemini-2.5-flash` | Standard tier (available on grandfathered accounts) | Backward-compatible fallback. |
| `gemini-2.0-flash` | Standard tier (available on grandfathered accounts) | High-volume backward-compatible fallback. |
| `gemini-1.5-flash` | Long-term support tier | Long context fallback. |

#### Advanced Tuning Parameters in `.env`:
```env
# Maximum retries per model before moving to fallback (default: 3)
GEMINI_MAX_RETRIES=3

# Initial backoff delay in seconds (default: 1.5)
GEMINI_INITIAL_BACKOFF=1.5

# Multiplier for each subsequent retry attempt (default: 2.0)
GEMINI_BACKOFF_FACTOR=2.0

# Maximum delay cap in seconds (default: 8.0)
GEMINI_MAX_BACKOFF=8.0

# HTTP connection & read timeout in seconds (default: 20.0)
GEMINI_TIMEOUT_SECONDS=20.0

# Custom fallback chain (comma-separated, optional)
# GEMINI_FALLBACK_MODELS=gemini-3.5-flash-lite,gemini-3-flash-preview
```

---

### Step 5: Test Your Gemini API Connection
Run the diagnostic script to verify your key and model connectivity:
```bash
python test_gemini.py
```
If your key is valid and connected to the internet, you will see:
```text
============================================================
[SUCCESS] Gemini API is working properly!
Active Working Model: gemini-3.5-flash
AI Response: NoteWise AI connection successful
============================================================
```

---

### Step 6: Run Database Migrations
Set up your SQLite database:
```bash
python manage.py migrate
```

---

### Step 7: Start the Development Server
Launch the NoteWise AI local server:
```bash
python manage.py runserver
```

Open your browser and navigate to:
👉 **`http://127.0.0.1:8000/`**

---

## 🎯 How to Use All Six Features

1. **Sign In or Create an Account**:
   - Click **Log In** (`/signin/`) or **Get Started** (`/signup/`).
   - You can log in using either your username or email.

2. **Upload or Paste Notes**:
   - Go to **Features** (`/features/`).
   - Enter a document name.
   - Choose a text-based PDF or paste your study notes into the textarea.
   - Click **Analyze Notes**.

3. **Use the 6 Study Tools**:
   - **AI-Powered PDF Chat**: Enter any question about your notes. You can continue asking follow-up questions in the conversation thread.
   - **Smart Quiz Generator**: Generates 5 multiple choice questions. Choose your answers and click **Submit Quiz for Scoring** to view your score, percentage, correct answers, and explanations.
   - **AI Flashcards**: Generates interactive flashcards. Click on any card or use the **Flip Card** button to toggle between front and back. Use **◀ Previous** and **Next ▶** to cycle through cards, or click **View All Cards (Grid)**.
   - **Notes Summary**: Generates an overview, key definitions, core concepts, and revision takeaways. Includes a one-click **Copy Text** button.
   - **Exam Answer Generator**: Enter an exam question or topic and choose your target marks (**2 Marks**, **5 Marks**, or **10 Marks**). Generates answers structured specifically for that mark allocation.
   - **Personalized Learning**: Generates a revision roadmap. If you have completed quizzes on this document, it prioritizes your weak areas; if not, it provides a comprehensive diagnostic study plan.

4. **Switch Between Saved Notes**:
   - In the **YOUR LIBRARY** section at the bottom of the Features page, click **Select** to switch active study material, or **Delete** to remove a document.

---

## 🧪 Automated Testing

Run the automated test suite covering all features, auth flows, JSON parsing, and security isolation:
```bash
python manage.py test core
```
Expected output:
```text
Ran 22 tests in ~9s
OK
```

---

## 📁 Files Changed & Added

| File | Change Description |
|---|---|
| `manage.py` | Added `python-dotenv` loader to ensure `.env` is loaded on startup. |
| `NoteWise/settings.py` | Added `.env` loading, `WhiteNoiseMiddleware`, `MEDIA_URL` / `MEDIA_ROOT`, static configuration, and clean console logging. |
| `NoteWise/urls.py` | Added static media routing for uploaded PDF files during local development. |
| `core/views.py` | Implemented exponential backoff retry with jitter for transient 503, 429, and 5xx errors; multi-model fallback cascade; configurable `GEMINI_MODEL`, `GEMINI_MAX_RETRIES`, `GEMINI_INITIAL_BACKOFF`, `GEMINI_BACKOFF_FACTOR`, `GEMINI_MAX_BACKOFF`, `GEMINI_TIMEOUT_SECONDS`; `clean_and_parse_json()`; user-friendly diagnostic error messages. |
| `core/tests.py` | Added comprehensive automated test suite (22 unit and integration tests covering retry, backoff, 503/429/401/404 handling, and tool flows). |
| `templates/features.html` | Added input forms for Chat and Exam Answers, and complete interactive results UI for all 6 tools. |
| `templates/home.html` | Fixed static asset tags. |
| `templates/about.html` | Fixed HTML layout structure and header/footer placement. |
| `templates/how-it-works.html` | Fixed HTML layout structure and header/footer placement. |
| `templates/signin.html` | Cleaned up split-screen auth layout. |
| `templates/signup.html` | Cleaned up split-screen auth layout. |
| `static/css/style.css` | Fixed markdown syntax errors (` ``` `); added CSS for flashcards, quiz options, scores, and chat bubbles. |
| `static/js/workspace.js` | Added interactive flashcard flipping, copy-to-clipboard, and loading indicators. |
| `test_gemini.py` | Diagnostic script verifying live connectivity, retry handling, model fallback, and guidance output. |
| `requirements.txt` | Created clean dependencies manifest. |
| `.env.example` | Comprehensive environment configuration template documenting all Gemini model options and retry variables. |

---

## ⚠️ Limitations & Notes
- **Scanned PDFs**: NoteWise AI extracts text from digital, selectable PDFs using `pypdf`. If a user uploads an image-only or scanned PDF, the system gracefully informs them that OCR is required and prompts them to paste their notes directly.
- **API Keys**: Never share or commit your `.env` file to version control. Keep your `GEMINI_API_KEY` private.
