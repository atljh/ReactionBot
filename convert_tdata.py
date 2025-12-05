import argparse
import asyncio
from pathlib import Path
from rich.console import Console

from config import get_settings
from src.database import Database
from src.tdata_converter import TDataConverter
from src.utils import parse_proxy_string


console = Console()


async def main():
    parser = argparse.ArgumentParser(description="Convert tdata to session+json")

    parser.add_argument(
        "--tdata", "-t",
        required=True,
        help="Path to tdata folder"
    )
    parser.add_argument(
        "--proxy", "-p",
        help="Proxy (socks5://user:pass@ip:port)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output directory (default: sessions/)"
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

    tdata_path = Path(args.tdata)
    if not tdata_path.exists():
        console.print(f"[red]Error: tdata not found: {tdata_path}[/red]")
        return

    output_dir = args.output or str(settings.sessions_dir)
    proxy_dict = parse_proxy_string(args.proxy) if args.proxy else None

    console.print(f"\n[bold]Converting tdata[/bold]")
    console.print(f"Source: {tdata_path}")
    console.print(f"Output: {output_dir}")
    if proxy_dict:
        console.print(f"Proxy: {proxy_dict['addr']}:{proxy_dict['port']}")

    converter = TDataConverter(settings.api_id, settings.api_hash)

    try:
        with console.status("[bold green]Converting..."):
            session_file, json_file, metadata = await converter.convert(
                str(tdata_path),
                output_dir,
                proxy_dict
            )

        phone = metadata.get("phone", "unknown")
        username = metadata.get("username", "-")

        console.print(f"\n[green]Conversion successful![/green]")
        console.print(f"Phone: {phone}")
        console.print(f"Username: @{username}")
        console.print(f"Session: {session_file}")
        console.print(f"JSON: {json_file}")

        db = Database(settings.database)
        await db.connect()

        await db.add_account(
            phone=phone,
            session_file=session_file,
            json_file=json_file,
            proxy=str(proxy_dict) if proxy_dict else None
        )

        await db.close()

        console.print(f"\n[green]Account added to database![/green]")

    except FileNotFoundError as e:
        console.print(f"\n[red]Error: {e}[/red]")
        console.print("\nInstall telegram-desktop-decrypter:")
        console.print("  pip install telegram-desktop-decrypter")

    except ValueError as e:
        console.print(f"\n[red]Error: {e}[/red]")

    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")


if __name__ == "__main__":
    asyncio.run(main())
