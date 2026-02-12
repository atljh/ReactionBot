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
    UsernameNotOccupiedError,
    UsernameInvalidError,
    FloodWaitError,
)
from .utils import proxy_to_telethon


class BaseThon:
    def __init__(
        self,
        session_file: Optional[Path] = None,
        json_data: Optional[Dict[str, Any]] = None,
        retries: int = 2,
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
            timeout=self._timeout,
            flood_sleep_threshold=0,
            receive_updates=False,
        )

    async def connect(self) -> bool:
        if not self.app_id or not self.app_hash:
            raise ValueError(f"Missing API credentials: app_id={self.app_id}, app_hash={bool(self.app_hash)}")

        await self.client.connect()
        self._me = await self.client.get_me()
        return self._me is not None

    async def disconnect(self):
        if self._client:
            await self._client.disconnect()

    async def check(self, test_entity: str = "telegram") -> str:
        """
        Check account status.

        Uses get_me() instead of is_user_authorized() because
        is_user_authorized() swallows FloodWaitError as RPCError
        and returns False, causing live flooded accounts to be
        mistakenly marked as UNAUTHORIZED.
        """
        try:
            if not self.app_id or not self.app_hash:
                return f"ERROR:missing api_id={self.app_id}"

            await self.client.connect()

            # get_me() properly raises FloodWaitError, AuthKeyUnregistered, etc.
            # unlike is_user_authorized() which catches all RPCError → False
            self._me = await self.client.get_me()

            if self._me is None:
                return "UNAUTHORIZED"

            # Test if account can search for other users/channels
            if test_entity:
                try:
                    await self.client.get_entity(test_entity)
                except (UsernameNotOccupiedError, UsernameInvalidError):
                    pass
                except FloodWaitError:
                    raise  # bubble up to outer FloodWaitError handler
                except Exception as e:
                    error_str = str(e).lower()
                    if "no user has" in error_str:
                        return "SEARCH_RESTRICTED"
                    raise

            return "OK"
        except FloodWaitError as e:
            return f"FLOOD:{e.seconds}s"
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
            if "no user has" in error_str:
                return "SEARCH_RESTRICTED"
            if "not authorized" in error_str or "unauthorized" in error_str:
                return "UNAUTHORIZED"
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
