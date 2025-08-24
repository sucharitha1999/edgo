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
STATE_ROADMAP_WHAT = "roadmap_what"
STATE_ROADMAP_EDUCATION = "roadmap_education"
STATE_ROADMAP_HOURS = "roadmap_hours"
STATE_LEARN_TOPIC = "learn_topic"
STATE_LEARN_LANGUAGE = "learn_language"
STATE_LEARN_DOWNLOAD = "learn_download"
STATE_SOLVE_PROBLEM = "solve_problem"

# Explicit font mappings
FONT_MAPPINGS = {
    # CRITICAL: The filename value here MUST EXACTLY match the filename
    # in your 'languages' folder, including case and any hyphens.
    "english": "NotoSans-Regular.ttf",
    "telugu": "NotoSansTelugu.ttf",
    "kannada": "NotoSansKannada.ttf",
    "tamil": "NotoSansTamil.ttf",
    "malayalam": "NotoSansMalayalam.ttf",
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

def load_fonts():
    """
    Checks for the existence of all fonts listed in the FONT_MAPPINGS dictionary
    and registers them with reportlab.
    """
    for lang, filename in FONT_MAPPINGS.items():
        font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "languages", filename)
        
        # This print statement helps debug font path issues.
        logging.info(f"Attempting to load font from: {font_path}")

        if os.path.exists(font_path):
            try:
                # Use a simple, predictable name for the font
                font_name = os.path.splitext(filename)[0]
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                logging.info(f"Registered font '{font_name}' for language '{lang}' from '{filename}'.")
            except Exception as e:
                logging.error(f"❌ Failed to register font '{filename}': {e}")
        else:
            logging.warning(f"Font file '{filename}' for language '{lang}' not found at '{font_path}'.")

def create_pdf_notes(title, content, language):
    """
    Generates a PDF file from the provided title and content,
    selecting the font based on the language.
    Returns the file data as a BytesIO object.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    # Get the font name based on the requested language from the mapping
    font_filename = FONT_MAPPINGS.get(language.lower())
    font_name = os.path.splitext(font_filename)[0] if font_filename else None
    
    # Try to use the specific font, or a fallback if it's not registered
    if font_name and font_name in pdfmetrics.get_font_names():
        styles['Normal'].fontName = font_name
        styles['Heading1'].fontName = font_name
    else:
        # Fallback to a common font, like the one for English
        fallback_font_filename = FONT_MAPPINGS.get("english")
        fallback_font_name = os.path.splitext(fallback_font_filename)[0] if fallback_font_filename else None
        if fallback_font_name and fallback_font_name in pdfmetrics.get_font_names():
            styles['Normal'].fontName = fallback_font_name
            styles['Heading1'].fontName = fallback_font_name
            logging.warning(f"No specific font found for '{language}', using fallback font '{fallback_font_name}'.")
        else:
            # Final fallback if no custom fonts could be registered
            logging.error(f"Could not find or register any custom font. Using default Reportlab font which may not support Unicode.")


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
            "1️⃣ Schedule a learning roadmap\n"
            "2️⃣ Learn a topic\n"
            "3️⃣ Solve a problem"
        )
        user_state[chat_id] = {"step": STATE_MENU}
        return

    if state.get("step") == STATE_MENU:
        handle_menu_selection(chat_id, incoming_msg, user_state)
        return

    if state.get("step") == STATE_ROADMAP_WHAT:
        state["topic"] = incoming_msg
        state["step"] = STATE_ROADMAP_EDUCATION
        send_message(chat_id, "🎓 What is your current education level? (e.g., high school, college, MBA)")
    
    elif state.get("step") == STATE_ROADMAP_EDUCATION:
        state["education"] = incoming_msg
        state["step"] = STATE_ROADMAP_HOURS
        send_message(chat_id, "⏱️ How many hours can you dedicate daily?")

    elif state.get("step") == STATE_ROADMAP_HOURS:
        handle_roadmap_request(chat_id, incoming_msg, user_state, state)

    elif state.get("step") == STATE_LEARN_TOPIC:
        state["topic"] = incoming_msg
        state["step"] = STATE_LEARN_LANGUAGE
        send_message(chat_id, "🌐 What language would you like the explanation in? (e.g., English, Hindi, Spanish)")

    elif state.get("step") == STATE_LEARN_LANGUAGE:
        handle_learn_topic_request(chat_id, incoming_msg, user_state, state)
        
    elif state.get("step") == STATE_LEARN_DOWNLOAD:
        handle_learn_download_request(chat_id, incoming_msg, user_state, state)

    elif state.get("step") == STATE_SOLVE_PROBLEM:
        handle_solve_problem_request(chat_id, incoming_msg, user_state)

    else:
        send_message(chat_id, "I'm not sure what you mean. Say 'hi edgo' to get started. 😊")

def handle_menu_selection(chat_id, incoming_msg, user_state):
    """Handles the user's choice from the main menu."""
    if incoming_msg == "1":
        user_state[chat_id] = {"step": STATE_ROADMAP_WHAT}
        send_message(chat_id, "📘 What do you need the roadmap for? (e.g., Python, Finance, etc.)")
    elif incoming_msg == "2":
        user_state[chat_id] = {"step": STATE_LEARN_TOPIC}
        send_message(chat_id, "📚 What topic would you like to learn about?")
    elif incoming_msg == "3":
        user_state[chat_id] = {"step": STATE_SOLVE_PROBLEM}
        send_message(chat_id, "🧠 What's the problem you'd like me to solve?")
    else:
        send_message(chat_id, "Please enter a valid option: 1, 2 or 3.")

def handle_roadmap_request(chat_id, incoming_msg, user_state, state):
    """Generates and sends the learning roadmap."""
    state["hours"] = incoming_msg
    prompt = (
        f"You're a friendly teaching assistant helping a complete beginner learn {state['topic']}.\n"
        f"The learner's education level is {state['education']} and they can study for {state['hours']} per day.\n\n"
        "Create a **1-month learning roadmap**, broken into **4 weekly sections**.\n"
        "Use emojis to make it engaging and visually clear.\n"
        "Format using **Markdown bullet points** for each week's tasks.\n"
        "Keep explanations simple, warm, and motivating.\n"
        "At the end, suggest 2–3 resources for continued learning.\n"
        "Keep the full response concise — under 800 words."
    )
    send_message(chat_id, "Thinking about your roadmap... ⏳")
    response = call_gemini(prompt)
    if response:
        formatted_response = format_bullet_points(response)
        send_message(chat_id, "🗺️ Here's your personalized learning roadmap:")
        for chunk in split_message(formatted_response):
            send_message(chat_id, chunk)
    else:
        send_message(chat_id, "❌ Couldn't generate the roadmap. Try again later.")
    user_state.pop(chat_id, None)

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
        f"Please provide a detailed, NCERT-style explanation in {language} in simple language using **Markdown bullet points**."
        f"Make sure to include a concluding sentence that directs the user to the official NCERT website, "
        f"like this: \"You can find the official NCERT textbooks for all subjects at ncert.nic.in/ebooks.php.\"\n"
        f"Do not include any other resources in this response."
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
        language = state.get("language", "english")
        
        if notes_text:
            send_message(chat_id, "Generating your notes as a PDF... �")
            pdf_data = create_pdf_notes(topic, notes_text, language)
            send_document(chat_id, pdf_data, f"{topic.replace(' ', '_')}_notes.pdf",
                          caption=f"Here are your downloadable notes for {topic}!")
        else:
            send_message(chat_id, "❌ I'm sorry, I couldn't find the notes to download.")
    else:
        send_message(chat_id, "Okay, skipping the download. Let me know if you need anything else.")
    
    user_state.pop(chat_id, None)

def handle_solve_problem_request(chat_id, incoming_msg, user_state):
    """Generates a step-by-step solution to a problem."""
    problem = incoming_msg
    prompt = (
        f"You're a smart tutor solving this problem step-by-step:\n\n"
        f"{problem}\n\n"
        "Give a detailed solution in simple steps, formatted using **Markdown bullet points**.\n"
        "If needed, include a relevant example.\n"
        "Use emojis to make it clear and engaging.\n"
        "Explain like you're helping a beginner understand each step."
    )
    send_message(chat_id, "Thinking about the problem... 🤔")
    response = call_gemini(prompt)
    if response:
        send_message(chat_id, "🧠 Here's the solution:")
        for chunk in split_message(response):
            send_message(chat_id, chunk)
    else:
        send_message(chat_id, "❌ Couldn't solve the problem. Try again later.")
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

    load_fonts()  # Load fonts at startup
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
