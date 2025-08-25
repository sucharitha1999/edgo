# app.py
# The main application file for the Telegram bot, with all functions consolidated.

# To resolve the 'reportlab' error, make sure you have a requirements.txt file
# in your project's root directory that includes the following line:
# reportlab

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
STATE_INITIAL_LANGUAGE_SELECTION = "initial_language_selection"
STATE_MENU = "menu"
STATE_LEARN_TOPIC = "learn_topic"
STATE_LEARN_DOWNLOAD = "learn_download"
STATE_MCQ_TOPIC = "mcq_topic"


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

def translate_text(text, target_language):
    """
    Translates text to the target language using the Gemini API.
    """
    prompt = f"Translate the following text to {target_language}. Just provide the translated text, nothing else.\n\nText: {text}"
    translated = call_gemini(prompt)
    return translated if translated else text

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
            # Find the last space or newline before the chunk end
            last_space = text.rfind(' ', start, end)
            if last_space != -1:
                end = last_space
            else:
                # If no space is found, break at the chunk size anyway
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
    # Replace markdown list items starting with * with a hyphen
    lines = text.split('\n')
    formatted_lines = []
    for line in lines:
        if line.strip().startswith('* '):
            # Change the prefix to a right-pointing arrow
            formatted_lines.append('➤ ' + line.strip()[2:])
        else:
            formatted_lines.append(line)
    return '\n'.join(formatted_lines)

def find_font_path(font_name="NotoSans-Regular.ttf"):
    """
    Tries to find the font file in the current directory.
    Returns the full path if found, otherwise returns None.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(current_dir, font_name)
    if os.path.exists(font_path):
        return font_path
    return None

def create_pdf_notes(title, content):
    """
    Generates a PDF file from the provided title and content.
    Returns the file data as a BytesIO object.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Attempt to register a Unicode-supporting font
    font_name = "NotoSans-Regular.ttf"
    font_path = find_font_path(font_name)
    if font_path:
        pdfmetrics.registerFont(TTFont('NotoSans', font_path))
        styles['Normal'].fontName = 'NotoSans'
        styles['Heading1'].fontName = 'NotoSans'
    else:
        logging.warning(f"Font file '{font_name}' not found. PDF may not display non-Latin characters correctly.")

    story.append(Paragraph(f"<b>{title}</b>", styles['Heading1']))
    story.append(Spacer(1, 12))
    
    # Replace markdown bullet points with a standard Unicode character
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
    if incoming_msg.lower() == "hi edgo":
        welcome_text = "Hi! 👋 To get started, please tell me your preferred language (e.g., English, Hindi, Spanish)."
        send_message(chat_id, welcome_text)
        user_state[chat_id] = {"step": STATE_INITIAL_LANGUAGE_SELECTION}
        return

    language = user_state.get(chat_id, {}).get("language", "English")

    if state.get("step") == STATE_INITIAL_LANGUAGE_SELECTION:
        user_state[chat_id]["language"] = incoming_msg.strip().capitalize()
        language = user_state[chat_id]["language"]
        
        menu_intro_en = "Great! I will provide all explanations in {}.\n\nWhat would you like help with today?\n\n*Reply with a number:*"
        menu_option_1_en = "1️⃣ Learn about a topic"
        menu_option_2_en = "2️⃣ Test your knowledge with MCQs"
        
        menu_intro_translated = translate_text(menu_intro_en.format(language), language)
        menu_option_1_translated = translate_text(menu_option_1_en, language)
        menu_option_2_translated = translate_text(menu_option_2_en, language)
        
        menu_text = (
            f"{menu_intro_translated}\n\n"
            f"{menu_option_1_translated}\n"
            f"{menu_option_2_translated}"
        )
        send_message(chat_id, menu_text)
        user_state[chat_id]["step"] = STATE_MENU
        return

    if state.get("step") == STATE_MENU:
        handle_menu_selection(chat_id, incoming_msg, user_state)
        return

    if state.get("step") == STATE_LEARN_TOPIC:
        handle_learn_topic_request(chat_id, incoming_msg, user_state, state)
        
    elif state.get("step") == STATE_LEARN_DOWNLOAD:
        handle_learn_download_request(chat_id, incoming_msg, user_state, state)

    elif state.get("step") == STATE_MCQ_TOPIC:
        handle_mcq_request(chat_id, incoming_msg, user_state)
    
    else:
        unknown_command_text = translate_text("I'm not sure what you mean. Say 'hi edgo' to get started. 😊", language)
        send_message(chat_id, unknown_command_text)

def handle_menu_selection(chat_id, incoming_msg, user_state):
    """Handles the user's choice from the main menu."""
    language = user_state.get(chat_id, {}).get("language", "English")
    if incoming_msg == "1":
        user_state[chat_id]["step"] = STATE_LEARN_TOPIC
        learn_prompt = translate_text("What topic would you like to learn about?", language)
        send_message(chat_id, f"📚 {learn_prompt}")
    elif incoming_msg == "2":
        user_state[chat_id]["step"] = STATE_MCQ_TOPIC
        mcq_prompt = translate_text("What topic would you like a quiz on?", language)
        send_message(chat_id, f"📝 {mcq_prompt}")
    else:
        invalid_option_text = translate_text("Please enter a valid option: 1 or 2.", language)
        send_message(chat_id, invalid_option_text)

def handle_learn_topic_request(chat_id, incoming_msg, user_state, state):
    """
    Generates a detailed explanation with external resources
    and prompts the user for a downloadable file.
    """
    topic = incoming_msg
    language = state.get("language", "English")
    state["topic"] = topic
    
    prompt = (
        f"Act as a friendly and knowledgeable tutor for all educational topics. "
        f"Your goal is to simplify and explain the following topic for a student in a simple and clear manner:\n\n"
        f"Topic: {topic}\n\n"
        f"Please provide a detailed, explanation in {language} in simple language using **Markdown bullet points**."
        f"After the main explanation, provide two sections:\n"
        f"1. **Explore More** with links to relevant websites for deeper learning.\n"
        f"2. **Watch and Learn** with links to relevant YouTube videos."
    )
    
    search_message = translate_text("Finding and explaining the topic for you... ⏳", language)
    send_message(chat_id, search_message)
    response = call_gemini(prompt)
    
    if response:
        state["full_notes"] = response
        formatted_response = format_bullet_points(response)
        notes_intro_translated = translate_text("Here's the explanation of '{}':", language).format(topic)
        send_message(chat_id, f"📘 {notes_intro_translated}")
        for chunk in split_message(formatted_response):
            send_message(chat_id, chunk)

        download_prompt = translate_text("Would you like to get a downloadable PDF of these notes?\nReply with 'Yes' to get them.", language)
        send_message(chat_id, download_prompt)
        user_state[chat_id]["step"] = STATE_LEARN_DOWNLOAD
    else:
        fetch_error = translate_text("Couldn't fetch learning content right now.", language)
        send_message(chat_id, f"❌ {fetch_error}")
        user_state.pop(chat_id, None)

def handle_learn_download_request(chat_id, incoming_msg, user_state, state):
    """Handles the user's request for a downloadable PDF."""
    language = user_state.get(chat_id, {}).get("language", "English")
    yes_translated = translate_text("yes", language)
    if incoming_msg.lower() == yes_translated.lower():
        notes_text = state.get("full_notes", "")
        topic = state.get("topic", "notes")
        
        if notes_text:
            download_success = translate_text("Generating your notes as a PDF... 📄", language)
            send_message(chat_id, download_success)
            pdf_data = create_pdf_notes(topic, notes_text)
            document_caption_translated = translate_text("Here are your downloadable notes for {}!", language).format(topic)
            send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                          caption=document_caption_translated)
        else:
            no_notes_translated = translate_text("I'm sorry, I couldn't find the notes to download.", language)
            send_message(chat_id, f"❌ {no_notes_translated}")
    else:
        skip_download_translated = translate_text("Okay, skipping the download. Let me know if you need anything else.", language)
        send_message(chat_id, skip_download_translated)
    
    user_state.pop(chat_id, None)

def handle_mcq_request(chat_id, incoming_msg, user_state):
    """Generates insightful MCQs and sends the solution."""
    topic = incoming_msg
    language = user_state.get(chat_id, {}).get("language", "English")

    prompt = (
        f"Create 5 challenging and insightful multiple-choice questions (MCQs) on the topic: '{topic}' in {language}.\n"
        f"For each question, provide 4 options (A, B, C, D).\n"
        f"Directly after each question, provide the correct answer and a brief, 1-2 line explanation of why it is correct.\n"
        f"Use Markdown to format the questions and answers clearly."
    )
    quiz_message = translate_text("Generating an insightful quiz on '{}'... 🤔", language).format(topic)
    send_message(chat_id, quiz_message)
    response = call_gemini(prompt)
    if response:
        quiz_intro = translate_text("Here's your quiz:", language)
        send_message(chat_id, f"🧠 {quiz_intro}")
        for chunk in split_message(response):
            send_message(chat_id, chunk)
    else:
        quiz_error = translate_text("Couldn't generate the MCQs. Try again later.", language)
        send_message(chat_id, f"❌ {quiz_error}")
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
        unknown_error = "Sorry, something went wrong. Please try again later."
        send_message(chat_id, f"❌ {unknown_error}")

    return "ok"


# -------------------- Startup --------------------

if __name__ == "__main__":
    if os.getenv("WEBHOOK_URL"):
        set_webhook()
    else:
        logging.error("WEBHOOK_URL environment variable is not set. Webhook will not be configured.")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.00", port=port)
