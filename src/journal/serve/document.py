"""Moved to ``journal.document`` (issue #47): the readers the web app and ``journal mcp`` share live in the core, so
``serve/`` and ``mcp/`` are siblings over it. This name stays an alias of that module (the very same module object),
so an import or a test's patch through either name reaches the same code."""
import sys

from .. import document

sys.modules[__name__] = document
