"""The ports the engine reaches the companion's world through: the chat transcript, the companion herself, and the
channel it sends into.

The engine (capture, the privacy pass, the conversation writer, the sweep, the outage catch-up, the context pack,
the mirror) is agent-neutral; how an agent stores its chat, how it is asked for text and how a message reaches the
chat's platform are not. Those couplings live behind these ports, and an adapter implements them:
``journal.hermes.transcript`` reads Hermes' sessions database into ``Row``s, ``journal.hermes.companion`` runs a
Hermes one-shot chat, ``journal.telegram.TelegramChannel`` sends through the Telegram Bot API and
``journal.hermes.channel.HermesChannel`` through Hermes' own senders for any other platform. ``Config.transcript()``,
``Config.companion()`` and ``Config.channel()`` choose the implementation; nothing else in the engine opens the
chat, names Hermes or calls a platform.

**Row.** One stored chat message of either role, read once by the adapter. ``kind`` is where the agent's row
semantics are decided (envelopes, notices, compaction frames, failed turns, audio placeholders), so the readers
keep their journal logic and never parse the agent's storage format:

* his rows (``role == "user"``): ``his`` (his words), ``handled`` (it carries the gateway hook's journal note: saved
  already), ``mirror`` (the journal's own send, mirrored into the chat), ``system`` (a platform row that is not his
  words: an attachment placeholder, a system note, a compaction frame), ``boundary`` (a scheduled delivery into the
  chat: it ends a conversation thread), ``empty``. The kind reads the stored text only; a platform notice (a row the
  platform typed as a notification rather than a message) keeps its text's kind and says so in ``meta["notice"]``.
* her rows (``role == "assistant"``): ``hers`` (her reply), ``tool_step`` (a step that called tools: narration, not
  the reply), ``failed_turn`` (the platform closing a turn that failed), ``turn_end`` (a turn closed without a
  reply: silent, interrupted), ``frame`` (a typed or compaction row that is not her reply), ``empty``.

``text`` is his words alone (envelopes, reply quote and notes removed; for ``system`` and ``handled`` rows the whole
text as read) or her text, stripped. ``stored`` is the text exactly as stored: her reply verbatim, and for his rows
the place the journal looks for its *own* marks (the off-the-record marker, the hook's note path), never for the
platform's envelopes. ``copy`` is the id of the row this one repeats (a compaction stored it again), else None.

``meta`` carries the adapter's other findings, under these keys (absent = False / None):

* ``uid`` (a stable message id of the platform's, for keys), ``stored_id`` (the platform message id as stored,
  before any envelope was read), ``active`` (False: retired by the platform, e.g. rewound), ``file_name`` (audio);
* his rows: ``notice`` (a platform notice, not a message he sent), ``steer`` (a message he sent while her turn
  was running, which the platform folded into that turn: her turn goes on after it), ``internal`` (the platform's
  internal notification), ``caption`` (his words under an attachment placeholder, for a ``system`` row), ``loose`` (his
  message with only the origin note, the reply quote and the journal's note removed: what the context pack, the
  quote verifier and the outage catch-up have always shown), ``frame`` (the stored text opens with a platform
  frame: compaction, a delivery, a system instruction), ``delivery`` (it is a delivery the context pack leaves
  out: a scheduled delivery, a system instruction, the journal's mirror, or no text at all);
* her rows: ``ends_turn`` (this row ends her turn for the privacy pass), ``outage`` (a failed turn that names a
  provider error), ``reply`` (a plain chat reply of hers, for aligning annotations).

The readers' definitions differed before the ports existed (``frame`` and ``delivery`` are not the same test, and
``loose`` is not ``text``); each is kept as it was, so no reader behaves differently (DECISIONS.md, WP7).

**Transcript.** Read-only, plus one framed write (``mirror``). Reads raise one of ``errors`` when the chat cannot be
read (the readers log it and try again next tick, as they always did). ``NullTranscript`` is the standalone answer:
no chat to read, so every chat-reading step is a no-op.

**Channel.** Sending into the journal's chat (``channel.platform``, ``channel.chat_id``): ``send`` returns the new
message's id, ``edit`` changes a sent message; both raise ``ChannelError``. ``caps`` says what the platform can do,
and the engine degrades on what it cannot: no ``edit``, no fold (the message stays as sent); no ``reply``, the
message goes on its own; no ``keyboard``, a phase goes without buttons (its text carries the commands); no
``html``, the adapter is given the composed HTML and sends it as plain text (``compose.plain``). ``send`` ignores a
``reply_to`` or a ``keyboard`` the platform cannot take, so a caller never has to; ``edit`` raises on a platform
that cannot edit, so a caller asks ``caps.edit`` first. ``blocking``: a send may take seconds of work in this
process's thread beyond a network call (it starts a process); the gateway's hooks run on its event loop, so an
answer nothing waits for is sent off it for such a channel (``inbound``).

**Companion.** ``ask`` returns her text, or None for an answer with nothing in it; it raises
``CompanionUnavailable`` when no answer could be had at all (an outage: the caller defers without spending an
attempt). ``agent.ask`` is the facade the engine calls. ``image`` (a path) attaches one picture to the question;
a companion that cannot take one answers None.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, runtime_checkable

HIS_KINDS = ("his", "handled", "mirror", "system", "boundary", "empty")
HER_KINDS = ("hers", "tool_step", "failed_turn", "turn_end", "frame", "empty")


@dataclass(frozen=True)
class Row:
    """One normalised transcript row, either role (see the module docstring for the kinds and ``meta``)."""
    id: int                          # monotonic in stored order (the order the model saw it)
    at: datetime                     # UTC
    role: str                        # "user" | "assistant"
    kind: str
    text: str
    platform_message_id: str | None  # his message's id (from the row, else from its envelope); None for hers
    quote: str | None = None         # the message he replied to, when the platform stores it
    source: str = "text"             # text | voice | audio
    audio_path: str | None = None
    meta: Mapping = field(default_factory=dict, hash=False, compare=False)
    copy: int | None = None          # the row this one repeats (stored again by compaction): read once
    stored: str = ""


class CompanionUnavailable(Exception):
    """No answer because no model/provider was reachable, or the run failed or timed out: an outage."""


class NotMirrored(Exception):
    """A mirror into the chat session did not happen, for a reason worth one log line."""


class ChatUnreadable(OSError):
    """The chat cannot be read safely right now (the privacy pass could not run)."""


class ChannelError(RuntimeError):
    """A message could not be sent into (or edited in) the chat; the text says why, never a secret."""


@dataclass(frozen=True)
class Caps:
    """What a platform can do with a message, as the engine uses it."""
    edit: bool = False       # change a sent message (the fold, a re-send's earlier message)
    reply: bool = False      # send as a reply to a given message (annotation replies, the catch-up)
    keyboard: bool = False   # reply-keyboard buttons (a program phase's /next /skip /back /break)
    html: bool = False       # takes the composed Telegram HTML as is; else the adapter sends it as plain text


@dataclass(frozen=True)
class Keyboard:
    """A reply keyboard: rows of button texts, each sent as typed when tapped. No rows removes the keyboard."""
    rows: tuple[tuple[str, ...], ...] = ()
    placeholder: str = ""


@runtime_checkable
class Transcript(Protocol):
    """Read-only view of the companion chat (+ one framed write: ``mirror``)."""
    source: str | None               # what is read (e.g. the database path), kept in state files; None: no chat
    errors: tuple                    # what a read raises when the chat cannot be read now

    def available(self) -> bool: ...                       # the chat can be read at all (its store exists)
    def rows(self, after_id: int = 0, since: datetime | None = None,
             upto_id: int | None = None) -> list[Row]: ...  # stored order; ``since`` inclusive; copies included
    def tail(self, n: int) -> list[Row]: ...               # the newest ``n`` rows, stored order (doctor --deep)
    def max_id(self) -> int: ...                           # the newest row id in the store (any chat)
    def anchor(self, row_id: int) -> dict | None: ...      # what a row looks like, to know the store again
    def first_id(self, since: datetime) -> int | None: ... # the first row of the chat at or after ``since``
    def busy(self) -> bool: ...                            # a turn of hers is running right now
    def last_activity(self, before: datetime) -> datetime | None: ...  # newest row of either role before then
    def mirror(self, text: str, *, flushing: bool = False) -> bool | str: ...  # True | "queued"; NotMirrored


@runtime_checkable
class Channel(Protocol):
    """Sending into the journal's chat on its platform (see the module docstring)."""
    platform: str
    caps: Caps
    blocking: bool

    def send(self, chat_id: str, html: str, *, reply_to: str | None = None, silent: bool = False,
             keyboard: Keyboard | None = None) -> str: ...      # the new message's id; ChannelError
    def edit(self, chat_id: str, message_id: str, html: str) -> None: ...  # only when caps.edit; ChannelError


class NoChannel:
    """No way to send on the configured platform here (``adapter: none`` off Telegram): every send is refused, so
    the tick counts a failed attempt and alerts, as for any other undeliverable prompt."""
    caps = Caps()
    blocking = False

    def __init__(self, platform: str):
        self.platform = platform

    def send(self, chat_id: str, html: str, *, reply_to: str | None = None, silent: bool = False,
             keyboard: Keyboard | None = None) -> str:
        raise ChannelError(f"no way to send to {self.platform} here (adapter: none; only Telegram is sent directly)")

    def edit(self, chat_id: str, message_id: str, html: str) -> None:
        raise ChannelError(f"{self.platform} messages cannot be edited here")


@runtime_checkable
class Companion(Protocol):
    def ask(self, query: str, budget: int, toolsets: str | None = None, image: str | None = None) -> str | None: ...


class NullTranscript:
    """Standalone: no chat to read. Every chat-reading step is a no-op, as it always was without Hermes."""
    source = None
    errors = (OSError,)

    def available(self) -> bool:
        return False

    def rows(self, after_id: int = 0, since: datetime | None = None, upto_id: int | None = None) -> list[Row]:
        return []

    def tail(self, n: int) -> list[Row]:
        return []

    def max_id(self) -> int:
        return 0

    def anchor(self, row_id: int) -> dict | None:
        return None

    def first_id(self, since: datetime) -> int | None:
        return None

    def busy(self) -> bool:
        return False

    def last_activity(self, before: datetime) -> datetime | None:
        return None

    def mirror(self, text: str, *, flushing: bool = False) -> bool | str:
        return False
