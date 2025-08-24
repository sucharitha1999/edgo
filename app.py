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
STATE_MENU = "menu"
STATE_LEARN_TOPIC = "learn_topic"
STATE_LEARN_LANGUAGE = "learn_language"
STATE_LEARN_DOWNLOAD = "learn_download"
STATE_MCQ_TOPIC = "mcq_topic"
STATE_GLOBAL_OPTION = "global_option"


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
        send_message(chat_id,
            "Hi! 👋 What would you like help with today?\n\n"
            "*Reply with a number:*\n"
            "1️⃣ Learn about a topic\n"
            "2️⃣ Test your knowledge with MCQs\n"
            "3️⃣ Explore a cultural and language exchange"
        )
        user_state[chat_id] = {"step": STATE_MENU}
        return

    if state.get("step") == STATE_MENU:
        handle_menu_selection(chat_id, incoming_msg, user_state)
        return

    if state.get("step") == STATE_LEARN_TOPIC:
        state["topic"] = incoming_msg
        state["step"] = STATE_LEARN_LANGUAGE
        send_message(chat_id, "🌐 What language would you like the explanation in? (e.g., English, Hindi, Spanish)")
    
    elif state.get("step") == STATE_LEARN_LANGUAGE:
        handle_learn_topic_request(chat_id, incoming_msg, user_state, state)
        
    elif state.get("step") == STATE_LEARN_DOWNLOAD:
        handle_learn_download_request(chat_id, incoming_msg, user_state, state)

    elif state.get("step") == STATE_MCQ_TOPIC:
        handle_mcq_request(chat_id, incoming_msg, user_state)
    
    elif state.get("step") == STATE_GLOBAL_OPTION:
        handle_global_option_request(chat_id, incoming_msg, user_state)

    else:
        send_message(chat_id, "I'm not sure what you mean. Say 'hi edgo' to get started. 😊")

def handle_menu_selection(chat_id, incoming_msg, user_state):
    """Handles the user's choice from the main menu."""
    if incoming_msg == "1":
        user_state[chat_id] = {"step": STATE_LEARN_TOPIC}
        send_message(chat_id, "📚 What topic would you like to learn about?")
    elif incoming_msg == "2":
        user_state[chat_id] = {"step": STATE_MCQ_TOPIC}
        send_message(chat_id, "📝 What topic would you like a quiz on?")
    elif incoming_msg == "3":
        user_state[chat_id] = {"step": STATE_GLOBAL_OPTION}
        send_message(chat_id, "🌍 This feature is still under development, but it will help you connect with learners from different cultures to practice languages and share knowledge. Come back soon for updates!")
        user_state.pop(chat_id, None)
    else:
        send_message(chat_id, "Please enter a valid option: 1, 2 or 3.")

def handle_learn_topic_request(chat_id, incoming_msg, user_state, state):
    """
    Generates a detailed explanation with external resources
    and prompts the user for a downloadable file.
    """
    topic = state.get("topic")
    language = incoming_msg
    state["language"] = language
    
    prompt = (
        f"Act as a friendly tutor for Indian NCERT textbooks. "
        f"Your goal is to simplify and explain the following topic for a student in a simple and clear manner:\n\n"
        f"Topic: {topic}\n\n"
        f"Please provide a detailed, explanation in {language} in simple language using **Markdown bullet points**."
        f"After the main explanation, provide two sections:\n"
        f"1. **Explore More** with links to relevant websites for deeper learning.\n"
        f"2. **Watch and Learn** with links to relevant YouTube videos.\n"
        f"If the topic is related to the Indian NCERT curriculum, please also include the following sentence at the very end of your response: "
        f"\"You can find the official NCERT textbooks for all subjects at ncert.nic.in/ebooks.php.\"\n"
    )
    
    send_message(chat_id, "Finding and explaining the topic for you... ⏳")
    response = call_gemini(prompt)
    
    if response:
        state["full_notes"] = response
        formatted_response = format_bullet_points(response)
        send_message(chat_id, f"📘 Here's the explanation of '{topic}':")
        for chunk in split_message(formatted_response):
            send_message(chat_id, chunk)

        send_message(chat_id,
            "Would you like to get a downloadable PDF of these notes?\n"
            "Reply with 'Yes' to get them."
        )
        user_state[chat_id]["step"] = STATE_LEARN_DOWNLOAD
    else:
        send_message(chat_id, "❌ Couldn't fetch learning content right now.")
        user_state.pop(chat_id, None)

def handle_learn_download_request(chat_id, incoming_msg, user_state, state):
    """Handles the user's request for a downloadable PDF."""
    if incoming_msg.lower() == "yes":
        notes_text = state.get("full_notes", "")
        topic = state.get("topic", "notes")
        
        if notes_text:
            send_message(chat_id, "Generating your notes as a PDF... 📄")
            pdf_data = create_pdf_notes(topic, notes_text)
            send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                          caption=f"Here are your downloadable notes for {topic}!")
        else:
            send_message(chat_id, "❌ I'm sorry, I couldn't find the notes to download.")
    else:
        send_message(chat_id, "Okay, skipping the download. Let me know if you need anything else.")
    
    user_state.pop(chat_id, None)

def handle_mcq_request(chat_id, incoming_msg, user_state):
    """Generates insightful MCQs and sends the solution."""
    topic = incoming_msg
    prompt = (
        f"Create 5 challenging and insightful multiple-choice questions (MCQs) on the topic: '{topic}'.\n"
        f"For each question, provide 4 options (A, B, C, D) and then, in a separate section, provide "
        f"the correct answer and a brief, simple explanation of why it is correct.\n"
        f"Use Markdown to format the questions and answers clearly."
    )
    send_message(chat_id, f"Generating an insightful quiz on '{topic}'... 🤔")
    response = call_gemini(prompt)
    if response:
        send_message(chat_id, "🧠 Here's your quiz:")
        for chunk in split_message(response):
            send_message(chat_id, chunk)
    else:
        send_message(chat_id, "❌ Couldn't generate the MCQs. Try again later.")
    user_state.pop(chat_id, None)

def handle_global_option_request(chat_id, incoming_msg, user_state):
    """
    Handles the new "Globalizing Education" option. This is a placeholder for future
    development.
    """
    send_message(chat_id, "🌍 This feature is still under development, but it will help you connect with learners from different cultures to practice languages and share knowledge. Come back soon for updates!")
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
        send_message(chat_id, "❌ Sorry, something went wrong. Please try again later.")

    return "ok"


# -------------------- Startup --------------------

if __name__ == "__main__":
    if os.getenv("WEBHOOK_URL"):
        set_webhook()
    else:
        logging.error("WEBHOOK_URL environment variable is not set. Webhook will not be configured.")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
