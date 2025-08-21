from flask import Flask, request
import requests
import cohere
import os
from dotenv import load_dotenv

app = Flask(__name__)

load_dotenv()

# API Keys
COHERE_API_KEY = os.getenv("COHERE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# Cohere Client
co = cohere.Client(COHERE_API_KEY)

# Session state for users
user_state = {}

# Utility: Send message to Telegram
def send_message(chat_id, text):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    requests.post(TELEGRAM_API_URL, json=payload)

# Utility: Split long messages
def split_message(text, chunk_size=1400):
    parts = []
    while len(text) > chunk_size:
        split_index = text.rfind("\n\n", 0, chunk_size)
        split_index = split_index if split_index != -1 else chunk_size
        parts.append(text[:split_index])
        text = text[split_index:].lstrip()
    parts.append(text)
    return parts

# Utility: Generate response using Cohere
def call_cohere(prompt, max_tokens=1000, temperature=0.7):
    try:
        response = co.generate(
            model="command-r-plus",
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature
        )
        return response.generations[0].text.strip()
    except Exception as e:
        print("❌ Cohere error:", e)
        return None

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
            "Format using **Markdown** (e.g., headings, bullet points).\n"
            "Keep explanations simple, warm, and motivating — like you're guiding a friend.\n"
            "Make each week's tasks specific and achievable in daily sessions.\n"
            "At the end, suggest 2–3 resources (courses, YouTube channels, or books) for continued learning.\n"
            "Keep the full response concise — under 800 words."
        )

        response = call_cohere(prompt)
        if response:
            send_message(chat_id, "🗺️ Here's your learning roadmap:")
            for chunk in split_message(response):
                send_message(chat_id, chunk)
        else:
            send_message(chat_id, "❌ Couldn't generate the roadmap. Try again later.")
        user_state.pop(chat_id, None)
        return "ok"

    # Learn topic flow
    if state.get("step") == "learn_topic":
        topic = incoming_msg
        user_state.pop(chat_id, None)

        prompt = (
            f"You're a friendly tutor explaining the topic: {topic}.\n"
            "First, give a **simple explanation** in 2–3 short paragraphs.\n"
            "Then, suggest **3 beginner-friendly YouTube videos**.\n"
            "Finally, suggest **2–3 websites or courses** for deeper learning.\n"
            "Use emojis and markdown for clarity and engagement."
        )

        response = call_cohere(prompt)
        if response:
            send_message(chat_id, "📘 Here's what I found:")
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

        response = call_cohere(prompt)
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

if __name__ == "__main__":
    app.run(debug=True, port=5000)
