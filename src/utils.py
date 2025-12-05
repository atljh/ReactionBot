import json
import random
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

_error_logger = None
_info_logger = None


def _setup_loggers():
    global _error_logger, _info_logger

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    if _error_logger is None:
        _error_logger = logging.getLogger("tg_reacter_errors")
        _error_logger.setLevel(logging.ERROR)
        handler = logging.FileHandler(logs_dir / "errors.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        _error_logger.addHandler(handler)

    if _info_logger is None:
        _info_logger = logging.getLogger("tg_reacter")
        _info_logger.setLevel(logging.INFO)
        handler = logging.FileHandler(logs_dir / "reacter.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        _info_logger.addHandler(handler)


def get_error_logger() -> logging.Logger:
    _setup_loggers()
    return _error_logger


def get_logger() -> logging.Logger:
    _setup_loggers()
    return _info_logger


def log_error(source: str, message: str, details: str = ""):
    logger = get_error_logger()
    log_line = f"{source} | {message}"
    if details:
        log_line += f" | {details}"
    logger.error(log_line)


def log_info(message: str):
    get_logger().info(message)


def log_reaction(phone: str, channel: str, message_id: int, reaction: str, success: bool):
    status = "OK" if success else "FAIL"
    get_logger().info(f"REACTION | {phone} | {channel}/{message_id} | {reaction} | {status}")


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


STATUS_FOLDERS = {
    "BANNED": "banned",
    "SESSION_REVOKED": "revoked",
    "RESTRICTED": "restricted",
    "SPAM": "spam",
    "FROZEN": "frozen",
    "UNAUTHORIZED": "unauthorized",
    "FLOOD": "flood",
}


def get_status_folder(status: str) -> Optional[str]:
    for key, folder in STATUS_FOLDERS.items():
        if key in status.upper():
            return folder
    if status.startswith("ERROR:"):
        return "errors"
    return None


def move_account_to_status_folder(
    session_file: Path,
    json_file: Path,
    status: str,
    sessions_dir: Path,
    tdatas_dir: Optional[Path] = None
) -> bool:
    folder_name = get_status_folder(status)
    if not folder_name:
        return False

    target_dir = sessions_dir.parent / f"sessions_{folder_name}"
    target_dir.mkdir(exist_ok=True)

    try:
        if session_file and session_file.exists():
            session_file.rename(target_dir / session_file.name)

        journal_file = session_file.with_suffix(".session-journal")
        if journal_file.exists():
            journal_file.rename(target_dir / journal_file.name)

        if json_file and json_file.exists():
            json_file.rename(target_dir / json_file.name)

        if tdatas_dir:
            phone_clean = session_file.stem.replace("+", "")
            tdata_source = None

            for item in tdatas_dir.iterdir():
                if item.is_dir() and item.name.replace("+", "") == phone_clean:
                    tdata_source = item
                    break

            if tdata_source:
                tdata_target_dir = tdatas_dir.parent / f"tdatas_{folder_name}"
                tdata_target_dir.mkdir(exist_ok=True)
                tdata_source.rename(tdata_target_dir / tdata_source.name)

        return True

    except Exception as e:
        log_error("move_account", str(session_file), str(e))
        return False
