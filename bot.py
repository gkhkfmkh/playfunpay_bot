import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
TIMEZONE = os.getenv("TIMEZONE", "Europe/Vienna")
DAILY_POST_HOUR = int(os.getenv("DAILY_POST_HOUR", "9"))
DB_PATH = os.getenv("DB_PATH", "data/playfunpay.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Заполни BOT_TOKEN в файле .env")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("playfunpay")

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

def init_db():
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        joined_at TEXT NOT NULL, referred_by INTEGER, is_banned INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS themes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        body TEXT DEFAULT '', published INTEGER DEFAULT 0, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', receipt_type TEXT,
        receipt_file_id TEXT, receipt_text TEXT, created_at TEXT NOT NULL,
        reviewed_at TEXT, reviewer_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY, expires_at TEXT NOT NULL,
        kicked INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER NOT NULL, theme_id INTEGER NOT NULL,
        PRIMARY KEY(user_id, theme_id)
    );
    """)
    defaults = {
        "price": "500",
        "description": "Доступ к приватному каналу playfunpay: эксклюзивные гайды, инструкции и полезные материалы по FunPay, Playerok, Discord и цифровым товарам.",
        "payment_instructions": "Здесь пока не указаны реквизиты. Напиши администратору.",
        "channel_id": "",
        "channel_title": "playfunpay VIP",
        "subscription_days": "30",
        "support_text": "Напиши свой вопрос одним сообщением. Администратор ответит, когда будет на связи."
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    db.commit()

def setting(key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def user_upsert(user):
    db.execute("""INSERT INTO users(user_id,username,first_name,joined_at)
        VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name""",
        (user.id, user.username or "", user.first_name or "", datetime.now().isoformat()))
    db.commit()

def add_user(user, ref=None):
    existing = db.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,)).fetchone()
    if not existing:
        db.execute("INSERT INTO users(user_id,username,first_name,joined_at,referred_by) VALUES(?,?,?,?,?)",
                   (user.id, user.username or "", user.first_name or "", datetime.now().isoformat(), ref))
        db.commit()
    else:
        user_upsert(user)

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Приватный канал", callback_data="private")],
        [InlineKeyboardButton(text="📚 Темы и гайды", callback_data="themes"),
         InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data="favorites"),
         InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="referral")],
        [InlineKeyboardButton(text="📰 Новости", callback_data="news"),
         InlineKeyboardButton(text="💬 Поддержка", callback_data="support")]
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")]])

def private_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Оформить доступ", callback_data="buy")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
         InlineKeyboardButton(text="📦 Заявки", callback_data="adm_orders")],
        [InlineKeyboardButton(text="✏️ Настройки", callback_data="adm_help"),
         InlineKeyboardButton(text="📚 Добавить тему", callback_data="adm_theme")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")]
    ])

def money():
    return setting("price", "500")

def format_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso

THEMES = [
"Что такое FunPay: знакомство с площадкой и её правилами",
"Что такое Playerok: обзор площадки для цифровых товаров",
"Как безопасно начать продавать цифровые товары",
"Как выбрать нишу для первого товара",
"Как изучить спрос перед созданием товара",
"Как анализировать конкурентов на маркетплейсе",
"Как оформить профиль продавца",
"Как придумать понятное название товара",
"Как написать честное и понятное описание объявления",
"Как сделать привлекательную обложку товара",
"Как составить список того, что получает покупатель",
"Как правильно определить цену цифрового товара",
"Как подготовить товар перед публикацией",
"Как проверить товар перед передачей покупателю",
"Как отвечать на вопросы покупателей",
"Как вежливо решать спорные ситуации",
"Как собирать отзывы без накруток и обмана",
"Как поддерживать хорошую репутацию продавца",
"Как обновлять старые объявления",
"Как вести учёт заказов и расходов",
"Идеи цифровых товаров для начинающего продавца",
"Как создать полезный PDF-гайд",
"Как сделать инструкцию со скриншотами",
"Как оформить содержание большого гайда",
"Как написать пошаговый обучающий материал",
"Как обновлять гайды при изменении интерфейсов",
"Как создавать наборы цифровых материалов",
"Как составить каталог своих гайдов",
"Как подготовить бонусный материал к покупке",
"Как защитить свои авторские материалы законными способами",
"Как создать Discord-сервер с нуля",
"Как продумать структуру категорий и каналов Discord",
"Как подобрать роли и права доступа",
"Как оформить приветствие нового участника",
"Как настроить систему тикетов",
"Как настроить заявки на вступление в команду",
"Как организовать модерацию Discord-сервера",
"Как подготовить Discord-сервер к передаче заказчику",
"Идеи готовых Discord-серверов для продажи",
"Как составить описание готового Discord-сервера",
"Как сделать портфолио Discord-дизайнера",
"Как создать Telegram-канал с нуля",
"Как оформить Telegram-канал в едином стиле",
"Как составить контент-план на месяц",
"Как писать полезные посты для подписчиков",
"Как делать обложки для публикаций",
"Как продвигать канал без спама",
"Как анализировать статистику Telegram-канала",
"Как создать Telegram-бота на Python",
"Какие задачи можно поручить Telegram-боту",
"Как создать меню Telegram-бота с кнопками",
"Как сделать админ-панель для Telegram-бота",
"Как хранить данные бота в SQLite",
"Как организовать поддержку через бота",
"Как сделать каталог товаров в Telegram",
"Как автоматизировать уведомления о новых заказах",
"Как безопасно хранить токен Telegram-бота",
"Как протестировать бота перед запуском",
"Как подготовить бота к работе 24/7",
"Как составить техническое задание для цифрового продукта",
"Как оценить время на выполнение заказа",
"Как составить понятный прайс-лист",
"Как оформить услугу пакетами",
"Как предложить дополнительные услуги без навязывания",
"Как организовать рабочее место продавца",
"Как вести календарь заказов",
"Как планировать время на создание товаров",
"Как работать с повторными клиентами",
"Как правильно оформить правила магазина",
"Как подготовить шаблоны ответов покупателям",
"Как сделать чек-лист перед публикацией объявления",
"Как улучшить качество цифрового продукта",
"Как находить ошибки в собственных инструкциях",
"Как тестировать ссылки и файлы перед отправкой",
"Как упаковать несколько гайдов в один комплект",
"Как сделать бесплатный материал для знакомства с магазином",
"Как использовать отзывы для улучшения товара",
"Как изучать обновления правил площадок",
"Как отличать реальные заказы от подозрительных предложений",
"Как не передавать покупателям лишние личные данные",
"Как распознавать фишинговые ссылки",
"Как защитить аккаунты с помощью двухфакторной аутентификации",
"Как не нарушать правила FunPay и Playerok",
"Как решать вопросы через официальную поддержку площадки",
"Как составить план развития магазина на 30 дней",
"Как определить товары, которые стоит улучшить",
"Как сравнивать результаты объявлений",
"Как тестировать разные варианты обложек",
"Как написать FAQ для покупателей",
"Как создать бренд цифрового магазина",
"Как выбрать единый стиль оформления",
"Как сделать рекламный пост без ложных обещаний",
"Как подготовить короткое видео о товаре",
"Как использовать нейросети для идей и черновиков",
"Как проверять факты, созданные нейросетью",
"Как сделать систему учёта доходов и расходов",
"Как рассчитать реальную прибыль с учётом комиссий",
"Как улучшать обслуживание покупателей",
"План запуска собственного магазина цифровых товаров"
]

def seed_themes():
    count = db.execute("SELECT COUNT(*) AS n FROM themes").fetchone()["n"]
    if count == 0:
        now = datetime.now().isoformat()
        db.executemany("INSERT INTO themes(title,body,created_at) VALUES(?,?,?)",
                       [(t, "", now) for t in THEMES])
        db.commit()

def subscription_active(user_id):
    row = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return False, None
    expiry = datetime.fromisoformat(row["expires_at"])
    return expiry > datetime.now(), expiry

def grant_subscription(user_id, days):
    current = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    now = datetime.now()
    start = max(now, datetime.fromisoformat(current["expires_at"])) if current else now
    expiry = start + timedelta(days=days)
    db.execute("INSERT INTO subscriptions(user_id,expires_at,kicked) VALUES(?,?,0) ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at,kicked=0",
               (user_id, expiry.isoformat()))
    db.commit()
    return expiry

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
pending_support = set()
pending_receipt = set()
pending_theme = set()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = (message.text or "").split(maxsplit=1)
    ref = None
    if len(args) > 1 and args[1].isdigit():
        candidate = int(args[1])
        if candidate != message.from_user.id:
            ref = candidate
    add_user(message.from_user, ref)
    await message.answer(
        f"💜 <b>Добро пожаловать в playfunpay!</b>\n\n"
        f"Гайды, полезные материалы и идеи для работы с цифровыми товарами, "
        f"FunPay, Playerok, Discord и Telegram.\n\n"
        f"Выбирай нужный раздел в меню 👇",
        reply_markup=main_menu()
    )

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Эта команда доступна только администратору.")
    await message.answer("🛠 <b>Панель управления playfunpay</b>\nВыбери раздел:", reply_markup=admin_kb())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    txt = "Команды:\n/start — главное меню\n/help — помощь\n"
    if is_admin(message.from_user.id):
        txt += ("/admin — админ-панель\n/setprice ЧИСЛО — цена\n"
                "/setdescription ТЕКСТ — описание\n/setpayment ТЕКСТ — инструкции оплаты\n"
                "/setchannel ID — ID приватного канала\n/setdays ЧИСЛО — срок доступа в днях\n"
                "/addtheme ТЕМА — добавить тему\n/broadcast ТЕКСТ — рассылка\n"
                "/stats — статистика\n/giveaccess ID ДНИ — выдать доступ\n")
    await message.answer(txt)

@dp.message(Command("setprice"))
async def setprice(message: Message):
    if not is_admin(message.from_user.id): return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].replace(".", "", 1).isdigit():
        return await message.answer("Использование: <code>/setprice 499</code>")
    set_setting("price", parts[1])
    await message.answer(f"✅ Цена обновлена: <b>{parts[1]}</b>")

@dp.message(Command("setdescription"))
async def setdescription(message: Message):
    if not is_admin(message.from_user.id): return
    value = (message.text or "").partition(" ")[2].strip()
    if not value: return await message.answer("Укажи текст после команды.")
    set_setting("description", value)
    await message.answer("✅ Описание приватного канала обновлено.")

@dp.message(Command("setpayment"))
async def setpayment(message: Message):
    if not is_admin(message.from_user.id): return
    value = (message.text or "").partition(" ")[2].strip()
    if not value: return await message.answer("Укажи реквизиты и инструкцию после команды.")
    set_setting("payment_instructions", value)
    await message.answer("✅ Инструкция по оплате обновлена.")

@dp.message(Command("setchannel"))
async def setchannel(message: Message):
    if not is_admin(message.from_user.id): return
    value = (message.text or "").partition(" ")[2].strip()
    if not value: return await message.answer("Пример: <code>/setchannel -1001234567890</code>")
    set_setting("channel_id", value)
    await message.answer("✅ ID канала сохранён. Добавь бота администратором с правом приглашать пользователей.")

@dp.message(Command("setdays"))
async def setdays(message: Message):
    if not is_admin(message.from_user.id): return
    value = (message.text or "").partition(" ")[2].strip()
    if not value.isdigit() or int(value) < 1:
        return await message.answer("Пример: <code>/setdays 30</code>")
    set_setting("subscription_days", value)
    await message.answer(f"✅ Срок подписки: {value} дней.")

@dp.message(Command("addtheme"))
async def addtheme(message: Message):
    if not is_admin(message.from_user.id): return
    value = (message.text or "").partition(" ")[2].strip()
    if not value: return await message.answer("Пример: <code>/addtheme Как оформить товар на FunPay</code>")
    db.execute("INSERT INTO themes(title,created_at) VALUES(?,?)", (value, datetime.now().isoformat()))
    db.commit()
    await message.answer("✅ Тема добавлена в архив.")

@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id): return
    users = db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    orders = db.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
    pending = db.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
    active = db.execute("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>?", (datetime.now().isoformat(),)).fetchone()["n"]
    themes = db.execute("SELECT COUNT(*) n FROM themes").fetchone()["n"]
    await message.answer(f"📊 <b>Статистика playfunpay</b>\n\n👥 Пользователей: {users}\n💳 Заявок: {orders}\n⏳ Ожидают проверки: {pending}\n💎 Активных подписок: {active}\n📚 Тем в базе: {themes}")

@dp.message(Command("broadcast"))
async def broadcast(message: Message):
    if not is_admin(message.from_user.id): return
    text = (message.text or "").partition(" ")[2].strip()
    if not text: return await message.answer("Пример: <code>/broadcast Новая тема уже доступна!</code>")
    ids = [r["user_id"] for r in db.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()]
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, "📢 <b>Новость playfunpay</b>\n\n" + text)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка завершена. Отправлено: {sent}/{len(ids)}")

@dp.message(Command("giveaccess"))
async def giveaccess(message: Message):
    if not is_admin(message.from_user.id): return
    p = (message.text or "").split()
    if len(p) != 3 or not p[1].isdigit() or not p[2].isdigit():
        return await message.answer("Пример: <code>/giveaccess 123456789 30</code>")
    expiry = grant_subscription(int(p[1]), int(p[2]))
    await message.answer(f"✅ Доступ выдан до {format_date(expiry.isoformat())}.")
    try:
        await bot.send_message(int(p[1]), f"💎 Администратор выдал тебе доступ до <b>{format_date(expiry.isoformat())}</b>.")
    except Exception:
        pass

@dp.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery):
    await call.message.edit_text("💜 <b>playfunpay</b>\n\nВыбери нужный раздел:", reply_markup=main_menu())
    await call.answer()

@dp.callback_query(F.data == "private")
async def cb_private(call: CallbackQuery):
    active, expiry = subscription_active(call.from_user.id)
    status = f"\n\n<badge>" if False else ""
    text = (f"💎 <b>{setting('channel_title','playfunpay VIP')}</b>\n\n"
            f"{setting('description')}\n\n"
            f"💰 Стоимость: <b>{money()}</b>\n"
            f"🗓 Срок доступа: <b>{setting('subscription_days','30')} дней</b>\n")
    if active:
        text += f"\n✅ У тебя уже есть активный доступ до <b>{format_date(expiry.isoformat())}</b>."
    await call.message.edit_text(text, reply_markup=private_kb())
    await call.answer()

@dp.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery):
    active, expiry = subscription_active(call.from_user.id)
    if active:
        await call.answer("У тебя уже есть активная подписка.", show_alert=True)
        return
    text = (f"🛒 <b>Оформление доступа</b>\n\n"
            f"Стоимость: <b>{money()}</b>\n\n"
            f"{setting('payment_instructions')}\n\n"
            f"После оплаты нажми кнопку ниже и отправь чек или текстовое подтверждение. "
            f"Заявку проверит администратор.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="paid")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="private")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "paid")
async def cb_paid(call: CallbackQuery):
    pending_receipt.add(call.from_user.id)
    await call.message.answer("📎 Отправь подтверждение оплаты: фотографию чека или текстовое сообщение. Не отправляй пароли и коды доступа.")
    await call.answer()

@dp.message(F.photo)
async def receipt_photo(message: Message):
    if message.from_user.id not in pending_receipt: return
    pending_receipt.discard(message.from_user.id)
    cur = db.execute("INSERT INTO orders(user_id,status,receipt_type,receipt_file_id,created_at) VALUES(?,?,?,?,?)",
                     (message.from_user.id, "pending", "photo", message.photo[-1].file_id, datetime.now().isoformat()))
    db.commit()
    order_id = cur.lastrowid
    await message.answer("✅ Подтверждение отправлено администратору. Дождись проверки.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{order_id}")
    ]])
    for admin in ADMIN_IDS:
        try:
            await bot.send_photo(admin, message.photo[-1].file_id,
                caption=f"🧾 <b>Заявка #{order_id}</b>\nПользователь: <code>{message.from_user.id}</code>\n@{message.from_user.username or 'нет username'}",
                reply_markup=kb)
        except Exception: pass

@dp.message()
async def general_message(message: Message):
    uid = message.from_user.id
    if uid in pending_receipt:
        pending_receipt.discard(uid)
        cur = db.execute("INSERT INTO orders(user_id,status,receipt_type,receipt_text,created_at) VALUES(?,?,?,?,?)",
                         (uid, "pending", "text", message.text or "[не текстовое сообщение]", datetime.now().isoformat()))
        db.commit()
        order_id = cur.lastrowid
        await message.answer("✅ Подтверждение отправлено администратору. Дождись проверки.")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{order_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{order_id}")
        ]])
        for admin in ADMIN_IDS:
            try:
                await bot.send_message(admin, f"🧾 <b>Заявка #{order_id}</b>\nПользователь: <code>{uid}</code>\nПодтверждение: {message.text}", reply_markup=kb)
            except Exception: pass
        return
    if uid in pending_support:
        pending_support.discard(uid)
        for admin in ADMIN_IDS:
            try:
                await bot.send_message(admin, f"💬 <b>Обращение в поддержку</b>\nОт: <code>{uid}</code>\n\n{message.text or '[вложение]'}")
            except Exception: pass
        return await message.answer("✅ Сообщение отправлено в поддержку.")
    if uid in pending_theme and is_admin(uid):
        pending_theme.discard(uid)
        db.execute("INSERT INTO themes(title,created_at) VALUES(?,?)", (message.text or "Новая тема", datetime.now().isoformat()))
        db.commit()
        return await message.answer("✅ Тема добавлена.")
    await message.answer("Используй кнопки меню или команду /start.", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("approve:"))
async def approve_order(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    order_id = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order["status"] != "pending":
        return await call.answer("Заявка уже обработана или не найдена.", show_alert=True)
    days = int(setting("subscription_days", "30"))
    expiry = grant_subscription(order["user_id"], days)
    db.execute("UPDATE orders SET status='approved',reviewed_at=?,reviewer_id=? WHERE id=?",
               (datetime.now().isoformat(), call.from_user.id, order_id))
    db.commit()
    channel_id = setting("channel_id")
    try:
        if channel_id:
            invite = await bot.create_chat_invite_link(
                chat_id=int(channel_id), member_limit=1,
                expire_date=datetime.now() + timedelta(days=2)
            )
            await bot.send_message(order["user_id"],
                f"🎉 <b>Оплата подтверждена!</b>\n\nТвой доступ действует до {format_date(expiry.isoformat())}.\n\nСсылка для входа (одноразовая):\n{invite.invite_link}")
        else:
            await bot.send_message(order["user_id"],
                f"🎉 <b>Оплата подтверждена!</b>\nДоступ активирован до {format_date(expiry.isoformat())}.\nАдминистратор ещё не настроил ID приватного канала. Напиши в поддержку для получения ссылки.")
    except Exception as e:
        log.exception("Не удалось выдать ссылку: %s", e)
        await bot.send_message(order["user_id"], "Оплата подтверждена, но ссылку автоматически выдать не удалось. Напиши администратору.")
    try: await call.message.edit_caption(caption=f"✅ Заявка #{order_id} одобрена администратором {call.from_user.id}.")
    except Exception:
        try: await call.message.edit_text(f"✅ Заявка #{order_id} одобрена.")
        except Exception: pass
    await call.answer("Одобрено.")

@dp.callback_query(F.data.startswith("reject:"))
async def reject_order(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    order_id = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order["status"] != "pending":
        return await call.answer("Заявка уже обработана или не найдена.", show_alert=True)
    db.execute("UPDATE orders SET status='rejected',reviewed_at=?,reviewer_id=? WHERE id=?",
               (datetime.now().isoformat(), call.from_user.id, order_id))
    db.commit()
    try: await bot.send_message(order["user_id"], "❌ Заявка отклонена. Если считаешь, что произошла ошибка, обратись в поддержку.")
    except Exception: pass
    try: await call.message.edit_caption(caption=f"❌ Заявка #{order_id} отклонена.")
    except Exception:
        try: await call.message.edit_text(f"❌ Заявка #{order_id} отклонена.")
        except Exception: pass
    await call.answer("Отклонено.")

@dp.callback_query(F.data == "themes")
async def cb_themes(call: CallbackQuery):
    rows = db.execute("SELECT id,title FROM themes ORDER BY id LIMIT 15").fetchall()
    if not rows:
        return await call.answer("Пока тем нет.", show_alert=True)
    buttons = [[InlineKeyboardButton(text=f"📌 {r['id']}. {r['title'][:45]}", callback_data=f"theme:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")])
    await call.message.edit_text("📚 <b>Темы и гайды</b>\n\nВыбери тему. Здесь собраны материалы playfunpay:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@dp.callback_query(F.data.startswith("theme:"))
async def cb_theme(call: CallbackQuery):
    theme_id = int(call.data.split(":")[1])
    row = db.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    if not row: return await call.answer("Тема не найдена.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav:{theme_id}")],
        [InlineKeyboardButton(text="⬅️ К темам", callback_data="themes")]
    ])
    body = row["body"] or "Материал по этой теме скоро появится. Следи за обновлениями!"
    await call.message.edit_text(f"📚 <b>Тема #{theme_id}</b>\n\n<b>{row['title']}</b>\n\n{body}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("fav:"))
async def cb_fav(call: CallbackQuery):
    tid = int(call.data.split(":")[1])
    db.execute("INSERT OR IGNORE INTO favorites(user_id,theme_id) VALUES(?,?)", (call.from_user.id, tid))
    db.commit()
    await call.answer("Добавлено в избранное ⭐", show_alert=True)

@dp.callback_query(F.data == "favorites")
async def cb_favorites(call: CallbackQuery):
    rows = db.execute("""SELECT t.id,t.title FROM themes t JOIN favorites f ON f.theme_id=t.id
                         WHERE f.user_id=? ORDER BY t.id""", (call.from_user.id,)).fetchall()
    if not rows:
        return await call.answer("Избранное пока пустое.", show_alert=True)
    buttons = [[InlineKeyboardButton(text=f"⭐ {r['title'][:48]}", callback_data=f"theme:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    await call.message.edit_text("⭐ <b>Избранное</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    row = db.execute("SELECT joined_at,referred_by FROM users WHERE user_id=?", (call.from_user.id,)).fetchone()
    active, expiry = subscription_active(call.from_user.id)
    refs = db.execute("SELECT COUNT(*) n FROM users WHERE referred_by=?", (call.from_user.id,)).fetchone()["n"]
    status = f"Активна до {format_date(expiry.isoformat())}" if active else "Нет активной подписки"
    await call.message.edit_text(
        f"👤 <b>Твой профиль</b>\n\n🆔 ID: <code>{call.from_user.id}</code>\n"
        f"📅 Регистрация: {format_date(row['joined_at']) if row else '—'}\n"
        f"💎 Подписка: {status}\n🎁 Приглашено: {refs}",
        reply_markup=back_menu())
    await call.answer()

@dp.callback_query(F.data == "referral")
async def cb_referral(call: CallbackQuery):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={call.from_user.id}"
    refs = db.execute("SELECT COUNT(*) n FROM users WHERE referred_by=?", (call.from_user.id,)).fetchone()["n"]
    await call.message.edit_text(
        f"🎁 <b>Реферальная система</b>\n\nТвоя ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено пользователей: <b>{refs}</b>\n\nНе рассылай ссылку спамом.",
        reply_markup=back_menu())
    await call.answer()

@dp.callback_query(F.data == "news")
async def cb_news(call: CallbackQuery):
    await call.message.edit_text("📰 <b>Новости playfunpay</b>\n\nЗдесь будут появляться объявления и обновления проекта.", reply_markup=back_menu())
    await call.answer()

@dp.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery):
    pending_support.add(call.from_user.id)
    await call.message.answer(setting("support_text"))
    await call.answer()

@dp.callback_query(F.data == "adm_stats")
async def cb_adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    users = db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    pending = db.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
    active = db.execute("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>?", (datetime.now().isoformat(),)).fetchone()["n"]
    await call.message.answer(f"📊 Пользователей: {users}\n⏳ Заявок на проверке: {pending}\n💎 Активных подписок: {active}")
    await call.answer()

@dp.callback_query(F.data == "adm_orders")
async def cb_adm_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    rows = db.execute("SELECT id,user_id,created_at FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        await call.message.answer("📦 Нет заявок, ожидающих проверки.")
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Одобрить", callback_data=f"approve:{r['id']}"),
            InlineKeyboardButton(text="Отклонить", callback_data=f"reject:{r['id']}")
        ]])
        await call.message.answer(f"Заявка #{r['id']} · пользователь <code>{r['user_id']}</code>\n{format_date(r['created_at'])}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "adm_help")
async def cb_adm_help(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    text = ("🛠 <b>Настройки командами</b>\n\n"
            "/setprice 500 — цена\n/setdescription Текст — описание\n"
            "/setpayment Реквизиты — инструкция оплаты\n/setchannel -100... — ID приватного канала\n"
            "/setdays 30 — срок подписки\n/addtheme Название — добавить тему\n"
            "/broadcast Текст — рассылка\n/giveaccess ID 30 — выдать доступ\n/stats — статистика")
    await call.message.answer(text)
    await call.answer()

@dp.callback_query(F.data == "adm_theme")
async def cb_adm_theme(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    pending_theme.add(call.from_user.id)
    await call.message.answer("✏️ Отправь следующим сообщением название новой темы.")
    await call.answer()

async def daily_publisher():
    while True:
        try:
            now = datetime.now(ZoneInfo(TIMEZONE))
            if now.hour == DAILY_POST_HOUR and now.minute == 0:
                row = db.execute("SELECT * FROM themes WHERE published=0 ORDER BY id LIMIT 1").fetchone()
                if row:
                    text = f"☀️ <b>Тема дня #{row['id']}</b>\n\n<b>{row['title']}</b>\n\nНовая тема от playfunpay! Заглядывай в раздел «Темы и гайды» бота."
                    ids = [r["user_id"] for r in db.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()]
                    for uid in ids:
                        try: await bot.send_message(uid, text)
                        except Exception: pass
                        await asyncio.sleep(0.03)
                    db.execute("UPDATE themes SET published=1 WHERE id=?", (row["id"],))
                    db.commit()
                await asyncio.sleep(65)
            else:
                await asyncio.sleep(20)
        except Exception:
            log.exception("Ошибка ежедневной публикации")
            await asyncio.sleep(30)


async def expire_subscriptions():
    while True:
        try:
            channel_id = setting("channel_id")
            if channel_id:
                now = datetime.now().isoformat()
                rows = db.execute(
                    "SELECT user_id FROM subscriptions WHERE expires_at<=? AND kicked=0",
                    (now,)
                ).fetchall()
                for row in rows:
                    uid = row["user_id"]
                    try:
                        # Ban + unban removes the member while allowing a future rejoin.
                        await bot.ban_chat_member(int(channel_id), uid)
                        await bot.unban_chat_member(int(channel_id), uid)
                        await bot.send_message(uid, "⌛ Срок доступа к приватному каналу playfunpay VIP закончился. Чтобы вернуться, оформи продление через меню бота.")
                    except Exception:
                        log.exception("Не удалось удалить пользователя %s из приватного канала", uid)
                    finally:
                        db.execute("UPDATE subscriptions SET kicked=1 WHERE user_id=?", (uid,))
                        db.commit()
            await asyncio.sleep(600)
        except Exception:
            log.exception("Ошибка проверки окончаний подписок")
            await asyncio.sleep(60)

async def main():
    init_db()
    seed_themes()
    asyncio.create_task(daily_publisher())
    asyncio.create_task(expire_subscriptions())
    log.info("playfunpay bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
