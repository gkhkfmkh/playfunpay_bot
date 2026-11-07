import aiosqlite
from pathlib import Path
from datetime import datetime, timedelta, timezone
from bot.config import settings

def now_iso():
    return datetime.now(timezone.utc).isoformat()

async def connect():
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys=ON")
    return db

async def init_db():
    db = await connect()
    await db.executescript('''
    CREATE TABLE IF NOT EXISTS users(
      user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
      referrer_id INTEGER, created_at TEXT NOT NULL, private_until TEXT
    );
    CREATE TABLE IF NOT EXISTS buttons(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
      kind TEXT NOT NULL, value TEXT DEFAULT '', parent_id INTEGER,
      sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
      FOREIGN KEY(parent_id) REFERENCES buttons(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS posts(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
      body TEXT NOT NULL, media_file_id TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      method TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL,
      status TEXT NOT NULL, payload TEXT, proof_file_id TEXT,
      created_at TEXT NOT NULL, reviewed_by INTEGER
    );
    CREATE TABLE IF NOT EXISTS promos(
      code TEXT PRIMARY KEY, days INTEGER NOT NULL, uses_left INTEGER NOT NULL,
      active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS promo_uses(
      code TEXT NOT NULL, user_id INTEGER NOT NULL, used_at TEXT NOT NULL,
      PRIMARY KEY(code,user_id)
    );
    CREATE TABLE IF NOT EXISTS support(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      message TEXT NOT NULL, status TEXT DEFAULT 'open', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    ''')
    cur = await db.execute("SELECT COUNT(*) n FROM buttons")
    if (await cur.fetchone())["n"] == 0:
        defaults = [
          ("📢 Наш канал","url","https://t.me/your_channel",None,10),
          ("💎 Реклама","url","https://t.me/your_ads",None,20),
          ("🛒 Что в ПРИВАТЕ?","text","В приватном разделе находятся способы заработка, гайды и полезные материалы.",None,30),
          ("🔐 Купить доступ","buy","",None,40),
          ("💰 Ежедневный заработок","daily","",None,50),
          ("🎮 Игры и гайды","category","",None,60),
          ("📚 Инструкция по безопасности","text","Не передавайте пароли и коды подтверждения. Проверяйте ссылки и не оплачивайте сомнительные предложения.",None,70),
          ("💬 Поддержка","text","Чтобы обратиться в поддержку, отправьте команду /support.",None,80),
          ("🎟 Ввести промокод","text","Чтобы активировать промокод, отправьте команду /promo.",None,90),
        ]
        await db.executemany("INSERT INTO buttons(title,kind,value,parent_id,sort_order) VALUES(?,?,?,?,?)", defaults)
        cur = await db.execute("SELECT id FROM buttons WHERE title='🎮 Игры и гайды'")
        parent = (await cur.fetchone())["id"]
        games = [("🤖 Roblox","text","Гайды Roblox",parent,10),("⚡ Standoff 2","text","Гайды Standoff 2",parent,20),
                 ("🌵 Brawl Stars","text","Гайды Brawl Stars",parent,30),("🔫 PUBG Mobile","text","Гайды PUBG Mobile",parent,40),
                 ("🏰 Clash of Clans","text","Гайды Clash of Clans",parent,50)]
        await db.executemany("INSERT INTO buttons(title,kind,value,parent_id,sort_order) VALUES(?,?,?,?,?)", games)
    await db.commit()
    await db.close()

async def execute(sql, params=()):
    db = await connect()
    cur = await db.execute(sql, params)
    await db.commit()
    rows = await cur.fetchall() if cur.description else []
    last = cur.lastrowid
    await db.close()
    return rows, last

async def fetchone(sql, params=()):
    rows, _ = await execute(sql, params)
    return rows[0] if rows else None

async def fetchall(sql, params=()):
    rows, _ = await execute(sql, params)
    return rows

async def upsert_setting(key, value):
    await execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))

async def get_setting(key, default=""):
    row = await fetchone("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default

async def register_user(user, referrer_id=None):
    old = await fetchone("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    if not old:
        if referrer_id == user.id:
            referrer_id = None
        await execute("INSERT INTO users(user_id,username,first_name,referrer_id,created_at) VALUES(?,?,?,?,?)",
                      (user.id,user.username,user.first_name,referrer_id,now_iso()))
    else:
        await execute("UPDATE users SET username=?,first_name=? WHERE user_id=?",
                      (user.username,user.first_name,user.id))

async def has_private(user_id):
    row = await fetchone("SELECT private_until FROM users WHERE user_id=?", (user_id,))
    if not row or not row["private_until"]:
        return False
    try:
        return datetime.fromisoformat(row["private_until"]) > datetime.now(timezone.utc)
    except ValueError:
        return False

async def grant_private(user_id, days):
    row = await fetchone("SELECT private_until FROM users WHERE user_id=?", (user_id,))
    base = datetime.now(timezone.utc)
    if row and row["private_until"]:
        try:
            old = datetime.fromisoformat(row["private_until"])
            if old > base: base = old
        except ValueError: pass
    until = (base + timedelta(days=days)).isoformat()
    await execute("UPDATE users SET private_until=? WHERE user_id=?", (until,user_id))
    return until
