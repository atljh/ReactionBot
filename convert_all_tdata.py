import argparse
import asyncio
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from config import get_settings
from src.database import Database
from src.tdata_converter import TDataConverter
from src.utils import parse_proxy_string, json_read


console = Console()


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


async def main():
    parser = argparse.ArgumentParser(description="Convert all tdata folders to session+json")

    parser.add_argument(
        "--proxy", "-p",
        help="Single proxy for all (socks5://user:pass@ip:port)"
    )
    parser.add_argument(
        "--threads", "-t",
        type=int,
        default=1,
        help="Parallel conversions (default: 1)"
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

    tdatas_dir = settings.tdatas_dir
    sessions_dir = settings.sessions_dir

    if not tdatas_dir.exists():
        console.print(f"[red]Error: tdatas folder not found: {tdatas_dir}[/red]")
        return

    tdata_folders = []
    for item in tdatas_dir.iterdir():
        if not item.is_dir():
            continue

        tdata_path = item / "tdata"
        if tdata_path.exists():
            tdata_folders.append((item.name, tdata_path))
        else:
            key_files = ["key_data", "key_datas"]
            if any((item / kf).exists() for kf in key_files):
                tdata_folders.append((item.name, item))

    if not tdata_folders:
        console.print(f"[yellow]No tdata folders found in {tdatas_dir}[/yellow]")
        console.print("\nExpected structure:")
        console.print("  tdatas/")
        console.print("  ├── account1/")
        console.print("  │   └── tdata/")
        console.print("  └── account2/")
        console.print("      └── tdata/")
        return

    console.print(f"\n[bold]Found {len(tdata_folders)} tdata folders[/bold]\n")

    proxies = []
    if args.proxy:
        proxy = parse_proxy_string(args.proxy)
        proxies = [proxy] * len(tdata_folders)
    else:
        proxies = load_proxies(settings.proxies_file)
        if not proxies:
            console.print("[yellow]Warning: No proxies found. Using direct connection.[/yellow]")
            proxies = [None] * len(tdata_folders)

    db = Database(settings.database)
    await db.connect()

    converter = TDataConverter(settings.api_id, settings.api_hash)

    results = []
    semaphore = asyncio.Semaphore(args.threads)

    async def convert_one(name: str, tdata_path: Path, proxy: dict, idx: int):
        async with semaphore:
            try:
                session_file, json_file, metadata = await converter.convert(
                    str(tdata_path),
                    str(sessions_dir),
                    proxy
                )

                phone = metadata.get("phone", "unknown")
                await db.add_account(
                    phone=phone,
                    session_file=session_file,
                    json_file=json_file,
                    proxy=str(proxy) if proxy else None
                )

                return name, True, phone, None

            except Exception as e:
                return name, False, None, str(e)[:50]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}/{task.total}"),
    ) as progress:
        task = progress.add_task("Converting...", total=len(tdata_folders))

        tasks = []
        for idx, (name, tdata_path) in enumerate(tdata_folders):
            proxy = proxies[idx % len(proxies)] if proxies else None
            tasks.append(convert_one(name, tdata_path, proxy, idx))

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            progress.advance(task)

    await db.close()

    table = Table(title="Conversion Results")
    table.add_column("Folder", style="cyan")
    table.add_column("Status")
    table.add_column("Phone")
    table.add_column("Error")

    success = 0
    failed = 0

    for name, ok, phone, error in sorted(results):
        if ok:
            table.add_row(name, "[green]OK[/green]", phone, "-")
            success += 1
        else:
            table.add_row(name, "[red]FAIL[/red]", "-", error or "Unknown")
            failed += 1

    console.print(table)
    console.print(f"\n[green]Success: {success}[/green] | [red]Failed: {failed}[/red]")


if __name__ == "__main__":
    asyncio.run(main())
