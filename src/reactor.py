import asyncio
import random
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from telethon.tl.functions.messages import SendReactionRequest, GetMessagesViewsRequest
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import ReactionEmoji, InputPeerChannel
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    ReactionInvalidError,
    UserNotParticipantError,
    MsgIdInvalidError,
    ChatWriteForbiddenError,
)
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .client import BaseThon
from .database import Database
from .parser import LinkParser, ParsedLink
from .utils import log_error, move_account_to_status_folder, get_status_folder


class ReactionResult:
    def __init__(self, phone: str, success: bool, error: Optional[str] = None):
        self.phone = phone
        self.success = success
        self.error = error


class Reactor:
    def __init__(
        self,
        database: Database,
        delay_range: Tuple[int, int] = (5, 15),
        max_reactions_per_day: int = 20,
        sessions_dir: Optional[Path] = None,
        tdatas_dir: Optional[Path] = None,
        console = None
    ):
        self.db = database
        self.delay_range = delay_range
        self.max_reactions_per_day = max_reactions_per_day
        self.sessions_dir = sessions_dir
        self.tdatas_dir = tdatas_dir
        self.console = console
        self.results: List[ReactionResult] = []
        self.moved_accounts: List[Tuple[str, str]] = []

    async def check_subscription(self, client: BaseThon, channel_id: int) -> bool:
        try:
            await client.client(GetParticipantRequest(channel_id, "me"))
            return True
        except UserNotParticipantError:
            return False
        except Exception:
            return False

    async def send_reaction(
        self,
        client: BaseThon,
        channel_id: int,
        message_id: int,
        reaction: str
    ) -> bool:
        try:
            entity = await client.client.get_entity(channel_id)
            await client.client(SendReactionRequest(
                peer=entity,
                msg_id=message_id,
                reaction=[ReactionEmoji(emoticon=reaction)]
            ))
            return True
        except FloodWaitError as e:
            raise e
        except (ReactionInvalidError, MsgIdInvalidError, ChatWriteForbiddenError) as e:
            raise e
        except Exception as e:
            raise e

    async def resolve_channel(self, client: BaseThon, parsed: ParsedLink) -> int:
        if parsed.channel_id != 0:
            return parsed.channel_id

        entity = await client.client.get_entity(parsed.username)
        return entity.id

    async def process_account(
        self,
        account: Dict[str, Any],
        channel_id: int,
        message_id: int,
        reaction: str,
        post_link: str,
        parsed: ParsedLink,
        semaphore: asyncio.Semaphore
    ) -> ReactionResult:
        phone = account["phone"]

        async with semaphore:
            session_file = Path(account["session_file"]) if account.get("session_file") else None
            json_file = Path(account["json_file"]) if account.get("json_file") else None

            if json_file and json_file.exists():
                from .utils import json_read
                json_data = json_read(json_file)
            else:
                json_data = {}

            client = BaseThon(session_file=session_file, json_data=json_data)

            try:
                check_result = await client.check()
                if check_result != "OK":
                    await self.db.set_account_active(account["id"], False)
                    log_error("reaction", phone, check_result)

                    if self.console:
                        self.console.print(f"  [red]✗ {phone}: {check_result}[/red]")

                    if self.sessions_dir and get_status_folder(check_result):
                        moved = move_account_to_status_folder(
                            session_file, json_file, check_result,
                            self.sessions_dir, self.tdatas_dir
                        )
                        if moved:
                            folder = get_status_folder(check_result)
                            self.moved_accounts.append((phone, folder))
                            if self.console:
                                self.console.print(f"    [dim]→ moved to sessions_{folder}/[/dim]")

                    return ReactionResult(phone, False, check_result)

                if self.console:
                    self.console.print(f"  [green]✓ {phone}: OK[/green]")

                if parsed.channel_id == 0:
                    actual_channel_id = await self.resolve_channel(client, parsed)
                else:
                    actual_channel_id = channel_id

                if parsed.is_private:
                    is_subscribed = await self.check_subscription(client, actual_channel_id)
                    if not is_subscribed:
                        log_error("reaction", phone, "NOT_SUBSCRIBED")
                        return ReactionResult(phone, False, "NOT_SUBSCRIBED")
                    await self.db.update_subscription(account["id"], actual_channel_id, True)

                await self.send_reaction(client, actual_channel_id, message_id, reaction)

                await self.db.log_reaction(
                    account["id"],
                    post_link,
                    actual_channel_id,
                    message_id,
                    reaction
                )

                delay = random.uniform(*self.delay_range)
                await asyncio.sleep(delay)

                return ReactionResult(phone, True)

            except FloodWaitError as e:
                error_msg = f"FLOOD:{e.seconds}s"
                log_error("reaction", phone, error_msg)
                if self.console:
                    self.console.print(f"  [yellow]⚠ {phone}: {error_msg}[/yellow]")
                return ReactionResult(phone, False, error_msg)

            except ChannelPrivateError:
                await self.db.update_subscription(account["id"], channel_id, False)
                log_error("reaction", phone, "CHANNEL_PRIVATE")
                if self.console:
                    self.console.print(f"  [yellow]⚠ {phone}: CHANNEL_PRIVATE[/yellow]")
                return ReactionResult(phone, False, "CHANNEL_PRIVATE")

            except ReactionInvalidError:
                log_error("reaction", phone, "REACTION_INVALID")
                if self.console:
                    self.console.print(f"  [yellow]⚠ {phone}: REACTION_INVALID[/yellow]")
                return ReactionResult(phone, False, "REACTION_INVALID")

            except MsgIdInvalidError:
                log_error("reaction", phone, "MSG_ID_INVALID")
                if self.console:
                    self.console.print(f"  [yellow]⚠ {phone}: MSG_ID_INVALID[/yellow]")
                return ReactionResult(phone, False, "MSG_ID_INVALID")

            except Exception as e:
                error_msg = str(e)
                log_error("reaction", phone, error_msg)

                error_lower = error_msg.lower()
                is_ban = any(x in error_lower for x in ["banned", "deactivated", "spam", "restrict"])

                if is_ban:
                    await self.db.set_account_active(account["id"], False)
                    if self.console:
                        self.console.print(f"  [red]✗ {phone}: {error_msg[:40]}[/red]")

                    if self.sessions_dir:
                        status = "BANNED" if "banned" in error_lower else "SPAM" if "spam" in error_lower else "RESTRICTED"
                        moved = move_account_to_status_folder(
                            session_file, json_file, status,
                            self.sessions_dir, self.tdatas_dir
                        )
                        if moved:
                            folder = get_status_folder(status)
                            self.moved_accounts.append((phone, folder))
                            if self.console:
                                self.console.print(f"    [dim]→ moved to sessions_{folder}/[/dim]")
                else:
                    if self.console:
                        self.console.print(f"  [yellow]⚠ {phone}: {error_msg[:40]}[/yellow]")

                return ReactionResult(phone, False, error_msg[:50])

            finally:
                await client.disconnect()

    async def run(
        self,
        post_link: str,
        reaction: str,
        count: int,
        threads: int = 5,
        dry_run: bool = False
    ) -> List[ReactionResult]:
        self.results = []

        parsed = LinkParser.parse(post_link)
        if not parsed:
            raise ValueError(f"Invalid link: {post_link}")

        channel_id = parsed.channel_id
        message_id = parsed.message_id

        accounts = await self.db.get_available_accounts(
            channel_id if channel_id != 0 else 0,
            message_id,
            count,
            self.max_reactions_per_day
        )

        if not accounts:
            return []

        if dry_run:
            for acc in accounts:
                self.results.append(ReactionResult(acc["phone"], True, "DRY_RUN"))
            return self.results

        semaphore = asyncio.Semaphore(threads)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}"),
        ) as progress:
            task = progress.add_task(f"Reactions", total=len(accounts))

            async def process_with_progress(account):
                result = await self.process_account(
                    account, channel_id, message_id, reaction, post_link, parsed, semaphore
                )
                self.results.append(result)
                progress.advance(task)
                return result

            await asyncio.gather(
                *[process_with_progress(acc) for acc in accounts],
                return_exceptions=True
            )

        return self.results

    def get_stats(self) -> Dict[str, Any]:
        success = sum(1 for r in self.results if r.success)
        failed = len(self.results) - success

        errors = {}
        for r in self.results:
            if not r.success and r.error:
                error_type = r.error.split(":")[0] if ":" in r.error else r.error
                errors[error_type] = errors.get(error_type, 0) + 1

        return {
            "total": len(self.results),
            "success": success,
            "failed": failed,
            "errors": errors
        }
