"""The ``-t`` pick the Hermes companion builds is one Hermes accepts (HERMES-TOOLSETS, DECISIONS.md).

Since Hermes runs every one-shot through its gateway (``gateway/session_policy.py`` ``_resolve_toolsets``), a pick
naming a toolset it does not know is refused (``invalid_params``) and no model runs. The engine passed ``-t none``
for "no tools" (an older Hermes warned "Unknown toolsets: none" and ran with none), so every model step failed, the
outage probe too, and the breaker stayed open for days.

KEEP IN SYNC WITH HERMES. ``ACCEPTED`` and ``NO_TOOL_NAMES`` mirror what ``_resolve_toolsets`` accepts for an explicit
pick (``hermes_cli.toolset_validation.saved_toolset_resolver``: a registered toolset, ``all``/``*``, a configured MCP
server, a plugin's toolset key, the ``no_mcp`` sentinel) as far as the engine uses it. When Hermes changes that rule,
re-run ``LiveHermesTest`` against its checkout (``JOURNAL_HERMES_AGENT=<checkout with venv/>``; it imports Hermes in a
child process only, with ``HERMES_DISABLE_LAZY_INSTALLS=1`` and a temporary ``HERMES_HOME``, never the live profile).

Nothing here imports Hermes in this process (``test_purity.py``).
"""
import ast
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, make_cfg, real_ask

from journal import agent, outage, tools
from journal.config import Config
from journal.hermes import tools as hermes_tools
from journal.hermes.companion import NO_TOOLS, HermesCompanion, hermes_toolsets, toolset_hazards

SRC = ROOT / "src" / "journal"
PLUGIN_TOOLSETS = {hermes_tools.TOOLSET, tools.READ_TOOLSET}  # what the journal plugin registers (``ctx.register_tool``)
ACCEPTED = {"all", "*", "no_mcp"} | PLUGIN_TOOLSETS  # (Hermes' own built-ins are accepted too; the engine names none)
NO_TOOL_NAMES = {"no_mcp"}  # alone in a pick: no toolsets at all (an explicit saved list holding only the sentinel)
REFUSAL = ("Hermes couldn't start the model connection: invalid_params (toolsets: unknown or invalid toolset name: "
           "{name!r}). Your message was not sent. Run `hermes doctor` to check the setup, or /model to pick a "
           "different provider.")


def picks(argv: list[str]) -> list[str] | None:
    """The names of the ``-t`` pick in ``argv`` (as Hermes splits it: commas, stripped, empties dropped), or None."""
    if "-t" not in argv:
        return None
    return [n.strip() for n in argv[argv.index("-t") + 1].split(",") if n.strip()]


def engine_toolsets() -> set:
    """Every ``toolsets`` value the engine passes to a model step: ``toolsets=`` keywords anywhere in ``src/journal``
    (string constants, ``READ_TOOLSET``, both arms of a conditional) and the outage probe's positional one."""
    found = set()

    def value(node):
        if isinstance(node, ast.Constant):
            found.add(node.value)
        elif isinstance(node, (ast.Name, ast.Attribute)) and (getattr(node, "id", None) or node.attr) == "READ_TOOLSET":
            found.add(tools.READ_TOOLSET)
        elif isinstance(node, ast.IfExp):
            value(node.body)
            value(node.orelse)
        else:
            raise AssertionError(f"a toolsets value this test cannot read: {ast.dump(node)}")

    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "toolsets":
                    value(kw.value)
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "ask" and len(node.args) >= 3
                    and isinstance(node.args[0], ast.Name) and node.args[0].id == "PROBE_QUERY"):
                value(node.args[2])
    found.discard(None)
    return found


class ArgvContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.hermes = {"command": "hermes"}

    def tearDown(self):
        self.tmp.cleanup()

    def argv(self, toolsets, image=None):
        return HermesCompanion(self.cfg).argv(Path("/q.md"), 120, toolsets, image)

    def test_no_tools_is_a_name_hermes_accepts_and_resolves_to_nothing(self):
        argv = self.argv("none")
        self.assertEqual(argv[argv.index("-t"):argv.index("-t") + 2], ["-t", NO_TOOLS])
        self.assertTrue(set(picks(argv)) <= NO_TOOL_NAMES)
        self.assertNotIn("none", argv)

    def test_the_read_toolset_passes_through(self):
        self.assertEqual(picks(self.argv(tools.READ_TOOLSET)), [tools.READ_TOOLSET])

    def test_no_pick_means_no_flag(self):
        self.assertIsNone(picks(self.argv(None)))
        self.assertIsNone(picks(self.argv("")))

    def test_the_whole_argv(self):
        self.assertEqual(self.argv("none", image="/p.jpg"),
                         ["hermes", "chat", "--oneshot", "--query-file", "/q.md", "--run-budget", "120",
                          "--source", "tool", "-Q", "-t", "no_mcp", "--image", "/p.jpg"])

    def test_every_pick_the_engine_makes_is_accepted(self):
        used = engine_toolsets()
        self.assertEqual(used, {"none", tools.READ_TOOLSET})  # (a new value is checked below, and named here)
        for value in used:
            names = picks(self.argv(value))
            self.assertTrue(names and set(names) <= ACCEPTED, f"{value!r} -> -t {names}: Hermes refuses it")

    def test_mapping(self):
        self.assertIsNone(hermes_toolsets(None))
        self.assertEqual(hermes_toolsets("none"), "no_mcp")
        self.assertEqual(hermes_toolsets("none,journal_read"), "journal_read")  # (no "none" in any pick)
        self.assertEqual(hermes_toolsets(" journal_read "), "journal_read")


class HazardTest(unittest.TestCase):
    """M1 (review of #62): ``-t no_mcp`` is no toolsets only while the profile has no xAI credentials and the default
    context engine; ``journal doctor`` says when either holds. Values are never read out, only their presence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "hermes"
        self.home = self.root / "profiles" / "companion"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def hazards(self, **env):
        return toolset_hazards(self.home, env)

    def test_a_plain_profile_has_none(self):
        (self.home / "config.yaml").write_text("context:\n  engine: compressor\nmodel: x\n")
        (self.home / ".env").write_text("OPENROUTER_API_KEY=k\nXAI_API_KEY=\n")
        (self.home / "auth.json").write_text(json.dumps({"providers": {"nous": {"x": 1}}}))
        self.assertEqual(self.hazards(), [])
        self.assertEqual(toolset_hazards(None), [])

    def test_each_condition(self):
        (self.home / "config.yaml").write_text("context:\n  engine: Some-Engine\n")
        self.assertEqual(len(self.hazards()), 1)
        self.assertIn("context.engine is 'some-engine'", self.hazards()[0])
        (self.home / "config.yaml").write_text("{}\n")
        for where in ("env", "dotenv", "auth", "root-auth", "pool"):
            with self.subTest(where):
                for f in (self.home / ".env", self.home / "auth.json", self.root / "auth.json"):
                    f.unlink(missing_ok=True)
                env = {}
                if where == "env":
                    env = {"XAI_API_KEY": "secret-value"}
                elif where == "dotenv":
                    (self.home / ".env").write_text("export XAI_API_KEY='secret-value'\n")
                elif where == "auth":
                    (self.home / "auth.json").write_text(json.dumps({"providers": {"xai-oauth": {"tokens": {}}}}))
                elif where == "root-auth":
                    (self.root / "auth.json").write_text(json.dumps({"providers": {"xai-oauth": {"tokens": {}}}}))
                else:
                    (self.home / "auth.json").write_text(json.dumps({"credential_pool": {"xai-oauth": [{"a": 1}]}}))
                found = self.hazards(**env)
                self.assertEqual(len(found), 1)
                self.assertIn("x_search", found[0])
                self.assertNotIn("secret-value", found[0])

    def test_doctor_says_it(self):
        from journal import status
        cfg = make_cfg(Path(self.tmp.name) / "j")
        cfg.hermes_home = self.home
        cfg.hermes = {"command": sys.executable}
        (self.home / "config.yaml").write_text("context:\n  engine: some-engine\n")
        with mock.patch.object(Config, "companion", lambda c: HermesCompanion(c)), \
                mock.patch.object(Config, "hermes_env", lambda c: {}):
            rows = [r for r in status.doctor(cfg, token=False) if r[0] == "one-shot tools"]
            self.assertEqual([r[1] for r in rows], ["warn"])
            (self.home / "config.yaml").write_text("{}\n")
            rows = [r for r in status.doctor(cfg, token=False) if r[0] == "one-shot tools"]
        self.assertEqual([r[1] for r in rows], ["ok"])


FAKE_HERMES = textwrap.dedent('''\
    import os, sys, textwrap
    ACCEPTED = set(sys.argv[1].split(","))
    REFUSAL = sys.argv[2]
    args = sys.argv[3:]
    if "-t" in args:
        for name in [n.strip() for n in args[args.index("-t") + 1].split(",") if n.strip()]:
            if name not in ACCEPTED:
                text = REFUSAL.format(name=name)
                if os.environ.get("FAKE_STYLE") == "tty":  # a terminal's copy: colour codes, wrapped at 80 columns
                    text = text.replace("invalid_params", "\\x1b[1minvalid_params\\x1b[22m")
                    text = "\\n".join("\\x1b[31m" + l + "\\x1b[0m" for l in textwrap.wrap(text, 80))
                print(text)
                sys.exit(1)
    print("ok")
    print("session_id: 20261117_000000_abcdef")
''')


class FakeHermesTest(unittest.TestCase):
    """End to end through the real adapter and ``agent.ask``: a ``hermes`` that refuses as the gateway does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cfg = make_cfg(tmp)
        script = tmp / "fake_hermes.py"
        script.write_text(FAKE_HERMES, encoding="utf-8")
        wrapper = tmp / "hermes"
        self.accepted = ",".join(sorted(ACCEPTED))
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} -I {script} \"$FAKE_ACCEPTED\" \"$FAKE_REFUSAL\" \"$@\"\n")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        self.cfg.hermes = {"command": str(wrapper)}
        agent._answered = False
        self.env = mock.patch.dict(os.environ, {"FAKE_ACCEPTED": self.accepted, "FAKE_REFUSAL": REFUSAL})
        self.env.start()
        self.companion = mock.patch.object(Config, "companion", lambda cfg: HermesCompanion(cfg))
        self.companion.start()
        self.real = mock.patch.object(agent, "ask", real_ask)  # (helpers stop a real agent run; this one is fake)
        self.real.start()

    def tearDown(self):
        self.real.stop()
        self.companion.stop()
        self.env.stop()
        os.environ.pop("FAKE_STYLE", None)
        agent._answered = False
        self.tmp.cleanup()

    def errors(self):
        path = self.cfg.state_dir / "errors.jsonl"
        return [json.loads(l)["error"] for l in path.read_text().splitlines()] if path.is_file() else []

    def test_the_engines_picks_run(self):
        self.assertEqual(agent.ask(self.cfg, "q", 60, toolsets="none"), "ok")
        self.assertEqual(agent.ask(self.cfg, "q", 60, toolsets=tools.READ_TOOLSET), "ok")
        self.assertIsNone(outage.known(self.cfg))
        self.assertEqual(self.errors(), [])

    def test_an_open_breaker_closes_on_its_next_probe(self):
        """The deploy: the breaker the refused probe held open closes at the first probe after it."""
        outage.trip(self.cfg, "exit 1", outage._now() - outage.probe_wait(self.cfg, 0) * 2)
        self.assertEqual(agent.ask(self.cfg, "q", 60, toolsets="none"), "ok")
        self.assertIsNone(outage.known(self.cfg))

    def test_a_refused_pick_holds_nothing(self):
        """Hermes as it was before this fix: ``-t none`` refused. The run and its probe are refused; the breaker
        stays shut, and the refusal has a line of its own."""
        os.environ["FAKE_ACCEPTED"] = "journal_read"
        with self.assertRaises(outage.RequestRefused):
            agent.ask(self.cfg, "q", 60, toolsets="none", trip_at_once=True)
        with self.assertRaises(outage.RequestRefused):
            agent.ask(self.cfg, "q", 60, toolsets="none")
        self.assertIsNone(outage.known(self.cfg))
        self.assertEqual(agent.ask(self.cfg, "q", 60, toolsets=tools.READ_TOOLSET), "ok")  # (nothing held)
        errors = self.errors()
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(e.startswith("model request refused (exit 1: invalid_params, toolsets): ") for e in errors))
        self.assertFalse(any("no_mcp" in e or "unknown or invalid" in e for e in errors))  # (Hermes' detail: not read)

    def test_a_refusal_in_colour_and_wrapped_is_still_one(self):
        """L2 (review of #62): Hermes' copy as a terminal prints it, with colour codes and wrapped at 80 columns, so
        the wire code sits on another line than the copy before it. Still a refusal, and still no detail read."""
        os.environ.update(FAKE_ACCEPTED="journal_read", FAKE_STYLE="tty")
        with self.assertRaises(outage.RequestRefused):
            agent.ask(self.cfg, "q", 60, toolsets="none", trip_at_once=True)
        self.assertIsNone(outage.known(self.cfg))
        errors = self.errors()
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("model request refused (exit 1: invalid_params, toolsets): "), errors)
        self.assertNotIn("\x1b", errors[0])
        self.assertFalse("no_mcp" in errors[0] or "unknown or invalid" in errors[0])


@unittest.skipUnless(os.environ.get("JOURNAL_HERMES_AGENT"), "set JOURNAL_HERMES_AGENT to a Hermes checkout to check")
class LiveHermesTest(unittest.TestCase):
    """Opt-in: what the real ``_resolve_toolsets`` makes of the engine's picks, in a child process with a temporary
    ``HERMES_HOME`` holding this repo as its one plugin (``HERMES_DISABLE_LAZY_INSTALLS=1``).

    It reads the toolset NAMES a pick enables, not only their tools: a toolset with no static tools (Hermes'
    ``context_engine``, whose tools the engine supplies at run time) flattens to nothing, so "no tools" read from the
    tools alone would pass with a context engine's tools in the run (M1, review of #62)."""

    PROBE = textwrap.dedent('''\
        import copy, json, os, sys
        from hermes_cli.config import load_config
        from hermes_cli.plugins import discover_plugins
        discover_plugins()
        from gateway.session_policy import _resolve_toolsets
        from toolsets import resolve_toolset
        base, out = load_config(), {}
        if sys.argv[2] == "hazards":  # the two conditions ``journal doctor`` warns of
            base["context"] = {"engine": "some-engine"}
            os.environ["XAI_API_KEY"] = "placeholder-not-a-key"
        for pick in json.loads(sys.argv[1]):
            try:
                enabled = _resolve_toolsets({"toolsets": pick}, copy.deepcopy(base), "tool", False)
                out[",".join(pick)] = {"toolsets": sorted(enabled),
                                       "tools": sorted({t for n in enabled for t in resolve_toolset(n)})}
            except Exception as e:
                out[",".join(pick)] = "refused: " + type(e).__name__ + " " + str(e)
        print(json.dumps(out))
    ''')

    def resolve(self, picks_: list, mode: str = "plain") -> dict:
        checkout = Path(os.environ["JOURNAL_HERMES_AGENT"])
        python = checkout / "venv" / "bin" / "python"
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / "plugins").mkdir(parents=True)
            (home / "plugins" / "journal").symlink_to(ROOT)
            (home / "config.yaml").write_text("plugins:\n  load_timeout_seconds: 0\n  enabled: [journal]\n"
                                              "  entries:\n    journal:\n      settings:\n"
                                              f"        config_file: {tmp}/none.json\n")
            env = {k: v for k, v in os.environ.items() if k != "XAI_API_KEY"}
            env.update(HERMES_HOME=str(home), HERMES_DISABLE_LAZY_INSTALLS="1", JOURNAL_STATE_DIR=f"{tmp}/state",
                       PYTHONPATH=str(checkout))
            out = subprocess.run([str(python), "-c", self.PROBE, json.dumps(picks_), mode],
                                 capture_output=True, text=True, timeout=300, cwd=tmp, env=env)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    def engine_picks(self) -> list:
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        cfg.hermes = {"command": "hermes"}
        return [picks(HermesCompanion(cfg).argv(Path("/q"), 1, v)) for v in sorted(engine_toolsets())]

    def test_the_engines_picks_against_hermes(self):
        got = self.resolve(self.engine_picks() + [["none"]])
        self.assertEqual(got[NO_TOOLS], {"toolsets": [], "tools": []})  # no toolsets at all, so no tools
        self.assertEqual(got[tools.READ_TOOLSET], {"toolsets": [tools.READ_TOOLSET], "tools": sorted(tools.READ_ONLY)})
        self.assertTrue(str(got["none"]).startswith("refused"), got["none"])  # (why this file exists)

    def test_what_journal_doctor_warns_of(self):
        """A profile with xAI credentials adds ``x_search`` to "no tools", and a context engine other than the
        default adds ``context_engine`` to every pick, the reflection's too (``journal doctor``:
        ``hermes.companion.toolset_hazards``)."""
        got = self.resolve(self.engine_picks(), "hazards")
        self.assertEqual(got[NO_TOOLS]["toolsets"], ["context_engine", "x_search"])
        self.assertEqual(got[tools.READ_TOOLSET]["toolsets"], ["context_engine", tools.READ_TOOLSET])


if __name__ == "__main__":
    unittest.main()
