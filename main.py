import argparse
import asyncio
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import get_settings
from src.database import Database
from src.reactor import Reactor
from src.session_loader import SessionLoader
from src.parser import LinkParser
from src.tdata_converter import TDataConverter
from src.utils import parse_proxy_string, json_read, json_write, log_error


console = Console()


def parse_delay(delay_str: str) -> tuple:
    if "-" in delay_str:
        parts = delay_str.split("-")
        return int(parts[0]), int(parts[1])
    val = int(delay_str)
    return val, val


def load_proxies(proxies_file: Path) -> list:
    if not proxies_file.exists():
        return []
    proxies = []
    with open(proxies_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                proxy = parse_proxy_string(line)
                if proxy:
                    proxies.append(proxy)
    return proxies


def get_existing_sessions(sessions_dir: Path) -> set:
    existing = set()
    for json_file in sessions_dir.glob("*.json"):
        data = json_read(json_file)
        if data:
            phone = data.get("phone", json_file.stem)
            existing.add(str(phone).replace("+", ""))
        existing.add(json_file.stem.replace("+", ""))

    for session_file in sessions_dir.glob("*.session"):
        existing.add(session_file.stem.replace("+", ""))

    return existing


def load_tdata_cache(tdatas_dir: Path) -> dict:
    cache_file = tdatas_dir / ".converted_cache.json"
    if cache_file.exists():
        return json_read(cache_file) or {}
    return {}


def save_tdata_cache(tdatas_dir: Path, cache: dict):
    cache_file = tdatas_dir / ".converted_cache.json"
    json_write(cache_file, cache)


def find_unconverted_tdata(tdatas_dir: Path, existing_sessions: set, tdata_cache: dict) -> list:
    unconverted = []
    if not tdatas_dir.exists():
        return unconverted

    for item in tdatas_dir.iterdir():
        if not item.is_dir():
            continue

        folder_name = item.name.replace("+", "")

        if folder_name in tdata_cache:
            continue

        if folder_name in existing_sessions:
            continue

        tdata_path = item / "tdata"
        if tdata_path.exists():
            unconverted.append((item.name, tdata_path))
        else:
            key_files = ["key_data", "key_datas"]
            if any((item / kf).exists() for kf in key_files):
                unconverted.append((item.name, item))

    return unconverted


async def auto_convert_tdata(settings, db: Database, proxies: list):
    existing_sessions = get_existing_sessions(settings.sessions_dir)
    tdata_cache = load_tdata_cache(settings.tdatas_dir)
    unconverted = find_unconverted_tdata(settings.tdatas_dir, existing_sessions, tdata_cache)

    if not unconverted:
        return 0

    console.print(f"\n[yellow]Found {len(unconverted)} unconverted tdata folders[/yellow]")

    if not proxies:
        console.print("[red]No proxies in proxies.txt! Cannot convert tdata without proxy.[/red]")
        return 0

    converter = TDataConverter(settings.api_id, settings.api_hash)
    converted = 0
    proxy_index = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
    ) as progress:
        task = progress.add_task("Converting tdata...", total=len(unconverted))

        for name, tdata_path in unconverted:
            proxy = proxies[proxy_index % len(proxies)]
            proxy_index += 1

            try:
                session_file, json_file, metadata = await converter.convert(
                    str(tdata_path),
                    str(settings.sessions_dir),
                    proxy
                )

                phone = metadata.get("phone", "unknown")

                tdata_cache[name] = phone
                save_tdata_cache(settings.tdatas_dir, tdata_cache)

                if metadata.get("already_exists"):
                    console.print(f"  [dim]~ Skipped: {name} -> {phone} (already exists)[/dim]")
                    continue

                await db.add_account(
                    phone=phone,
                    session_file=session_file,
                    json_file=json_file,
                    proxy=str(proxy) if proxy else None
                )

                console.print(f"  [green]+ Converted: {name} -> {phone}[/green]")
                converted += 1

            except Exception as e:
                import traceback
                error_msg = str(e)
                full_traceback = traceback.format_exc()
                console.print(f"  [red]x Failed: {name} - {error_msg[:50]}[/red]")
                log_error("tdata_convert", name, f"{error_msg}\n{full_traceback}")

            progress.advance(task)

    return converted


async def sync_sessions(db: Database, loader: SessionLoader, proxies: list):
    proxy_index = 0
    synced = 0

    for session_file, json_file, json_data in loader.find_sessions():
        phone = json_data.get("phone", session_file.stem)
        proxy = json_data.get("proxy")

        if not proxy and proxies:
            proxy = proxies[proxy_index % len(proxies)]
            proxy_index += 1
            json_data["proxy"] = proxy
            json_write(json_file, json_data)
            console.print(f"  [cyan]~ Assigned proxy to: {phone}[/cyan]")

        proxy_str = str(proxy) if proxy else None

        existing = await db.get_account(phone)
        if not existing:
            await db.add_account(
                phone=phone,
                session_file=str(session_file),
                json_file=str(json_file),
                proxy=proxy_str
            )
            console.print(f"  [green]+ Added account: {phone}[/green]")
            synced += 1

    return synced


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
        "--invite", "-i",
        help="Invite link for private channels (https://t.me/+XXX)"
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
    parser.add_argument(
        "--no-convert",
        action="store_true",
        help="Skip auto-conversion of tdata folders"
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
    proxies = load_proxies(settings.proxies_file)

    if proxies:
        console.print(f"[dim]Loaded {len(proxies)} proxies[/dim]")

    if not args.no_convert:
        await auto_convert_tdata(settings, db, proxies)

    if args.sync:
        synced = await sync_sessions(db, loader, proxies)
        console.print(f"[green]Sync completed! Added: {synced}[/green]")
        await db.close()
        return

    if args.stats:
        accounts = await db.get_all_accounts(active_only=False)

        table = Table(title="Account Statistics")
        table.add_column("Phone", style="cyan")
        table.add_column("Active", style="green")
        table.add_column("Today", style="yellow")
        table.add_column("Proxy")
        table.add_column("Last Used")

        for acc in accounts:
            proxy_info = "-"
            if acc.get("proxy"):
                try:
                    p = eval(acc["proxy"]) if isinstance(acc["proxy"], str) else acc["proxy"]
                    if isinstance(p, dict):
                        proxy_info = f"{p.get('addr', '?')}:{p.get('port', '?')}"
                except:
                    proxy_info = "yes"

            table.add_row(
                acc["phone"],
                "Yes" if acc["is_active"] else "No",
                str(acc["reactions_today"] or 0),
                proxy_info,
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
    if args.invite:
        console.print(f"Invite: {args.invite}")
    console.print(f"Reaction: {reaction_emoji}")
    console.print(f"Count: {args.count}")
    console.print(f"Threads: {args.threads}")
    console.print(f"Delay: {delay_range[0]}-{delay_range[1]}s")
    if args.dry_run:
        console.print("[yellow]DRY RUN MODE[/yellow]")
    console.print()

    await sync_sessions(db, loader, proxies)

    reactor = Reactor(
        database=db,
        delay_range=delay_range,
        max_reactions_per_day=settings.max_reactions_per_day,
        sessions_dir=settings.sessions_dir,
        tdatas_dir=settings.tdatas_dir,
        console=console
    )

    results = await reactor.run(
        post_link=args.link,
        reaction=reaction_emoji,
        count=args.count,
        threads=args.threads,
        dry_run=args.dry_run,
        invite_link=args.invite
    )

    stats = reactor.get_stats()

    console.print()
    console.print(f"[green]Success: {stats['success']}[/green]")
    console.print(f"[red]Failed: {stats['failed']}[/red]")

    if reactor.moved_accounts:
        console.print(f"\n[yellow]Moved accounts: {len(reactor.moved_accounts)}[/yellow]")
        for phone, folder in reactor.moved_accounts:
            console.print(f"  - {phone} -> sessions_{folder}/")

    if stats['errors']:
        console.print("\nErrors:")
        for error, count in stats['errors'].items():
            console.print(f"  - {error}: {count}")

    for r in results:
        if not r.success and r.phone not in [p for p, _ in reactor.moved_accounts]:
            console.print(f"  [dim]{r.phone}: {r.error}[/dim]")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
