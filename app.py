# app.py (improved)
# Telegram education chatbot — fixes for state persistence, duplicate status message,
# robust translation + fonts fallback, larger message chunks, safer webhook error handling,
# request timeouts, and cleaner structure.
#
# Requirements (add to requirements.txt):
# flask
# python-dotenv
# requests
# reportlab
# googletrans==4.0.0rc1
#
# Optional (no external service required): none — state uses local SQLite file.

from flask import Flask, request
from dotenv import load_dotenv
import os
import json
import logging
import requests
import time
import threading
import sqlite3
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from googletrans import Translator

# -------------------- Logging --------------------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -------------------- App & Env --------------------
app = Flask(__name__)
load_dotenv()

# Load API keys from environment variables (validate early)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN is missing. Set it in your environment.")
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY is missing. Set it in your environment.")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-preview-05-20:generateContent"
)

# -------------------- Conversation States --------------------
STATE_MENU = "menu"
STATE_LEARN_TOPIC = "learn_topic"
STATE_MCQ_TOPIC = "mcq_topic"
STATE_LEARN_LANGUAGE_SELECTION = "learn_language_selection"
STATE_MCQ_LANGUAGE_SELECTION = "mcq_language_selection"
STATE_POST_LEARN = "post_learn"
STATE_POST_QUIZ = "post_quiz"

# -------------------- Phrases --------------------
PHRASES = {
    "welcome": (
        "Hi! 👋 What would you like help with today?\n\n*Reply with a number:*\n"
        "1️⃣ Learn about a topic\n2️⃣ Test your knowledge with MCQs"
    ),
    "learn_prompt": "📚 What topic would you like to learn about?",
    "mcq_prompt": "📝 What topic would you like a quiz on?",
    "language_prompt": "Great choice! Now, please tell me the language you want to learn in (e.g., English, Hindi, Spanish).",
    "invalid_option": "Please enter a valid option: 1 or 2.",
    "search_message": "Finding and explaining the topic for you... ⏳",
    "notes_intro": "📘 Here's the explanation of '{}':",
    "post_learn_prompt": "Would you like a downloadable PDF of these notes or a quiz to test your knowledge?\n\nReply with 'PDF' or 'Quiz'.",
    "post_quiz_prompt": "Would you like a downloadable PDF of the notes? Reply with 'Yes' to get them.",
    "download_success": "Generating your notes as a PDF... 📄",
    "document_caption": "Here are your downloadable notes for {}!",
    "no_notes": "❌ I'm sorry, I couldn't find the notes to download.",
    "quiz_message": "Generating an insightful quiz on '{}'... 🤔",
    "quiz_intro": "🧠 Here's your quiz:",
    "quiz_error": "❌ Couldn't generate the MCQs. Try again later.",
    "fetch_error": "❌ Couldn't fetch learning content right now.",
    "unknown_error": "❌ Sorry, something went wrong. Please try again later.",
    "unknown_command": "I'm not sure what you mean. Say 'hi edgo' to get started. 😊",
    "pdf_word": "pdf",
    "quiz_word": "quiz",
    "yes_word": "yes",
    "end_conversation": "Okay, let me know if you need anything else! 😊",
    "pdf_font_error": (
        "❌ I couldn't find a compatible font for PDF in your chosen language. "
        "Sending a basic PDF that may not render all characters perfectly."
    )
}

# -------------------- Translation & Fonts --------------------
translator = Translator()

# Map human-readable names to language codes expected by googletrans
LANG_CODE_MAP = {
    'english': 'en', 'hindi': 'hi', 'telugu': 'te', 'kannada': 'kn', 'tamil': 'ta',
    'marathi': 'mr', 'malayalam': 'ml', 'spanish': 'es', 'french': 'fr', 'german': 'de'
}

# Map language names to font file paths (optional fonts). If not found, we fallback safely.
FONT_MAP = {
    'Hindi': 'languages/NotoSansHindi.ttf',
    'Telugu': 'languages/NotoSansTelugu.ttf',
    'Kannada': 'languages/NotoSansKannada.ttf',
    'Tamil': 'languages/NotoSansTamil.ttf',
    'Marathi': 'languages/NotoSansHindi.ttf',
    'Malayalam': 'languages/NotoSansMalayalam.ttf'  # fixed spelling
}

# -------------------- SQLite State Store --------------------
DB_PATH = os.getenv("STATE_DB_PATH", "state.db")

def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_state (chat_id TEXT PRIMARY KEY, state_json TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

_init_db()

def load_state(chat_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM user_state WHERE chat_id=?", (str(chat_id),))
    row = cur.fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return {}
    return {}

def save_state(chat_id: str, state: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "REPLACE INTO user_state (chat_id, state_json) VALUES (?, ?)",
        (str(chat_id), json.dumps(state))
    )
    conn.commit()
    conn.close()

def clear_state(chat_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM user_state WHERE chat_id=?", (str(chat_id),))
    conn.commit()
    conn.close()

# -------------------- Telegram Helpers --------------------
DEFAULT_PARSE_MODE = "Markdown"  # keep Markdown; Gemini outputs Markdown already

REQUEST_TIMEOUT = (8, 30)  # (connect, read) seconds

def send_message(chat_id, text, parse_mode=DEFAULT_PARSE_MODE):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        logger.info("✅ Message sent to %s", chat_id)
    except requests.exceptions.RequestException as e:
        logger.error("❌ Failed to send message: %s", e)


def send_document(chat_id, file_data, filename, caption=None):
    url = f"{TELEGRAM_API_URL}/sendDocument"
    files = {'document': (filename, file_data, 'application/pdf')}
    payload = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
    try:
        response = requests.post(url, data=payload, files=files, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        logger.info("✅ Document sent to %s", chat_id)
    except requests.exceptions.RequestException as e:
        logger.error("❌ Failed to send document: %s", e)

# -------------------- Gemini Client --------------------

def call_gemini(prompt: str) -> str | None:
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    headers = {"Content-Type": "application/json"}
    retries = 0
    max_retries = 3
    backoff = 2
    while retries <= max_retries:
        try:
            resp = requests.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                headers=headers,
                data=json.dumps(payload),
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                delay = backoff ** retries
                logger.warning("Gemini rate-limited. Retry in %s s", delay)
                time.sleep(delay)
                retries += 1
                continue
            resp.raise_for_status()
            data = resp.json()
            cand = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text")
            )
            return cand.strip() if cand else None
        except requests.exceptions.RequestException as e:
            logger.error("Gemini API error: %s", e)
            retries += 1
            time.sleep(1.5 * retries)
        except Exception as e:
            logger.exception("Unexpected Gemini error: %s", e)
            return None
    return None

# -------------------- Utilities --------------------

def normalize_language_name(name: str) -> tuple[str, str]:
    # returns (DisplayName, lang_code)
    if not name:
        return ("English", "en")
    key = name.strip().lower()
    code = LANG_CODE_MAP.get(key, 'en')
    display = key.capitalize()
    return (display if display else "English", code)


def get_translated_phrase(language: str, key: str) -> str:
    phrase = PHRASES.get(key, "")
    if not phrase or language.lower() == "english":
        return phrase
    try:
        _, lang_code = normalize_language_name(language)
        translated = translator.translate(phrase, dest=lang_code).text
        return translated
    except Exception as e:
        logger.error("Translation failed for '%s' to '%s': %s", phrase, language, e)
        return phrase


def set_webhook():
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL not set; skipping webhook setup.")
        return
    url = f"{TELEGRAM_API_URL}/setWebhook?url={WEBHOOK_URL}"
    try:
        res = requests.get(url, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        logger.info("🔗 Webhook set: %s", res.json())
    except requests.exceptions.RequestException as e:
        logger.error("❌ Failed to set webhook: %s", e)


def split_message(text: str, chunk_size: int = 3500) -> list[str]:
    parts = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n and text[end] not in (' ', '\n', '\t'):
            last_space = text.rfind(' ', start, end)
            if last_space != -1:
                end = last_space
        chunk = text[start:end].strip()
        if chunk:
            parts.append(chunk)
        start = end
    return parts


def format_bullet_points(text: str) -> str:
    lines = text.split('\n')
    formatted = []
    for line in lines:
        if line.strip().startswith('* '):
            formatted.append('➤ ' + line.strip()[2:])
        else:
            formatted.append(line)
    return '\n'.join(formatted)


def create_pdf_notes(title: str, content: str, language: str) -> BytesIO | None:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    font_path = FONT_MAP.get(language)
    font_name = 'UnicodeFont'

    font_loaded = False
    if font_path and os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_name, font_path))
            styles['Normal'].fontName = font_name
            styles['Heading1'].fontName = font_name
            font_loaded = True
        except Exception as e:
            logger.error("Failed to load font '%s': %s", font_path, e)

    # If language is non-English and font not loaded, we still proceed with a warning
    if language.lower() != 'english' and not font_loaded:
        logger.warning("Font for language '%s' not found. Falling back to default.", language)

    story.append(Paragraph(f"<b>{title}</b>", styles['Heading1']))
    story.append(Spacer(1, 12))

    pdf_content = content.replace('* ', '\n\u2022 ').replace('**', '')
    for line in pdf_content.split('\n'):
        story.append(Paragraph(line, styles['Normal']))
        story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)
    return buffer

# -------------------- Conversation Handlers --------------------

def handle_message(chat_id: str, incoming_msg: str, state: dict):
    text = incoming_msg.strip()

    if text.lower() == "hi edgo":
        send_message(chat_id, get_translated_phrase("English", "welcome"))
        new_state = {"step": STATE_MENU}
        save_state(chat_id, new_state)
        return

    step = state.get("step")

    if step == STATE_MENU:
        handle_menu_selection(chat_id, text)
        return

    if step == STATE_LEARN_TOPIC:
        state["topic"] = text
        send_message(chat_id, get_translated_phrase("English", "language_prompt"))
        state["step"] = STATE_LEARN_LANGUAGE_SELECTION
        save_state(chat_id, state)
        return

    if step == STATE_LEARN_LANGUAGE_SELECTION:
        lang_display, _ = normalize_language_name(text)
        state["language"] = lang_display
        save_state(chat_id, state)
        # Prevent duplicate processing if Gemini is already running
        if state.get("processing"):
            logger.info("Duplicate learn request ignored (already processing).")
            return
        state["processing"] = True
        save_state(chat_id, state)
        send_message(chat_id, get_translated_phrase("English", "search_message"))
        threading.Thread(target=_process_learn_topic, args=(chat_id,), daemon=True).start()
        return

    if step == STATE_POST_LEARN:
        handle_post_learn_request(chat_id, text, state)
        return

    if step == STATE_POST_QUIZ:
        handle_post_quiz_request(chat_id, text, state)
        return

    if step == STATE_MCQ_TOPIC:
        state["topic"] = text
        send_message(chat_id, get_translated_phrase("English", "language_prompt"))
        state["step"] = STATE_MCQ_LANGUAGE_SELECTION
        save_state(chat_id, state)
        return

    if step == STATE_MCQ_LANGUAGE_SELECTION:
        lang_display, _ = normalize_language_name(text)
        state["language"] = lang_display
        save_state(chat_id, state)
        if state.get("processing"):
            logger.info("Duplicate quiz request ignored (already processing).")
            return
        state["processing"] = True
        save_state(chat_id, state)
        send_message(chat_id, get_translated_phrase("English", "quiz_message").format(state.get("topic", "")))
        threading.Thread(target=_process_mcq, args=(chat_id,), daemon=True).start()
        return

    send_message(chat_id, get_translated_phrase("English", "unknown_command"))


def handle_menu_selection(chat_id: str, incoming_msg: str):
    if incoming_msg == "1":
        state = {"step": STATE_LEARN_TOPIC}
        save_state(chat_id, state)
        send_message(chat_id, get_translated_phrase("English", "learn_prompt"))
    elif incoming_msg == "2":
        state = {"step": STATE_MCQ_TOPIC}
        save_state(chat_id, state)
        send_message(chat_id, get_translated_phrase("English", "mcq_prompt"))
    else:
        send_message(chat_id, get_translated_phrase("English", "invalid_option"))

# ---- Background processors to avoid webhook timeouts & duplicate status lines ----

def _process_learn_topic(chat_id: str):
    state = load_state(chat_id)
    topic = state.get("topic")
    language = state.get("language", "English")

    prompt = (
        f"Act as a friendly and knowledgeable tutor for all educational topics. "
        f"Your goal is to simplify and explain the following topic for a student in a simple and clear manner:\n\n"
        f"Topic: {topic}\n\n"
        f"Please provide a detailed, explanation in {language} in simple language using **Markdown bullet points**."
        f"After the main explanation, provide two sections:\n"
        f"1. **Explore More** with links to relevant websites for deeper learning.\n"
        f"2. **Watch and Learn** with links to relevant YouTube videos."
    )

    response = call_gemini(prompt)

    if response:
        state["full_notes"] = response
        formatted = format_bullet_points(response)
        send_message(chat_id, get_translated_phrase("English", "notes_intro").format(topic))
        for chunk in split_message(formatted):
            send_message(chat_id, chunk)
        send_message(chat_id, get_translated_phrase("English", "post_learn_prompt"))
        state["step"] = STATE_POST_LEARN
    else:
        send_message(chat_id, get_translated_phrase("English", "fetch_error"))
        # Reset to menu for smoother UX
        state = {"step": STATE_MENU}

    state.pop("processing", None)
    save_state(chat_id, state)


def _process_mcq(chat_id: str):
    state = load_state(chat_id)
    topic = state.get("topic")
    language = state.get("language", "English")

    prompt = (
        f"Create 5 challenging and insightful multiple-choice questions (MCQs) on the topic: '{topic}' in {language}.\n"
        f"For each question, provide 4 options (A, B, C, D).\n"
        f"Directly after each question, provide the correct answer and a brief, 1-2 line explanation of why it is correct.\n"
        f"Use Markdown to format the questions and answers clearly."
    )

    response = call_gemini(prompt)

    if response:
        send_message(chat_id, get_translated_phrase("English", "quiz_intro"))
        for chunk in split_message(response):
            send_message(chat_id, chunk)
        send_message(chat_id, get_translated_phrase("English", "post_quiz_prompt"))
        state["step"] = STATE_POST_QUIZ
    else:
        send_message(chat_id, get_translated_phrase("English", "quiz_error"))
        state = {"step": STATE_MENU}

    state.pop("processing", None)
    save_state(chat_id, state)


def handle_post_learn_request(chat_id: str, incoming_msg: str, state: dict):
    language = state.get("language", "English")

    pdf_word = get_translated_phrase(language, "pdf_word").lower()
    quiz_word = get_translated_phrase(language, "quiz_word").lower()

    lower = incoming_msg.lower()
    if lower == pdf_word:
        notes_text = state.get("full_notes", "")
        topic = state.get("topic", "notes")
        if notes_text:
            # If non-English and no font, warn but still send basic PDF
            if language.lower() != 'english':
                # Check if our font exists; if not, notify politely
                font_path = FONT_MAP.get(language)
                if not (font_path and os.path.exists(font_path)):
                    send_message(chat_id, get_translated_phrase("English", "pdf_font_error"))
            send_message(chat_id, get_translated_phrase("English", "download_success"))
            pdf_data = create_pdf_notes(topic, notes_text, language)
            if pdf_data:
                send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                              caption=get_translated_phrase("English", "document_caption").format(topic))
            else:
                send_message(chat_id, get_translated_phrase("English", "no_notes"))
        else:
            send_message(chat_id, get_translated_phrase("English", "no_notes"))
        clear_state(chat_id)
        save_state(chat_id, {"step": STATE_MENU})
        return

    if lower == quiz_word:
        # switch to quiz generation flow
        state["step"] = STATE_MCQ_LANGUAGE_SELECTION
        save_state(chat_id, state)
        # directly trigger the MCQ worker (avoid extra status duplication)
        if state.get("processing"):
            return
        state["processing"] = True
        save_state(chat_id, state)
        send_message(chat_id, get_translated_phrase("English", "quiz_message").format(state.get("topic", "")))
        threading.Thread(target=_process_mcq, args=(chat_id,), daemon=True).start()
        return

    send_message(chat_id, "Please reply with 'PDF' or 'Quiz'.")
    # keep them in POST_LEARN state for retry
    save_state(chat_id, state)


def handle_post_quiz_request(chat_id: str, incoming_msg: str, state: dict):
    language = state.get("language", "English")
    yes_word = get_translated_phrase(language, "yes_word").lower()

    if incoming_msg.lower() == yes_word:
        notes_text = state.get("full_notes", "")
        topic = state.get("topic", "notes")
        if notes_text:
            if language.lower() != 'english':
                font_path = FONT_MAP.get(language)
                if not (font_path and os.path.exists(font_path)):
                    send_message(chat_id, get_translated_phrase("English", "pdf_font_error"))
            send_message(chat_id, get_translated_phrase("English", "download_success"))
            pdf_data = create_pdf_notes(topic, notes_text, language)
            if pdf_data:
                send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                              caption=get_translated_phrase("English", "document_caption").format(topic))
            else:
                send_message(chat_id, get_translated_phrase("English", "no_notes"))
        else:
            send_message(chat_id, get_translated_phrase("English", "no_notes"))
    else:
        send_message(chat_id, get_translated_phrase("English", "end_conversation"))

    clear_state(chat_id)
    save_state(chat_id, {"step": STATE_MENU})

# -------------------- Routes --------------------

@app.route("/")
def home():
    return "🚀 Edgo Telegram bot is running!"

@app.route("/webhook", methods=["POST"]) 
def telegram_webhook():
    chat_id = None
    try:
        data = request.get_json(force=True, silent=True) or {}
        logger.info("📩 Incoming: %s", json.dumps(data, indent=2))

        message = data.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text")

        if not chat_id or not isinstance(text, str):
            logger.warning("Invalid update payload; no chat_id or text.")
            return "ok"

        # Load current state from SQLite
        state = load_state(chat_id)
        if not state:
            state = {"step": STATE_MENU}
            save_state(chat_id, state)

        handle_message(chat_id, text, state)

    except Exception as e:
        logger.exception("Webhook processing error: %s", e)
        if chat_id:
            send_message(chat_id, get_translated_phrase("English", "unknown_error"))
    return "ok"

# -------------------- Startup --------------------

if __name__ == "__main__":
    if WEBHOOK_URL:
        set_webhook()
    else:
        logger.warning("WEBHOOK_URL not set. Configure it to receive Telegram updates.")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port) 
