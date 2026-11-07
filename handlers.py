import asyncio
from datetime import datetime, timezone
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.deep_linking import create_start_link

from bot.config import settings
from bot import db
from bot.keyboards import *

router = Router()

class AdminFlow(StatesGroup):
    button_title = State()
    button_kind = State()
    button_value = State()
    edit_title = State()
    edit_value = State()
    post_title = State()
    post_body = State()
    promo_code = State()
    promo_days = State()
    promo_uses = State()
    broadcast = State()
    payment_details = State()
    payment_text = State()
    support_reply = State()

class UserFlow(StatesGroup):
    proof = State()
    support = State()
    promo = State()

def is_admin(user_id):
    return user_id in settings.admin_ids

async def show_home(target, user_id):
    rows = await db.fetchall("SELECT * FROM buttons WHERE parent_id IS NULL AND active=1 ORDER BY sort_order,id")
    markup = main_menu(rows, is_admin(user_id))
    if isinstance(target, Message):
        await target.answer(f"⚡ <b>{settings.bot_name}</b>\nПривет! Выбирай нужный раздел 👇", reply_markup=markup)
    else:
        await target.message.edit_text(f"⚡ <b>{settings.bot_name}</b>\nГлавное меню:", reply_markup=markup)

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    arg = (message.text or "").split(maxsplit=1)
    ref = None
    if len(arg) > 1 and arg[1].startswith("ref_"):
        try: ref = int(arg[1][4:])
        except ValueError: pass
    await db.register_user(message.from_user, ref)
    await show_home(message, message.from_user.id)

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("⚙️ <b>Панель администратора</b>", reply_markup=admin_menu())

@router.callback_query(F.data == "home")
async def home_cb(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(c, c.from_user.id)
    await c.answer()

@router.callback_query(F.data == "admin")
async def admin_cb(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа", show_alert=True)
    await c.message.edit_text("⚙️ <b>Панель администратора</b>", reply_markup=admin_menu())
    await c.answer()

@router.callback_query(F.data.startswith("back:"))
async def back_cb(c: CallbackQuery):
    parent = int(c.data.split(":")[1])
    if parent:
        row = await db.fetchone("SELECT * FROM buttons WHERE id=?", (parent,))
        children = await db.fetchall("SELECT * FROM buttons WHERE parent_id=? AND active=1 ORDER BY sort_order,id",(parent,))
        await c.message.edit_text(row["title"], reply_markup=main_menu(children,is_admin(c.from_user.id)))
        await c.message.answer("Выберите раздел:", reply_markup=back_menu(row["parent_id"]))
    else:
        await show_home(c, c.from_user.id)
    await c.answer()

@router.callback_query(F.data.startswith("btn:"))
async def button_click(c: CallbackQuery):
    bid = int(c.data.split(":")[1])
    row = await db.fetchone("SELECT * FROM buttons WHERE id=? AND active=1",(bid,))
    if not row: return await c.answer("Кнопка больше недоступна", show_alert=True)
    kind, value = row["kind"], row["value"]
    if kind == "url":
        await c.message.answer(f"🔗 <a href=\"{value}\">{row['title']}</a>")
    elif kind == "category":
        children = await db.fetchall("SELECT * FROM buttons WHERE parent_id=? AND active=1 ORDER BY sort_order,id",(bid,))
        if not children: await c.message.answer("В этом разделе пока нет материалов.")
        else: await c.message.answer(row["title"], reply_markup=main_menu(children,is_admin(c.from_user.id)))
        await c.message.answer("Выберите раздел:", reply_markup=back_menu(row["parent_id"]))
    elif kind == "private":
        if not await db.has_private(c.from_user.id):
            await c.message.answer("🔒 Этот раздел доступен после покупки привата.", reply_markup=payment_menu())
        else:
            await c.message.answer(value or "Добро пожаловать в приват!")
    elif kind == "buy":
        await c.message.answer("🔐 <b>Покупка доступа</b>\nВыберите способ оплаты:", reply_markup=payment_menu())
    elif kind == "daily":
        posts = await db.fetchall("SELECT * FROM posts ORDER BY id DESC LIMIT 10")
        if not posts: await c.message.answer("Пока нет публикаций. Загляни позже!")
        for p in posts:
            await c.message.answer(f"💰 <b>{p['title']}</b>\n\n{p['body']}")
            if p["media_file_id"]:
                await c.message.answer_photo(p["media_file_id"])
    else:
        await c.message.answer(value or "Раздел пока не заполнен.")
    await c.answer()

@router.callback_query(F.data == "pay:stars")
async def pay_stars(c: CallbackQuery):
    amount = settings.private_price_stars
    payload = f"private:{c.from_user.id}:{int(datetime.now().timestamp())}"
    await c.message.answer_invoice(
        title="Доступ в приват", description=f"Доступ на {settings.private_days} дней",
        payload=payload, currency="XTR", prices=[LabeledPrice(label="Доступ в приват", amount=amount)]
    )
    await c.answer()

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message):
    sp = message.successful_payment
    if not sp.invoice_payload.startswith(f"private:{message.from_user.id}:"):
        return
    await db.grant_private(message.from_user.id, settings.private_days)
    await db.execute("INSERT INTO payments(user_id,method,amount,currency,status,payload,created_at) VALUES(?,?,?,?,?,?,?)",
        (message.from_user.id,"stars",sp.total_amount,sp.currency,"paid",sp.telegram_payment_charge_id,db.now_iso()))
    await reward_referrer(message.from_user.id, message.bot)
    await message.answer(f"✅ Оплата прошла! Доступ активирован на {settings.private_days} дней.")

@router.callback_query(F.data == "pay:manual")
async def pay_manual(c: CallbackQuery):
    _, pid = await db.execute("INSERT INTO payments(user_id,method,amount,currency,status,created_at) VALUES(?,?,?,?,?,?)",
        (c.from_user.id,"manual",0,"RUB","awaiting_proof",db.now_iso()))
    details = await db.get_setting("manual_payment_details", settings.manual_payment_details)
    text = await db.get_setting("manual_payment_text", settings.manual_payment_text)
    await c.message.answer(f"🏦 <b>Ручная оплата</b>\n{details}\n\n{text}", reply_markup=manual_paid(pid))
    await c.answer()

@router.callback_query(F.data.startswith("manualproof:"))
async def manual_proof_start(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[1])
    row = await db.fetchone("SELECT * FROM payments WHERE id=? AND user_id=? AND status='awaiting_proof'",(pid,c.from_user.id))
    if not row: return await c.answer("Платёж не найден",show_alert=True)
    await state.update_data(payment_id=pid)
    await state.set_state(UserFlow.proof)
    await c.message.answer("Отправьте фото или документ с подтверждением оплаты.")
    await c.answer()

@router.message(UserFlow.proof, F.photo | F.document)
async def receive_proof(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await db.execute("UPDATE payments SET proof_file_id=?,status='review' WHERE id=? AND user_id=?",
                     (file_id,data["payment_id"],message.from_user.id))
    for admin in settings.admin_ids:
        await bot.send_message(admin,f"🧾 Запрос ручной оплаты №{data['payment_id']} от <code>{message.from_user.id}</code>",
                               reply_markup=review_payment(data["payment_id"],message.from_user.id))
        if message.photo: await bot.send_photo(admin,file_id)
        else: await bot.send_document(admin,file_id)
    await message.answer("✅ Подтверждение отправлено администратору. Доступ появится после проверки.")
    await state.clear()

@router.callback_query(F.data.startswith("approve:"))
async def approve_payment(c: CallbackQuery, bot: Bot):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    _, pid, uid = c.data.split(":")
    row = await db.fetchone("SELECT * FROM payments WHERE id=? AND status='review'",(int(pid),))
    if not row: return await c.answer("Заявка уже обработана",show_alert=True)
    await db.execute("UPDATE payments SET status='paid',reviewed_by=? WHERE id=?",(c.from_user.id,int(pid)))
    await db.grant_private(int(uid),settings.private_days)
    await reward_referrer(int(uid), bot)
    await bot.send_message(int(uid),f"✅ Оплата подтверждена. Доступ активирован на {settings.private_days} дней.")
    await c.message.edit_reply_markup(reply_markup=None)
    await c.answer("Доступ выдан")

@router.callback_query(F.data.startswith("reject:"))
async def reject_payment(c: CallbackQuery, bot: Bot):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    _, pid, uid = c.data.split(":")
    await db.execute("UPDATE payments SET status='rejected',reviewed_by=? WHERE id=? AND status='review'",(c.from_user.id,int(pid)))
    await bot.send_message(int(uid),"❌ Подтверждение оплаты отклонено. Если произошла ошибка, обратитесь в поддержку.")
    await c.message.edit_reply_markup(reply_markup=None)
    await c.answer("Отклонено")

@router.callback_query(F.data == "adm:buttons")
async def admin_buttons(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    rows = await db.fetchall("SELECT * FROM buttons ORDER BY COALESCE(parent_id,0),sort_order,id")
    await c.message.edit_text("🧩 <b>Кнопки меню</b>\nВыберите кнопку для изменения:",reply_markup=buttons_admin(rows))
    await c.answer()

@router.callback_query(F.data.startswith("editbtn:"))
async def edit_btn(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    bid=int(c.data.split(":")[1]); row=await db.fetchone("SELECT * FROM buttons WHERE id=?",(bid,))
    if not row: return await c.answer("Не найдено")
    await c.message.edit_text(f"<b>{row['title']}</b>\nТип: <code>{row['kind']}</code>\nСодержимое: {row['value'][:500]}",reply_markup=edit_button_menu(bid))
    await c.answer()

@router.callback_query(F.data.startswith("bfield:"))
async def edit_btn_field(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    _, field, bid=c.data.split(":")
    await state.update_data(button_id=int(bid),field=field)
    await state.set_state(AdminFlow.edit_title if field=="title" else AdminFlow.edit_value)
    await c.message.answer("Отправьте новое значение:")
    await c.answer()

@router.message(AdminFlow.edit_title)
async def save_btn_title(message: Message,state: FSMContext):
    d=await state.get_data(); await db.execute("UPDATE buttons SET title=? WHERE id=?",(message.text[:60],d["button_id"]))
    await state.clear(); await message.answer("✅ Название изменено. /admin → Кнопки меню")

@router.message(AdminFlow.edit_value)
async def save_btn_value(message: Message,state: FSMContext):
    d=await state.get_data(); await db.execute("UPDATE buttons SET value=? WHERE id=?",(message.text,d["button_id"]))
    await state.clear(); await message.answer("✅ Содержимое/ссылка изменены. /admin → Кнопки меню")

@router.callback_query(F.data.startswith("delbtn:"))
async def delete_btn(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    bid=int(c.data.split(":")[1]); await db.execute("DELETE FROM buttons WHERE id=?",(bid,))
    await c.message.edit_text("🗑 Кнопка и её вложенные кнопки удалены.",reply_markup=admin_menu()); await c.answer()

@router.callback_query(F.data == "addbtn")
async def add_btn_start(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    await state.set_state(AdminFlow.button_title)
    await c.message.answer("Введите название новой кнопки:")
    await c.answer()

@router.message(AdminFlow.button_title)
async def add_btn_title(message: Message,state: FSMContext):
    await state.update_data(title=message.text[:60])
    await state.set_state(AdminFlow.button_kind)
    await message.answer("Укажите тип: text, url, category, private или buy.")

@router.message(AdminFlow.button_kind)
async def add_btn_kind(message: Message,state: FSMContext):
    kind=message.text.strip().lower()
    if kind not in {"text","url","category","private","buy"}:
        return await message.answer("Неверный тип. Выберите: text, url, category, private, buy.")
    await state.update_data(kind=kind)
    await state.set_state(AdminFlow.button_parent)
    await message.answer("ID родительского раздела (0 — главное меню):")

@router.message(AdminFlow.button_parent)
async def add_btn_parent(message: Message,state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введите числовой ID раздела или 0.")
    parent_id = int(message.text) or None
    if parent_id:
        parent = await db.fetchone("SELECT id FROM buttons WHERE id=? AND kind='category'", (parent_id,))
        if not parent:
            return await message.answer("Родительский ID не найден или это не категория. Попробуйте ещё раз.")
    await state.update_data(parent_id=parent_id)
    await state.set_state(AdminFlow.button_value)
    await message.answer("Отправьте ссылку или текст. Для пустого значения отправьте -")

@router.message(AdminFlow.button_value)
async def add_btn_value(message: Message,state: FSMContext):
    d=await state.get_data(); value="" if message.text=="-" else message.text
    _, bid=await db.execute("INSERT INTO buttons(title,kind,value,parent_id,sort_order) VALUES(?,?,?,?,?)",
        (d["title"],d["kind"],value,d.get("parent_id"),100))
    await state.clear(); await message.answer(f"✅ Кнопка создана, ID: {bid}. Чтобы вложить в раздел, укажите его ID при создании.")

@router.callback_query(F.data == "adm:posts")
async def posts_admin(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    b=InlineKeyboardBuilder(); b.button(text="➕ Добавить способ заработка",callback_data="addpost")
    b.button(text="⬅️ Назад",callback_data="admin"); b.adjust(1)
    posts=await db.fetchall("SELECT id,title FROM posts ORDER BY id DESC LIMIT 20")
    text="💰 <b>Публикации</b>\n"+("\n".join(f"#{p['id']} — {p['title']}" for p in posts) if posts else "Пока нет публикаций.")
    await c.message.edit_text(text,reply_markup=b.as_markup()); await c.answer()

@router.callback_query(F.data == "addpost")
async def add_post_start(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    await state.set_state(AdminFlow.post_title); await c.message.answer("Название способа заработка:"); await c.answer()

@router.message(AdminFlow.post_title)
async def post_title(message: Message,state: FSMContext):
    await state.update_data(title=message.text[:120]); await state.set_state(AdminFlow.post_body)
    await message.answer("Теперь отправьте описание. Можно приложить ссылку в тексте.")

@router.message(AdminFlow.post_body)
async def post_body(message: Message,state: FSMContext):
    d=await state.get_data()
    media=message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
    body=message.caption or message.text or ""
    await db.execute("INSERT INTO posts(title,body,media_file_id,created_at) VALUES(?,?,?,?)",
                     (d["title"],body,media,db.now_iso()))
    await state.clear(); await message.answer("✅ Публикация добавлена и доступна в меню «Ежедневный заработок».")

@router.callback_query(F.data == "adm:promos")
async def promos_admin(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    b=InlineKeyboardBuilder(); b.button(text="➕ Создать промокод",callback_data="addpromo")
    b.button(text="⬅️ Назад",callback_data="admin"); b.adjust(1)
    rows=await db.fetchall("SELECT code,days,uses_left FROM promos WHERE active=1")
    text="🎟 <b>Промокоды</b>\n"+("\n".join(f"{r['code']} — {r['days']} дн., осталось {r['uses_left']}" for r in rows) if rows else "Промокодов пока нет.")
    await c.message.edit_text(text,reply_markup=b.as_markup()); await c.answer()

@router.callback_query(F.data == "addpromo")
async def addpromo(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    await state.set_state(AdminFlow.promo_code); await c.message.answer("Введите код промокода (латиница/цифры):"); await c.answer()

@router.message(AdminFlow.promo_code)
async def promo_code(message: Message,state: FSMContext):
    code=message.text.strip().upper()
    if not code.isalnum(): return await message.answer("Используйте только буквы и цифры.")
    await state.update_data(code=code); await state.set_state(AdminFlow.promo_days); await message.answer("Сколько дней доступа выдаёт код?")

@router.message(AdminFlow.promo_days)
async def promo_days(message: Message,state: FSMContext):
    if not message.text.isdigit() or int(message.text)<1: return await message.answer("Введите целое число дней больше нуля.")
    await state.update_data(days=int(message.text)); await state.set_state(AdminFlow.promo_uses); await message.answer("Сколько раз можно использовать промокод?")

@router.message(AdminFlow.promo_uses)
async def promo_uses(message: Message,state: FSMContext):
    if not message.text.isdigit() or int(message.text)<1: return await message.answer("Введите число использований больше нуля.")
    d=await state.get_data()
    try:
        await db.execute("INSERT INTO promos(code,days,uses_left) VALUES(?,?,?)",(d["code"],d["days"],int(message.text)))
    except Exception:
        return await message.answer("Такой промокод уже существует. Начните заново через /admin.")
    await state.clear(); await message.answer(f"✅ Промокод {d['code']} создан.")

@router.message(Command("promo"))
async def promo_command(message: Message,state: FSMContext):
    await state.set_state(UserFlow.promo); await message.answer("Введите промокод:")

@router.message(UserFlow.promo)
async def redeem_promo(message: Message,state: FSMContext):
    code=message.text.strip().upper()
    row=await db.fetchone("SELECT * FROM promos WHERE code=? AND active=1 AND uses_left>0",(code,))
    used=await db.fetchone("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?",(code,message.from_user.id))
    if not row or used:
        await state.clear(); return await message.answer("❌ Промокод недействителен или уже использован.")
    await db.execute("INSERT INTO promo_uses(code,user_id,used_at) VALUES(?,?,?)",(code,message.from_user.id,db.now_iso()))
    await db.execute("UPDATE promos SET uses_left=uses_left-1 WHERE code=?",(code,))
    await db.grant_private(message.from_user.id,row["days"])
    await state.clear(); await message.answer(f"🎉 Промокод активирован! Доступ продлён на {row['days']} дней.")

@router.callback_query(F.data == "adm:payment")
async def payment_settings(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    b=InlineKeyboardBuilder()
    b.button(text="Изменить реквизиты/инструкцию",callback_data="paysettings:details")
    b.button(text="Изменить текст оплаты",callback_data="paysettings:text")
    b.button(text="⬅️ Назад",callback_data="admin"); b.adjust(1)
    details=await db.get_setting("manual_payment_details",settings.manual_payment_details)
    text=await db.get_setting("manual_payment_text",settings.manual_payment_text)
    await c.message.edit_text(f"💳 <b>Ручная оплата</b>\nРеквизиты: {details}\nИнструкция: {text}",reply_markup=b.as_markup()); await c.answer()

@router.callback_query(F.data.startswith("paysettings:"))
async def payment_setting_start(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    field=c.data.split(":")[1]; await state.update_data(pay_field=field)
    await state.set_state(AdminFlow.payment_details if field=="details" else AdminFlow.payment_text)
    await c.message.answer("Отправьте новое значение:"); await c.answer()

@router.message(AdminFlow.payment_details)
async def save_payment_details(message: Message,state: FSMContext):
    await db.upsert_setting("manual_payment_details",message.text)
    await state.clear(); await message.answer("✅ Реквизиты сохранены.")

@router.message(AdminFlow.payment_text)
async def save_payment_text(message: Message,state: FSMContext):
    await db.upsert_setting("manual_payment_text",message.text)
    await state.clear(); await message.answer("✅ Текст оплаты сохранён.")

@router.callback_query(F.data == "adm:stats")
async def stats(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    users=await db.fetchone("SELECT COUNT(*) n FROM users")
    paid=await db.fetchone("SELECT COUNT(*) n FROM payments WHERE status='paid'")
    revenue=await db.fetchone("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE status='paid'")
    active=await db.fetchone("SELECT COUNT(*) n FROM users WHERE private_until > ?", (datetime.now(timezone.utc).isoformat(),))
    active_count=active["n"]
    await c.message.edit_text(f"📊 <b>Статистика</b>\nПользователей: {users['n']}\nОплаченных платежей: {paid['n']}\nСумма платежей (включая разные валюты — не суммировать как одну валюту): {revenue['n']}\nЗаписей с доступом: {active_count}",reply_markup=admin_menu())
    await c.answer()

@router.callback_query(F.data == "adm:broadcast")
async def broadcast_start(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    await state.set_state(AdminFlow.broadcast); await c.message.answer("Отправьте текст рассылки. HTML-разметка поддерживается."); await c.answer()

@router.message(AdminFlow.broadcast)
async def broadcast_send(message: Message,state: FSMContext,bot: Bot):
    if not is_admin(message.from_user.id): return
    users=await db.fetchall("SELECT user_id FROM users")
    ok=0; failed=0
    for row in users:
        try:
            await bot.send_message(row["user_id"],message.html_text or message.text)
            ok+=1
            await asyncio.sleep(0.04)
        except Exception: failed+=1
    await state.clear(); await message.answer(f"📨 Рассылка завершена. Доставлено: {ok}, ошибок: {failed}.")

@router.message(F.text == "💬 Поддержка")
@router.message(Command("support"))
async def support_start(message: Message,state: FSMContext):
    await state.set_state(UserFlow.support)
    await message.answer(await db.get_setting("support_text",settings.support_text))

@router.message(UserFlow.support)
async def support_send(message: Message,state: FSMContext,bot: Bot):
    text=message.text or message.caption or "[вложение]"
    _, ticket=await db.execute("INSERT INTO support(user_id,message,created_at) VALUES(?,?,?)",(message.from_user.id,text,db.now_iso()))
    for admin in settings.admin_ids:
        await bot.send_message(admin,f"📩 <b>Обращение #{ticket}</b> от <code>{message.from_user.id}</code>\n{text}",reply_markup=support_reply(ticket))
    await state.clear(); await message.answer("✅ Обращение отправлено.")

@router.callback_query(F.data == "adm:support")
async def support_admin(c: CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    rows=await db.fetchall("SELECT * FROM support WHERE status='open' ORDER BY id DESC LIMIT 20")
    if not rows: return await c.answer("Открытых обращений нет",show_alert=True)
    for r in rows:
        await c.message.answer(f"📩 #{r['id']} от {r['user_id']}\n{r['message']}",reply_markup=support_reply(r["id"]))
    await c.answer()

@router.callback_query(F.data.startswith("reply:"))
async def support_reply_start(c: CallbackQuery,state: FSMContext):
    if not is_admin(c.from_user.id): return await c.answer("Нет доступа",show_alert=True)
    ticket=int(c.data.split(":")[1]); row=await db.fetchone("SELECT user_id FROM support WHERE id=?",(ticket,))
    if not row: return await c.answer("Обращение не найдено")
    await state.update_data(ticket=ticket,user_id=row["user_id"]); await state.set_state(AdminFlow.support_reply)
    await c.message.answer("Введите ответ пользователю:"); await c.answer()

@router.message(AdminFlow.support_reply)
async def support_reply_send(message: Message,state: FSMContext,bot: Bot):
    d=await state.get_data()
    await bot.send_message(d["user_id"],f"💬 Ответ поддержки:\n{message.text}")
    await db.execute("UPDATE support SET status='closed' WHERE id=?",(d["ticket"],))
    await state.clear(); await message.answer("✅ Ответ отправлен.")

@router.message(Command("ref"))
async def referral(message: Message,bot: Bot):
    link=await create_start_link(bot,f"ref_{message.from_user.id}",encode=False)
    count=await db.fetchone("SELECT COUNT(*) n FROM users WHERE referrer_id=?",(message.from_user.id,))
    await message.answer(f"🎁 <b>Твоя реферальная ссылка:</b>\n{link}\nПриглашено: {count['n']}")

@router.callback_query(F.data == "admin")
async def admin_menu_callback(c: CallbackQuery):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("⚙️ <b>Панель администратора</b>",reply_markup=admin_menu())

@router.message(Command("id"))
async def my_id(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("menu"))
async def menu_command(message: Message):
    await show_home(message, message.from_user.id)

@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "Команды:\n/menu — главное меню\n/promo — активировать промокод\n/ref — реферальная ссылка\n/support — поддержка\n/id — узнать свой Telegram ID"
    )
