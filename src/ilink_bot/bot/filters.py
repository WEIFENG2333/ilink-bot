"""Message filter combinators for routing inbound messages.

Filters can be combined with ``&`` (AND), ``|`` (OR), and ``~`` (NOT)::

    from ilink_bot import filters

    @bot.on_message(filters.text & filters.contains("help"))
    async def help_handler(msg):
        await msg.reply("Here is some help...")
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ilink_bot.models.messages import Message, MessageItemType

if TYPE_CHECKING:
    from collections.abc import Callable


class Filter:
    """Base class for composable message filters."""

    def __init__(self, func: Callable[[Message], bool], name: str = "") -> None:
        self._func = func
        self._name = name or func.__name__

    def __call__(self, msg: Message) -> bool:
        return self._func(msg)

    def __and__(self, other: Filter) -> Filter:
        return Filter(lambda m: self(m) and other(m), f"({self._name} & {other._name})")

    def __or__(self, other: Filter) -> Filter:
        return Filter(lambda m: self(m) or other(m), f"({self._name} | {other._name})")

    def __invert__(self) -> Filter:
        return Filter(lambda m: not self(m), f"~{self._name}")

    def __repr__(self) -> str:
        return f"Filter({self._name})"


# ---------------------------------------------------------------------------
# Built-in filter constructors
# ---------------------------------------------------------------------------

def _is_text(msg: Message) -> bool:
    return msg.type == MessageItemType.TEXT


def _is_image(msg: Message) -> bool:
    return msg.type == MessageItemType.IMAGE


def _is_voice(msg: Message) -> bool:
    return msg.type == MessageItemType.VOICE


def _is_file(msg: Message) -> bool:
    return msg.type == MessageItemType.FILE


def _is_video(msg: Message) -> bool:
    return msg.type == MessageItemType.VIDEO


def _all_messages(_msg: Message) -> bool:
    return True


def contains(keyword: str) -> Filter:
    """Match messages whose text contains *keyword* (case-sensitive)."""
    def _check(msg: Message) -> bool:
        return keyword in (msg.text or "")
    return Filter(_check, f"contains({keyword!r})")


def regex(pattern: str, flags: int = 0) -> Filter:
    """Match messages whose text matches a regular expression."""
    compiled = re.compile(pattern, flags)
    def _check(msg: Message) -> bool:
        return bool(compiled.search(msg.text or ""))
    return Filter(_check, f"regex({pattern!r})")


def command(cmd: str) -> Filter:
    """Match messages that start with ``/cmd`` (slash-command style)."""
    prefix = f"/{cmd}"
    def _check(msg: Message) -> bool:
        text = msg.text or ""
        return text == prefix or text.startswith(f"{prefix} ")
    return Filter(_check, f"command({cmd!r})")


def from_user(user_id: str) -> Filter:
    """Match messages from a specific user."""
    def _check(msg: Message) -> bool:
        return msg.from_user == user_id
    return Filter(_check, f"from_user({user_id!r})")


# ---------------------------------------------------------------------------
# Public namespace — ``from ilink_bot import filters``
# ---------------------------------------------------------------------------

class _Filters:
    """Namespace object that holds all built-in filters as attributes."""

    text = Filter(_is_text, "text")
    image = Filter(_is_image, "image")
    voice = Filter(_is_voice, "voice")
    file = Filter(_is_file, "file")
    video = Filter(_is_video, "video")
    all = Filter(_all_messages, "all")

    # Factories (return new Filter instances)
    contains = staticmethod(contains)
    regex = staticmethod(regex)
    command = staticmethod(command)
    from_user = staticmethod(from_user)


filters = _Filters()
