"""The journal's chat: which platform and chat it is, whose messages in it are his, and which records belong to it.

The journal works in one chat (``channel.platform``, ``channel.chat_id``; by default Telegram and
``telegram.chat_id``), on any platform the companion's gateway runs. Every place that asks "is this the journal's
chat" asks here, never a platform string of its own: the inbound message (``ours``), the turn (``turn_ours``) and a
ledger record (``same_chat``). How the engine *sends* into the chat is the ``ports.Channel`` port
(``Config.channel()``); this module is only about identity, and it fails closed: an unknown or empty platform, or a
sender who is not one of ``channel.user_ids`` when that is set, is never the journal's.

A record made before the journal had a channel names no platform: it was Telegram's (``platform_of``). A record made
for another platform says so (``stamp``), and only then, so Telegram's records stay as they always were.
"""
from __future__ import annotations

from .config import Config

DEFAULT = "telegram"  # the platform of a record that names none: every record before CHANNELS
# How the model is told where his chat is (``label``); a platform not listed is its name, capitalised.
LABELS = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack", "signal": "Signal", "whatsapp": "WhatsApp",
          "whatsapp_cloud": "WhatsApp", "matrix": "Matrix", "mattermost": "Mattermost", "sms": "SMS",
          "email": "email", "bluebubbles": "iMessage", "teams": "Teams", "google_chat": "Google Chat"}


def norm(platform) -> str:
    """A platform as Hermes names it, lower case (an enum's ``value``, or the string)."""
    return str(getattr(platform, "value", platform) or "").strip().lower()


def is_platform(cfg: Config, platform) -> bool:
    """The message or turn came over the journal's platform (an empty or unknown one never does)."""
    p = norm(platform)
    return bool(p) and p == cfg.platform


def owner(cfg: Config, user_id) -> bool:
    """The sender is him: any sender when ``channel.user_ids`` is empty (the chat is his), else one of those ids.
    With ids configured, a sender the adapter could not name is not him."""
    if not cfg.owner_ids:
        return True
    return user_id is not None and str(user_id).strip() in cfg.owner_ids


def ours(cfg: Config, platform, chat_id, user_id=None) -> bool:
    """One message is the journal's: on its platform, in its chat, from him."""
    return (bool(cfg.chat_id) and is_platform(cfg, platform) and str(chat_id) == str(cfg.chat_id)
            and owner(cfg, user_id))


def turn_ours(cfg: Config, platform, chat_id=None, user_id=None) -> bool:
    """A model turn is in the journal's chat. The platform must match; the chat and the sender are checked when the
    adapter knows them (Hermes' ``pre_llm_call`` names only the platform, and the hook reads the rest from the
    session when it can), so a turn in another chat of the same platform is not the journal's."""
    if not is_platform(cfg, platform):
        return False
    if chat_id not in (None, "") and str(chat_id) != str(cfg.chat_id):
        return False
    return not cfg.owner_ids or user_id in (None, "") or owner(cfg, user_id)


def platform_of(record: dict) -> str:
    """The platform a ledger record (a prompt, a send) was made on."""
    return norm(record.get("platform")) or DEFAULT


def same_chat(cfg: Config, record: dict) -> bool:
    """A record was made in the journal's chat as it is configured now (its chat and its platform)."""
    return str(record.get("chat_id")) == str(cfg.chat_id) and platform_of(record) == cfg.platform


def same_chats(a: dict, b: dict) -> bool:
    """Two records were made in the same chat."""
    return str(a.get("chat_id")) == str(b.get("chat_id")) and platform_of(a) == platform_of(b)


def stamp(cfg: Config, record: dict) -> dict:
    """Name the platform on a record made now, unless it is Telegram's (whose records never named it)."""
    if cfg.platform != DEFAULT:
        record["platform"] = cfg.platform
    return record


def label(cfg: Config) -> str:
    """The platform's name as the companion's instructions say it ("Telegram", "Discord")."""
    return LABELS.get(cfg.platform) or cfg.platform.replace("_", " ").title()


def cite(cfg: Config) -> str:
    """The citation key of his chat messages in the context pack: ``telegram`` on Telegram, as always; ``chat``
    anywhere else (``validate`` takes both as his chat)."""
    return "telegram" if cfg.platform == DEFAULT else "chat"
