
import os
import json
import time
from pathlib import Path
from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
from telebot import TeleBot, types
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
def preprocess_image_for_ocr(image: Image.Image) -> Image.Image:
    # Перевод в оттенки серого
    gray = image.convert('L')

    # Усиление контраста
    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.0)

    # Чистим шум
    gray = gray.filter(ImageFilter.MedianFilter(size=3))

    # Бинаризация (черно-белое изображение)
    bw = gray.point(lambda x: 0 if x < 140 else 255, '1')

    return bw


USED_TRIALS_FILE = "used_trials.json"
TRIAL_TIMES_FILE = "trial_times.json"
MEMORY_DIR = "memory"
ADMIN_ID = 1034982624
MAX_HISTORY = 20
TRIAL_TOKEN_LIMIT = 10_000
TRIAL_DURATION_SECONDS = 86400  # 24 часа
BOT_NAME = "Neiro Max"

user_token_limits = {}
user_modes = {}
user_histories = {}
user_models = {}
trial_start_times = {}
# ✅ Блок проверки подписки и пробника
def check_access_and_notify(chat_id):
    now = time.time()
    tokens_used = user_token_limits.get(chat_id, 0)

    # === Проверка пробного периода ===
    is_trial = str(chat_id) not in user_models or user_models[str(chat_id)] == "gpt-3.5-turbo"
    trial_start = trial_start_times.get(str(chat_id))

    if is_trial and trial_start:
        time_elapsed = now - trial_start
        if time_elapsed > TRIAL_DURATION_SECONDS or tokens_used >= TRIAL_TOKEN_LIMIT:
            # ЖЁСТКАЯ БЛОКИРОВКА
            bot.send_message(chat_id, "⛔ Пробный период завершён. Для продолжения выберите тариф.")
            return False

    # === Проверка оплаченного тарифа ===
    subscription_file = "subscriptions.json"
    if os.path.exists(subscription_file):
        with open(subscription_file, "r", encoding="utf-8") as f:
            subscriptions = json.load(f)
    else:
        subscriptions = {}

    sub_data = subscriptions.get(str(chat_id))
    if sub_data:
        expires_at = sub_data.get("expires_at")
        warned = sub_data.get("warned", False)
        token_limit = sub_data.get("token_limit", 100000)

        # Лимит токенов исчерпан — блок
        if tokens_used >= token_limit:
            bot.send_message(chat_id, "⛔ Вы исчерпали лимит токенов. Пожалуйста, продлите подписку.")
            return False

        # Срок действия подписки истёк — блок
        if expires_at and now > expires_at:
            bot.send_message(chat_id, "⛔ Срок действия вашего тарифа истёк. Пожалуйста, выберите новый тариф.")
            return False

        # Предупреждение за 24 часа до окончания
        if expires_at and not warned and expires_at - now <= 86400:
            bot.send_message(chat_id, "⚠️ Ваш тариф заканчивается через 24 часа. Не забудьте продлить доступ.")
            subscriptions[str(chat_id)]["warned"] = True
            with open(subscription_file, "w", encoding="utf-8") as f:
                json.dump(subscriptions, f, indent=2)

    return True


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
    markup.add("📘 Правила", "📞 Поддержка")
    if chat_id and is_admin(chat_id):
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

    # Минимальная инициализация
    user_modes[message.chat.id] = "копирайтер"
    user_histories[message.chat.id] = []

    if message.chat.id == ADMIN_ID:
        user_models[message.chat.id] = "gpt-4o"
    else:
        user_models[message.chat.id] = "gpt-3.5-turbo"

    user_token_limits[message.chat.id] = 0

    bot.send_message(
        message.chat.id,
        f"Привет! Я {BOT_NAME} — твой AI-ассистент 🤖\n\nНажми кнопку «🚀 Запустить Neiro Max» ниже, чтобы начать.",
        reply_markup=main_menu(message.chat.id)
    )

from PIL import Image, ImageEnhance, ImageFilter
from pdf2image import convert_from_bytes
import pytesseract
from io import BytesIO

@bot.message_handler(content_types=['document', 'photo'])
def handle_ocr_file(message):
    try:
        file_id = message.document.file_id if message.content_type == 'document' else message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        file_bytes = BytesIO(downloaded_file)

        text = ''
        if message.content_type == 'document' and message.document.mime_type == 'application/pdf':
            images = convert_from_bytes(file_bytes.read(), dpi=300)
        else:
            img = Image.open(file_bytes)
            images = [img]

        for img in images:
            # Предобработка изображения
            processed_img = preprocess_image_for_ocr(img)

            # OCR
            text += pytesseract.image_to_string(processed_img, lang='rus+eng') + '\n'

        text = text.strip()
        if not text:
            text = '🧐 Не удалось распознать текст. Загрузите более чёткое изображение или PDF.'
            # Выводим распознанный текст в консоль
print("📄 Результат OCR:\n", text)

# Сохраняем изображение, которое подали в Tesseract
# (Это поможет понять, правильно ли оно предобработалось)
img.save(f"/tmp/ocr_debug_{time.time()}.png")


        bot.send_message(message.chat.id, f'📄 Распознанный текст:\n\n{text[:4000]}')

    except Exception as e:
        bot.send_message(message.chat.id, f'❌ Ошибка при обработке файла:\n{e}')




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
        ("GPT-4o: Business Pro – 2000₽", 2000, "GPT-4o Business Pro"),

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

# === Business Pro (GPT-4o) — меню и функции ===
# Зависимости (если частично уже импортированы — дубли безопасны)
import os, io, re, json, base64, zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import telebot
from telebot import types

# --- внешние библиотеки для работы с файлами ---
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    import docx  # python-docx
except Exception:
    docx = None

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas
except Exception:
    rl_canvas = None

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
except Exception:
    openpyxl = None

# --- OpenAI клиент: поддержка нового и старого SDK ---
_OAI_MODELS = {"business_pro": "gpt-4o"}

def _oai_client_factory():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        def chat(messages, model="gpt-4o", temperature=0.2):
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature
            )
            return resp.choices[0].message.content
        def chat_vision(messages, model="gpt-4o"):
            # messages — уже в формате [{"role":"user","content":[{type:"text"...},{type:"input_image",...}]}]
            resp = client.chat.completions.create(model=model, messages=messages)
            return resp.choices[0].message.content
        return chat, chat_vision
    except Exception:
        # старый SDK
        try:
            import openai
            openai.api_key = api_key
            def chat(messages, model="gpt-4o", temperature=0.2):
                resp = openai.ChatCompletion.create(
                    model=model,
                    messages=messages,
                    temperature=temperature
                )
                return resp["choices"][0]["message"]["content"]
            # старый SDK не поддерживает vision-формат в ChatCompletion удобно — дадим OCR+текст без картинки
            def chat_vision(messages, model="gpt-4o"):
                # упадём обратно на обычный чат: в messages ожидаем первый текстовый кусок
                flat = []
                for m in messages:
                    text_parts = []
                    content = m.get("content")
                    if isinstance(content, list):
                        for part in content:
                            if part.get("type") == "text":
                                text_parts.append(part.get("text",""))
                    else:
                        text_parts.append(str(content))
                    flat.append({"role": m.get("role","user"), "content": "\n".join(text_parts)})
                return chat(flat, model=model)
            return chat, chat_vision
        except Exception:
            return None, None

_bp_chat, _bp_chat_vision = _oai_client_factory()

# --- директории ---
BP_DIR = Path(__file__).parent / "bp_files"
BP_DIR.mkdir(parents=True, exist_ok=True)

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

# --- таблица подписок Business Pro (PostgreSQL через pg_conn, если доступен) ---
def _bp_db_init():
    try:
        conn = pg_conn()
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_tariffs (
                    user_id BIGINT PRIMARY KEY,
                    tariff_code TEXT NOT NULL,
                    expires TIMESTAMP NOT NULL
                )
            """)
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[BP] DB init skip/fallback: {e}")

_bp_db_init()

def bp_set_tariff(user_id: int, tariff_code: str, days: int):
    """Выдать/продлить тариф пользователю (используется YooKassa-хендлером или админом)."""
    try:
        conn = pg_conn()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT expires FROM user_tariffs WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            now = _utcnow()
            cur_exp = row[0] if row else None
            new_exp = (max(cur_exp, now) if cur_exp else now) + timedelta(days=max(1, days))
            cur.execute("""
                INSERT INTO user_tariffs (user_id, tariff_code, expires)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET tariff_code=EXCLUDED.tariff_code, expires=EXCLUDED.expires
            """, (user_id, tariff_code, new_exp))
        conn.commit(); conn.close()
        return new_exp
    except Exception as e:
        print(f"[BP] bp_set_tariff fallback ({e}). Allow ADMIN_ID only.")
        # Фоллбек: без БД — только админ имеет доступ
        return None

def bp_is_active_business_pro(user_id: int) -> bool:
    """Проверка активности Business Pro. Фоллбек: если нет БД — активен только для ADMIN_ID."""
    try:
        conn = pg_conn()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT tariff_code, expires FROM user_tariffs WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
        conn.close()
        if not row:
            return False
        code, exp = row
        return (code == "business_pro") and (_utcnow() < exp)
    except Exception:
        return (str(user_id) == str(ADMIN_ID))  # фоллбек-настройка: тестим на администраторе

# --- мини-состояния диалога Business Pro ---
BP_STATE = {}  # { user_id: {"mode": "...", ...} }

def bp_require_active(message) -> bool:
    """Гейт: пропускает только активных по тарифу."""
    uid = message.from_user.id
    if not bp_is_active_business_pro(uid):
        bot.send_message(message.chat.id, "🔒 Доступно по тарифу <b>GPT-4o Business Pro</b>.\nОформите/активируйте тариф и повторите.", parse_mode="HTML")
        return False
    if _bp_chat is None:
        bot.send_message(message.chat.id, "⚠️ Не настроен OPENAI_API_KEY. Укажи ключ и перезапусти бота.")
        return False
    return True

# --- главное меню Business Pro ---
def bp_menu_markup():
    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("📄 Проанализировать документ", callback_data="bp_doc"))
    mk.add(types.InlineKeyboardButton("🖼 Фото: OCR + разбор (GPT-4o Vision)", callback_data="bp_photo"))
    mk.add(types.InlineKeyboardButton("🧾 Сгенерировать документ (DOCX/PDF)", callback_data="bp_gen"))
    mk.add(types.InlineKeyboardButton("📊 Excel-ассистент", callback_data="bp_excel"))
    return mk

@bot.message_handler(commands=['business_pro','bp'])
def bp_cmd_menu(message):
    if message.chat.type != "private":
        bot.reply_to(message, "Открой это меню в ЛС со мной.")
        return
    if not bp_require_active(message): return
    BP_STATE.pop(message.from_user.id, None)
    bot.send_message(
        message.chat.id,
        "📂 <b>Business Pro</b> — расширенные инструменты документов, фото и Excel.",
        reply_markup=bp_menu_markup(),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda c: c.data in ["bp_doc","bp_photo","bp_gen","bp_excel"])
def bp_cb_menu(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    fake = types.SimpleNamespace(chat=types.SimpleNamespace(id=call.message.chat.id), from_user=call.from_user)
    if call.message.chat.type != "private":
        bot.send_message(call.message.chat.id, "Открой это меню в ЛС со мной.")
        return
    if not bp_is_active_business_pro(call.from_user.id):
        bot.send_message(call.message.chat.id, "🔒 Доступно по тарифу <b>GPT-4o Business Pro</b>.", parse_mode="HTML"); return

    uid = call.from_user.id
    if call.data == "bp_doc":
        BP_STATE[uid] = {"mode":"doc_wait"}
        bot.send_message(call.message.chat.id, "📄 Пришли файл: PDF/DOCX/TXT/RTF/ODT. Можно несколько подряд.")
    elif call.data == "bp_photo":
        BP_STATE[uid] = {"mode":"photo_wait"}
        bot.send_message(call.message.chat.id, "🖼 Пришли фото (jpg/png). Я сделаю OCR и разбор содержимого.")
    elif call.data == "bp_gen":
        # выбор формата
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("DOCX", callback_data="bp_gen_docx"),
               types.InlineKeyboardButton("PDF",  callback_data="bp_gen_pdf"))
        BP_STATE[uid] = {"mode":"gen_choose"}
        bot.send_message(call.message.chat.id, "🧾 Выбери формат итогового файла:", reply_markup=mk)
    elif call.data == "bp_excel":
        BP_STATE[uid] = {"mode":"excel_wait_file"}
        bot.send_message(call.message.chat.id,
                         "📊 Пришли Excel (.xlsx) или напиши «новая таблица» — затем отправь инструкцию (что сделать).")

@bot.callback_query_handler(func=lambda c: c.data in ["bp_gen_docx","bp_gen_pdf"])
def bp_cb_gen_format(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    if call.message.chat.type != "private":
        return
    if not bp_is_active_business_pro(call.from_user.id):
        bot.send_message(call.message.chat.id, "🔒 Доступно по тарифу <b>GPT-4o Business Pro</b>.", parse_mode="HTML"); return
    fmt = "docx" if call.data.endswith("docx") else "pdf"
    BP_STATE[call.from_user.id] = {"mode":"gen_prompt", "format": fmt}
    bot.send_message(call.message.chat.id, "🧾 Опиши, что сгенерировать (структура, стиль, язык). Я создам файл.")

# --- парсинг документов ---
def _read_txt(path: Path) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_bytes().decode(errors="ignore")

def _read_docx(path: Path) -> str:
    if not docx: return ""
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    # таблицы
    for t in d.tables:
        for row in t.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(p for p in parts if p)

def _read_pdf(path: Path) -> str:
    if not fitz: return ""
    try:
        doc = fitz.open(str(path))
        text_chunks = []
        for page in doc:
            t = page.get_text().strip()
            if t:
                text_chunks.append(t)
        return "\n".join(text_chunks).strip()
    except Exception:
        return ""

def _pdf_ocr(path: Path) -> str:
    """OCR всех страниц PDF в текст (если обычного текста нет)."""
    if not (fitz and Image and pytesseract):
        return ""
    try:
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            pm = page.get_pixmap(dpi=220)
            img = Image.open(io.BytesIO(pm.tobytes()))
            txt = pytesseract.image_to_string(img, lang="rus+eng")
            if txt.strip():
                parts.append(txt)
        return "\n".join(parts).strip()
    except Exception:
        return ""

def _read_rtf(raw: str) -> str:
    # очень грубая очистка RTF (для полноценного — пандок/rtfparse)
    txt = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)      # hex escapes
    txt = re.sub(r"\\[a-z]+-?\d*", " ", txt)          # команды \b, \par, \fs24 и т.п.
    txt = re.sub(r"[{}]", " ", txt)                   # фигурные скобки
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()

def _read_odt(path: Path) -> str:
    # распаковка и выдёргивание content.xml
    try:
        with zipfile.ZipFile(str(path), 'r') as z:
            with z.open("content.xml") as f:
                xml = f.read().decode("utf-8", errors="ignore")
        # грубый текст из XML
        xml = re.sub(r"<text:line-break\s*/?>", "\n", xml)
        xml = re.sub(r"<[^>]+>", " ", xml)
        xml = re.sub(r"\s+", " ", xml)
        return xml.strip()
    except Exception:
        return ""

def bp_extract_text(file_path: Path, mime: str|None, ext: str) -> str:
    ext = ext.lower().lstrip(".")
    try:
        if ext == "pdf":
            t = _read_pdf(file_path)
            if not t:
                t = _pdf_ocr(file_path)
            return t
        if ext == "docx" and docx:
            return _read_docx(file_path)
        if ext == "txt":
            return _read_txt(file_path)
        if ext == "rtf":
            raw = _read_txt(file_path)
            return _read_rtf(raw)
        if ext == "odt":
            return _read_odt(file_path)
    except Exception as e:
        print(f"[BP] extract error: {e}")
    return ""

def bp_analyze_text_with_gpt(text: str, filename: str) -> str:
    text_short = text[:15000]  # защита токенов
    prompt = (
        "Ты — строгий аналитик документов. Кратко и по делу:\n"
        "1) Суть документа (2–4 предложения).\n"
        "2) Важные факты и цифры (списком).\n"
        "3) Риски/несоответствия.\n"
        "4) Итог/рекомендации.\n\n"
        f"Имя файла: {filename}\nТекст:\n{text_short}"
    )
    return _bp_chat([
        {"role":"system","content":"Отвечай по-русски, чётко, без воды."},
        {"role":"user","content":prompt}
    ], model=_OAI_MODELS["business_pro"])

def _save_tele_file(file_id: str, prefer_ext: str|None=None) -> Path:
    f = bot.get_file(file_id)
    b = bot.download_file(f.file_path)
    ext = os.path.splitext(f.file_path)[1] or (prefer_ext or "")
    name = f"bp_{int(datetime.now().timestamp())}_{file_id.replace('/','_')}{ext}"
    path = BP_DIR / name
    with open(path, "wb") as out:
        out.write(b)
    return path

# --- обработка документов ---
@bot.message_handler(content_types=['document'])
def bp_on_document(message):
    if message.chat.type != "private": 
        return
    uid = message.from_user.id
    state = BP_STATE.get(uid, {})
    if state.get("mode") not in ("doc_wait","excel_wait_file"):
        return  # не наш сценарий

    if not bp_require_active(message): 
        return

    doc = message.document
    file_path = _save_tele_file(doc.file_id)
    ext = (doc.file_name or "").split(".")[-1].lower() if doc.file_name else file_path.suffix.lstrip(".")
    mime = doc.mime_type or ""

    # Excel-ветка
    if state.get("mode") == "excel_wait_file":
        if ext != "xlsx" or not openpyxl:
            bot.reply_to(message, "Нужен .xlsx (и установлен openpyxl). Пришли Excel или напиши «новая таблица».")
            return
        BP_STATE[uid] = {"mode":"excel_wait_instr", "xlsx_path": str(file_path)}
        bot.reply_to(message, "Файл получен. Теперь пришли инструкцию: что нужно сделать с таблицей.")
        return

    # Анализ документа
    bot.send_chat_action(message.chat.id, "typing")
    text = bp_extract_text(file_path, mime, ext)
    if not text:
        bot.reply_to(message, "Не удалось извлечь текст (нужны PyMuPDF/python-docx/pytesseract).")
        return
    try:
        analysis = bp_analyze_text_with_gpt(text, doc.file_name or file_path.name)
        bot.send_message(message.chat.id, f"✅ Разбор файла <b>{doc.file_name or file_path.name}</b>:\n\n{analysis}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка анализа: {e}")

# --- обработка фото: OCR + Vision ---
@bot.message_handler(content_types=['photo'])
def bp_on_photo(message):
    if message.chat.type != "private": 
        return
    uid = message.from_user.id
    state = BP_STATE.get(uid, {})
    if state.get("mode") != "photo_wait":
        return
    if not bp_require_active(message):
        return

    if Image is None:
        bot.reply_to(message, "Нужна библиотека Pillow (PIL).")
        return

    ph = message.photo[-1]  # лучшее качество
    img_path = _save_tele_file(ph.file_id, prefer_ext=".jpg")

    # OCR
    ocr_text = ""
    if pytesseract:
        try:
            img = Image.open(str(img_path))
            ocr_text = pytesseract.image_to_string(img, lang="rus+eng").strip()
        except Exception as e:
            print(f"[BP] OCR error: {e}")

    # Vision (если доступен новый SDK)
    vision_note = ""
    gpt_desc = ""
    if _bp_chat_vision is not None:
        try:
            with open(str(img_path), "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            messages = [{
                "role":"user",
                "content":[
                    {"type":"text","text":"Опиши, что на изображении, и сделай краткий анализ (если это документ/скрин — прокомментируй смысл)."},
                    {"type":"input_image","image_url": f"data:image/jpeg;base64,{b64}"}
                ]
            }]
            gpt_desc = _bp_chat_vision(messages, model=_OAI_MODELS["business_pro"])
        except Exception as e:
            vision_note = f"\n(vision недоступен: {e})"

    result_parts = []
    if gpt_desc:
        result_parts.append("🖼 <b>Визуальный разбор</b>:\n" + gpt_desc)
    if ocr_text:
        # Сжимаем OCR с комментарием GPT
        try:
            comment = _bp_chat([
                {"role":"system","content":"Кратко резюмируй и структурируй текст. Отвечай по-русски."},
                {"role":"user","content": f"Текст из OCR изображения:\n{ocr_text[:8000]}"}
            ], model=_OAI_MODELS["business_pro"])
        except Exception as e:
            comment = f"(не удалось проанализировать OCR: {e})"
        result_parts.append("🔎 <b>OCR-текст (сжато)</b>:\n" + comment)

    if not result_parts:
        bot.reply_to(message, f"Не удалось проанализировать изображение.{vision_note}")
    else:
        bot.send_message(message.chat.id, "\n\n".join(result_parts), parse_mode="HTML")

# --- генерация документов DOCX/PDF ---
def bp_generate_docx(text: str, out_path: Path):
    if not docx:
        raise RuntimeError("python-docx не установлен")
    d = docx.Document()
    for para in text.split("\n"):
        d.add_paragraph(para)
    d.save(str(out_path))

def bp_generate_pdf(text: str, out_path: Path):
    if not rl_canvas:
        raise RuntimeError("reportlab не установлен")
    c = rl_canvas.Canvas(str(out_path), pagesize=A4)
    width, height = A4
    x, y = 40, height - 40
    for line in text.split("\n"):
        if y < 60:
            c.showPage(); y = height - 40
        c.drawString(x, y, line[:120])
        y -= 16
    c.save()

@bot.message_handler(func=lambda m: BP_STATE.get(m.from_user.id,{}).get("mode")=="gen_prompt", content_types=['text'])
def bp_on_gen_prompt(message):
    if not bp_require_active(message): return
    uid = message.from_user.id
    fmt = BP_STATE[uid].get("format","docx")
    bot.send_chat_action(message.chat.id, "typing")

    # просим GPT сделать контент
    prompt = (
        "Сгенерируй структурированный документ по запросу пользователя. "
        "Добавь заголовки, списки, краткие выводы, без воды."
    )
    try:
        content = _bp_chat([
            {"role":"system","content":"Ты — сильный русскоязычный техрайтер. Делаешь структурированный документ."},
            {"role":"user","content": prompt + "\nЗапрос:\n" + message.text}
        ], model=_OAI_MODELS["business_pro"])
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка генерации: {e}")
        return

    ts = int(datetime.now().timestamp())
    out = BP_DIR / f"bp_doc_{ts}.{fmt}"
    try:
        if fmt == "docx":
            bp_generate_docx(content, out)
        else:
            bp_generate_pdf(content, out)
        bot.send_document(message.chat.id, open(out, "rb"), visible_file_name=out.name,
                          caption=f"✅ Готово: {out.name}")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Не удалось сохранить файл: {e}")
    finally:
        BP_STATE.pop(uid, None)

# --- Excel-ассистент ---
def bp_excel_parse_plan(nl_text: str) -> dict:
    """
    Просим GPT выдать JSON-план (разрешённые операции):
      - add_sheet: {"name": str}
      - set_cell: {"sheet":str,"cell":str,"value":str}
      - sum_column: {"sheet":str,"column":str,"to_cell":str}
      - write_table: {"sheet":str,"start_cell":str,"headers":[...], "rows":[[...],...]}
    """
    plan_raw = _bp_chat([
        {"role":"system","content":"Преобразуй инструкцию пользователя для Excel в JSON-план с операциями из белого списка."},
        {"role":"user","content": (
            "Разрешённые операции: add_sheet, set_cell, sum_column, write_table.\n"
            "Формат строго JSON: {\"ops\":[{\"op\":\"add_sheet\",\"name\":\"Лист1\"}, ...]}.\n"
            "Если нужна новая книга — просто используй операции.\n"
            "Инструкция:\n" + nl_text
        )}
    ], model=_OAI_MODELS["business_pro"])
    try:
        m = re.search(r"\{.*\}", plan_raw, flags=re.S)
        data = json.loads(m.group(0) if m else plan_raw)
        if not isinstance(data, dict) or "ops" not in data:
            raise ValueError("bad plan")
        return data
    except Exception:
        return {"ops":[]}

def bp_excel_apply_plan(xlsx_path: Path|None, plan: dict) -> Path:
    if not openpyxl:
        raise RuntimeError("openpyxl не установлен")
    if xlsx_path and Path(xlsx_path).exists():
        wb = openpyxl.load_workbook(str(xlsx_path))
    else:
        wb = openpyxl.Workbook()

    def get_ws(name: str):
        return wb[name] if name in wb.sheetnames else wb.create_sheet(title=name)

    for op in plan.get("ops", []):
        try:
            if op.get("op") == "add_sheet":
                nm = op.get("name","Лист1")[:31]
                if nm not in wb.sheetnames:
                    wb.create_sheet(title=nm)
            elif op.get("op") == "set_cell":
                ws = get_ws(op.get("sheet","Лист1"))
                ws[op.get("cell","A1")].value = op.get("value","")
            elif op.get("op") == "sum_column":
                ws = get_ws(op.get("sheet","Лист1"))
                col = op.get("column","A").upper()
                to_cell = op.get("to_cell","A100")
                # ищем последнюю заполненную строку
                last = 1
                for r in range(1, ws.max_row+1):
                    if ws[f"{col}{r}"].value not in (None,""):
                        last = r
                ws[to_cell] = f"=SUM({col}1:{col}{last})"
            elif op.get("op") == "write_table":
                ws = get_ws(op.get("sheet","Лист1"))
                start = op.get("start_cell","A1")
                m = re.match(r"([A-Z]+)(\d+)", start)
                if not m: 
                    continue
                c0, r0 = m.group(1), int(m.group(2))
                headers = op.get("headers",[])
                rows = op.get("rows",[])
                # заголовки
                for i, h in enumerate(headers, start=0):
                    col_letter = get_column_letter(openpyxl.utils.column_index_from_string(c0) + i)
                    ws[f"{col_letter}{r0}"] = h
                # данные
                for j, row in enumerate(rows, start=1):
                    for i, v in enumerate(row, start=0):
                        col_letter = get_column_letter(openpyxl.utils.column_index_from_string(c0) + i)
                        ws[f"{col_letter}{r0+j}"] = v
        except Exception as e:
            print(f"[BP] excel op error: {e}")

    out = BP_DIR / f"bp_excel_{int(datetime.now().timestamp())}.xlsx"
    # удаляем пустой дефолтный лист, если не нужен
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        try: 
            ws = wb["Sheet"]; wb.remove(ws)
        except Exception: 
            pass
    wb.save(str(out))
    return out

@bot.message_handler(func=lambda m: BP_STATE.get(m.from_user.id,{}).get("mode")=="excel_wait_file", content_types=['text'])
def bp_excel_text_new_or_wait(message):
    if message.chat.type != "private": return
    if not bp_require_active(message): return
    txt = (message.text or "").strip().lower()
    if "новая" in txt:
        # без файла — ждём инструкцию
        BP_STATE[message.from_user.id] = {"mode":"excel_wait_instr", "xlsx_path": None}
        bot.reply_to(message, "Ок. Пришли инструкцию — что создать в таблице.")
    else:
        bot.reply_to(message, "Пришли .xlsx или напиши «новая таблица».")

@bot.message_handler(func=lambda m: BP_STATE.get(m.from_user.id,{}).get("mode")=="excel_wait_instr", content_types=['text'])
def bp_excel_on_instruction(message):
    if not bp_require_active(message): return
    st = BP_STATE.get(message.from_user.id,{})
    xlsx_path = st.get("xlsx_path")
    bot.send_chat_action(message.chat.id, "typing")
    try:
        plan = bp_excel_parse_plan(message.text)
        out = bp_excel_apply_plan(Path(xlsx_path) if xlsx_path else None, plan)
        bot.send_document(message.chat.id, open(out,"rb"), visible_file_name=out.name, caption="✅ Готово")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Не удалось обработать Excel: {e}")
    finally:
        BP_STATE.pop(message.from_user.id, None)

# --- YooKassa webhook: поддержка Business Pro (опционально) ---
# Если в metadata платежа есть {"tariff":"business_pro","bp_user_id": "<ид>","days":"30"},
# этот блок активирует подписку пользователю.
from flask import request
try:
    @app.route("/yookassa-webhook-business-pro", methods=["POST"])
    def yk_bp_webhook():
        try:
            payload = request.get_json(force=True) or {}
            if payload.get("event") != "payment.succeeded":
                return "", 200
            obj  = payload.get("object", {}) or {}
            meta = obj.get("metadata", {}) or {}

            if str(meta.get("tariff")) != "business_pro":
                return "", 200

            uid_raw = str(meta.get("bp_user_id") or "").strip()
            days_raw = str(meta.get("days") or "30").strip()
            uid = int(uid_raw) if uid_raw.isdigit() else None
            days = int(days_raw) if days_raw.isdigit() else 30
            if not uid:
                return "", 200

            exp = bp_set_tariff(uid, "business_pro", days)
            try:
                bot.send_message(uid, f"✅ Тариф <b>GPT-4o Business Pro</b> активирован. Доступ до {exp.strftime('%d.%m.%Y %H:%M')} UTC.\nКоманда: /bp", parse_mode="HTML")
            except Exception as e:
                print(f"[BP] notify user err: {e}")
        except Exception as e:
            print(f"[BP] yk webhook err: {e}")
        return "", 200
except Exception as e:
    print(f"[BP] webhook route skip: {e}")


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


@bot.message_handler(func=lambda msg: any(phrase in msg.text.lower() for phrase in [
    "как тебя зовут", "твоё имя", "ты кто", "как звать", "называешься", "назови себя"
]))

def handle_bot_name(message):
    bot.send_message(message.chat.id, f"Я — {BOT_NAME}, твой персональный AI-ассистент 😉")



@bot.message_handler(func=lambda msg: msg.text == "📋 Главное меню")
def handle_main_menu(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu(message.chat.id))



@bot.message_handler(func=lambda msg: msg.text == "🚀 Запустить Neiro Max")
def handle_launch_neiro_max(message):
    bot.send_message(message.chat.id, "Готов к работе! Чем могу помочь?", reply_markup=main_menu(message.chat.id))
@bot.message_handler(func=lambda msg: msg.text == "📞 Поддержка")
def handle_support(message):
    bot.send_message(
        message.chat.id,
        "🛠 <b>Поддержка</b>\n\nЕсли возникли вопросы или проблемы, напишите разработчику:\n\n"
        "Telegram: @neiro_max\n"
        "Email: support@neiro-max.ai",
        parse_mode="HTML"
    )




@bot.message_handler(func=lambda msg: msg.text.lower() in [m.lower() for m in available_modes])
def handle_style_selection(message):
    chat_id = str(message.chat.id)
    selected = message.text.lower()
    user_modes[chat_id] = selected
    bot.send_message(chat_id, f"✅ Стиль общения изменён на: <b>{selected.capitalize()}</b>", parse_mode="HTML")
@bot.message_handler(func=lambda msg: msg.text == "🚀 Запустить Neiro Max")
def handle_launch(message):
    chat_id = str(message.chat.id)

    # Повторная инициализация (на всякий случай)
    user_modes[message.chat.id] = "копирайтер"
    user_histories[message.chat.id] = []
    user_models[message.chat.id] = "gpt-3.5-turbo"
    user_token_limits[message.chat.id] = 0

    bot.send_message(
        message.chat.id,
        "Готов к работе! Чем могу помочь? 😉",
        reply_markup=main_menu(chat_id)
    )



@bot.message_handler(func=lambda msg: True)
def handle_prompt(message):
    chat_id = str(message.chat.id)

    # 🔒 Проверка доступа (тариф/пробник)
    if not check_access_and_notify(chat_id):
        return

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
    

    # Проверяем статус
    if data.get("object", {}).get("status") == "succeeded":
        description = data.get("object", {}).get("description", "")
        payment_id = data.get("object", {}).get("id")

        # Получаем chat_id из описания
        try:
            parts = description.split(":")
            chat_id = int(parts[1])
            tariff = parts[2]

            # Устанавливаем модель
            if "gpt-4" in tariff.lower():
                user_models[str(chat_id)] = "gpt-4o"
            else:
                user_models[str(chat_id)] = "gpt-3.5-turbo"

            # Устанавливаем срок подписки (например, 30 дней)
            now = int(time.time())
            subscriptions_file = "subscriptions.json"
            if os.path.exists(subscriptions_file):
                with open(subscriptions_file, "r", encoding="utf-8") as f:
                    subscriptions = json.load(f)
            else:
                subscriptions = {}

            subscriptions[str(chat_id)] = {
                "model": user_models[str(chat_id)],
                "activated_at": now,
                "expires_at": now + 30 * 24 * 60 * 60,
                "token_limit": 100000,
                "warned": False
            }

            with open(subscriptions_file, "w", encoding="utf-8") as f:
                json.dump(subscriptions, f, ensure_ascii=False, indent=2)

            # Уведомляем пользователя
            bot.send_message(chat_id, f"✅ Оплата прошла успешно! Вам активирован тариф: *{tariff}*", parse_mode="Markdown")

        except Exception as e:
            print(f"[webhook error] Ошибка при обработке описания: {e}")

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

        # ✅ Активируем модель
        user_models[chat_id] = model
                # 🎯 Назначение лимита токенов по тарифу
        token_limits = {
            "GPT-3.5 Lite": 50000,
            "GPT-3.5 Pro": 100000,
            "GPT-3.5 Max": 1000000,
            "GPT-4o Lite": 30000,
            "GPT-4o Pro": 60000,
            "GPT-4o Max": 1000000
        }
        token_limit = token_limits.get(description, 100000)

        # 🗓️ Запись срока действия тарифа (30 дней)
        subscription_file = "subscriptions.json"
        try:
            if os.path.exists(subscription_file):
                with open(subscription_file, "r", encoding="utf-8") as f:
                    subscriptions = json.load(f)
            else:
                subscriptions = {}

            expires_at = int(time.time()) + 30 * 86400  # 30 дней вперёд
            subscriptions[str(chat_id)] = {
                "expires_at": expires_at,
                "warned": False,
                "token_limit": token_limit
            }

            with open(subscription_file, "w", encoding="utf-8") as f:
                json.dump(subscriptions, f, indent=2)

            print(f"[YooKassa] Подписка активирована для {chat_id} до {expires_at}")
        except Exception as e:
            print(f"[Ошибка записи подписки]: {e}")

        bot.send_message(chat_id, f"✅ Оплата прошла успешно!\nАктивирован тариф: <b>{description}</b>", parse_mode="HTML")
        return jsonify({"status": "ok"})

    return jsonify({"status": "ignored"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
