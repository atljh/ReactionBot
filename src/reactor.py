import asyncio
import random
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from telethon.tl.functions.messages import SendReactionRequest, GetMessagesViewsRequest, ImportChatInviteRequest
from telethon.tl.functions.channels import GetParticipantRequest, JoinChannelRequest
from telethon.tl.types import ReactionEmoji, InputPeerChannel
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    ReactionInvalidError,
    UserNotParticipantError,
    MsgIdInvalidError,
    MessageIdInvalidError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    InviteHashInvalidError,
    InviteHashExpiredError,
    ChannelsTooMuchError,
    UsersTooMuchError,
    InviteRequestSentError,
)
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .client import BaseThon
from .database import Database
from .parser import LinkParser, ParsedLink
from .utils import log_error, log_info, log_reaction, move_account_to_status_folder, get_status_folder


class ReactionResult:
    def __init__(self, phone: str, success: bool, error: Optional[str] = None,
                 should_move: bool = False, session_file: Path = None, json_file: Path = None):
        self.phone = phone
        self.success = success
        self.error = error
        self.should_move = should_move
        self.session_file = session_file
        self.json_file = json_file


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

    async def check_subscription(self, client: BaseThon, channel: Any) -> Tuple[bool, Optional[str]]:
        """
        Check if account is subscribed to channel.
        channel can be entity, channel_id, or username.
        Returns (is_subscribed, error_if_any).
        """
        try:
            # If channel is an entity with access_hash, use it directly
            # Otherwise Telethon will try to resolve it
            await client.client(GetParticipantRequest(channel, "me"))
            return True, None
        except UserNotParticipantError:
            return False, None
        except ChannelPrivateError:
            return False, "CHANNEL_PRIVATE"
        except Exception as e:
            return False, str(e)[:50]

    async def join_channel(
        self,
        client: BaseThon,
        channel: Any,  # Can be entity, channel_id, or will use username
        invite_hash: str = None,
        phone: Optional[str] = None,
        is_private: bool = False,
        username: Optional[str] = None,
    ) -> Tuple[str, Optional[Any]]:
        """
        Join a channel and return (status, entity).
        Returns entity on success for subsequent operations.
        """
        phone_label = phone or getattr(client, "phone", "UNKNOWN")

        # For private /c/ links (or tg://privatepost) we cannot resolve the
        # channel by ID alone for accounts that are not already members.
        # In that case an explicit invite hash is required, otherwise
        # Telethon raises "Could not find the input entity for PeerChannel".
        if is_private and not invite_hash:
            status = "CHANNEL_PRIVATE"
            msg = f"{status}: invite link is required to join a private channel"
            log_error("join", phone_label, msg)
            if self.console:
                self.console.print(
                    f"  [yellow]⚠ {phone_label}: CHANNEL_PRIVATE – private link without invite; use --invite[/yellow]"
                )
            return status, None

        try:
            if invite_hash:
                await client.client(ImportChatInviteRequest(invite_hash))
                # After joining via invite, get entity
                if hasattr(channel, 'id'):
                    entity = channel
                else:
                    entity = await client.client.get_entity(channel)
            elif hasattr(channel, 'id'):
                # Already have entity with access_hash - use it directly
                entity = channel
                await client.client(JoinChannelRequest(entity))
            elif username:
                # For public channels, use username directly - this properly resolves access_hash
                entity = await client.client.get_entity(username)
                await client.client(JoinChannelRequest(entity))
            else:
                entity = await client.client.get_entity(channel)
                await client.client(JoinChannelRequest(entity))
            return "OK", entity
        except FloodWaitError:
            raise
        except InviteHashInvalidError:
            return "INVITE_INVALID", None
        except InviteHashExpiredError:
            return "INVITE_EXPIRED", None
        except ChannelsTooMuchError:
            return "CHANNELS_TOO_MUCH", None
        except UsersTooMuchError:
            return "USERS_TOO_MUCH", None
        except InviteRequestSentError:
            return "INVITE_REQUEST_SENT", None
        except UserBannedInChannelError:
            return "BANNED_IN_CHANNEL", None
        except ChannelPrivateError as e:
            log_error("join", phone_label, f"CHANNEL_PRIVATE: {str(e)}")
            return "CHANNEL_PRIVATE", None
        except Exception as e:
            error_msg = str(e)
            log_error("join", phone_label, f"JOIN_ERROR: {error_msg}")
            # Check for specific error patterns
            error_lower = error_msg.lower()
            if "private" in error_lower or "not accessible" in error_lower:
                return "CHANNEL_PRIVATE", None
            if "banned" in error_lower:
                return "BANNED_IN_CHANNEL", None
            return "JOIN_ERROR", None

    async def send_reaction(
        self,
        client: BaseThon,
        channel: Any,  # Can be entity or channel_id
        message_id: int,
        reaction: str
    ) -> bool:
        try:
            # If channel is already an entity, use it directly
            # Otherwise resolve by ID
            if hasattr(channel, 'id'):
                entity = channel
            else:
                entity = await client.client.get_entity(channel)
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

    async def resolve_channel(self, client: BaseThon, parsed: ParsedLink) -> Tuple[int, Optional[Any]]:
        """
        Resolve channel and return (channel_id, entity).
        For public channels, returns entity with access_hash.
        """
        if parsed.channel_id != 0:
            # For private channels, we only have ID
            return parsed.channel_id, None

        # For public channels, resolve by username to get proper access_hash
        entity = await client.client.get_entity(parsed.username)
        return entity.id, entity

    async def check_account_search_ability(self, client: BaseThon) -> bool:
        """
        Check if account can search for users/channels by testing with @telegram.
        Returns True if account can search, False if SEARCH_RESTRICTED.
        """
        try:
            await client.client.get_entity("telegram")
            return True
        except Exception as e:
            error_str = str(e).lower()
            if "no user has" in error_str:
                return False
            # Other errors (flood, connection) - assume account is OK
            return True

    async def process_account(
        self,
        account: Dict[str, Any],
        channel_id: int,
        message_id: int,
        reaction: str,
        post_link: str,
        parsed: ParsedLink,
        semaphore: asyncio.Semaphore,
        invite_hash: str = None,
        progress = None
    ) -> ReactionResult:
        phone = account["phone"]
        console = progress.console if progress else self.console

        async with semaphore:
            session_file = Path(account["session_file"]) if account.get("session_file") else None
            json_file = Path(account["json_file"]) if account.get("json_file") else None

            if json_file and json_file.exists():
                from .utils import json_read
                json_data = json_read(json_file)
            else:
                json_data = None

            if not json_data:
                log_error("reaction", phone, f"JSON file missing or corrupt: {json_file}")
                return ReactionResult(phone, False, "NO_JSON_DATA")

            client = BaseThon(session_file=session_file, json_data=json_data)

            actual_channel_id = channel_id
            try:
                authorized = await client.connect()
                if not authorized:
                    log_error("reaction", phone, "NOT_AUTHORIZED at connect")
                    return ReactionResult(phone, False, "UNAUTHORIZED")

                # Resolve channel - for public channels we get entity with access_hash
                channel_entity = None
                if channel_id != 0:
                    actual_channel_id = channel_id
                else:
                    try:
                        actual_channel_id, channel_entity = await self.resolve_channel(client, parsed)
                    except ChannelPrivateError:
                        log_error("resolve", phone, "CHANNEL_PRIVATE at resolve_channel")
                        if console:
                            console.print(f"  [red]✗ {phone}: CHANNEL_PRIVATE – cannot resolve channel[/red]")
                        return ReactionResult(phone, False, "CHANNEL_PRIVATE")
                    except Exception as e:
                        log_error("resolve", phone, f"Error resolving channel: {str(e)}")
                        if console:
                            console.print(f"  [red]✗ {phone}: Cannot resolve channel – {str(e)[:40]}[/red]")
                        return ReactionResult(phone, False, f"RESOLVE_ERROR:{str(e)[:30]}")

                # Use entity if available, otherwise use username for public channels
                check_target = channel_entity or parsed.username or actual_channel_id
                is_subscribed, sub_error = await self.check_subscription(client, check_target)

                # If check_subscription returned CHANNEL_PRIVATE, try joining anyway
                if sub_error == "CHANNEL_PRIVATE":
                    log_error("check_sub", phone, f"CHANNEL_PRIVATE at check_subscription, will try to join")
                    is_subscribed = False
                elif sub_error:
                    log_error("check_sub", phone, f"Error: {sub_error}")

                if not is_subscribed:
                    join_status, joined_entity = await self.join_channel(
                        client,
                        channel_entity or actual_channel_id,  # Prefer entity if available
                        invite_hash,
                        phone,
                        parsed.is_private,
                        username=parsed.username,  # Pass username for public channels
                    )
                    if join_status != "OK":
                        log_error("reaction", phone, join_status)
                        if console:
                            human = {
                                "INVITE_INVALID": "invite link is invalid",
                                "INVITE_EXPIRED": "invite link has expired",
                                "BANNED_IN_CHANNEL": "account is banned in this channel",
                                "CHANNELS_TOO_MUCH": "account joined too many channels",
                                "USERS_TOO_MUCH": "channel reached member limit",
                                "CHANNEL_PRIVATE": "cannot join – account restricted or banned by channel",
                                "INVITE_REQUEST_SENT": "join request sent, waiting for approval",
                            }.get(join_status, "failed to join the channel")
                            console.print(
                                f"  [red]✗ {phone}: {human}[/red]"
                            )
                        return ReactionResult(phone, False, join_status)
                    # Use entity from join_channel for subsequent operations
                    if joined_entity:
                        channel_entity = joined_entity
                    log_info(f"JOIN | {phone} | channel={actual_channel_id}")
                    await self.db.update_subscription(account["id"], actual_channel_id, True)

                    # Delay after join before reacting — instant reaction after join is suspicious
                    join_delay = random.uniform(3, 8)
                    await asyncio.sleep(join_delay)

                # Use entity if available, otherwise fall back to ID
                await self.send_reaction(client, channel_entity or actual_channel_id, message_id, reaction)

                await self.db.log_reaction(
                    account["id"],
                    post_link,
                    actual_channel_id,
                    message_id,
                    reaction
                )

                log_reaction(phone, str(actual_channel_id), message_id, reaction, True)

                delay = random.uniform(*self.delay_range)
                await asyncio.sleep(delay)

                return ReactionResult(phone, True)

            except FloodWaitError as e:
                error_msg = f"FLOOD:{e.seconds}s"
                log_error("reaction", phone, error_msg)
                if console:
                    console.print(f"  [yellow]⚠ {phone}: {error_msg}[/yellow]")
                return ReactionResult(phone, False, error_msg)

            except ChannelPrivateError:
                # Channel is not accessible for this account (not subscribed or revoked access)
                await self.db.update_subscription(account["id"], actual_channel_id, False)
                log_error("reaction", phone, "CHANNEL_PRIVATE")
                if console:
                    console.print(f"  [yellow]⚠ {phone}: CHANNEL_PRIVATE[/yellow]")
                return ReactionResult(phone, False, "CHANNEL_PRIVATE")

            except UserBannedInChannelError:
                # Account is banned specifically in this channel/group, but in general it's alive
                log_error("reaction", phone, "BANNED_IN_CHANNEL")
                if console:
                    console.print(f"  [yellow]⚠ {phone}: BANNED_IN_CHANNEL[/yellow]")
                return ReactionResult(phone, False, "BANNED_IN_CHANNEL")

            except ChatWriteForbiddenError:
                # No rights to write in this chat
                log_error("reaction", phone, "CHAT_WRITE_FORBIDDEN")
                if console:
                    console.print(f"  [yellow]⚠ {phone}: CHAT_WRITE_FORBIDDEN[/yellow]")
                return ReactionResult(phone, False, "CHAT_WRITE_FORBIDDEN")

            except ReactionInvalidError:
                log_error("reaction", phone, "REACTION_INVALID")
                if console:
                    console.print(f"  [yellow]⚠ {phone}: REACTION_INVALID[/yellow]")
                return ReactionResult(phone, False, "REACTION_INVALID")

            except (MsgIdInvalidError, MessageIdInvalidError):
                log_error("reaction", phone, "MSG_ID_INVALID")
                if console:
                    console.print(f"  [yellow]⚠ {phone}: MSG_ID_INVALID[/yellow]")
                return ReactionResult(phone, False, "MSG_ID_INVALID")

            except Exception as e:
                error_msg = str(e)
                error_lower = error_msg.lower()

                if "message" in error_lower and "invalid" in error_lower:
                    log_error("reaction", phone, "MSG_ID_INVALID")
                    if console:
                        console.print(f"  [yellow]⚠ {phone}: MSG_ID_INVALID[/yellow]")
                    return ReactionResult(phone, False, "MSG_ID_INVALID")

                # "No user has X as username" error - check if it's account problem or channel problem
                if "no user has" in error_lower and "username" in error_lower:
                    # Test if account can search at all by checking @telegram
                    can_search = await self.check_account_search_ability(client)

                    if not can_search:
                        # Account is search-restricted (frozen/limited)
                        log_error("reaction", phone, "SEARCH_RESTRICTED")
                        await self.db.set_account_active(account["id"], False)
                        if console:
                            console.print(f"  [red]✗ {phone}: SEARCH_RESTRICTED – account cannot search users/channels[/red]")

                        # Move session immediately
                        if self.sessions_dir and session_file:
                            moved = move_account_to_status_folder(
                                session_file, json_file, "SEARCH_RESTRICTED",
                                self.sessions_dir, self.tdatas_dir
                            )
                            if moved:
                                self.moved_accounts.append((phone, "search_restricted"))

                        return ReactionResult(phone, False, "SEARCH_RESTRICTED")
                    else:
                        # Account is OK, channel/username doesn't exist
                        log_error("reaction", phone, f"CHANNEL_NOT_FOUND: {error_msg[:50]}")
                        if console:
                            console.print(f"  [yellow]⚠ {phone}: CHANNEL_NOT_FOUND – channel/username does not exist[/yellow]")
                        return ReactionResult(phone, False, "CHANNEL_NOT_FOUND")

                log_error("reaction", phone, error_msg)

                # Temporary limits - don't move, just warn
                if "spam" in error_lower or "flood" in error_lower:
                    if console:
                        console.print(f"  [yellow]⚠ {phone}: Temporary limit – {error_msg[:40]}[/yellow]")
                    return ReactionResult(phone, False, error_msg[:50])

                # Permanent bans - move account
                is_ban = any(x in error_lower for x in ["banned", "deactivated", "restrict"])

                if is_ban:
                    await self.db.set_account_active(account["id"], False)
                    if console:
                        console.print(f"  [red]✗ {phone}: {error_msg[:40]}[/red]")

                    # Move session immediately
                    if self.sessions_dir and session_file:
                        if "banned" in error_lower or "deactivated" in error_lower:
                            status = "banned"
                        else:
                            status = "restricted"
                        moved = move_account_to_status_folder(
                            session_file, json_file, status.upper(),
                            self.sessions_dir, self.tdatas_dir
                        )
                        if moved:
                            self.moved_accounts.append((phone, status))

                    return ReactionResult(phone, False, error_msg[:50])
                else:
                    if console:
                        console.print(f"  [yellow]⚠ {phone}: {error_msg[:40]}[/yellow]")

                return ReactionResult(phone, False, error_msg[:50])

            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def check_accounts(self, accounts: List[Dict[str, Any]], threads: int) -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(1, threads))

        async def check_one(account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            phone = account["phone"]
            session_file = Path(account["session_file"]) if account.get("session_file") else None
            json_file = Path(account["json_file"]) if account.get("json_file") else None

            if json_file and json_file.exists():
                from .utils import json_read
                json_data = json_read(json_file)
            else:
                json_data = None

            if not json_data:
                log_error("check", phone, f"JSON file missing or corrupt: {json_file}")
                return None

            client = BaseThon(session_file=session_file, json_data=json_data)
            should_move = False
            move_status = None

            try:
                async with semaphore:
                    check_result = await client.check(test_entity=None)

                if check_result != "OK":
                    is_temporary = check_result.startswith("FLOOD") or check_result == "CONNECTION_ERROR"

                    if not is_temporary:
                        await self.db.set_account_active(account["id"], False)

                    log_error("check", phone, check_result)

                    if not is_temporary and self.sessions_dir and get_status_folder(check_result):
                        should_move = True
                        move_status = check_result

                    return None
                else:
                    return account

            except Exception as e:
                log_error("check", phone, str(e))
                return None

            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

                if should_move and move_status:
                    moved = move_account_to_status_folder(
                        session_file, json_file, move_status,
                        self.sessions_dir, self.tdatas_dir
                    )
                    if moved:
                        folder = get_status_folder(move_status)
                        self.moved_accounts.append((phone, folder))

        results = await asyncio.gather(*(check_one(acc) for acc in accounts))
        return [acc for acc in results if acc is not None]

    @staticmethod
    def parse_invite_hash(invite_link: str) -> Optional[str]:
        if not invite_link:
            return None
        invite_link = invite_link.strip()
        if "/+" in invite_link:
            return invite_link.split("/+")[-1]
        if "joinchat/" in invite_link:
            return invite_link.split("joinchat/")[-1]
        return None

    async def run(
        self,
        post_link: str,
        reaction: str,
        count: int,
        threads: int = 10,
        dry_run: bool = False,
        invite_link: str = None
    ) -> List[ReactionResult]:
        self.results = []

        parsed = LinkParser.parse(post_link)
        if not parsed:
            raise ValueError(f"Invalid link: {post_link}")

        invite_hash = self.parse_invite_hash(invite_link)

        channel_id = parsed.channel_id
        message_id = parsed.message_id

        accounts = await self.db.get_available_accounts(
            channel_id if channel_id != 0 else 0,
            message_id,
            count,
            self.max_reactions_per_day
        )

        if not accounts:
            if self.console:
                self.console.print("[yellow]No available accounts[/yellow]")
            return []

        if self.console:
            self.console.print(f"[bold]Checking {len(accounts)} accounts...[/bold]")

        valid_accounts = await self.check_accounts(accounts, threads)
        invalid_count = len(accounts) - len(valid_accounts)

        if self.console:
            if invalid_count > 0:
                self.console.print(f"[green]{len(valid_accounts)} OK[/green] | [red]{invalid_count} invalid[/red]")
            else:
                self.console.print(f"[green]{len(valid_accounts)} OK[/green]")

        if not valid_accounts:
            if self.console:
                self.console.print("[yellow]No valid accounts[/yellow]")
            return []

        if self.console:
            self.console.print(f"\n[bold]Sending reactions...[/bold]")

        if dry_run:
            for acc in valid_accounts:
                self.results.append(ReactionResult(acc["phone"], True, "DRY_RUN"))
            return self.results

        semaphore = asyncio.Semaphore(threads)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[cyan]{task.completed}/{task.total}"),
            console=self.console,
        ) as progress:
            task = progress.add_task(f"Reactions", total=len(valid_accounts))

            async def process_with_progress(account):
                result = await self.process_account(
                    account, channel_id, message_id, reaction, post_link, parsed, semaphore, invite_hash,
                    progress=progress
                )
                self.results.append(result)
                progress.advance(task)
                return result

            await asyncio.gather(
                *[process_with_progress(acc) for acc in valid_accounts],
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
