import os
import json
import time
from pathlib import Path
from io import BytesIO
from flask import Flask, request
from telebot import TeleBot, types
from docx import Document
from reportlab.pdfgen import canvas
from yookassa import Configuration, Payment
import openai

# === НАСТРОЙКИ ===
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# === КОНСТАНТЫ ===
USED_TRIALS_FILE = "used_trials.json"
MEMORY_DIR = "memory"
ADMIN_ID = 1034982624
MAX_HISTORY = 20
TRIAL_TOKEN_LIMIT = 10_000
TRIAL_DURATION_SECONDS = 24 * 3600
BOT_NAME = "Neiro Max"

# === ХРАНИЛИЩА ===
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

# === ИНИЦИАЛИЗАЦИЯ ===
used_trials = json.load(open(USED_TRIALS_FILE, encoding="utf-8")) if os.path.exists(USED_TRIALS_FILE) else {}
openai.api_key = OPENAI_API_KEY
bot = TeleBot(TELEGRAM_TOKEN)
if WEBHOOK_URL:
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
Path(MEMORY_DIR).mkdir(exist_ok=True)
app = Flask(__name__)

# === УТИЛИТЫ ===
def load_history(chat_id):
    path = f"{MEMORY_DIR}/{chat_id}.json"
    return json.load(open(path, "r", encoding="utf-8")) if os.path.exists(path) else []

def save_history(chat_id, history):
    path = f"{MEMORY_DIR}/{chat_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)

def main_menu(chat_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("\ud83d\ude80 Запустить Neiro Max")
    markup.add("\ud83d\udca1 Сменить стиль", "\ud83d\udcc4 Тарифы")
    markup.add("\ud83d\udcdc Правила")
    if str(chat_id) == str(ADMIN_ID):
        markup.add("\u267b\ufe0f Сброс пробника")
    return markup

def style_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for mode in available_modes:
        markup.add(mode.capitalize())
    markup.add("\ud83d\udccb Главное меню")
    return markup

def format_buttons():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("\ud83d\udcc4 PDF", callback_data="save_pdf"))
    markup.add(types.InlineKeyboardButton("\ud83d\udcdd Word", callback_data="save_word"))
    return markup

def save_used_trials(data):
    with open(USED_TRIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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
        print("\u274c Ошибка при создании платежа:", e)
        return None

# === ОБРАБОТЧИКИ ===
@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = str(message.chat.id)
    if chat_id in used_trials:
        bot.send_message(chat_id, "\u26d4\ufe0f Вы уже использовали пробный доступ.")
        return
    used_trials[chat_id] = True
    trial_start_times[chat_id] = time.time()
    save_used_trials(used_trials)
    bot.send_message(chat_id, f"Привет! Я {BOT_NAME} — твой ассистент. Чем могу помочь?", reply_markup=main_menu(chat_id))
    user_modes[message.chat.id] = "копирайтер"
    user_histories[message.chat.id] = []
    user_models[message.chat.id] = "gpt-3.5-turbo"
    user_token_limits[message.chat.id] = 0

@bot.message_handler(func=lambda msg: msg.text == "\ud83d\udcc4 Тарифы")
def handle_tariffs(message):
    return_url = WEBHOOK_URL
    buttons = []
    tariffs = [
        ("GPT-3.5: Lite — 199₽", 199, "GPT-3.5 Lite"),
        ("GPT-4o: Max — 999₽", 999, "GPT-4o Max"),
    ]
    for label, price, desc in tariffs:
        url = create_payment(price, desc, return_url, message.chat.id)
        if url:
            buttons.append(types.InlineKeyboardButton(f"\ud83d\udcb3 {label}", url=url))
    markup = types.InlineKeyboardMarkup(row_width=1)
    for btn in buttons:
        markup.add(btn)
    bot.send_message(message.chat.id, "\ud83d\udce6 Выберите тариф:", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "\ud83d\udca1 Сменить стиль")
def handle_style_change(message):
    bot.send_message(message.chat.id, "Выберите стиль общения:", reply_markup=style_keyboard())

@bot.message_handler(func=lambda msg: msg.text in [m.capitalize() for m in available_modes])
def handle_style_selection(message):
    selected = message.text.lower()
    user_modes[message.chat.id] = selected
    bot.send_message(message.chat.id, f"\u2705 Стиль общения установлен: {selected}", reply_markup=main_menu(message.chat.id))

@bot.message_handler(func=lambda msg: True)
def handle_prompt(message):
    chat_id = str(message.chat.id)
    if chat_id not in trial_start_times:
        trial_start_times[chat_id] = time.time()
    time_elapsed = time.time() - trial_start_times[chat_id]
    tokens_used = user_token_limits.get(chat_id, 0)
    if time_elapsed > TRIAL_DURATION_SECONDS or tokens_used >= TRIAL_TOKEN_LIMIT:
        bot.send_message(chat_id, "\u26d4\ufe0f Пробный период завершён. Пожалуйста, выберите тариф.")
        return
    prompt = message.text
    mode = user_modes.get(int(chat_id), "копирайтер")
    history = load_history(chat_id)
    messages = [{"role": "system", "content": available_modes[mode]}] + history + [{"role": "user", "content": prompt}]
    model = user_models.get(int(chat_id), "gpt-3.5-turbo")
    try:
        response = openai.ChatCompletion.create(model=model, messages=messages)
        reply = response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка: {e}")
        return
    user_token_limits[chat_id] = tokens_used + len(prompt)
    history.append({"role": "user", "content": prompt})
    history.append({"role": "assistant", "content": reply})
    save_history(chat_id, history)
    bot.send_message(chat_id, reply, reply_markup=format_buttons())

@bot.callback_query_handler(func=lambda call: call.data in ["save_pdf", "save_word"])
def handle_file_format(call):
    chat_id = call.message.chat.id
    history = load_history(str(chat_id))
    text = "\n".join(m["content"] for m in history if m["role"] != "system")
    if call.data == "save_pdf":
        pdf_bytes = BytesIO()
        pdf = canvas.Canvas(pdf_bytes)
        y = 800
        for line in text.split("\n"):
            pdf.drawString(40, y, line)
            y -= 15
        pdf.save()
        pdf_bytes.seek(0)
        bot.send_document(chat_id, ("neiro_max_output.pdf", pdf_bytes))
    else:
        doc = Document()
        doc.add_paragraph(text)
        word_bytes = BytesIO()
        doc.save(word_bytes)
        word_bytes.seek(0)
        bot.send_document(chat_id, ("neiro_max_output.docx", word_bytes))

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    else:
        return "Invalid content type", 403

@app.route("/webhook", methods=["GET"])
def confirm_payment():
    chat_id = request.args.get("chat_id")
    tariff = request.args.get("tariff")
    if not chat_id or not tariff:
        return "Недостаточно данных", 400
    if "GPT-4o" in tariff:
        user_models[int(chat_id)] = "gpt-4o"
    else:
        user_models[int(chat_id)] = "gpt-3.5-turbo"
    bot.send_message(int(chat_id), f"\u2705 Оплата тарифа '{tariff}' прошла успешно! Используется модель: {user_models[int(chat_id)]}.", reply_markup=main_menu(int(chat_id)))
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
