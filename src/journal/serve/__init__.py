"""``journal serve``: the private web app and its HTTP API over the engine's own files.

``app`` (the server, the order of checks), ``api`` (one function per route), ``auth`` (token, pairing, cookie,
Host/Origin), ``document`` (entry files as two-voice documents), ``sse`` (``/events``), ``static`` (the built web
app or the placeholder), ``unit`` (``--print-unit``). It reads state, never the chat transcript, never calls a
model and never takes ``tick.lock``; see docs/API.md.
"""
