#!/usr/bin/env python3
import argparse
import asyncio
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table

from config import get_settings
from src.database import Database
from src.reactor import Reactor
from src.session_loader import SessionLoader
from src.parser import LinkParser


console = Console()


def parse_delay(delay_str: str) -> tuple:
    if "-" in delay_str:
        parts = delay_str.split("-")
        return int(parts[0]), int(parts[1])
    val = int(delay_str)
    return val, val


async def sync_sessions(db: Database, loader: SessionLoader):
    for session_file, json_file, json_data in loader.find_sessions():
        phone = json_data.get("phone", session_file.stem)
        proxy = json_data.get("proxy")
        proxy_str = str(proxy) if proxy else None

        existing = await db.get_account(phone)
        if not existing:
            await db.add_account(
                phone=phone,
                session_file=str(session_file),
                json_file=str(json_file),
                proxy=proxy_str
            )
            console.print(f"[green]+ Added account: {phone}[/green]")


async def main():
    parser = argparse.ArgumentParser(
        description="TG Reacter - Telegram Reactions Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--link", "-l",
        help="Post link (https://t.me/channel/123 or https://t.me/c/123456789/123)"
    )
    parser.add_argument(
        "--reaction", "-r",
        default="thumbsup",
        help="Reaction type (thumbsup, heart, fire, etc.) or emoji"
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=10,
        help="Number of reactions to place (default: 10)"
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=5,
        help="Number of parallel threads (default: 5)"
    )
    parser.add_argument(
        "--delay", "-d",
        default="5-15",
        help="Delay range in seconds (default: 5-15)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test run without sending reactions"
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml"
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Sync sessions from sessions/ folder to database"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show account statistics"
    )
    parser.add_argument(
        "--clear-history",
        action="store_true",
        help="Clear reaction history for specified post"
    )

    args = parser.parse_args()

    try:
        settings = get_settings(args.config)
    except FileNotFoundError:
        console.print("[red]Error: config.yaml not found![/red]")
        console.print("Copy config/config.yaml.example to config.yaml and configure it")
        sys.exit(1)

    db = Database(settings.database)
    await db.connect()

    loader = SessionLoader(settings.sessions_dir)

    if args.sync:
        await sync_sessions(db, loader)
        console.print("[green]Sync completed![/green]")
        await db.close()
        return

    if args.stats:
        accounts = await db.get_all_accounts(active_only=False)

        table = Table(title="Account Statistics")
        table.add_column("Phone", style="cyan")
        table.add_column("Active", style="green")
        table.add_column("Today", style="yellow")
        table.add_column("Last Used")

        for acc in accounts:
            table.add_row(
                acc["phone"],
                "Yes" if acc["is_active"] else "No",
                str(acc["reactions_today"] or 0),
                str(acc["last_used"] or "Never")[:19]
            )

        console.print(table)
        console.print(f"\nTotal: {len(accounts)} | Active: {sum(1 for a in accounts if a['is_active'])}")
        await db.close()
        return

    if not args.link:
        parser.print_help()
        await db.close()
        return

    if not LinkParser.is_valid(args.link):
        console.print(f"[red]Error: Invalid link format: {args.link}[/red]")
        await db.close()
        sys.exit(1)

    if args.clear_history:
        parsed = LinkParser.parse(args.link)
        await db.clear_reactions(parsed.channel_id, parsed.message_id)
        console.print(f"[green]Cleared reaction history for post[/green]")
        await db.close()
        return

    reaction_emoji = settings.get_reaction_emoji(args.reaction)
    delay_range = parse_delay(args.delay)

    console.print(f"\n[bold]TG Reacter[/bold]")
    console.print(f"Link: {args.link}")
    console.print(f"Reaction: {reaction_emoji}")
    console.print(f"Count: {args.count}")
    console.print(f"Threads: {args.threads}")
    console.print(f"Delay: {delay_range[0]}-{delay_range[1]}s")
    if args.dry_run:
        console.print("[yellow]DRY RUN MODE[/yellow]")
    console.print()

    await sync_sessions(db, loader)

    reactor = Reactor(
        database=db,
        delay_range=delay_range,
        max_reactions_per_day=settings.max_reactions_per_day
    )

    results = await reactor.run(
        post_link=args.link,
        reaction=reaction_emoji,
        count=args.count,
        threads=args.threads,
        dry_run=args.dry_run
    )

    stats = reactor.get_stats()

    console.print()
    console.print(f"[green]Success: {stats['success']}[/green]")
    console.print(f"[red]Failed: {stats['failed']}[/red]")

    if stats['errors']:
        console.print("\nErrors:")
        for error, count in stats['errors'].items():
            console.print(f"  - {error}: {count}")

    for r in results:
        if not r.success:
            console.print(f"  [dim]{r.phone}: {r.error}[/dim]")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
