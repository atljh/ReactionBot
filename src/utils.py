import json
import random
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime


def json_read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def json_write(path: Path, data: Dict[str, Any]) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def parse_proxy_string(proxy_str: str) -> Optional[Dict[str, Any]]:
    if not proxy_str or not proxy_str.strip():
        return None

    proxy_str = proxy_str.strip()

    if "://" in proxy_str:
        protocol, rest = proxy_str.split("://", 1)

        if "@" in rest:
            auth, host_port = rest.rsplit("@", 1)
            if ":" in auth:
                username, password = auth.split(":", 1)
            else:
                username, password = auth, ""
        else:
            host_port = rest
            username, password = None, None

        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 1080

    else:
        parts = proxy_str.split(":")

        if len(parts) == 4:
            host, port_str, username, password = parts
            port = int(port_str)
        elif len(parts) == 3:
            host, port_str, username = parts
            port = int(port_str)
            password = ""
        elif len(parts) == 2:
            host, port_str = parts
            port = int(port_str)
            username, password = None, None
        else:
            return None

        protocol = "socks5"

    result = {
        "proxy_type": protocol,
        "addr": host,
        "port": port,
    }

    if username:
        result["username"] = username
        result["password"] = password or ""

    return result


def proxy_to_telethon(proxy: Dict[str, Any]) -> Optional[Tuple]:
    if not proxy:
        return None

    import socks

    proxy_type_map = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }

    proxy_type = proxy_type_map.get(proxy.get("proxy_type", "socks5"), socks.SOCKS5)

    return (
        proxy_type,
        proxy["addr"],
        proxy["port"],
        True,
        proxy.get("username"),
        proxy.get("password"),
    )


def random_delay(delay_range: Tuple[int, int]) -> float:
    return random.uniform(delay_range[0], delay_range[1])


def format_phone(phone: str) -> str:
    phone = "".join(c for c in phone if c.isdigit() or c == "+")
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
