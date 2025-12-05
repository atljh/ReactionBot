import json
import sqlite3
import asyncio
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
from telethon.sessions import StringSession
from telethon.crypto import AuthKey


DC_SERVERS = {
    1: ("149.154.175.53", 443),
    2: ("149.154.167.41", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.92", 443),
    5: ("91.108.56.130", 443),
}


class TDataConverter:
    def __init__(self, api_id: int, api_hash: str):
        self.api_id = api_id
        self.api_hash = api_hash

    async def convert(
        self,
        tdata_path: str,
        output_dir: str,
        proxy: Optional[Dict] = None
    ) -> Tuple[str, str, Dict]:
        tdata_path = Path(tdata_path)

        if tdata_path.name != "tdata":
            potential = tdata_path / "tdata"
            if potential.exists():
                tdata_path = potential

        tdata_info = await self._extract_tdata_info(tdata_path)

        if not tdata_info.get("accounts"):
            raise ValueError("No accounts found in tdata")

        account_data = tdata_info["accounts"][0]

        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_session = Path(temp_dir) / "temp.session"
            self._create_sqlite_session(account_data, temp_session)
            session_string = self._sqlite_to_string(temp_session)

        from telethon import TelegramClient
        from .utils import proxy_to_telethon

        client = TelegramClient(
            StringSession(session_string),
            self.api_id,
            self.api_hash,
            proxy=proxy_to_telethon(proxy) if proxy else None,
            connection_retries=3,
            timeout=10
        )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise ValueError("Account is not authorized")

            me = await client.get_me()
            phone = me.phone or "unknown"
            phone_clean = phone.replace("+", "") if phone else "unknown"

            metadata = {
                "app_id": self.api_id,
                "app_hash": self.api_hash,
                "phone": phone_clean,
                "telegram_id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "string_session": session_string,
                "device": "Desktop",
                "sdk": "Windows 10",
                "app_version": "4.8.0",
                "lang_code": "en",
                "system_lang_code": "en-US",
                "proxy": proxy
            }

        finally:
            await client.disconnect()

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        session_file = output_path / f"{phone_clean}.session"
        json_file = output_path / f"{phone_clean}.json"

        if session_file.exists() and json_file.exists():
            return str(session_file), str(json_file), {"phone": phone_clean, "already_exists": True}

        self._create_sqlite_session(account_data, session_file)

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        return str(session_file), str(json_file), metadata

    async def _extract_tdata_info(self, tdata_path: Path) -> Dict:
        tdesktop_cmd = self._find_tdesktop_decrypter()

        cmd = [str(tdesktop_cmd), str(tdata_path), "-j"]

        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await asyncio.wait_for(
            result.communicate(), timeout=60.0
        )

        if result.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore").strip()
            if "passcode" in error_msg.lower():
                raise ValueError("tdata is protected with local passcode")
            raise Exception(f"tdesktop-decrypter failed: {error_msg}")

        return json.loads(stdout.decode("utf-8"))

    def _find_tdesktop_decrypter(self) -> Path:
        python_bin = Path(sys.executable).parent

        for name in ["tdesktop-decrypter", "telegram-desktop-decrypter"]:
            cmd = python_bin / name
            if cmd.exists():
                return cmd

            system_cmd = shutil.which(name)
            if system_cmd:
                return Path(system_cmd)

        raise FileNotFoundError(
            "tdesktop-decrypter not found. Install: pip install telegram-desktop-decrypter"
        )

    def _create_sqlite_session(self, account_data: Dict, output_path: Path):
        dc_id = account_data["main_dc_id"]
        auth_key_hex = account_data["dc_auth_keys"][str(dc_id)]
        auth_key = bytes.fromhex(auth_key_hex)
        server_address, port = DC_SERVERS.get(dc_id, (f"dc{dc_id}.telegram.org", 443))

        if output_path.exists():
            output_path.unlink()
        journal = output_path.with_suffix(".session-journal")
        if journal.exists():
            journal.unlink()

        conn = sqlite3.connect(str(output_path))
        c = conn.cursor()

        c.execute("DROP TABLE IF EXISTS sessions")
        c.execute("DROP TABLE IF EXISTS entities")
        c.execute("DROP TABLE IF EXISTS sent_files")
        c.execute("DROP TABLE IF EXISTS update_state")
        c.execute("DROP TABLE IF EXISTS version")

        c.execute("""
            CREATE TABLE sessions (
                dc_id INTEGER PRIMARY KEY,
                server_address TEXT,
                port INTEGER,
                auth_key BLOB,
                takeout_id INTEGER
            )
        """)

        c.execute("""
            CREATE TABLE entities (
                id INTEGER PRIMARY KEY,
                hash INTEGER NOT NULL,
                username TEXT,
                phone INTEGER,
                name TEXT,
                date INTEGER
            )
        """)

        c.execute("""
            CREATE TABLE sent_files (
                md5_digest BLOB,
                file_size INTEGER,
                type INTEGER,
                id INTEGER,
                hash INTEGER,
                PRIMARY KEY(md5_digest, file_size, type)
            )
        """)

        c.execute("""
            CREATE TABLE update_state (
                id INTEGER PRIMARY KEY,
                pts INTEGER,
                qts INTEGER,
                date INTEGER,
                seq INTEGER
            )
        """)

        c.execute("""
            CREATE TABLE version (version INTEGER PRIMARY KEY)
        """)

        c.execute("INSERT INTO version VALUES (8)")
        c.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?, ?, ?)",
            (dc_id, server_address, port, auth_key, None)
        )

        conn.commit()
        conn.close()

    def _sqlite_to_string(self, session_path: Path) -> str:
        conn = sqlite3.connect(str(session_path))
        c = conn.cursor()

        c.execute("SELECT dc_id, server_address, port, auth_key FROM sessions")
        row = c.fetchone()
        conn.close()

        if not row:
            raise ValueError("No session data found")

        dc_id, server_address, port, auth_key = row

        ss = StringSession()
        ss._dc_id = dc_id
        ss._server_address = server_address
        ss._port = port
        ss._auth_key = AuthKey(data=auth_key)
        ss._takeout_id = None

        return ss.save()
