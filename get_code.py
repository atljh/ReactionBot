import asyncio
from pathlib import Path
from telethon import TelegramClient
from src.utils import json_read, proxy_to_telethon

PHONE = "12097067447"
SESSIONS_DIR = Path("sessions")

async def main():
    session_file = SESSIONS_DIR / f"{PHONE}.session"
    json_file = SESSIONS_DIR / f"{PHONE}.json"

    data = json_read(json_file)
    proxy = proxy_to_telethon(data.get("proxy"))

    client = TelegramClient(
        str(session_file),
        api_id=data.get("app_id") or data.get("api_id"),
        api_hash=data.get("app_hash") or data.get("api_hash"),
        device_model=data.get("device", "Desktop"),
        system_version=data.get("sdk", "Windows 10"),
        app_version=data.get("app_version", "4.8.0"),
        lang_code=data.get("lang_code", "en"),
        system_lang_code=data.get("system_lang_code", "en-US"),
        proxy=proxy,
        timeout=15,
        flood_sleep_threshold=0,
        receive_updates=False,
    )

    await client.connect()

    if not await client.is_user_authorized():
        print("Account not authorized!")
        return

    print(f"Connected as {PHONE}\n")
    print("Last messages from Telegram (service messages):\n")

    # Get messages from Telegram service account (777000)
    async for msg in client.iter_messages(777000, limit=5):
        print(f"[{msg.date}]")
        print(msg.text)
        print("-" * 40)

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
