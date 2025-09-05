# app.py
# The main application file for the Telegram bot, with all functions consolidated.
# Now with concurrent user handling and improved error management.

# To resolve the 'reportlab' error, make sure you have a requirements.txt file
# in your project's root directory that includes the following lines:
# reportlab
# googletrans
# concurrent.futures

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
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from functools import wraps

# Set up logging for better debugging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

load_dotenv()

# Global state for managing conversations with thread safety
user_state = {}
user_state_lock = threading.Lock()

# Thread pool for handling concurrent users
executor = ThreadPoolExecutor(max_workers=20)

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
    "getting_content_message": "🔍 Getting content for '{}'...\n⏳ This might take a moment, please wait!",
    "search_message": "Finding and explaining the topic for you... ⏳",
    "notes_intro": "📘 Here's the explanation of '{}':",
    "post_learn_prompt": "Would you like a downloadable PDF of these notes or a quiz to test your knowledge?\n\nReply with 'PDF' or 'Quiz'.",
    "post_quiz_prompt": "Would you like a downloadable PDF of the notes? Reply with 'Yes' to get them.",
    "download_success": "Generating your notes as a PDF... 📄",
    "document_caption": "Here are your downloadable notes for {}!",
    "no_notes": "❌ I'm sorry, I couldn't find the notes to download.",
    "quiz_getting_content": "🧠 Generating quiz questions for '{}'...\n⏳ This might take a moment, please wait!",
    "quiz_message": "Generating an insightful quiz on '{}'... 🤔",
    "quiz_intro": "🧠 Here's your quiz:",
    "quiz_error": "❌ Couldn't generate the MCQs. Try again later.",
    "fetch_error": "❌ Couldn't fetch learning content right now.",
    "unknown_error": "❌ Something went wrong! Please say 'hi edgo' to start again.",
    "unknown_command": "I'm not sure what you mean. Please say 'hi edgo' to get the main menu.",
    "pdf_word": "pdf",
    "quiz_word": "quiz",
    "yes_word": "yes",
    "end_conversation": "Okay, let me know if you need anything else! 😊",
    "pdf_font_error": "❌ I couldn't generate the PDF because the required font file for your language could not be found. Please ensure you have a font file that supports your language (e.g., 'NotoSans-Regular.ttf') in the same directory as the bot script.",
    "connection_error": "❌ Something went wrong! Please say 'hi edgo' to start again."
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

# -------------------- Error Handling Decorators --------------------

def safe_user_operation(func):
    """Decorator to safely handle user operations and clean up on errors."""
    @wraps(func)
    def wrapper(chat_id, *args, **kwargs):
        try:
            return func(chat_id, *args, **kwargs)
        except Exception as e:
            logging.error(f"Error in {func.__name__} for user {chat_id}: {e}", exc_info=True)
            # Clean up user state on error
            cleanup_user_state(chat_id)
            # Send error message to user
            send_message(chat_id, get_translated_phrase("English", "connection_error"))
            return None
    return wrapper

def cleanup_user_state(chat_id):
    """Safely remove user state with thread safety."""
    with user_state_lock:
        user_state.pop(chat_id, None)
    logging.info(f"Cleaned up state for user {chat_id}")

def get_user_state(chat_id):
    """Safely get user state with thread safety."""
    with user_state_lock:
        return user_state.get(chat_id, {}).copy()

def set_user_state(chat_id, state):
    """Safely set user state with thread safety."""
    with user_state_lock:
        user_state[chat_id] = state

def update_user_state(chat_id, updates):
    """Safely update specific fields in user state."""
    with user_state_lock:
        if chat_id not in user_state:
            user_state[chat_id] = {}
        user_state[chat_id].update(updates)

# -------------------- API Client Functions --------------------

def send_message(chat_id, text, parse_mode="Markdown"):
    """Sends a message to a specific Telegram chat ID with error handling."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info("✅ Message sent successfully to chat ID: %s", chat_id)
        return True
    except requests.exceptions.RequestException as e:
        logging.error("❌ Failed to send message to Telegram for user %s: %s", chat_id, e)
        return False

def send_document(chat_id, file_data, filename, caption=None):
    """Sends a document (e.g., PDF) to a specific Telegram chat ID with error handling."""
    url = f"{TELEGRAM_API_URL}/sendDocument"
    files = {
        'document': (filename, file_data, 'application/pdf')
    }
    payload = {
        "chat_id": chat_id,
        "caption": caption
    }
    try:
        response = requests.post(url, data=payload, files=files, timeout=30)
        response.raise_for_status()
        logging.info("✅ Document sent successfully to chat ID: %s", chat_id)
        return True
    except requests.exceptions.RequestException as e:
        logging.error("❌ Failed to send document to Telegram for user %s: %s", chat_id, e)
        return False

def call_gemini(prompt, timeout=30):
    """
    Calls the Gemini API with exponential backoff for retries and timeout.
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
            response = requests.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", 
                headers=headers, 
                data=json.dumps(payload),
                timeout=timeout
            )
            if response.status_code == 429:  # Too Many Requests
                delay = min(2**retries, 10)  # Cap delay at 10 seconds
                logging.warning("Rate limit exceeded. Retrying in %d seconds...", delay)
                time.sleep(delay)
                retries += 1
            else:
                response.raise_for_status()
                break

        if retries == max_retries:
            logging.error("❌ Max retries reached for Gemini API. Giving up.")
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
    Translates a key's phrase to the specified language with error handling.
    If translation fails or is for English, returns the original phrase.
    """
    phrase = PHRASES.get(key, "")
    if not phrase or language.lower() == "english":
        return phrase
    
    try:
        translated = translator.translate(phrase, dest=language, timeout=10).text
        return translated
    except Exception as e:
        logging.error(f"Translation failed for '{phrase}' to '{language}': {e}")
        return phrase

def set_webhook():
    """Sets the Telegram webhook for the bot."""
    webhook_url = os.getenv("WEBHOOK_URL")
    url = f"{TELEGRAM_API_URL}/setWebhook?url={webhook_url}"
    try:
        res = requests.get(url, timeout=10)
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
    try:
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
            if line.strip():
                story.append(Paragraph(line, styles['Normal']))
                story.append(Spacer(1, 6))

        doc.build(story)
        buffer.seek(0)
        return buffer
    except Exception as e:
        logging.error(f"Error creating PDF: {e}")
        return None


# -------------------- Handler Functions --------------------

@safe_user_operation
def handle_message(chat_id, incoming_msg, state, user_state_dict):
    """
    Main handler function that routes messages based on the user's state.
    """
    # Check for specific trigger phrases first, regardless of current state
    if incoming_msg.lower() == "/start" or incoming_msg.lower() == "hi edgo":
        send_message(chat_id, get_translated_phrase("English", "welcome"))
        set_user_state(chat_id, {"step": STATE_MENU})
        return

    if state.get("step") == STATE_MENU:
        handle_menu_selection(chat_id, incoming_msg)
        return

    if state.get("step") == STATE_LEARN_TOPIC:
        topic = incoming_msg.strip()
        update_user_state(chat_id, {"topic": topic})
        # Send immediate feedback message
        send_message(chat_id, get_translated_phrase("English", "getting_content_message").format(topic))
        send_message(chat_id, get_translated_phrase("English", "language_prompt"))
        update_user_state(chat_id, {"step": STATE_LEARN_LANGUAGE_SELECTION})
        return
        
    elif state.get("step") == STATE_LEARN_LANGUAGE_SELECTION:
        language = incoming_msg.strip().capitalize()
        update_user_state(chat_id, {"language": language})
        handle_learn_topic_request(chat_id)
        return
    
    elif state.get("step") == STATE_POST_LEARN:
        handle_post_learn_request(chat_id, incoming_msg)
        return

    elif state.get("step") == STATE_POST_QUIZ:
        handle_post_quiz_request(chat_id, incoming_msg)
        return

    elif state.get("step") == STATE_MCQ_TOPIC:
        topic = incoming_msg.strip()
        update_user_state(chat_id, {"topic": topic})
        # Send immediate feedback message for quiz
        send_message(chat_id, get_translated_phrase("English", "quiz_getting_content").format(topic))
        send_message(chat_id, get_translated_phrase("English", "language_prompt"))
        update_user_state(chat_id, {"step": STATE_MCQ_LANGUAGE_SELECTION})
        return

    elif state.get("step") == STATE_MCQ_LANGUAGE_SELECTION:
        language = incoming_msg.strip().capitalize()
        update_user_state(chat_id, {"language": language})
        handle_mcq_request(chat_id)
        return

    else:
        send_message(chat_id, get_translated_phrase("English", "unknown_command"))
        return

@safe_user_operation
def handle_menu_selection(chat_id, incoming_msg):
    """Handles the user's choice from the main menu."""
    if incoming_msg == "1":
        update_user_state(chat_id, {"step": STATE_LEARN_TOPIC})
        send_message(chat_id, get_translated_phrase("English", "learn_prompt"))
    elif incoming_msg == "2":
        update_user_state(chat_id, {"step": STATE_MCQ_TOPIC})
        send_message(chat_id, get_translated_phrase("English", "mcq_prompt"))
    else:
        send_message(chat_id, get_translated_phrase("English", "invalid_option"))

@safe_user_operation
def handle_learn_topic_request(chat_id):
    """
    Generates a detailed explanation with external resources
    and prompts the user for a downloadable file.
    """
    state = get_user_state(chat_id)
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
    
    # Send processing message
    send_message(chat_id, "🔄 Processing your request...")
    response = call_gemini(prompt)
    
    if response:
        update_user_state(chat_id, {"full_notes": response})
        formatted_response = format_bullet_points(response)
        send_message(chat_id, get_translated_phrase("English", "notes_intro").format(topic))
        
        # Send message chunks
        for chunk in split_message(formatted_response):
            if not send_message(chat_id, chunk):
                # If message sending fails, cleanup and return
                cleanup_user_state(chat_id)
                return

        send_message(chat_id, get_translated_phrase("English", "post_learn_prompt"))
        update_user_state(chat_id, {"step": STATE_POST_LEARN})
    else:
        send_message(chat_id, get_translated_phrase("English", "fetch_error"))
        cleanup_user_state(chat_id)

@safe_user_operation
def handle_post_learn_request(chat_id, incoming_msg):
    """Handles the user's request for either a PDF or an MCQ quiz."""
    state = get_user_state(chat_id)
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
        
        cleanup_user_state(chat_id)

    elif incoming_msg.lower() == quiz_word:
        handle_mcq_request(chat_id)

    else:
        send_message(chat_id, "Please reply with 'PDF' or 'Quiz'.")
        cleanup_user_state(chat_id)

@safe_user_operation
def handle_post_quiz_request(chat_id, incoming_msg):
    """Handles the user's request for a PDF after completing the quiz."""
    state = get_user_state(chat_id)
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
    
    cleanup_user_state(chat_id)

@safe_user_operation
def handle_mcq_request(chat_id):
    """Generates insightful MCQs and sends the solution, then prompts for PDF."""
    state = get_user_state(chat_id)
    topic = state.get("topic")
    language = state.get("language", "English")

    prompt = (
        f"Create 5 challenging and insightful multiple-choice questions (MCQs) on the topic: '{topic}' in {language}.\n"
        f"For each question, provide 4 options (A, B, C, D).\n"
        f"Directly after each question, provide the correct answer and a brief, 1-2 line explanation of why it is correct.\n"
        f"Use Markdown to format the questions and answers clearly."
    )
    
    # Send processing message
    send_message(chat_id, "🔄 Processing your quiz...")
    response = call_gemini(prompt)
    
    if response:
        send_message(chat_id, get_translated_phrase("English", "quiz_intro"))
        
        # Send message chunks
        for chunk in split_message(response):
            if not send_message(chat_id, chunk):
                # If message sending fails, cleanup and return
                cleanup_user_state(chat_id)
                return
        
        send_message(chat_id, get_translated_phrase("English", "post_quiz_prompt"))
        update_user_state(chat_id, {"step": STATE_POST_QUIZ})
    else:
        send_message(chat_id, get_translated_phrase("English", "quiz_error"))
        cleanup_user_state(chat_id)


# -------------------- Concurrent Processing --------------------

def process_user_message(chat_id, incoming_msg):
    """Process a single user's message in a separate thread."""
    try:
        state = get_user_state(chat_id)
        handle_message(chat_id, incoming_msg, state, user_state)
    except Exception as e:
        logging.error(f"Error processing message for user {chat_id}: {e}", exc_info=True)
        cleanup_user_state(chat_id)
        send_message(chat_id, get_translated_phrase("English", "unknown_error"))


# -------------------- Routes --------------------

@app.route("/")
def home():
    """A simple home route to check if the bot is running."""
    return "🚀 Edgo Telegram bot is running! Handling up to 20 concurrent users."

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Handles all incoming messages from Telegram with concurrent processing."""
    try:
        data = request.get_json()
        logging.info("📩 Incoming message received")

        if not data or "message" not in data or "text" not in data["message"]:
            logging.warning("Received invalid message data.")
            return "ok"

        chat_id = data["message"]["chat"]["id"]
        incoming_msg = data["message"]["text"].strip()

        # Submit the message processing to the thread pool for concurrent handling
        future = executor.submit(process_user_message, chat_id, incoming_msg)
        
        # Don't wait for completion - return immediately to handle more requests
        logging.info(f"Message from user {chat_id} submitted to thread pool")

    except Exception as e:
        logging.error("An error occurred during webhook processing: %s", e, exc_info=True)
        # Try to send error message if we have chat_id
        try:
            if 'chat_id' in locals():
                cleanup_user_state(chat_id)
                send_message(chat_id, get_translated_phrase("English", "unknown_error"))
        except:
            pass  # Silently fail if we can't even send error message

    return "ok"

@app.route("/health")
def health_check():
    """Health check endpoint to monitor bot status."""
    try:
        # Check if we can make a basic API call
        url = f"{TELEGRAM_API_URL}/getMe"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        
        active_users = len(user_state)
        return {
            "status": "healthy",
            "active_users": active_users,
            "max_workers": 20
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }, 500


# -------------------- Startup --------------------

if __name__ == "__main__":
    try:
        if os.getenv("WEBHOOK_URL"):
            set_webhook()
        else:
            logging.error("WEBHOOK_URL environment variable is not set. Webhook will not be configured.")

        port = int(os.environ.get("PORT", 5000))
        logging.info(f"🚀 Starting Edgo bot with concurrent handling for 20 users on port {port}")
        app.run(host="0.0.0.0", port=port, threaded=True)
    except Exception as e:
        logging.error(f"Failed to start the application: {e}", exc_info=True)
    finally:
        # Cleanup thread pool on shutdown
        executor.shutdown(wait=True)