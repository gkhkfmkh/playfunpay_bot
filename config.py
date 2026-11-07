import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _ids(value: str):
    result = set()
    for part in value.split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result

@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: set = frozenset(_ids(os.getenv("ADMIN_IDS", "")))
    database_path: str = os.getenv("DATABASE_PATH", "data/bot.db")
    bot_name: str = os.getenv("BOT_NAME", "PlayFunpay")
    private_price_stars: int = int(os.getenv("PRIVATE_PRICE_STARS", "99"))
    private_days: int = int(os.getenv("PRIVATE_DAYS", "30"))
    manual_payment_text: str = os.getenv("MANUAL_PAYMENT_TEXT", "После оплаты отправьте подтверждение.")
    manual_payment_details: str = os.getenv("MANUAL_PAYMENT_DETAILS", "Реквизиты уточняются у администратора.")
    support_text: str = os.getenv("SUPPORT_TEXT", "Напишите сообщение для поддержки.")

settings = Settings()
