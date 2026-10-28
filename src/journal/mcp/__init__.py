"""``journal mcp``: the journal's tools for any MCP client (Claude Desktop, Claude Code, others), over stdio.

``handlers`` is one plain function per tool, each a call into the engine the other faces already use (stdlib only,
tested without the SDK); ``server`` registers them with the official MCP Python SDK, the optional extra
``journal[mcp]``. Like ``journal serve``, this face never reads the chat transcript (``tests/test_mcp_fence.py``),
never calls a model, and appends his words only through ``capture`` under the ledger lock.
"""
