import argparse
import asyncio
import json
from pathlib import Path
from rich.console import Console
from telethon import TelegramClient
from telethon.sessions import StringSession

from config import get_settings
from src.database import Database
from src.utils import parse_proxy_string, proxy_to_telethon, json_write


console = Console()


async def main():
    parser = argparse.ArgumentParser(description="Add new Telegram account")

    parser.add_argument(
        "--phone", "-p",
        required=True,
        help="Phone number with country code (+380...)"
    )
    parser.add_argument(
        "--proxy",
        help="Proxy (socks5://user:pass@ip:port)"
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml"
    )

    args = parser.parse_args()

    try:
        settings = get_settings(args.config)
    except FileNotFoundError:
        console.print("[red]Error: config.yaml not found![/red]")
        return

    phone = args.phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    proxy_dict = parse_proxy_string(args.proxy) if args.proxy else None
    proxy_tuple = proxy_to_telethon(proxy_dict) if proxy_dict else None

    console.print(f"\n[bold]Adding account: {phone}[/bold]")
    if proxy_dict:
        console.print(f"Proxy: {proxy_dict['addr']}:{proxy_dict['port']}")

    session_string = StringSession()

    client = TelegramClient(
        session_string,
        settings.api_id,
        settings.api_hash,
        proxy=proxy_tuple,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.8.0",
        lang_code="en",
        system_lang_code="en-US"
    )

    await client.connect()

    if not await client.is_user_authorized():
        console.print("\n[yellow]Sending code to phone...[/yellow]")
        await client.send_code_request(phone)

        code = console.input("[cyan]Enter code from Telegram: [/cyan]")

        try:
            await client.sign_in(phone, code)
        except Exception as e:
            if "password" in str(e).lower() or "2fa" in str(e).lower():
                password = console.input("[cyan]Enter 2FA password: [/cyan]")
                await client.sign_in(password=password)
            else:
                raise e

    me = await client.get_me()
    console.print(f"\n[green]Authorized as: {me.first_name} (@{me.username})[/green]")

    saved_session = client.session.save()

    sessions_dir = settings.sessions_dir
    sessions_dir.mkdir(parents=True, exist_ok=True)

    phone_clean = phone.replace("+", "")
    session_file = sessions_dir / f"{phone_clean}.session"
    json_file = sessions_dir / f"{phone_clean}.json"

    file_client = TelegramClient(
        str(session_file),
        settings.api_id,
        settings.api_hash,
        proxy=proxy_tuple
    )
    await file_client.connect()
    file_client.session.set_dc(
        client.session.dc_id,
        client.session.server_address,
        client.session.port
    )
    file_client.session.auth_key = client.session.auth_key
    file_client.session.save()
    await file_client.disconnect()

    json_data = {
        "app_id": settings.api_id,
        "app_hash": settings.api_hash,
        "phone": phone,
        "telegram_id": me.id,
        "username": me.username,
        "first_name": me.first_name,
        "last_name": me.last_name,
        "string_session": saved_session,
        "device": "Desktop",
        "sdk": "Windows 10",
        "app_version": "4.8.0",
        "lang_code": "en",
        "system_lang_code": "en-US",
        "proxy": proxy_dict
    }

    json_write(json_file, json_data)

    await client.disconnect()

    db = Database(settings.database)
    await db.connect()

    await db.add_account(
        phone=phone,
        session_file=str(session_file),
        json_file=str(json_file),
        proxy=str(proxy_dict) if proxy_dict else None
    )

    await db.close()

    console.print(f"\n[green]Account saved![/green]")
    console.print(f"Session: {session_file}")
    console.print(f"JSON: {json_file}")


if __name__ == "__main__":
    asyncio.run(main())
