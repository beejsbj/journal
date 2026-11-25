"""The chat face: what the engine does in the journal's chat, apart from the tick that schedules it (issue #43).

``deliver`` is the one send operation (send one message to the chat, let the caller record it, mirror what it says):
every message the engine sends on its own goes through it. ``send`` delivers a prompt record (``send_prompt``: retries,
the ledger's account, ``sent.jsonl``, re-sends); ``fold`` edits a sent prompt once it was answered or its window passed; ``replies`` sends each annotation as a
reply to the message it annotates. The tick calls them as its steps, in the order its docstring gives, under its lock;
``programs`` delivers phases through ``send_prompt`` too. The readers of the chat (``sweep``, ``conversations``) move
here last (#43's order)."""
