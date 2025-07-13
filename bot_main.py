
import os
import json
import time
from telebot import TeleBot, types
from pathlib import Path
from io import BytesIO
from docx import Document
from reportlab.pdfgen import canvas
import openai
from flask import Flask, request
from yookassa import Configuration, Payment

# === КОНФИГ ===
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

USED_TRIALS_FILE = "used_trials.json"
MEMORY_DIR = "memory"
ADMIN_ID = 1034982624
MAX_HISTORY = 20
TRIAL_TOKEN_LIMIT = 10_000
TRIAL_DURATION_SECONDS = 24 * 3600
BOT_NAME = "Neiro Max"

user_token_limits = {}
user_modes = {}
user_histories = {}
user_models = {}
trial_start_times = {}

available_modes = {
    "психолог": "Ты — внимательный и эмпатичный психолог. Говори с заботой, мягко и поддерживающе.",
    "копирайтер": "Ты — профессиональный копирайтер. Пиши живо, увлекательно и убедительно.",
    "юморист": "Ты — остроумный собеседник с отличным чувством юмора. Отвечай с сарказмом и шутками.",
    "деловой": "Ты — деловой помощник. Отвечай строго по делу, формально и без лишних эмоций.",
    "философ": "Ты — мудрый философ. Говори глубоко, рассуждай и вдохновляй.",
    "профессор": "Ты — профессор. Объясняй подробно, академично и с примерами.",
    "гопник": "Ты — гопник из 90-х. Говори дерзко, с уличным сленгом и акцентом.",
    "истории": "Ты — рассказчик. Превращай каждый ответ в интересную историю."
}

def create_payment(amount_rub, description, return_url, chat_id):
    try:
        payment = Payment.create({
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": return_url
            },
            "capture": True,
            "description": description,
            "metadata": {"chat_id": str(chat_id), "model": "gpt-4o" if "4o" in description else "gpt-3.5-turbo"}
        })
        return payment.confirmation.confirmation_url
    except Exception as e:
        print("Ошибка при создании платежа:", e)
        return None

def load_used_trials():
    if os.path.exists(USED_TRIALS_FILE):
        with open(USED_TRIALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_used_trials(data):
    with open(USED_TRIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def is_admin(chat_id):
    return int(chat_id) == ADMIN_ID

def load_history(chat_id):
    path = f"{MEMORY_DIR}/{chat_id}.json"
    return json.load(open(path, "r", encoding="utf-8")) if os.path.exists(path) else []

def save_history(chat_id, history):
    path = f"{MEMORY_DIR}/{chat_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)

def main_menu(chat_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🚀 Запустить Neiro Max")
    markup.add("💡 Сменить стиль", "📄 Тарифы")
    markup.add("📘 Правила")
    if is_admin(chat_id):
        markup.add("♻️ Сброс пробника")
    return markup

def style_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for mode in available_modes:
        markup.add(mode.capitalize())
    markup.add("📋 Главное меню")
    return markup

def format_buttons():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📄 PDF", callback_data="save_pdf"))
    markup.add(types.InlineKeyboardButton("📝 Word", callback_data="save_word"))
    return markup

used_trials = load_used_trials()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY
bot = TeleBot(TELEGRAM_TOKEN)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if WEBHOOK_URL:
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
Path(MEMORY_DIR).mkdir(exist_ok=True)

# === ОБРАБОТЧИКИ ===
@bot.message_handler(func=lambda msg: msg.text == "💡 Сменить стиль")
def handle_style_change(message):
    bot.send_message(message.chat.id, "Выберите стиль общения:", reply_markup=style_keyboard())

@bot.message_handler(func=lambda msg: msg.text in [mode.capitalize() for mode in available_modes])
def handle_style_selection(message):
    mode = message.text.lower()
    if mode in available_modes:
        user_modes[message.chat.id] = mode
        bot.send_message(message.chat.id, f"✅ Стиль сменён на: {mode}", reply_markup=main_menu(message.chat.id))

@bot.message_handler(func=lambda msg: msg.text == "📘 Правила")
def handle_rules(message):
    rules = (
        "<b>Правила использования бота Neiro Max:</b>

"
        "✅ <b>Бесплатный пробный доступ:</b>
"
        "• Длительность — 24 часа или 10 000 токенов (что наступит раньше).

"
        "❌ <b>Запрещено:</b>
"
        "• Запросы, нарушающие законодательство РФ;
"
        "• Темы: насилие, терроризм, экстремизм, порнография, дискриминация, мошенничество.

"
        "⚠️ <b>Важно:</b>
"
        "• GPT-чат может допускать ошибки.
"
        "• Ответы не являются истиной в последней инстанции.

"
        "Спасибо, что выбрали Neiro Max!"
    )
    bot.send_message(message.chat.id, rules, parse_mode="HTML")

@bot.message_handler(func=lambda msg: msg.text == "♻️ Сброс пробника")
def handle_reset_trial(message):
    if is_admin(message.chat.id):
        chat_id = message.chat.id
        used_trials.pop(str(chat_id), None)
        trial_start_times.pop(chat_id, None)
        save_used_trials(used_trials)
        bot.send_message(chat_id, "✅ Пробный период сброшен.")

# === FLASK APP ===
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "!", 200
    return "Invalid content type", 403

@app.route("/webhook", methods=["GET"])
def confirm_payment():
    chat_id = request.args.get("chat_id")
    tariff = request.args.get("tariff")
    if not chat_id or not tariff:
        return "Missing chat_id or tariff", 400

    if "GPT-4o" in tariff:
        user_models[int(chat_id)] = "gpt-4o"
    else:
        user_models[int(chat_id)] = "gpt-3.5-turbo"

    bot.send_message(int(chat_id), f"✅ Оплата тарифа «{tariff}» прошла успешно!
Модель активирована: {user_models[int(chat_id)]}.", reply_markup=main_menu(int(chat_id)))
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
