import argparse
import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table

from config import get_settings
from src.session_loader import SessionLoader
from src.utils import json_read, json_write, parse_proxy_string


console = Console()


def proxy_to_string(proxy: dict) -> str:
    if not proxy:
        return "-"

    proxy_type = proxy.get("proxy_type", "socks5")
    addr = proxy.get("addr", "?")
    port = proxy.get("port", "?")
    username = proxy.get("username")
    password = proxy.get("password")

    if username:
        return f"{proxy_type}://{username}:{password}@{addr}:{port}"
    return f"{proxy_type}://{addr}:{port}"


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


def list_accounts(loader: SessionLoader):
    sessions = list(loader.find_sessions())

    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    table = Table(title="Account Proxies")
    table.add_column("#", style="dim")
    table.add_column("Phone", style="cyan")
    table.add_column("Proxy")
    table.add_column("Status")

    for i, (session_file, json_file, json_data) in enumerate(sessions, 1):
        phone = json_data.get("phone", session_file.stem)
        proxy = json_data.get("proxy")
        proxy_str = proxy_to_string(proxy)

        if proxy:
            status = "[green]OK[/green]"
        else:
            status = "[yellow]No proxy[/yellow]"

        table.add_row(str(i), phone, proxy_str, status)

    console.print(table)

    with_proxy = sum(1 for _, _, d in sessions if d.get("proxy"))
    without_proxy = len(sessions) - with_proxy

    console.print(f"\nTotal: {len(sessions)} | With proxy: {with_proxy} | Without proxy: {without_proxy}")


def set_proxy(loader: SessionLoader, phone: str, proxy_str: str):
    result = loader.get_session(phone)

    if not result:
        console.print(f"[red]Account not found: {phone}[/red]")
        return False

    session_file, json_file, json_data = result

    proxy = parse_proxy_string(proxy_str)
    if not proxy:
        console.print(f"[red]Invalid proxy format: {proxy_str}[/red]")
        console.print("[dim]Expected: socks5://user:pass@ip:port or ip:port:user:pass[/dim]")
        return False

    old_proxy = json_data.get("proxy")
    json_data["proxy"] = proxy

    if json_write(json_file, json_data):
        actual_phone = json_data.get("phone", session_file.stem)
        console.print(f"[green]Proxy updated for {actual_phone}[/green]")
        if old_proxy:
            console.print(f"  Old: {proxy_to_string(old_proxy)}")
        console.print(f"  New: {proxy_to_string(proxy)}")
        return True
    else:
        console.print(f"[red]Failed to save JSON file[/red]")
        return False


def remove_proxy(loader: SessionLoader, phone: str):
    result = loader.get_session(phone)

    if not result:
        console.print(f"[red]Account not found: {phone}[/red]")
        return False

    session_file, json_file, json_data = result

    if "proxy" not in json_data or not json_data["proxy"]:
        actual_phone = json_data.get("phone", session_file.stem)
        console.print(f"[yellow]Account {actual_phone} has no proxy[/yellow]")
        return False

    old_proxy = json_data.pop("proxy")

    if json_write(json_file, json_data):
        actual_phone = json_data.get("phone", session_file.stem)
        console.print(f"[green]Proxy removed from {actual_phone}[/green]")
        console.print(f"  Was: {proxy_to_string(old_proxy)}")
        return True
    else:
        console.print(f"[red]Failed to save JSON file[/red]")
        return False


def assign_proxies(loader: SessionLoader, proxies: list, force: bool = False):
    sessions = list(loader.find_sessions())

    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    if not proxies:
        console.print("[red]No proxies loaded from proxies.txt[/red]")
        return

    if force:
        accounts_to_update = sessions
    else:
        accounts_to_update = [(s, j, d) for s, j, d in sessions if not d.get("proxy")]

    if not accounts_to_update:
        console.print("[yellow]All accounts already have proxies[/yellow]")
        console.print("[dim]Use --replace-all to replace existing proxies[/dim]")
        return

    console.print(f"Assigning proxies to {len(accounts_to_update)} accounts...")

    updated = 0
    for i, (session_file, json_file, json_data) in enumerate(accounts_to_update):
        proxy = proxies[i % len(proxies)]
        phone = json_data.get("phone", session_file.stem)

        old_proxy = json_data.get("proxy")
        json_data["proxy"] = proxy

        if json_write(json_file, json_data):
            if old_proxy:
                console.print(f"  [cyan]{phone}[/cyan]: replaced -> {proxy_to_string(proxy)}")
            else:
                console.print(f"  [cyan]{phone}[/cyan]: assigned -> {proxy_to_string(proxy)}")
            updated += 1
        else:
            console.print(f"  [red]{phone}[/red]: failed to save")

    console.print(f"\n[green]Updated: {updated}/{len(accounts_to_update)}[/green]")

    if len(accounts_to_update) > len(proxies):
        console.print(f"[yellow]Warning: {len(accounts_to_update)} accounts but only {len(proxies)} proxies (proxies were reused)[/yellow]")


def show_proxy(loader: SessionLoader, phone: str):
    result = loader.get_session(phone)

    if not result:
        console.print(f"[red]Account not found: {phone}[/red]")
        return

    session_file, json_file, json_data = result
    actual_phone = json_data.get("phone", session_file.stem)
    proxy = json_data.get("proxy")

    console.print(f"\n[bold]Account: {actual_phone}[/bold]")

    if proxy:
        console.print(f"Proxy: {proxy_to_string(proxy)}")
        console.print(f"\n[dim]Details:[/dim]")
        console.print(f"  Type: {proxy.get('proxy_type', 'socks5')}")
        console.print(f"  Host: {proxy.get('addr')}")
        console.print(f"  Port: {proxy.get('port')}")
        if proxy.get("username"):
            console.print(f"  Username: {proxy.get('username')}")
            console.print(f"  Password: {proxy.get('password')}")
    else:
        console.print("[yellow]No proxy assigned[/yellow]")


def main():
    parser = argparse.ArgumentParser(
        description="TG Comments - Proxy Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python proxy_manager.py --list
  python proxy_manager.py --show 14474652632
  python proxy_manager.py --set 14474652632 socks5://user:pass@1.2.3.4:5555
  python proxy_manager.py --remove 14474652632
  python proxy_manager.py --assign
  python proxy_manager.py --replace-all
        """
    )

    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all accounts and their proxies"
    )
    parser.add_argument(
        "--show", "-s",
        metavar="PHONE",
        help="Show proxy details for specific account"
    )
    parser.add_argument(
        "--set",
        nargs=2,
        metavar=("PHONE", "PROXY"),
        help="Set proxy for specific account"
    )
    parser.add_argument(
        "--remove", "-r",
        metavar="PHONE",
        help="Remove proxy from specific account"
    )
    parser.add_argument(
        "--assign", "-a",
        action="store_true",
        help="Assign proxies from proxies.txt to accounts without proxy"
    )
    parser.add_argument(
        "--replace-all",
        action="store_true",
        help="Replace ALL proxies from proxies.txt (overwrites existing)"
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml"
    )

    args = parser.parse_args()

    if not any([args.list, args.show, args.set, args.remove, args.assign, args.replace_all]):
        parser.print_help()
        return

    try:
        settings = get_settings(args.config)
    except FileNotFoundError:
        console.print("[red]Error: config.yaml not found![/red]")
        sys.exit(1)

    loader = SessionLoader(settings.sessions_dir)
    proxies = load_proxies(settings.proxies_file)

    if args.list:
        list_accounts(loader)

    elif args.show:
        show_proxy(loader, args.show)

    elif args.set:
        phone, proxy_str = args.set
        set_proxy(loader, phone, proxy_str)

    elif args.remove:
        remove_proxy(loader, args.remove)

    elif args.assign:
        if not proxies:
            console.print("[red]No proxies found in proxies.txt[/red]")
            sys.exit(1)
        console.print(f"[dim]Loaded {len(proxies)} proxies from proxies.txt[/dim]\n")
        assign_proxies(loader, proxies, force=False)

    elif args.replace_all:
        if not proxies:
            console.print("[red]No proxies found in proxies.txt[/red]")
            sys.exit(1)
        console.print(f"[dim]Loaded {len(proxies)} proxies from proxies.txt[/dim]\n")
        console.print("[yellow]Warning: This will replace ALL existing proxies![/yellow]")
        confirm = input("Continue? [y/N]: ")
        if confirm.lower() == "y":
            assign_proxies(loader, proxies, force=True)
        else:
            console.print("Cancelled")


if __name__ == "__main__":
    main()
