# app.py
# The main application file for the Telegram bot, with all functions consolidated.

# To resolve the 'reportlab' error, make sure you have a requirements.txt file
# in your project's root directory that includes the following lines:
# reportlab
# googletrans

from flask import Flask, request
from dotenv import load_dotenv
import os
import json
import logging
import requests
import time
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from googletrans import Translator

# Set up logging for better debugging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

load_dotenv()

# Global state for managing conversations
user_state = {}

# Load API keys from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent"

# Constants for conversation states
STATE_MENU = "menu"
STATE_LEARN_TOPIC = "learn_topic"
STATE_MCQ_TOPIC = "mcq_topic"
STATE_LEARN_LANGUAGE_SELECTION = "learn_language_selection"
STATE_MCQ_LANGUAGE_SELECTION = "mcq_language_selection"
STATE_POST_LEARN = "post_learn"
STATE_POST_QUIZ = "post_quiz"

# Static phrases to be translated
PHRASES = {
    "welcome": "Hi! 👋 What would you like help with today?\n\n*Reply with a number:*\n1️⃣ Learn about a topic\n2️⃣ Test your knowledge with MCQs",
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
    "unknown_command": "I'm not sure what you mean. Say '/start' to get started. 😊",
    "pdf_word": "pdf",
    "quiz_word": "quiz",
    "yes_word": "yes",
    "end_conversation": "Okay, let me know if you need anything else! 😊",
    "pdf_font_error": "❌ I couldn't generate the PDF because the required font file for your language could not be found. Please ensure you have a font file that supports your language (e.g., 'NotoSans-Regular.ttf') in the same directory as the bot script."
}

# Initialize a global translator instance
translator = Translator()

# Map language names to their respective font file paths
# NOTE: The font files must be available in these exact locations.
FONT_MAP = {
    'Hindi': 'languages/hindi/Hindi.ttf',
    'Telugu': 'languages/telugu/NotoSans-Telugu-Regular.ttf',
    'Kannada': 'languages/kannada/Kannada.ttf',
    'Tamil': 'languages/tamil/Tamil.ttf',
}

# -------------------- API Client Functions --------------------

def send_message(chat_id, text, parse_mode="Markdown"):
    """Sends a message to a specific Telegram chat ID."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info("✅ Message sent successfully to chat ID: %s", chat_id)
    except requests.exceptions.RequestException as e:
        logging.error("❌ Failed to send message to Telegram: %s", e)

def send_document(chat_id, file_data, filename, caption=None):
    """Sends a document (e.g., PDF) to a specific Telegram chat ID."""
    url = f"{TELEGRAM_API_URL}/sendDocument"
    files = {
        'document': (filename, file_data, 'application/pdf')
    }
    payload = {
        "chat_id": chat_id,
        "caption": caption
    }
    try:
        response = requests.post(url, data=payload, files=files)
        response.raise_for_status()
        logging.info("✅ Document sent successfully to chat ID: %s", chat_id)
    except requests.exceptions.RequestException as e:
        logging.error("❌ Failed to send document to Telegram: %s", e)

def call_gemini(prompt):
    """
    Calls the Gemini API with exponential backoff for retries.
    Returns the generated text or None on failure.
    """
    try:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]
        }
        headers = {"Content-Type": "application/json"}
        
        retries = 0
        max_retries = 3
        while retries < max_retries:
            response = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", headers=headers, data=json.dumps(payload))
            if response.status_code == 429:  # Too Many Requests
                delay = 2**retries
                logging.warning("Rate limit exceeded. Retrying in %d seconds...", delay)
                time.sleep(delay)
                retries += 1
            else:
                response.raise_for_status()
                break

        if retries == max_retries:
            logging.error("❌ Max retries reached. Giving up.")
            return None

        result = response.json()
        if "candidates" in result and len(result["candidates"]) > 0 and \
           "content" in result["candidates"][0] and "parts" in result["candidates"][0]["content"] and \
           len(result["candidates"][0]["content"]["parts"]) > 0:
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            logging.error("❌ Gemini API response was not in the expected format: %s", result)
            return None
    except Exception as e:
        logging.error("❌ Gemini API error: %s", e, exc_info=True)
        return None


# -------------------- Utility Functions --------------------

def get_translated_phrase(language, key):
    """
    Translates a key's phrase to the specified language.
    If translation fails or is for English, returns the original phrase.
    """
    phrase = PHRASES.get(key, "")
    if not phrase or language.lower() == "english":
        return phrase
    
    try:
        translated = translator.translate(phrase, dest=language).text
        return translated
    except Exception as e:
        logging.error(f"Translation failed for '{phrase}' to '{language}': {e}")
        return phrase

def set_webhook():
    """Sets the Telegram webhook for the bot."""
    webhook_url = os.getenv("WEBHOOK_URL")
    url = f"{TELEGRAM_API_URL}/setWebhook?url={webhook_url}"
    try:
        res = requests.get(url)
        res.raise_for_status()
        logging.info("🔗 Webhook set successfully: %s", res.json())
    except requests.exceptions.RequestException as e:
        logging.error("❌ Failed to set webhook: %s", e)

def split_message(text, chunk_size=1400):
    """
    Splits a long message into smaller chunks for Telegram, ensuring words are
    not broken across chunks.
    """
    parts = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text) and text[end] not in (' ', '\n', '\t'):
            last_space = text.rfind(' ', start, end)
            if last_space != -1:
                end = last_space
            else:
                pass
        
        chunk = text[start:end].strip()
        if chunk:
            parts.append(chunk)
        start = end
        
    return parts

def format_bullet_points(text):
    """
    Ensures bullet points are consistently formatted with a hyphen for better
    display on different chat clients.
    """
    lines = text.split('\n')
    formatted_lines = []
    for line in lines:
        if line.strip().startswith('* '):
            formatted_lines.append('➤ ' + line.strip()[2:])
        else:
            formatted_lines.append(line)
    return '\n'.join(formatted_lines)

def create_pdf_notes(title, content, language):
    """
    Generates a PDF file from the provided title and content, using a
    language-specific font for correct rendering.
    Returns the file data as a BytesIO object.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Get the correct font file path based on the user's selected language
    font_path = FONT_MAP.get(language, 'Vera.ttf')  # Fallback to Vera.ttf if language not mapped
    font_name = 'UnicodeFont' # A generic name for the registered font

    try:
        pdfmetrics.registerFont(TTFont(font_name, font_path))
        styles['Normal'].fontName = font_name
        styles['Heading1'].fontName = font_name
    except Exception as e:
        logging.error(f"Failed to find or load font file '{font_path}'. Error: {e}")
        return None

    story.append(Paragraph(f"<b>{title}</b>", styles['Heading1']))
    story.append(Spacer(1, 12))
    
    pdf_content = content.replace('* ', '\n\u2022 ').replace('**', '')
    for line in pdf_content.split('\n'):
        story.append(Paragraph(line, styles['Normal']))
        story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)
    return buffer


# -------------------- Handler Functions --------------------

def handle_message(chat_id, incoming_msg, state, user_state):
    """
    Main handler function that routes messages based on the user's state.
    """
    if incoming_msg.lower() == "/start":
        send_message(chat_id, get_translated_phrase("English", "welcome"))
        user_state[chat_id] = {"step": STATE_MENU}
        return

    if state.get("step") == STATE_MENU:
        handle_menu_selection(chat_id, incoming_msg, user_state)
        return

    if state.get("step") == STATE_LEARN_TOPIC:
        user_state[chat_id]["topic"] = incoming_msg.strip()
        send_message(chat_id, get_translated_phrase("English", "language_prompt"))
        user_state[chat_id]["step"] = STATE_LEARN_LANGUAGE_SELECTION
        return
        
    elif state.get("step") == STATE_LEARN_LANGUAGE_SELECTION:
        language = incoming_msg.strip().capitalize()
        user_state[chat_id]["language"] = language
        handle_learn_topic_request(chat_id, user_state, state)
        return
    
    elif state.get("step") == STATE_POST_LEARN:
        handle_post_learn_request(chat_id, incoming_msg, user_state, state)
        return

    elif state.get("step") == STATE_POST_QUIZ:
        handle_post_quiz_request(chat_id, incoming_msg, user_state, state)
        return

    elif state.get("step") == STATE_MCQ_TOPIC:
        user_state[chat_id]["topic"] = incoming_msg.strip()
        send_message(chat_id, get_translated_phrase("English", "language_prompt"))
        user_state[chat_id]["step"] = STATE_MCQ_LANGUAGE_SELECTION
        return

    elif state.get("step") == STATE_MCQ_LANGUAGE_SELECTION:
        language = incoming_msg.strip().capitalize()
        user_state[chat_id]["language"] = language
        handle_mcq_request(chat_id, user_state, state)
        return

    else:
        send_message(chat_id, get_translated_phrase("English", "unknown_command"))

def handle_menu_selection(chat_id, incoming_msg, user_state):
    """Handles the user's choice from the main menu."""
    if incoming_msg == "1":
        user_state[chat_id]["step"] = STATE_LEARN_TOPIC
        send_message(chat_id, get_translated_phrase("English", "learn_prompt"))
    elif incoming_msg == "2":
        user_state[chat_id]["step"] = STATE_MCQ_TOPIC
        send_message(chat_id, get_translated_phrase("English", "mcq_prompt"))
    else:
        send_message(chat_id, get_translated_phrase("English", "invalid_option"))

def handle_learn_topic_request(chat_id, user_state, state):
    """
    Generates a detailed explanation with external resources
    and prompts the user for a downloadable file.
    """
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
    
    send_message(chat_id, get_translated_phrase("English", "search_message"))
    response = call_gemini(prompt)
    
    if response:
        state["full_notes"] = response
        formatted_response = format_bullet_points(response)
        send_message(chat_id, get_translated_phrase("English", "notes_intro").format(topic))
        for chunk in split_message(formatted_response):
            send_message(chat_id, chunk)

        send_message(chat_id, get_translated_phrase("English", "post_learn_prompt"))
        user_state[chat_id]["step"] = STATE_POST_LEARN
    else:
        send_message(chat_id, get_translated_phrase("English", "fetch_error"))
        user_state.pop(chat_id, None)

def handle_post_learn_request(chat_id, incoming_msg, user_state, state):
    """Handles the user's request for either a PDF or an MCQ quiz."""
    language = state.get("language", "English")
    
    # Check for translated versions of "PDF" and "Quiz"
    pdf_word = get_translated_phrase(language, "pdf_word").lower()
    quiz_word = get_translated_phrase(language, "quiz_word").lower()
    
    if incoming_msg.lower() == pdf_word:
        notes_text = state.get("full_notes", "")
        topic = state.get("topic", "notes")
        
        if notes_text:
            send_message(chat_id, get_translated_phrase("English", "download_success"))
            pdf_data = create_pdf_notes(topic, notes_text, language)
            if pdf_data:
                send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                              caption=get_translated_phrase("English", "document_caption").format(topic))
            else:
                send_message(chat_id, get_translated_phrase("English", "pdf_font_error"))
        else:
            send_message(chat_id, get_translated_phrase("English", "no_notes"))
        
        user_state.pop(chat_id, None)

    elif incoming_msg.lower() == quiz_word:
        handle_mcq_request(chat_id, user_state, state)

    else:
        send_message(chat_id, "Please reply with 'PDF' or 'Quiz'.")
        user_state.pop(chat_id, None)

def handle_post_quiz_request(chat_id, incoming_msg, user_state, state):
    """Handles the user's request for a PDF after completing the quiz."""
    language = state.get("language", "English")
    yes_word = get_translated_phrase(language, "yes_word").lower()

    if incoming_msg.lower() == yes_word:
        notes_text = state.get("full_notes", "")
        topic = state.get("topic", "notes")
        
        if notes_text:
            send_message(chat_id, get_translated_phrase("English", "download_success"))
            pdf_data = create_pdf_notes(topic, notes_text, language)
            if pdf_data:
                send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                              caption=get_translated_phrase("English", "document_caption").format(topic))
            else:
                send_message(chat_id, get_translated_phrase("English", "pdf_font_error"))
        else:
            send_message(chat_id, get_translated_phrase("English", "no_notes"))
    else:
        send_message(chat_id, get_translated_phrase("English", "end_conversation"))
    
    user_state.pop(chat_id, None)

def handle_mcq_request(chat_id, user_state, state):
    """Generates insightful MCQs and sends the solution, then prompts for PDF."""
    topic = state.get("topic")
    language = state.get("language", "English")

    prompt = (
        f"Create 5 challenging and insightful multiple-choice questions (MCQs) on the topic: '{topic}' in {language}.\n"
        f"For each question, provide 4 options (A, B, C, D).\n"
        f"Directly after each question, provide the correct answer and a brief, 1-2 line explanation of why it is correct.\n"
        f"Use Markdown to format the questions and answers clearly."
    )
    send_message(chat_id, get_translated_phrase("English", "quiz_message").format(topic))
    response = call_gemini(prompt)
    if response:
        send_message(chat_id, get_translated_phrase("English", "quiz_intro"))
        for chunk in split_message(response):
            send_message(chat_id, chunk)
        
        send_message(chat_id, get_translated_phrase("English", "post_quiz_prompt"))
        user_state[chat_id]["step"] = STATE_POST_QUIZ
    else:
        send_message(chat_id, get_translated_phrase("English", "quiz_error"))
        user_state.pop(chat_id, None)


# -------------------- Routes --------------------

@app.route("/")
def home():
    """A simple home route to check if the bot is running."""
    return "🚀 Edgo Telegram bot is running!"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Handles all incoming messages from Telegram."""
    try:
        data = request.get_json()
        logging.info("📩 Incoming message: %s", json.dumps(data, indent=2))

        if not data or "message" not in data or "text" not in data["message"]:
            logging.warning("Received invalid message data.")
            return "ok"

        chat_id = data["message"]["chat"]["id"]
        incoming_msg = data["message"]["text"].strip()
        state = user_state.get(chat_id, {})

        handle_message(chat_id, incoming_msg, state, user_state)

    except Exception as e:
        logging.error("An error occurred during webhook processing: %s", e, exc_info=True)
        send_message(chat_id, get_translated_phrase("English", "unknown_error"))

    return "ok"


# -------------------- Startup --------------------

if __name__ == "__main__":
    if os.getenv("WEBHOOK_URL"):
        set_webhook()
    else:
        logging.error("WEBHOOK_URL environment variable is not set. Webhook will not be configured.")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
