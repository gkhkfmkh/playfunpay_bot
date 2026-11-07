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
        body TEXT DEFAULT '', published INTEGER DEFAULT 0, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', receipt_type TEXT,
        receipt_file_id TEXT, receipt_text TEXT, created_at TEXT NOT NULL,
        reviewed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY, expires_at TEXT NOT NULL, kicked INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS tip_claims (
        user_id INTEGER PRIMARY KEY, last_claimed_at TEXT NOT NULL
    );
    """)
    defaults = {
        "price": "99",
        "card_number": "2200702075574801",
        "bank_name": "Т-Банк (Тинькофф)",
        "channel_url": "https://t.me/telegram",
        "ads_url": "https://t.me/telegram",
        "description": "💸 Способы заработка\n⭐ Способы фарма отзывов\n🥈 Приватные Гайды\n📚 Разное (софт и фишки)"
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
    waiting_for_theme = State()
    waiting_for_price = State()
    waiting_for_card = State()
    waiting_for_broadcast = State()

def main_menu_kb(is_adm=False):
    kb = [
        [InlineKeyboardButton(text="📢 Наш канал", url=get_setting("channel_url", "https://t.me/telegram"))],
        [InlineKeyboardButton(text="💎 Реклама", url=get_setting("ads_url", "https://t.me/telegram"))],
        [InlineKeyboardButton(text="🛒 Что в ПРИВАТЕ?", callback_data="menu_privat")],
        [InlineKeyboardButton(text="💡 Идея по заработку (24ч)", callback_data="get_daily_tip")],
        [InlineKeyboardButton(text="🔐 Купить доступ", callback_data="menu_buy")]
    ]
    if is_adm:
        kb.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def privat_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Roblox", callback_data="game_roblox"),
         InlineKeyboardButton(text="⚡ Standoff 2", callback_data="game_standoff")],
        [InlineKeyboardButton(text="🌵 Brawl Stars", callback_data="game_brawl"),
         InlineKeyboardButton(text="🔫 PUBG Mobile", callback_data="game_pubg")],
        [InlineKeyboardButton(text="🏰 Clash of Clans", callback_data="game_clash")],
        [InlineKeyboardButton(text="📦 Разное (софт и фишки)", callback_data="game_misc")],
        [InlineKeyboardButton(text="📜 Инструкция по безопасности", callback_data="game_safety")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])

def buy_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])

def admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
         InlineKeyboardButton(text="📦 Заявки", callback_data="adm_orders")],
        [InlineKeyboardButton(text="➕ Добавить тему/идею", callback_data="adm_add_theme"),
         InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data="adm_set_price"),
         InlineKeyboardButton(text="💳 Изменить реквизиты", callback_data="adm_set_card")],
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
        f"Привет, <b>{message.from_user.first_name}</b>! Ты в команде. "
        f"Используй кнопки ниже, чтобы зарабатывать деньги и развивать свои проекты. 👇"
    )
    await message.answer(text, reply_markup=main_menu_kb(is_admin(uid)))

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = call.from_user.id
    text = f"⚡ <b>PlayFunpay Team | Официальный бот проекта</b>\n\nВыбери нужный раздел в меню ниже: 👇"
    try:
        await call.message.edit_text(text, reply_markup=main_menu_kb(is_admin(uid)))
    except Exception:
        await call.message.answer(text, reply_markup=main_menu_kb(is_admin(uid)))
    await call.answer()

@dp.callback_query(F.data == "menu_privat")
async def cb_menu_privat(call: CallbackQuery):
    desc = get_setting("description", "🛒 Что входит в ПРИВАТ:\n\n💸 Способы заработка\n⭐ Способы фарма отзывов\n🥈 Приватные Гайды\n📚 Разное (софт и фишки)")
    text = f"🛒 <b>Что входит в ПРИВАТ:</b>\n\n{desc}\n\n<b>Выбери категорию, чтобы узнать подробнее:</b>"
    await call.message.edit_text(text, reply_markup=privat_menu_kb())
    await call.answer()

CATEGORY_CONTENT = {
    "game_roblox": ("🤖 Roblox", "В этом разделе собраны лучшие схемы заработка и продажи активов в Roblox (донат, робуксы, перепродажа аккаунтов)."),
    "game_standoff": ("⚡ Standoff 2", "Гайды по фарму голды, перепродаже скинов на рынке и безопасности сделок в Standoff 2."),
    "game_brawl": ("🌵 Brawl Stars", "Способы поднятия аккаунтов, продажа прокачанных профилей и аренда бойцов."),
    "game_pubg": ("🔫 PUBG Mobile", "Схемы фарма UC, продажа редких скинов и прокачка рангов в PUBG Mobile."),
    "game_clash": ("🏰 Clash of Clans", "Продажа и раскачка деревень Clash of Clans, безопасные сделки."),
    "game_misc": ("📦 Разное (софт и фишки)", "Полезный софт, скрипты автоматизации, прокси и секретные фишки для продавцов."),
    "game_safety": ("📜 Инструкция по безопасности", "Как не попасться на мошенников, правильно принимать оплату и защитить свои аккаунты.")
}

@dp.callback_query(F.data.in_(CATEGORY_CONTENT.keys()))
async def cb_category(call: CallbackQuery):
    title, desc = CATEGORY_CONTENT[call.data]
    uid = call.from_user.id
    sub = db.execute("SELECT expires_at FROM subscriptions WHERE user_id=? AND expires_at>?", (uid, datetime.now().isoformat())).fetchone()
    
    content_text = f"📂 <b>{title}</b>\n\n{desc}\n\n"
    if sub or is_admin(uid):
        content_text += "🟢 <b>Доступ открыт!</b> Тебе доступны все материалы этого раздела."
    else:
        price = get_setting("price", "99")
        content_text += f"🔒 <b>Материал заблокирован.</b> Для доступа ко всем приватным материалам приобрети подписку всего за <b>{price}₽</b>."
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="menu_privat")]])
    await call.message.edit_text(content_text, reply_markup=kb)
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
        f"Администратор проверит чек и выдаст доступ."
    )
    await call.message.edit_text(text, reply_markup=buy_menu_kb())
    await call.answer()

@dp.callback_query(F.data == "get_daily_tip")
async def cb_get_daily_tip(call: CallbackQuery):
    uid = call.from_user.id
    now = datetime.now()
    
    claim = db.execute("SELECT last_claimed_at FROM tip_claims WHERE user_id=?", (uid,)).fetchone()
    if claim:
        last_time = datetime.fromisoformat(claim["last_claimed_at"])
        diff = now - last_time
        if diff < timedelta(hours=24):
            remaining = timedelta(hours=24) - diff
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            return await call.answer(f"⏳ Следующая идея будет доступна через {hours} ч. {minutes} мин.!", show_alert=True)
            
    themes = db.execute("SELECT * FROM themes ORDER BY RANDOM() LIMIT 1").fetchone()
    if not themes:
        return await call.answer("📭 В базе пока нет тем. Администратор скоро их добавит!", show_alert=True)
        
    db.execute("INSERT INTO tip_claims(user_id, last_claimed_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_claimed_at=?",
               (uid, now.isoformat(), now.isoformat()))
    db.commit()
    
    text = f"💡 <b>Эксклюзивная идея по заработку / тема:</b>\n\n📌 <b>{themes['title']}</b>\n\n{themes['body'] or 'Следите за обновлениями в канале!'}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")]])
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

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
        f"📚 Тем/идей в базе: {themes_count}"
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
    await state.set_state(AdminStates.waiting_for_theme)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await call.message.edit_text("➕ <b>Добавление новой темы / идеи:</b>\n\nОтправьте текст темы следующим сообщением (первая строка — заголовок, далее описание):", reply_markup=kb)
    await call.answer()

@dp.message(AdminStates.waiting_for_theme)
async def process_add_theme(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    text = message.text or ""
    lines = text.split("\n", 1)
    title = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ""
    
    db.execute("INSERT INTO themes(title, body, created_at) VALUES(?,?,?)", (title, body, datetime.now().isoformat()))
    db.commit()
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="admin_panel")]])
    await message.answer("✅ <b>Тема успешно добавлена в базу!</b>", reply_markup=kb)

@dp.callback_query(F.data == "adm_set_price")
async def cb_adm_set_price(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer("Нет доступа.", show_alert=True)
    await state.set_state(AdminStates.waiting_for_price)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")]])
    await call.message.edit_text("💰 Введите новую стоимость подписки (число):", reply_markup=kb)
    await call.answer()

@dp.message(AdminStates.waiting_for_price)
async def process_set_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    val = message.text.strip()
    if not val.isdigit():
        return await message.answer("⚠️ Введите число.")
    set_setting("price", val)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 В админ-панель", callback_data="admin_panel")]])
    await message.answer(f"✅ Цена изменена на <b>{val}₽</b>!", reply_markup=kb)

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

@dp.message(F.photo | F.document | F.text)
async def handle_user_input(message: Message):
    uid = message.from_user.id
    if is_admin(uid):
        return
        
    if message.photo:
        file_id = message.photo[-1].file_id
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
        await bot.send_message(order["user_id"], "❌ <b>К сожалению, ваш чек был отклонен.</b> Проверьте реквизиты или обратитесь в поддержку.")
    except Exception:
        pass
        
    if call.message.caption:
        await call.message.edit_caption(caption=call.message.caption + "\n\n❌ <b>ОТКЛОНЕНО</b>")
    else:
        await call.message.edit_text(call.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>")
    await call.answer("Заявка отклонена.")

async def main():
    log.info("Starting updated PlayFunpay bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
