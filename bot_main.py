import os
import json
import time
from telebot import TeleBot, types
from pathlib import Path
from io import BytesIO
from docx import Document
from reportlab.pdfgen import canvas
import openai
from flask import Flask, request, jsonify
from yookassa import Configuration, Payment

# === КОНФИГ ===
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

USED_TRIALS_FILE = "used_trials.json"
TRIAL_TIMES_FILE = "trial_times.json"
MEMORY_DIR = "memory"
ADMIN_ID = 1034982624
MAX_HISTORY = 20
TRIAL_TOKEN_LIMIT = 10_000
TRIAL_DURATION_SECONDS = 3600  # 1 час
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

def extract_chat_id_from_description(description):
    import re
    match = re.search(r'chat_id[:\s]*(\d+)', description)
    return int(match.group(1)) if match else None


def create_payment(amount_rub, description, return_url, chat_id):
    try:
        payment = Payment.create({
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": return_url
            },
            "capture": True,
            "description": description,  # Только название тарифа
            "metadata": {
                "chat_id": str(chat_id)
            }
        })
        print("✅ Ссылка на оплату:", payment.confirmation.confirmation_url)
        return payment.confirmation.confirmation_url
        return payment.confirmation.confirmation_url
    except Exception as e:
        print("❌ Ошибка при создании платежа:")
        import traceback
        traceback.print_exc()
        return None
def load_used_trials():
    if os.path.exists(USED_TRIALS_FILE):
        with open(USED_TRIALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_trial_times(data):
    with open(TRIAL_TIMES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_token_usage():
    if os.path.exists("token_usage.json"):
        with open("token_usage.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_token_usage(data):
    with open("token_usage.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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
try:
    with open(TRIAL_TIMES_FILE, "r", encoding="utf-8") as f:
        trial_start_times = json.load(f)
        print("🎯 trial_start_times загружен:", trial_start_times)
except:
    trial_start_times = {}
    print("⚠️ trial_start_times не найден или пустой. Создан пустой словарь.")
    pass
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY
bot = TeleBot(TELEGRAM_TOKEN)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if WEBHOOK_URL:
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
Path(MEMORY_DIR).mkdir(exist_ok=True)

@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = str(message.chat.id)
    if chat_id in used_trials:
        bot.send_message(chat_id, "⛔ Вы уже использовали пробный доступ.")
        return
    used_trials[chat_id] = True
    trial_start_times = load_trial_times()
    save_used_trials(used_trials)
    bot.send_message(chat_id, f"Привет! Я {BOT_NAME} — твой ассистент. Чем могу помочь? 😉", reply_markup=main_menu(message.chat.id))
    user_modes[message.chat.id] = "копирайтер"
    user_histories[message.chat.id] = []
    user_models[message.chat.id] = "gpt-3.5-turbo"
    user_token_limits[message.chat.id] = 0
@bot.message_handler(func=lambda msg: msg.text == "📄 Тарифы")
def handle_tariffs(message):
    return_url = "https://t.me/NeiroMaxBot"
    buttons = []
    tariffs = [
        ("GPT-3.5: Lite — 199₽", 199, "GPT-3.5 Lite"),
        ("GPT-3.5: Pro — 299₽", 299, "GPT-3.5 Pro"),
        ("GPT-3.5: Max — 399₽", 399, "GPT-3.5 Max"),
        ("GPT-4o: Lite — 299₽", 299, "GPT-4o Lite"),
        ("GPT-4o: Pro — 499₽", 499, "GPT-4o Pro"),
        ("GPT-4o: Max — 999₽", 999, "GPT-4o Max"),
    ]
    for label, price, desc in tariffs:
        full_desc = desc  # 🔧 УБРАЛ chat_id
        url = create_payment(price, full_desc, return_url, message.chat.id)
        if url:
            buttons.append(types.InlineKeyboardButton(f"💳 {label}", url=url))
    markup = types.InlineKeyboardMarkup(row_width=1)
    for btn in buttons:
        markup.add(btn)
    bot.send_message(message.chat.id, "📦 Выберите тариф:", reply_markup=markup)


@bot.message_handler(func=lambda msg: msg.text == "♻️ Сброс пробника")
def handle_reset_trial(message):
    bot.send_message(message.chat.id, "Введи ID пользователя, которому сбросить пробный доступ (можно свой):")
    bot.register_next_step_handler(message, reset_trial_by_id)

def reset_trial_by_id(message):
    target_id = message.text.strip()
    if not target_id.isdigit():
        bot.send_message(message.chat.id, "❌ Введи только цифры — это должен быть chat_id.")
        return
    if target_id in used_trials:
        del used_trials[target_id]
    trial_start_times.pop(target_id, None)
    save_used_trials(used_trials)
    bot.send_message(message.chat.id, f"✅ Пробный доступ сброшен для chat_id {target_id}.")

@bot.message_handler(func=lambda msg: msg.text == "💡 Сменить стиль")
def handle_change_style(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for mode in available_modes:
        markup.add(mode.capitalize())
    markup.add("📋 Главное меню")
    bot.send_message(message.chat.id, "Выбери стиль общения:", reply_markup=markup)


@bot.message_handler(func=lambda msg: msg.text == "📘 Правила")
def handle_rules(message):
    rules_text = (
        "<b>Правила использования бота Neiro Max:</b>\n\n"
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
    bot.send_message(message.chat.id, rules_text, parse_mode="HTML")


@bot.message_handler(func=lambda msg: msg.text.lower() in ["как тебя зовут", "как тебя зовут?", "твоё имя", "ты кто", "ты кто?"])
def handle_bot_name(message):
    bot.send_message(message.chat.id, f"Я — {BOT_NAME}, твой персональный AI-ассистент 😉")



@bot.message_handler(func=lambda msg: msg.text == "📋 Главное меню")
def handle_main_menu(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu(message.chat.id))



@bot.message_handler(func=lambda msg: msg.text == "🚀 Запустить Neiro Max")
def handle_launch_neiro_max(message):
    bot.send_message(message.chat.id, "Готов к работе! Чем могу помочь?", reply_markup=main_menu(message.chat.id))



@bot.message_handler(func=lambda msg: msg.text.lower() in [m.lower() for m in available_modes])
def handle_style_selection(message):
    chat_id = str(message.chat.id)
    selected = message.text.lower()
    user_modes[chat_id] = selected
    bot.send_message(chat_id, f"✅ Стиль общения изменён на: <b>{selected.capitalize()}</b>", parse_mode="HTML")


@bot.message_handler(func=lambda msg: True)
def handle_prompt(message):
    chat_id = str(message.chat.id)

    # ✅ Гарантируем, что старт пробника установлен
    if chat_id not in trial_start_times:
        trial_start_times[chat_id] = time.time()

    # ✅ Проверка лимитов токенов и времени
    tokens_used = user_token_limits.get(chat_id, 0)
    time_elapsed = time.time() - trial_start_times[chat_id]
    if time_elapsed > TRIAL_DURATION_SECONDS or tokens_used >= TRIAL_TOKEN_LIMIT:
    # ⚠️ Уведомление о завершении пробника + кнопки с тарифами
        return_url = "https://t.me/NeiroMaxBot"
        buttons = []
        tariffs = [
            ("GPT-3.5: Lite — 199₽", 199, "GPT-3.5 Lite"),
            ("GPT-3.5: Pro — 299₽", 299, "GPT-3.5 Pro"),
            ("GPT-3.5: Max — 399₽", 399, "GPT-3.5 Max"),
            ("GPT-4o: Lite — 299₽", 299, "GPT-4o Lite"),
            ("GPT-4o: Pro — 499₽", 499, "GPT-4o Pro"),
            ("GPT-4o: Max — 999₽", 999, "GPT-4o Max")
        ]
        for label, price, desc in tariffs:
            url = create_payment(price, desc, return_url, chat_id)
            if url:
                buttons.append(types.InlineKeyboardButton(f"💳 {label}", url=url))
        markup = types.InlineKeyboardMarkup(row_width=1)
        for btn in buttons:
            markup.add(btn)
        bot.send_message(
            chat_id,
            "⛔ Пробный период завершён.\n\nВыберите тариф для продолжения работы:",
            reply_markup=markup
        )
        return
    prompt = message.text.strip()
    mode = user_modes.get(chat_id, "копирайтер")
    model = user_models.get(chat_id, "gpt-3.5-turbo")

    # 🔒 Фильтрация по стилю
    forbidden = {
        "копирайтер": ["психолог", "депресс", "поддерж", "тревож"],
        "деловой": ["юмор", "шутк", "прикол"],
        "гопник": ["академ", "научн", "профессор"],
        "профессор": ["шутк", "гопник", "жиза"]
    }
    if any(word in prompt.lower() for word in forbidden.get(mode, [])):
        bot.send_message(chat_id, f"⚠️ Сейчас выбран стиль: <b>{mode.capitalize()}</b>.\nЗапрос не соответствует выбранному стилю.\nСначала измени стиль через кнопку 💡", parse_mode="HTML")
        return

    # Загрузка истории
    history = load_history(chat_id)
    messages = [{"role": "system", "content": available_modes[mode]}] + history + [{"role": "user", "content": prompt}]

    try:
        response = openai.ChatCompletion.create(model=model, messages=messages)
        reply = response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка: {e}")
        return

    # ✅ Сохраняем токены и историю
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
      @bot.message_handler(func=lambda message: message.text.lower() in ['привет', 'начать', 'запуск', 'hello', 'hi'])
def handle_first_message(message):
    chat_id = str(message.chat.id)

    if chat_id not in used_trials:
        # Устанавливаем пробник
        used_trials[chat_id] = True
        trial_start_times[chat_id] = time.time()
        save_used_trials(used_trials)
        bot.send_message(chat_id, f"Привет! Я {BOT_NAME} — твой ассистент. Чем могу помочь? 😏", reply_markup=main_menu(chat_id))
        user_modes[message.chat.id] = "копирайтер"
        user_histories[message.chat.id] = []
        user_models[message.chat.id] = "gpt-3.5-turbo"
        user_token_limits[message.chat.id] = 0
    else:
        bot.send_message(chat_id, f"Привет снова! Вот твоё главное меню:", reply_markup=main_menu(chat_id))

print("🤖 Neiro Max запущен.")
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

@app.route("/yookassa/webhook", methods=["POST"])
def yookassa_webhook():
    data = request.json
    if data.get('event') == 'payment.succeeded':
        obj = data['object']
        description = obj.get("description", "")
        metadata = obj.get("metadata", {})
        chat_id = metadata.get("chat_id")

        if not chat_id:
            return jsonify({"status": "chat_id missing"})

        # Определяем модель
        if "GPT-3.5" in description:
            model = "gpt-3.5-turbo"
        elif "GPT-4" in description:
            model = "gpt-4o"
        else:
            return jsonify({"status": "unknown model"})

        # 🔐 Защита от повторной отправки
        if chat_id in user_models:
            print(f"[Webhook] Модель уже активирована для chat_id={chat_id}")
            return jsonify({"status": "already activated"})

        user_models[chat_id] = model
        bot.send_message(chat_id, f"✅ Оплата прошла успешно!\nАктивирован тариф: <b>{description}</b>", parse_mode="HTML")

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
