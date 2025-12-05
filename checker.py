import argparse
import asyncio
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import get_settings
from src.database import Database
from src.session_loader import SessionLoader
from src.client import BaseThon
from src.utils import json_read, log_error, move_account_to_status_folder, get_status_folder


console = Console()


async def check_account(session_file: Path, json_file: Path, json_data: dict) -> tuple:
    phone = json_data.get("phone", session_file.stem)
    client = BaseThon(session_file=session_file, json_data=json_data)

    try:
        result = await client.check()
        if result == "OK":
            me = await client.get_me()
            username = f"@{me.username}" if me.username else "-"
            return phone, "OK", username, session_file, json_file
        log_error("checker", phone, result)
        return phone, result, "-", session_file, json_file
    except Exception as e:
        error_msg = str(e)
        log_error("checker", phone, error_msg)
        return phone, f"ERROR:{error_msg[:30]}", "-", session_file, json_file
    finally:
        await client.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="Check Telegram accounts")

    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Check all accounts"
    )
    parser.add_argument(
        "--phone", "-p",
        help="Check specific phone number"
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=3,
        help="Number of parallel checks (default: 3)"
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml"
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move bad accounts to status folders"
    )

    args = parser.parse_args()

    try:
        settings = get_settings(args.config)
    except FileNotFoundError:
        console.print("[red]Error: config.yaml not found![/red]")
        return

    db = Database(settings.database)
    await db.connect()

    loader = SessionLoader(settings.sessions_dir)

    if args.phone:
        result = loader.get_session(args.phone)
        if not result:
            console.print(f"[red]Account not found: {args.phone}[/red]")
            await db.close()
            return

        session_file, json_file, json_data = result
        phone, status, username, _, _ = await check_account(session_file, json_file, json_data)

        if status == "OK":
            console.print(f"[green]{phone} - OK ({username})[/green]")
        else:
            console.print(f"[red]{phone} - {status}[/red]")
            if args.move and get_status_folder(status):
                moved = move_account_to_status_folder(
                    session_file, json_file, status,
                    settings.sessions_dir, settings.tdatas_dir
                )
                if moved:
                    folder = get_status_folder(status)
                    console.print(f"  [dim]→ moved to sessions_{folder}/[/dim]")

        await db.close()
        return

    sessions = list(loader.find_sessions())
    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        await db.close()
        return

    console.print(f"Checking {len(sessions)} accounts...\n")

    results = []
    semaphore = asyncio.Semaphore(args.threads)

    async def check_with_semaphore(session_file, json_file, json_data):
        async with semaphore:
            return await check_account(session_file, json_file, json_data)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
    ) as progress:
        task = progress.add_task("Checking accounts...", total=len(sessions))

        tasks = []
        for session_file, json_file, json_data in sessions:
            tasks.append(check_with_semaphore(session_file, json_file, json_data))

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            progress.advance(task)

    table = Table(title="Account Check Results")
    table.add_column("Phone", style="cyan")
    table.add_column("Status")
    table.add_column("Username")
    if args.move:
        table.add_column("Action")

    ok_count = 0
    error_count = 0
    moved_count = 0

    for phone, status, username, session_file, json_file in sorted(results, key=lambda x: x[0]):
        if status == "OK":
            if args.move:
                table.add_row(phone, "[green]OK[/green]", username, "-")
            else:
                table.add_row(phone, "[green]OK[/green]", username)
            ok_count += 1
        else:
            action = "-"
            error_count += 1

            account = await db.get_account(phone)
            if account:
                await db.set_account_active(account["id"], False)

            if args.move and get_status_folder(status):
                moved = move_account_to_status_folder(
                    session_file, json_file, status,
                    settings.sessions_dir, settings.tdatas_dir
                )
                if moved:
                    folder = get_status_folder(status)
                    action = f"[yellow]→ {folder}[/yellow]"
                    moved_count += 1

            if args.move:
                table.add_row(phone, f"[red]{status}[/red]", username, action)
            else:
                table.add_row(phone, f"[red]{status}[/red]", username)

    console.print(table)
    console.print(f"\n[green]OK: {ok_count}[/green] | [red]Errors: {error_count}[/red]")
    if args.move and moved_count > 0:
        console.print(f"[yellow]Moved: {moved_count}[/yellow]")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
