import asyncio
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import ChannelPrivateError, FloodWaitError

from src.utils import json_read, proxy_to_telethon


CHANNEL = "truexanewsua"
SESSIONS_DIR = Path("sessions")


async def test_join(session_file: Path):
    json_file = session_file.with_suffix(".json")
    if not json_file.exists():
        return

    data = json_read(json_file)
    phone = data.get("phone", session_file.stem)
    proxy = proxy_to_telethon(data.get("proxy"))

    client = TelegramClient(
        str(session_file),
        api_id=data.get("app_id") or data.get("api_id"),
        api_hash=data.get("app_hash") or data.get("api_hash"),
        proxy=proxy,
        timeout=15
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            print(f"  {phone}: NOT AUTHORIZED")
            return

        # Get entity by username
        print(f"  {phone}: Getting entity...")
        entity = await client.get_entity(CHANNEL)
        print(f"  {phone}: Entity OK (id={entity.id})")

        # Try to join
        print(f"  {phone}: Joining channel...")
        await client(JoinChannelRequest(entity))
        print(f"  ✓ {phone}: JOINED SUCCESSFULLY!")

    except ChannelPrivateError as e:
        print(f"  ✗ {phone}: CHANNEL_PRIVATE - {e}")
    except FloodWaitError as e:
        print(f"  ⚠ {phone}: FLOOD_WAIT {e.seconds}s")
    except Exception as e:
        print(f"  ✗ {phone}: ERROR - {type(e).__name__}: {e}")
    finally:
        await client.disconnect()


async def main():
    print(f"Testing join to @{CHANNEL}\n")

    sessions = list(SESSIONS_DIR.glob("*.session"))
    print(f"Found {len(sessions)} sessions\n")

    for session_file in sessions:
        await test_join(session_file)
        print()


if __name__ == "__main__":
    asyncio.run(main())
