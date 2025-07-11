import os
import json
import time
from telebot import TeleBot, types
from pathlib import Path
from io import BytesIO
from docx import Document
from reportlab.pdfgen import canvas
import openai

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
# === НАСТРОЙКИ ===
USED_TRIALS_FILE = "used_trials.json"
MEMORY_DIR = "memory"
ADMIN_ID = 1034982624
MAX_HISTORY = 20
TRIAL_TOKEN_LIMIT = 10_000
TRIAL_DURATION_SECONDS = 24 * 3600
BOT_NAME = "Neiro Max"

# === СОСТОЯНИЕ ===
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

# === УТИЛИТЫ ===
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

# === ИНИЦИАЛИЗАЦИЯ БОТА ===
used_trials = load_used_trials()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("TELEGRAM_TOKEN или OPENAI_API_KEY не заданы.")

openai.api_key = OPENAI_API_KEY
bot = TeleBot(TELEGRAM_TOKEN)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if WEBHOOK_URL:
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
Path(MEMORY_DIR).mkdir(exist_ok=True)

# === ОБРАБОТЧИКИ ===

@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = str(message.chat.id)
    if chat_id in used_trials:
        bot.send_message(chat_id, "⛔ Вы уже использовали пробный доступ.")
        return
    used_trials[chat_id] = True
    trial_start_times[chat_id] = time.time()
    save_used_trials(used_trials)
    bot.send_message(chat_id, f"Привет! Я {BOT_NAME} — твой ассистент. Чем могу помочь? 😉", reply_markup=main_menu(message.chat.id))
    user_modes[message.chat.id] = "копирайтер"
    user_histories[message.chat.id] = []
    user_models[message.chat.id] = "gpt-3.5-turbo"
    user_token_limits[message.chat.id] = 0

@bot.message_handler(func=lambda msg: msg.text == "📋 Главное меню")
def handle_menu(message):
    bot.send_message(message.chat.id, "📋 Главное меню:", reply_markup=main_menu(message.chat.id))

@bot.message_handler(func=lambda msg: msg.text == "💡 Сменить стиль")
def handle_change_style(message):
    bot.send_message(message.chat.id, "🧠 Выбери стиль общения:", reply_markup=style_keyboard())

@bot.message_handler(func=lambda msg: msg.text in [m.capitalize() for m in available_modes])
def handle_style_selection(message):
    mode = message.text.lower()
    user_modes[message.chat.id] = mode
    bot.send_message(message.chat.id, f"✅ Выбран стиль: <b>{mode.capitalize()}</b>", parse_mode="HTML", reply_markup=main_menu(message.chat.id))

@bot.message_handler(func=lambda msg: msg.text == "📘 Правила")
def handle_rules(message):
    text = (
        "📘 <b>Правила использования бота Neiro Max:</b>\n\n"
        "✅ <b>Бесплатный пробный доступ:</b>\n"
        "• Длительность — 24 часа или 10 000 токенов (что наступит раньше).\n\n"
        "❌ <b>Запрещено:</b>\n"
        "• Запросы, нарушающие законодательство РФ;\n"
        "• Темы: насилие, терроризм, экстремизм, порнография, дискриминация, мошенничество.\n\n"
        "⚠️ <b>Важно:</b>\n"
        "• GPT-чат может допускать ошибки.\n"
        "• Ответы не являются истиной в последней инстанции.\n\n"
        "Спасибо, что выбрали Neiro Max!"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda msg: msg.text == "📄 Тарифы")
def handle_tariffs(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🟢 GPT-3.5: Lite — 199₽", url="https://yookassa.ru/pay/gpt35_lite"))
    markup.add(types.InlineKeyboardButton("🟢 GPT-3.5: Pro — 299₽", url="https://yookassa.ru/pay/gpt35_pro"))
    markup.add(types.InlineKeyboardButton("🟢 GPT-3.5: Max — 399₽", url="https://yookassa.ru/pay/gpt35_max"))
    markup.add(types.InlineKeyboardButton("🔵 GPT-4o: Lite — 299₽", url="https://yookassa.ru/pay/gpt4o_lite"))
    markup.add(types.InlineKeyboardButton("🔵 GPT-4o: Pro — 499₽", url="https://yookassa.ru/pay/gpt4o_pro"))
    markup.add(types.InlineKeyboardButton("🔵 GPT-4o: Max — 999₽", url="https://yookassa.ru/pay/gpt4o_max"))

    text = (
        "📦 *Тарифы Neiro Max:*\n\n"
        "*GPT-3.5:*\n"
        "• Lite — 200K токенов / 30 дней — 199₽\n"
        "• Pro — 500K токенов / 30 дней — 299₽\n"
        "• Max — 1M токенов / 30 дней — 399₽\n\n"
        "*GPT-4o:*\n"
        "• Lite — 200K токенов / 30 дней — 299₽\n"
        "• Pro — 500K токенов / 30 дней — 499₽\n"
        "• Max — 1M токенов / 30 дней — 999₽"
    )

    bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "♻️ Сброс пробника")
def handle_reset_button(message):
    bot.send_message(message.chat.id, "📘 Введите ID пользователя, чей пробник сбросить:\nНапример: /reset_trial 1034982624")

@bot.message_handler(commands=["reset_trial"])
def reset_trial_command(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "⛔ У вас нет доступа к этой команде.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "❌ Использование: /reset_trial <user_id>")
        return
    target_id = parts[1]
    if target_id in used_trials:
        del used_trials[target_id]
        save_used_trials(used_trials)
        bot.send_message(message.chat.id, f"✅ Пробный доступ пользователя {target_id} сброшен.")
    else:
        bot.send_message(message.chat.id, f"ℹ️ У пользователя {target_id} не был использован пробник.")

@bot.message_handler(func=lambda msg: msg.text == "🚀 Запустить Neiro Max")
def handle_launch(message):
    model = user_models.get(message.chat.id, "gpt-3.5-turbo")
    bot.send_message(message.chat.id, f"Привет! Я {BOT_NAME} 🤖")
    bot.send_message(message.chat.id, f"Модель: {model}. Напиши запрос — я отвечу.")

@bot.message_handler(func=lambda msg: True)
def handle_prompt(message):
    chat_id = str(message.chat.id)
    if chat_id not in trial_start_times:
        trial_start_times[chat_id] = time.time()

    time_elapsed = time.time() - trial_start_times[chat_id]
    tokens_used = user_token_limits.get(chat_id, 0)

    if time_elapsed > TRIAL_DURATION_SECONDS or tokens_used >= TRIAL_TOKEN_LIMIT:
        bot.send_message(chat_id, "⛔ Пробный период завершён. Пожалуйста, выберите тариф в разделе 📄 Тарифы.")
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
    text = "\n\n".join(m["content"] for m in history if m["role"] != "system")
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

print("🤖 Neiro Max запущен.")
from flask import Flask, request

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "!", 200
    else:
        return "Invalid content type", 403

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
