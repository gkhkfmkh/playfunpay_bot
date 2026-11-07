from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def main_menu(rows, is_admin=False):
    b = InlineKeyboardBuilder()
    for row in rows:
        b.button(text=row["title"], callback_data=f"btn:{row['id']}")
    b.adjust(1)
    if is_admin:
        b.row(InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin"))
    return b.as_markup()

def back_menu(parent_id=None):
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data=f"back:{parent_id or 0}")
    b.adjust(1)
    return b.as_markup()

def admin_menu():
    b = InlineKeyboardBuilder()
    for title, data in [
        ("🧩 Кнопки меню","adm:buttons"),("💰 Способы заработка","adm:posts"),
        ("💳 Настройки оплаты","adm:payment"),("🎟 Промокоды","adm:promos"),
        ("📨 Рассылка","adm:broadcast"),("👥 Пользователи и статистика","adm:stats"),
        ("📩 Обращения в поддержку","adm:support"),
    ]: b.button(text=title, callback_data=data)
    b.button(text="⬅️ Главное меню", callback_data="home")
    b.adjust(1)
    return b.as_markup()

def buttons_admin(rows):
    b = InlineKeyboardBuilder()
    for row in rows:
        b.button(text=f"✏️ {row['title'][:35]}", callback_data=f"editbtn:{row['id']}")
    b.button(text="➕ Добавить кнопку", callback_data="addbtn")
    b.button(text="⬅️ В админ-панель", callback_data="admin")
    b.adjust(1)
    return b.as_markup()

def edit_button_menu(button_id):
    b = InlineKeyboardBuilder()
    b.button(text="Изменить название", callback_data=f"bfield:title:{button_id}")
    b.button(text="Изменить действие/содержимое", callback_data=f"bfield:value:{button_id}")
    b.button(text="Удалить кнопку", callback_data=f"delbtn:{button_id}")
    b.button(text="⬅️ К списку", callback_data="adm:buttons")
    b.adjust(1)
    return b.as_markup()

def payment_menu():
    b = InlineKeyboardBuilder()
    b.button(text="⭐ Оплатить Telegram Stars", callback_data="pay:stars")
    b.button(text="🏦 Другой способ оплаты", callback_data="pay:manual")
    b.button(text="⬅️ Назад", callback_data="home")
    b.adjust(1)
    return b.as_markup()

def manual_paid(payment_id):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Я оплатил — отправить чек", callback_data=f"manualproof:{payment_id}")
    b.adjust(1)
    return b.as_markup()

def review_payment(payment_id, user_id):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить", callback_data=f"approve:{payment_id}:{user_id}")
    b.button(text="❌ Отклонить", callback_data=f"reject:{payment_id}:{user_id}")
    b.adjust(2)
    return b.as_markup()

def support_reply(ticket_id):
    b = InlineKeyboardBuilder()
    b.button(text="Ответить", callback_data=f"reply:{ticket_id}")
    b.adjust(1)
    return b.as_markup()
