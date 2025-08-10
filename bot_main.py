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
from pdf2image import convert_from_bytes  # >>> FIX: был импорт ниже, поднял к остальным

# === КОНФИГ ===
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# === Business Pro: константы и колбэки (gpt-4o закреплён) ===
BUSINESS_PRO_TIER = "business_pro"
BUSINESS_PRO_MODEL = "gpt-4o"
CB_BP_MENU = "bp_menu"
CB_BP_DOC_ANALYZE = "bp_doc_analyze"
CB_BP_OCR_IMAGE = "bp_ocr_image"
CB_BP_EXCEL = "bp_excel"
CB_BP_GEN_DOC = "bp_gen_doc"

def preprocess_image_for_ocr(image: Image.Image) -> Image.Image:
    # 1) фиксируем поворот по EXIF
    img = ImageOps.exif_transpose(image)
    # 2) перевод в градации серого
    img = img.convert("L")
    # 3) апскейл, если мелко
    w, h = img.size
    if max(w, h) < 1600:
        img = img.resize((w * 2, h * 2))
    # 4) автоконтраст + шумодав + лёгкая резкость
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    img = img.filter(ImageFilter.UnsharpMask(radius=1.3, percent=160, threshold=2))
    # 5) бинаризация (порог выше — лучше для rus+eng)
    bw = img.point(lambda x: 0 if x < 160 else 255, '1')
    # Tesseract стабильнее ест 8-битный серый
    return bw.convert("L")


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
    "истории": "Ты — рассказчик. Преврати каждый ответ в интересную историю."
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
        return payment.confirmation.confirmation_url  # >>> FIX: удалён повторяющийся return
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

# >>> FIX: добавил кнопку Business Pro в главное меню, если тариф активен
def main_menu(chat_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🚀 Запустить Neiro Max")
    markup.add("💡 Сменить стиль", "📄 Тарифы")
    markup.add("📘 Правила", "📞 Поддержка")
    if chat_id and is_business_pro_active(chat_id):
        markup.add("📂 Business Pro")
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
# === ADMIN: ручная активация Business Pro ===
@bot.message_handler(commands=['bp_on'])
def bp_on(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "Только админ может включать тариф.")
        return
    chat_id = message.chat.id
    set_active_tier_for_chat(chat_id, BUSINESS_PRO_TIER)  # флаг тарифа
    notify_business_pro_activated(chat_id)                 # сообщение + кнопка


# === Business Pro: простое JSON-хранилище тарифа + проверка ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVE_TIERS_FILE = os.path.join(BASE_DIR, "active_tiers.json")

def _json_read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _json_write(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_active_tier_for_chat(chat_id: int) -> str | None:
    data = _json_read(ACTIVE_TIERS_FILE)
    return data.get(str(chat_id))

def set_active_tier_for_chat(chat_id: int, tier: str | None):
    data = _json_read(ACTIVE_TIERS_FILE)
    key = str(chat_id)
    if tier is None:
        data.pop(key, None)
    else:
        data[key] = tier
    _json_write(ACTIVE_TIERS_FILE, data)

def is_business_pro_active(chat_id: int) -> bool:
    try:
        return get_active_tier_for_chat(chat_id) == BUSINESS_PRO_TIER
    except Exception:
        return False

def notify_business_pro_activated(chat_id: int):
    """Сообщение после активации тарифа + кнопка Business Pro."""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📂 Business Pro", callback_data=CB_BP_MENU))
    bot.send_message(
        chat_id,
        "✅ Ваш тариф: GPT-4o Business Pro активирован\n"
        "Теперь доступны расширенные функции для работы с документами, фото и Excel.",
        reply_markup=kb,
    )

def send_bp_menu(chat_id: int):
    """Инлайн-меню Business Pro."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📄 Анализ документа", callback_data=CB_BP_DOC_ANALYZE),
        types.InlineKeyboardButton("🖼️ OCR / разбор фото", callback_data=CB_BP_OCR_IMAGE),
        types.InlineKeyboardButton("📊 Excel-ассистент", callback_data=CB_BP_EXCEL),
        types.InlineKeyboardButton("📝 Сгенерировать документ", callback_data=CB_BP_GEN_DOC),
    )
    bot.send_message(chat_id, "Выберите функцию Business Pro:", reply_markup=kb)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if WEBHOOK_URL:
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
Path(MEMORY_DIR).mkdir(exist_ok=True)

# === Business Pro: состояние
BP_STATE = {}  # { user_id: {"mode": "doc"|"photo"|"gen"|"excel", "fmt": "docx"|"pdf", "excel_path": "..." } }

# === Business Pro: кнопка в Reply и колбэк
@bot.message_handler(func=lambda m: m.text == "📂 Business Pro")
def open_bp_menu_by_text(message):
    if not is_business_pro_active(message.chat.id):
        bot.reply_to(message, "Эта кнопка доступна только на тарифе Business Pro.")
        return
    send_bp_menu(message.chat.id)

@bot.callback_query_handler(func=lambda c: c.data == CB_BP_MENU)
def open_bp_menu_by_callback(call):
    if not is_business_pro_active(call.message.chat.id):
        bot.answer_callback_query(call.id, "Недоступно без Business Pro")
        return
    send_bp_menu(call.message.chat.id)

# === Business Pro: роутинг инлайн-кнопок
@bot.callback_query_handler(func=lambda c: c.data in (CB_BP_DOC_ANALYZE, CB_BP_OCR_IMAGE, CB_BP_EXCEL, CB_BP_GEN_DOC))
def bp_menu_router(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    chat_id = call.message.chat.id
    user_id = call.from_user.id

    if not is_business_pro_active(chat_id):
        bot.send_message(chat_id, "🔒 Доступно по тарифу <b>GPT-4o Business Pro</b>.", parse_mode="HTML")
        return

    if call.data == CB_BP_DOC_ANALYZE:
        BP_STATE[user_id] = {"mode": "doc"}
        bot.send_message(chat_id, "📄 Пришлите файл: PDF/DOCX/TXT/RTF/ODT.")
        return

    if call.data == CB_BP_OCR_IMAGE:
        BP_STATE[user_id] = {"mode": "photo"}
        bot.send_message(chat_id, "🖼 Пришлите фото/скан (JPG/PNG) или PDF. Сделаю OCR и краткий разбор.")
        return

    if call.data == CB_BP_EXCEL:
        BP_STATE[user_id] = {"mode": "excel"}
        bot.send_message(chat_id, "📊 Пришлите .xlsx или напишите «новая таблица». После — опишите задачу.")
        return

    if call.data == CB_BP_GEN_DOC:
        BP_STATE[user_id] = {"mode": "gen", "fmt": "docx"}  # по умолчанию docx
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("DOCX", callback_data="bp_fmt_docx"),
               types.InlineKeyboardButton("PDF",  callback_data="bp_fmt_pdf"))
        bot.send_message(chat_id, "🧾 Выберите формат результата:", reply_markup=kb)
        return

@bot.callback_query_handler(func=lambda c: c.data in ("bp_fmt_docx", "bp_fmt_pdf"))
def bp_fmt_select(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    st = BP_STATE.get(user_id, {})
    if st.get("mode") != "gen":
        return
    st["fmt"] = "docx" if call.data.endswith("docx") else "pdf"
    BP_STATE[user_id] = st
    bot.send_message(chat_id, "Опишите, какой документ нужен (структура, пункты, стиль).")

# ===== Business Pro: Шаг 2 — анализ документов =====
import io
from datetime import datetime

# Опциональные зависимости — блок работает и без них (тогда PDF/DOCX не разберёт)
try:
    import fitz  # PyMuPDF для PDF
except Exception:
    fitz = None

try:
    import docx as docx_lib  # python-docx для DOCX (не путать с Document)
except Exception:
    docx_lib = None

# Мини-хелпер GPT-4o
def _gpt4o(messages):
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        r = client.chat.completions.create(model="gpt-4o", messages=messages, temperature=0.2)
        return r.choices[0].message.content
    except Exception:
        import openai
        openai.api_key = os.getenv("OPENAI_API_KEY")
        r = openai.ChatCompletion.create(model="gpt-4o", messages=messages, temperature=0.2)
        return r["choices"][0]["message"]["content"]

# Каталог для временных файлов
BP_DIR = Path(__file__).parent / "bp_files"
BP_DIR.mkdir(exist_ok=True)

def _save_tg_file(file_id: str, prefer_ext: str = "") -> Path:
    f = bot.get_file(file_id)
    b = bot.download_file(f.file_path)
    ext = os.path.splitext(f.file_path)[1] or prefer_ext
    name = f"bp_{int(datetime.now().timestamp())}_{file_id.replace('/','_')}{ext}"
    p = BP_DIR / name
    with open(p, "wb") as w:
        w.write(b)
    return p

def _read_pdf_text(path: Path) -> str:
    if not fitz:
        return ""
    try:
        doc = fitz.open(str(path))
        chunks = []
        for page in doc:
            t = page.get_text()
            if t:
                chunks.append(t.strip())
        return "\n".join(chunks).strip()
    except Exception:
        return ""

def _read_docx_text(path: Path) -> str:
    if not docx_lib:
        return ""
    try:
        d = docx_lib.Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text]
        for t in d.tables:
            for row in t.rows:
                parts.append("\t".join(c.text for c in row.cells))
        return "\n".join(parts).strip()
    except Exception:
        return ""

def _read_txt(path: Path) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_bytes().decode(errors="ignore")

def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf_text(path)
    if ext == ".docx":
        return _read_docx_text(path)
    if ext in (".txt", ".rtf", ".odt"):
        # rtf/odt — упрощённо читаем как текст
        return _read_txt(path)
    return ""

@bot.message_handler(content_types=['document'])
def bp_handle_document(message):
    st = BP_STATE.get(message.from_user.id, {})
    mode = st.get("mode")
    if mode not in ("doc", "excel"):
        return  # не наш режим — пропускаем, чтобы не конфликтовать с другими хендлерами

    if message.chat.type != "private":
        bot.reply_to(message, "Отправьте файл в ЛС.")
        return

    if not is_business_pro_active(message.chat.id):
        bot.reply_to(message, "🔒 Доступно по тарифу <b>GPT-4o Business Pro</b>.", parse_mode="HTML")
        return

    # Excel режим
    if mode == "excel":
        if not (message.document and message.document.file_name.lower().endswith(".xlsx")):
            bot.reply_to(message, "Нужен .xlsx файл.")
            return
        path = _save_tg_file(message.document.file_id)
        BP_STATE[message.from_user.id]["excel_path"] = str(path)
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), data_only=True)
            infos = []
            for name in wb.sheetnames:
                ws = wb[name]
                dims = f"{ws.max_row} строк × {ws.max_column} столб."
                headers = [str(c.value) if c.value is not None else "" for c in ws[1]]
                infos.append(f"• {name}: {dims}\n  Заголовки: {', '.join(headers[:10])}")
            bot.reply_to(message, "📊 Найдены листы:\n" + "\n".join(infos) + "\n\nОпишите задание по таблице.")
        except Exception as e:
            bot.reply_to(message, f"⚠️ Ошибка чтения Excel: {e}")
        return

    # Документы (pdf/docx/txt/rtf/odt)
    path = _save_tg_file(message.document.file_id)
    if message.document.file_name.lower().endswith(".xlsx"):
        # Если пользователь перепутал режим — подскажем
        bot.reply_to(message, "Это Excel. Откройте «📊 Excel-ассистент» в меню Business Pro.")
        return

    text = _extract_text(path)
    if not text:
        bot.reply_to(message, "Не удалось извлечь текст. Установи PyMuPDF (PDF) и python-docx (DOCX).")
        BP_STATE.pop(message.from_user.id, None)
        return

    bot.send_chat_action(message.chat.id, "typing")
    try:
        brief = _gpt4o([
            {"role": "system", "content": "Отвечай по-русски, чётко и кратко."},
            {"role": "user", "content":
                "Проанализируй документ и дай:\n"
                "1) краткое резюме (2–3 предложения),\n"
                "2) ключевые факты (списком),\n"
                "3) риски/неточности,\n"
                "4) рекомендации.\n\n"
                f"Имя файла: {message.document.file_name}\n"
                f"Текст:\n{text[:12000]}"}
        ])
        bot.send_message(
            message.chat.id,
            f"✅ Разбор <b>{message.document.file_name}</b>:\n\n{brief}",
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка анализа: {e}")
    finally:
        BP_STATE.pop(message.from_user.id, None)

        if message.content_type == 'document' and (message.document.mime_type == 'application/pdf' or message.document.file_name.lower().endswith(".pdf")):
            # СНАЧАЛА пытаемся вытащить встроенный текст из PDF (без OCR)
            pdf_bytes = file_bytes.getvalue()
            try:
                import fitz  # PyMuPDF
                with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                    parts = []
                    for page in doc:
                        t = page.get_text("text").strip()
                        if t:
                            parts.append(t)
                pdf_text = "\n".join(parts).strip()
            except Exception:
                pdf_text = ""

            if pdf_text:
                # В PDF был нормальный текст — отдаём его без OCR (будет заметно чище)
                bot.send_message(message.chat.id, f'📄 Текст из PDF (без OCR):\n\n{pdf_text[:4000]}')
                return

# Если текста в PDF нет (скан), только тогда идём в OCR
file_bytes.seek(0)
images = convert_from_bytes(file_bytes.read(), dpi=350)  # можно 350–400
  # было 300

        else:
            img = Image.open(file_bytes)
            images = [img]

        text = ''
        for img in images:
            processed_img = preprocess_image_for_ocr(img)
            text += pytesseract.image_to_string(
    processed_img,
    lang='rus+eng',
    config="--oem 3 --psm 6 -c preserve_interword_spaces=1"
) + '\n'


        text = text.strip()
        if not text:
            text = '🧐 Не удалось распознать текст. Загрузите более чёткое изображение или PDF.'

        # >>> FIX: эти строки раньше были «выпали» из функции — ломали код
        print("📄 Результат OCR:\n", text[:500])

        # Сохраняем первый кадр для отладки
        try:
            images[0].save(f"/tmp/ocr_debug_{int(time.time())}.png")
        except Exception:
            pass

        # Если режим Business Pro (photo) — даём структурированный разбор GPT-4o
        if in_bp_photo and is_business_pro_active(message.chat.id):
            summary = _gpt4o([
                {"role": "system", "content": "Кратко структурируй распознанный текст: заголовок, ключевые факты, даты, суммы, имена, возможные действия."},
                {"role": "user", "content": text[:12000]}
            ])
            bot.send_message(message.chat.id, f'🖼️ OCR + разбор:\n\n{summary[:4000]}')
            BP_STATE.pop(message.from_user.id, None)
        else:
            bot.send_message(message.chat.id, f'📄 Распознанный текст:\n\n{text[:4000]}')

    except Exception as e:
        bot.send_message(message.chat.id, f'❌ Ошибка при обработке файла:\n{e}')

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
        full_desc = desc
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

# >>> FIX: убрал дублирующийся второй хэндлер на ту же кнопку, чтобы не было двойных ответов

@bot.message_handler(func=lambda msg: True)
def handle_prompt(message):
    chat_id = str(message.chat.id)

    # Если пользователь в Business Pro ждёт текст по генерации/Excel — обрабатываем здесь в первую очередь
    st = BP_STATE.get(message.from_user.id, {})
    mode = st.get("mode")
    if is_business_pro_active(message.chat.id) and mode in ("gen", "excel"):
        if mode == "gen":
            # Генерация DOCX/PDF из описания
            text_spec = (message.text or "").strip()
            if not text_spec:
                bot.reply_to(message, "Нужен текст с описанием документа.")
                return
            # Получаем черновик текста у gpt-4o
            body = _gpt4o([
                {"role": "system", "content": "Собери структурированный деловой документ на основе описания пользователя. Русский язык."},
                {"role": "user", "content": text_spec[:8000]}
            ])
            # Сохранение DOCX
            doc = Document()
            for para in body.split("\n\n"):
                doc.add_paragraph(para)
            doc_bytes = BytesIO()
            doc.save(doc_bytes)
            doc_bytes.seek(0)

            # PDF (простой вывод)
            pdf_bytes = None
            if st.get("fmt") == "pdf":
                pdf_bytes = BytesIO()
                pdf = canvas.Canvas(pdf_bytes)
                y = 800
                for line in body.split("\n"):
                    pdf.drawString(40, y, line[:95])
                    y -= 15
                    if y < 40:
                        pdf.showPage()
                        y = 800
                pdf.save()
                pdf_bytes.seek(0)

            bot.send_message(message.chat.id, "Готово. Отправляю файлы:")
            bot.send_document(message.chat.id, ("document.docx", doc_bytes))
            if pdf_bytes:
                bot.send_document(message.chat.id, ("document.pdf", pdf_bytes))
            BP_STATE.pop(message.from_user.id, None)
            return

        if mode == "excel":
            # Описание задачи по Excel
            task = (message.text or "").strip().lower()
            path = st.get("excel_path")
            if task.startswith("новая таблица") and not path:
                # создаём минимальную таблицу
                try:
                    import openpyxl
                    from openpyxl import Workbook
                    wb = Workbook()
                    ws = wb.active
                    ws.title = "Data"
                    ws.append(["date", "category", "amount"])
                    ws.append(["2025-08-01", "sales", 1200])
                    ws.append(["2025-08-02", "ads", -300])
                    tmp = BytesIO()
                    wb.save(tmp)
                    tmp.seek(0)
                    bot.send_document(message.chat.id, ("template.xlsx", tmp))
                except Exception as e:
                    bot.send_message(message.chat.id, f"⚠️ Не удалось создать таблицу: {e}")
                BP_STATE.pop(message.from_user.id, None)
                return

            if not path:
                bot.send_message(message.chat.id, "Пришлите .xlsx файл или напишите «новая таблица».")
                return

            # Здесь можно встроить реальное редактирование Excel.
            plan = _gpt4o([
                {"role": "system", "content": "Ты помощник по Excel. Сформируй план шагов по задаче пользователя, укажи формулы/сводные, если уместно."},
                {"role": "user", "content": f"Файл: {os.path.basename(path)}\nЗадача: {message.text[:4000]}"}
            ])
            bot.send_message(message.chat.id, f"📊 План действий:\n{plan}")
            try:
                with open(path, "rb") as f:
                    bot.send_document(message.chat.id, f, visible_file_name=os.path.basename(path))
            except Exception:
                pass
            BP_STATE.pop(message.from_user.id, None)
            return

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
            pdf.drawString(40, y, line[:95])
            y -= 15
            if y < 40:
                pdf.showPage()
                y = 800
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

    # Старый вариант статуса (оставлен для совместимости)
    if data.get("object", {}).get("status") == "succeeded":
        description = data.get("object", {}).get("description", "")
        payment_id = data.get("object", {}).get("id")
        # chat_id теперь берём из metadata во втором блоке — этот оставлен как fallback
        pass

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
            # но всё равно обновим Business Pro, если это он
            if "Business Pro" in description:
                set_active_tier_for_chat(chat_id, BUSINESS_PRO_TIER)
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
            "GPT-4o Max": 1000000,
            "GPT-4o Business Pro": 200000  # >>> Business Pro лимит
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

        # >>> Business Pro: включаем флаг тарифа и показываем меню
        if "Business Pro" in description:
            set_active_tier_for_chat(chat_id, BUSINESS_PRO_TIER)
            notify_business_pro_activated(chat_id)

        bot.send_message(chat_id, f"✅ Оплата прошла успешно!\nАктивирован тариф: <b>{description}</b>", parse_mode="HTML")
        return jsonify({"status": "ok"})

    return jsonify({"status": "ignored"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
