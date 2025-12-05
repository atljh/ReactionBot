from pathlib import Path
from typing import Dict, Any, Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserRestrictedError,
    PhoneNumberBannedError,
)
from .utils import proxy_to_telethon


class BaseThon:
    def __init__(
        self,
        session_file: Optional[Path] = None,
        json_data: Optional[Dict[str, Any]] = None,
        retries: int = 5,
        timeout: int = 10
    ):
        self._session_file = session_file
        self._json_data = json_data or {}
        self._retries = retries
        self._timeout = timeout
        self._client: Optional[TelegramClient] = None
        self._me = None

    @property
    def app_id(self) -> int:
        return int(self._json_data.get("app_id") or self._json_data.get("api_id", 0))

    @property
    def app_hash(self) -> str:
        return self._json_data.get("app_hash") or self._json_data.get("api_hash", "")

    @property
    def device(self) -> str:
        return self._json_data.get("device", "Desktop")

    @property
    def sdk(self) -> str:
        return self._json_data.get("sdk", "Windows 10")

    @property
    def app_version(self) -> str:
        return self._json_data.get("app_version", "4.8.0")

    @property
    def lang_code(self) -> str:
        return self._json_data.get("lang_code", "en")

    @property
    def system_lang_code(self) -> str:
        return self._json_data.get("system_lang_code", "en-US")

    @property
    def string_session(self) -> Optional[str]:
        return self._json_data.get("string_session")

    @property
    def proxy(self) -> Optional[Dict]:
        return self._json_data.get("proxy")

    @property
    def phone(self) -> Optional[str]:
        return self._json_data.get("phone")

    @property
    def client(self) -> TelegramClient:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> TelegramClient:
        if self._session_file and self._session_file.exists():
            session = str(self._session_file)
        elif self.string_session:
            session = StringSession(self.string_session)
        else:
            raise ValueError("No session file or string_session provided")

        return TelegramClient(
            session=session,
            api_id=self.app_id,
            api_hash=self.app_hash,
            device_model=self.device,
            system_version=self.sdk,
            app_version=self.app_version,
            lang_code=self.lang_code,
            system_lang_code=self.system_lang_code,
            connection_retries=self._retries,
            request_retries=self._retries,
            proxy=proxy_to_telethon(self.proxy),
            timeout=self._timeout
        )

    async def connect(self) -> bool:
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def disconnect(self):
        if self._client:
            await self._client.disconnect()

    async def check(self) -> str:
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                return "UNAUTHORIZED"
            self._me = await self.client.get_me()
            return "OK"
        except (UserDeactivatedError, UserDeactivatedBanError, PhoneNumberBannedError):
            return "BANNED"
        except (AuthKeyUnregisteredError, SessionRevokedError):
            return "SESSION_REVOKED"
        except UserRestrictedError:
            return "RESTRICTED"
        except ConnectionError:
            return "CONNECTION_ERROR"
        except Exception as e:
            error_str = str(e).lower()
            if "spam" in error_str or "spambot" in error_str:
                return "SPAM"
            if "flood" in error_str:
                return "FLOOD"
            if "frozen" in error_str:
                return "FROZEN"
            if "restrict" in error_str:
                return "RESTRICTED"
            return f"ERROR:{str(e)[:50]}"

    async def get_me(self):
        if self._me is None:
            self._me = await self.client.get_me()
        return self._me

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
