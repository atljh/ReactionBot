import re
from typing import Optional, Tuple, NamedTuple


class ParsedLink(NamedTuple):
    channel_id: int
    message_id: int
    is_private: bool
    username: Optional[str] = None


class LinkParser:
    PATTERNS = [
        re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)"),
        re.compile(r"(?:https?://)?t\.me/([a-zA-Z][a-zA-Z0-9_]{3,31})/(\d+)"),
        re.compile(r"tg://privatepost\?channel=(\d+)&post=(\d+)"),
    ]

    @classmethod
    def parse(cls, link: str) -> Optional[ParsedLink]:
        link = link.strip()

        match = cls.PATTERNS[0].search(link)
        if match:
            channel_id = int(match.group(1))
            message_id = int(match.group(2))
            return ParsedLink(
                channel_id=-1000000000000 - channel_id,
                message_id=message_id,
                is_private=True
            )

        match = cls.PATTERNS[1].search(link)
        if match:
            username = match.group(1)
            message_id = int(match.group(2))
            return ParsedLink(
                channel_id=0,
                message_id=message_id,
                is_private=False,
                username=username
            )

        match = cls.PATTERNS[2].search(link)
        if match:
            channel_id = int(match.group(1))
            message_id = int(match.group(2))
            return ParsedLink(
                channel_id=-1000000000000 - channel_id,
                message_id=message_id,
                is_private=True
            )

        return None

    @classmethod
    def is_valid(cls, link: str) -> bool:
        return cls.parse(link) is not None

    @classmethod
    def get_channel_info(cls, link: str) -> Tuple[Optional[int], Optional[str], int]:
        parsed = cls.parse(link)
        if not parsed:
            return None, None, 0

        if parsed.is_private:
            return parsed.channel_id, None, parsed.message_id
        else:
            return None, parsed.username, parsed.message_id
