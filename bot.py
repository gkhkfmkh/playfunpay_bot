import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
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
        body TEXT DEFAULT '', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', receipt_type TEXT,
        receipt_file_id TEXT, created_at TEXT NOT NULL, reviewed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY, expires_at TEXT NOT NULL, kicked INTEGER DEFAULT 0
    );
    """)
    
    # Добавим стартовые темы, если база пустая (как на вашем скриншоте)
    count = db.execute("SELECT COUNT(*) n FROM themes").fetchone()["n"]
    if count == 0:
        initial_themes = [
            ("Что такое FunPay: знакомство с площадкой", "FunPay — это крупнейшая биржа игровых ценностей, где игроки напрямую торгуют друг с другом аккаунтами, валютой и услугами."),
            ("Что такое Playerok: обзор площадки для торговли", "Playerok — популярная платформа для быстрой продажи игровых аккаунтов и цифровых товаров с удобным интерфейсом."),
            ("Как безопасно начать продавать цифровые товары", "Всегда сохраняйте переписку, видеозаписи передачи данных и никогда не соглашайтесь на сомнительные сделки вне гаранта."),
            ("Как выбрать нишу для первого товара", "Изучите спрос, найдите игры с высоким суточным онлайн-рынком и минимальным порогом входа для новичков."),
            ("Как изучить спрос перед созданием товара", "Проанализируйте топ продавцов, посмотрите категории с наибольшим количеством отзывов и активных сделок."),
            ("Как анализировать конкурентов на маркетплейсе", "Обращайте внимание на цены, скорость ответа продавца и количество успешно закрытых заказов."),
            ("Как оформить профиль продавца", "Поставьте понятную аватарку, напишите вежливое описание и укажите график работы для повышения доверия покупателей."),
            ("Как придумать понятное название товара", "Название должно четко отражать суть: игра, сервер, регион и объем (например: [Россия] Робуксы 100 R$)."),
            ("Как написать честное и понятное описание", "Опишите пошагово процесс получения товара, сроки выполнения и возможные нюансы.")
        ]
        for t, b in initial_themes:
            db.execute("INSERT INTO themes(title, body, created_at) VALUES(?,?,?)", (t, b, datetime.now().isoformat()))
        db.commit()

    defaults = {
        "price": "99",
        "card_number": "2200702075574801",
        "bank_name": "Т-Банк (Тинькофф)",
        "channel_url": "https://t.me/playfunpay",
        "ads_url": "https://t.me/playfunpay1"
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    db.commit()

init_db()

def get_setting(key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (key, value, value))
    db.commit()

def is_admin(user_id):
    return user_id in ADMIN_IDS

class AdminStates(StatesGroup):
    waiting_for_theme_title = State()
    waiting_for_theme_body = State()
    waiting_for_price = State()
    waiting_for_card = State()
    waiting_for_broadcast = State()

# Временное хранилище для пошагового создания темы админом
temp_theme_data = {}

def main_menu_kb(is_adm=False):
    kb = [
        [InlineKeyboardButton(text="📢 Наш канал", url=get_setting("channel_url", "https://t.me/telegram"))],
        [InlineKeyboardButton(text="💎 Реклама", url=get_setting("ads_url", "https://t.me/telegram"))],
        [InlineKeyboardButton(text="📚 Темы и гайды", callback_data="menu_themes")],
        [InlineKeyboardButton(text="🔐 Купить доступ (99₽)", callback_data="menu_buy")]
    ]
    if is_adm:
        kb.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def themes_menu_kb(themes):
    kb = []
    for idx, t in enumerate(themes, 1):
        kb.append([InlineKeyboardButton(text=f"📌 {idx}. {t['title'][:35]}...", callback_data=f"show_theme:{t['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
         InlineKeyboardButton(text="📦 Заявки", callback_data="adm_orders")],
        [InlineKeyboardButton(text="➕ Добавить тему", callback_data="adm_add_theme"),
         InlineKeyboardButton(text="🗑 Удалить темы", callback_data="adm_del_themes")],
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data="adm_set_price"),
         InlineKeyboardButton(text="💳 Изменить реквизиты", callback_data="adm_set_card")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="main_menu")]
    ])

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    db.execute("INSERT OR IGNORE INTO users(user_id, username, first_name, joined_at) VALUES(?,?,?,?)",
               (uid, message.from_user.username, message.from_user.first_name, datetime.now().isoformat()))
    db.commit()

    text = (
        f"⚡ <b>PlayFunpay Team | Официальный бот проекта</b>\n\n"
        f"Привет, <b>{message.from_user.first_name}</b>! Используй кнопки ниже, чтобы изучать материалы и зарабатывать. 👇"
    )
    await message.answer(text, reply_markup=main_menu_kb(is_admin(uid)))

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    text = "⚡ <b>PlayFunpay Team | Официальный бот проекта</b>\n\nВыбери нужный раздел в меню ниже: 👇"
    try:
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_admin(uid)))
    except Exception:
        await call.message.answer(text, reply_markup=main_menu_kb(is_admin(uid)))
    await call.answer()

@dp.callback_query(F.data == "menu_themes")
async def cb_menu_themes(call: CallbackQuery):
    themes = db.execute("SELECT id, title FROM themes ORDER BY id ASC").fetchall()
    if not themes:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
        return await call.message.edit_text("📚 <b>Темы и гайды</b>\n\nПока материалы не добавлены.", reply_markup=kb)
    
    text = "📚 <b>Темы и гайды</b>\n\nВыбери тему. Здесь собраны материалы PlayFunpay:"
    await call.message.edit_text(text, reply_markup=themes_menu_kb(themes))
    await call.answer()

@dp.callback_query(F.data.startswith("show_theme:"))
async def cb_show_theme(call: CallbackQuery):
    theme_id = int(call.data.split(":")[1])
    theme = db.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    
    uid = call.from_user.id
    sub = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=? AND expires_at>?", (uid, datetime.now().isoformat())).fetchone()
    
    if not theme:
        return await call.answer("Тема не найдена.", show_alert=True)
        
    text = f"📌 <b>{theme['title']}</b>\n\n{theme['body']}"
    
    if not sub and not is_admin(uid):
        price = get_setting("price", "99")
        text += f"\n\n🔒 <i>Полный доступ к приватным материалам закрыт. Приобрети подписку всего за {price}₽.</i>"
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К списку тем", callback_data="menu_themes")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "menu_buy")
async def cb_menu_buy(call: CallbackQuery):
    price = get_setting("price", "99")
    card = get_setting("card_number", "2200702075574801")
    bank = get_setting("bank_name", "Т-Банк (Тинькофф)")
    
    text = (
        f"🔐 <b>Оплата доступа в PRIVAT:</b>\n\n"
        f"💳 <b>Номер карты:</b> <code>{card}</code>\n"
        f"💰 <b>Сумма:</b> <b>{price} рублей</b>\n"
        f"🏛 <b>Банк:</b> {bank}\n\n"
        f"⚠️ <b>ВНИМАНИЕ:</b> После оплаты отправьте скриншот чека сюда в чат.\n\n"
        f"Администратор проверит его и выдаст доступ."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

# --- ADMIN PANEL ---
@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Нет доступа.", show_alert=True)
    text = "🛠 <b>Панель управления администратора</b>\n\nВыбери нужное действие:"
    await call.message.edit_text(text, reply_markup=admin_panel_kb())
    await call.answer()

@dp.callback_query(F.data == "adm_stats")
async def cb_adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    users = db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    orders = db.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
    pending = db.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
    active = db.execute("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>?", (datetime.now().isoformat(),)).fetchone()["n"]
    themes_count = db.execute("SELECT COUNT(*) n FROM themes").fetchone()["n"]
    
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"💳 Всего заявок: {orders}\n"
        f"⏳ Ожидают проверки: {pending}\n"
        f"💎 Активных подписок: {active}\n"
        f"📚 Тем в базе: {themes_count}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "adm_orders")
async def cb_adm_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    rows = db.execute("SELECT id, user_id, created_at FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 5").fetchall()
    if not rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]])
        return await call.message.edit_text("📦 Нет заявок, ожидающих проверки.", reply_markup=kb)
    
    await call.message.edit_text(f"📦 <b>Непроверенные заявки ({len(rows)}):</b>")
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve:{r['id']}"),
             InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{r['id']}")]
        ])
        await call.message.answer(f"🧾 <b>Заявка #{r['id']}</b>\nОт ID: <code>{r['user_id']}</code>\nДата: {r['created_at']}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "adm_add_theme")
async def cb_adm_add_theme(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    await state.set_state(AdminStates.waiting_for_theme_title)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await call.message.edit_text("➕ <b>Шаг 1 из 2:</b> Введите <b>название</b> новой темы или гайда:", reply_markup=kb)
    await call.answer()

@dp.message(AdminStates.waiting_for_theme_title)
async def process_theme_title(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    temp_theme_data['title'] = message.text.strip()
    await state.set_state(AdminStates.waiting_for_theme_body)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await message.answer("➕ <b>Шаг 2 из 2:</b> Теперь отправьте <b>текст (содержимое)</b> этого гайда:", reply_markup=kb)

@dp.message(AdminStates.waiting_for_theme_body)
async def process_theme_body(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    title = temp_theme_data.get('title', 'Без названия')
    body = message.text.strip()
    
    db.execute("INSERT INTO themes(title, body, created_at) VALUES(?,?,?)", (title, body, datetime.now().isoformat()))
    db.commit()
    await state.clear()
    temp_theme_data.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="admin_panel")]])
    await message.answer("✅ <b>Тема и гайд успешно добавлены в список!</b>", reply_markup=kb)

@dp.callback_query(F.data == "adm_del_themes")
async def cb_adm_del_themes(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    themes = db.execute("SELECT id, title FROM themes ORDER BY id ASC").fetchall()
    if not themes:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]])
        return await call.message.edit_text("📭 Тем для удаления нет.", reply_markup=kb)
    
    kb_list = []
    for t in themes:
        kb_list.append([InlineKeyboardButton(text=f"❌ Удалить: {t['title'][:25]}...", callback_data=f"del_theme:{t['id']}")])
    kb_list.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")])
    
    await call.message.edit_text("🗑 <b>Управление темами:</b>\nНажмите на тему, чтобы мгновенно удалить её:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))
    await call.answer()

@dp.callback_query(F.data.startswith("del_theme:"))
async def cb_do_del_theme(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    theme_id = int(call.data.split(":")[1])
    db.execute("DELETE FROM themes WHERE id=?", (theme_id,))
    db.commit()
    await call.answer("Тема удалена!", show_alert=True)
    # Обновим список
    themes = db.execute("SELECT id, title FROM themes ORDER BY id ASC").fetchall()
    if not themes:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]])
        return await call.message.edit_text("📭 Тем больше не осталось.", reply_markup=kb)
    
    kb_list = []
    for t in themes:
        kb_list.append([InlineKeyboardButton(text=f"❌ Удалить: {t['title'][:25]}...", callback_data=f"del_theme:{t['id']}")])
    kb_list.append([InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")])
    await call.message.edit_text("🗑 <b>Управление темами:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

@dp.callback_query(F.data == "adm_set_price")
async def cb_adm_set_price(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    await state.set_state(AdminStates.waiting_for_price)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await call.message.edit_text("💰 Введите новую стоимость подписки (только число):", reply_markup=kb)
    await call.answer()

@dp.message(AdminStates.waiting_for_price)
async def process_set_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    val = message.text.strip()
    if not val.isdigit():
        return await message.answer("⚠️ Введите корректное число.")
    set_setting("price", val)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="admin_panel")]])
    await message.answer(f"✅ Цена подписки изменена на <b>{val}₽</b>!", reply_markup=kb)

@dp.callback_query(F.data == "adm_set_card")
async def cb_adm_set_card(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    await state.set_state(AdminStates.waiting_for_card)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await call.message.edit_text("💳 Введите новый номер карты:", reply_markup=kb)
    await call.answer()

@dp.message(AdminStates.waiting_for_card)
async def process_set_card(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    val = message.text.strip()
    set_setting("card_number", val)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="admin_panel")]])
    await message.answer(f"✅ Номер карты обновлен: <code>{val}</code>", reply_markup=kb)

@dp.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    await state.set_state(AdminStates.waiting_for_broadcast)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await call.message.edit_text("📢 Отправьте текст для рассылки всем пользователям:", reply_markup=kb)
    await call.answer()

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    text = message.text
    await state.clear()
    users = db.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], f"📢 <b>Объявление:</b>\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="admin_panel")]])
    await message.answer(f"✅ Рассылка завершена. Отправлено: {sent}/{len(users)}", reply_markup=kb)

# --- ORDERS & RECEIPTS ---
@dp.message(F.photo | F.document)
async def handle_receipt(message: Message):
    uid = message.from_user.id
    if is_admin(uid):
        return
        
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    cursor = db.execute("INSERT INTO orders(user_id, status, receipt_type, receipt_file_id, created_at) VALUES(?,?,?,?,?)",
                        (uid, "pending", "photo", file_id, datetime.now().isoformat()))
    db.commit()
    order_id = cursor.lastrowid
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve:{order_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{order_id}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await bot.send_photo(admin, file_id, caption=f"🧾 <b>Чек на проверку #{order_id}</b>\nОт ID: <code>{uid}</code>", reply_markup=kb)
        except Exception:
            pass
    await message.answer("✅ <b>Чек отправлен администратору!</b> Ожидайте подтверждения.")

@dp.callback_query(F.data.startswith("approve:"))
async def cb_approve_order(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    order_id = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order["status"] != "pending":
        return await call.answer("Заявка уже обработана.", show_alert=True)
        
    db.execute("UPDATE orders SET status='approved', reviewed_at=? WHERE id=?", (datetime.now().isoformat(), order_id))
    target_uid = order["user_id"]
    
    expires = (datetime.now() + timedelta(days=30)).isoformat()
    db.execute("INSERT INTO subscriptions(user_id, expires_at, kicked) VALUES(?,?,0) ON CONFLICT(user_id) DO UPDATE SET expires_at=?, kicked=0",
               (target_uid, expires, expires))
    db.commit()
    
    try:
        await bot.send_message(target_uid, "🎉 <b>Оплата подтверждена!</b> Доступ к ПРИВАТУ активирован на 30 дней.")
    except Exception:
        pass
        
    await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n✅ <b>ОДОБРЕНО</b>")
    await call.answer("Подписка успешно выдана!")

@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject_order(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    order_id = int(call.data.split(":")[1])
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or order["status"] != "pending":
        return await call.answer("Заявка уже обработана.", show_alert=True)
        
    db.execute("UPDATE orders SET status='rejected', reviewed_at=? WHERE id=?", (datetime.now().isoformat(), order_id))
    db.commit()
    
    try:
        await bot.send_message(order["user_id"], "❌ <b>К сожалению, ваш чек был отклонен.</b> Проверьте правильность перевода или обратитесь в поддержку.")
    except Exception:
        pass
        
    if call.message.caption:
        await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>ОТКЛОНЕНО</b>")
    else:
        await call.message.edit_text(call.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>")
    await call.answer("Заявка отклонена.")

async def main():
    log.info("Starting refined PlayFunpay bot with themes list...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
