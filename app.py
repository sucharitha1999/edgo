from flask import Flask, request
import requests
import os
from dotenv import load_dotenv
import json
import time

app = Flask(__name__)

load_dotenv()

# API Keys
# You will need to provide a Gemini API key in your .env file
# as the 'gemini' model is not provided by the Canvas environment directly.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent"

# Session state for users
user_state = {}

# -------------------- Utilities --------------------

def send_message(chat_id, text):
    """Send message to Telegram"""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    requests.post(TELEGRAM_API_URL, json=payload)

def split_message(text, chunk_size=1400):
    """Split long responses so Telegram accepts them"""
    parts = []
    while len(text) > chunk_size:
        split_index = text.rfind("\n\n", 0, chunk_size)
        split_index = split_index if split_index != -1 else chunk_size
        parts.append(text[:split_index])
        text = text[split_index:].lstrip()
    parts.append(text)
    return parts

def call_gemini(prompt):
    """Call Gemini API with exponential backoff for retries"""
    try:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ]
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        retries = 0
        max_retries = 3
        while retries < max_retries:
            response = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", headers=headers, data=json.dumps(payload))
            if response.status_code == 429: # Too Many Requests
                delay = 2**retries
                print(f"Rate limit exceeded. Retrying in {delay} seconds...")
                time.sleep(delay)
                retries += 1
            else:
                response.raise_for_status() # Raise an error for bad status codes
                break
        
        if retries == max_retries:
            print("❌ Max retries reached. Giving up.")
            return None

        result = response.json()
        if "candidates" in result and len(result["candidates"]) > 0 and \
           "content" in result["candidates"][0] and "parts" in result["candidates"][0]["content"] and \
           len(result["candidates"][0]["content"]["parts"]) > 0:
            return result["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            print("❌ Gemini API response was not in the expected format.")
            return None
    except Exception as e:
        print("❌ Gemini error:", e)
        return None

# -------------------- Routes --------------------

@app.route("/")
def home():
    return "🚀 Telegram bot is running!"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json()
    print("📩 Incoming:", data)

    if "message" not in data or "text" not in data["message"]:
        return "ok"

    chat_id = data["message"]["chat"]["id"]
    incoming_msg = data["message"]["text"].strip()
    state = user_state.get(chat_id, {})

    # Entry point
    if incoming_msg.lower() == "hi edgo":
        send_message(chat_id,
            "Hi! 👋 What would you like help with today?\n\n"
            "*Reply with a number:*\n"
            "1️⃣ Schedule a learning roadmap\n"
            "2️⃣ Learn a topic\n"
            "3️⃣ Solve a problem"
        )
        user_state[chat_id] = {"step": "menu"}
        return "ok"

    # Handle menu selection
    if state.get("step") == "menu":
        if incoming_msg == "1":
            user_state[chat_id] = {"step": "roadmap_what"}
            send_message(chat_id, "📘 What do you need the roadmap for? (e.g., Python, Finance, etc.)")
        elif incoming_msg == "2":
            user_state[chat_id] = {"step": "learn_topic"}
            send_message(chat_id, "📚 What topic would you like to learn about?")
        elif incoming_msg == "3":
            user_state[chat_id] = {"step": "solve_problem"}
            send_message(chat_id, "🧠 What's the problem you'd like me to solve?")
        else:
            send_message(chat_id, "Please enter a valid option: 1, 2 or 3.")
        return "ok"

    # Roadmap flow
    if state.get("step") == "roadmap_what":
        state["topic"] = incoming_msg
        state["step"] = "roadmap_education"
        send_message(chat_id, "🎓 What is your current education level? (e.g., high school, college, MBA)")
        return "ok"

    if state.get("step") == "roadmap_education":
        state["education"] = incoming_msg
        state["step"] = "roadmap_hours"
        send_message(chat_id, "⏱️ How many hours can you dedicate daily?")
        return "ok"

    if state.get("step") == "roadmap_hours":
        state["hours"] = incoming_msg
        prompt = (
            f"You're a friendly teaching assistant helping a complete beginner learn {state['topic']}.\n"
            f"The learner's education level is {state['education']} and they can study for {state['hours']} per day.\n\n"
            "Create a **1-month learning roadmap**, broken into **4 weekly sections**.\n"
            "Use emojis to make it engaging and visually clear.\n"
            "Format using **Markdown**.\n"
            "Keep explanations simple, warm, and motivating.\n"
            "At the end, suggest 2–3 resources for continued learning.\n"
            "Keep the full response concise — under 800 words."
        )

        response = call_gemini(prompt)
        if response:
            send_message(chat_id, "🗺️ Here's your learning roadmap:")
            for chunk in split_message(response):
                send_message(chat_id, chunk)
        else:
            send_message(chat_id, "❌ Couldn't generate the roadmap. Try again later.")
        user_state.pop(chat_id, None)
        return "ok"

    # Learn topic flow (modified to ask for language and use NCERT prompt)
    if state.get("step") == "learn_topic":
        state["topic"] = incoming_msg
        user_state[chat_id] = {"step": "learn_language", "topic": incoming_msg}
        send_message(chat_id, "🌐 What language would you like the explanation in? (e.g., English, Hindi, Spanish)")
        return "ok"
    
    if state.get("step") == "learn_language":
        topic = state.get("topic")
        language = incoming_msg
        user_state.pop(chat_id, None)

        # Prompt for NCERT-style explanation
        prompt = (
            f"Act as a friendly tutor for Indian NCERT textbooks. "
            f"Your goal is to simplify and explain the following topic for a student in a simple and clear manner:\n\n"
            f"Topic: {topic}\n\n"
            f"Please provide the explanation in a single, friendly paragraph, without using any headings or bullet points."
            f"After the explanation, add a concluding sentence that directs the user to the official NCERT website, like this: "
            f"\"You can find the official NCERT textbooks for all subjects at ncert.nic.in/ebooks.php.\"\n\n"
            f"Then, translate the entire explanation and the concluding sentence into {language}."
        )

        response = call_gemini(prompt)
        if response:
            send_message(chat_id, f"📘 Here's what I found about '{topic}' in {language}:")
            for chunk in split_message(response):
                send_message(chat_id, chunk)
        else:
            send_message(chat_id, "❌ Couldn't fetch learning content right now.")
        return "ok"

    # Solve a problem flow
    if state.get("step") == "solve_problem":
        problem = incoming_msg
        user_state.pop(chat_id, None)

        prompt = (
            f"You're a smart tutor solving this problem step-by-step:\n\n"
            f"{problem}\n\n"
            "Give a detailed solution in simple steps.\n"
            "If needed, include a **relevant example**.\n"
            "Use **markdown formatting** and emojis to make it clear and engaging.\n"
            "Explain like you're helping a beginner understand each step."
        )

        response = call_gemini(prompt)
        if response:
            send_message(chat_id, "🧠 Here's the solution:")
            for chunk in split_message(response):
                send_message(chat_id, chunk)
        else:
            send_message(chat_id, "❌ Couldn't solve the problem. Try again later.")
        return "ok"

    # Fallback
    send_message(chat_id, "Say 'hi edgo' to get started. 😊")
    return "ok"

# -------------------- Webhook Setup --------------------

def set_webhook():
    """Set Telegram webhook only once when app starts"""
    if os.getenv("WEBHOOK_URL"): # Only set webhook if URL is available
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={WEBHOOK_URL}"
        res = requests.get(url)
        print("🔗 Webhook set:", res.json())

if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
