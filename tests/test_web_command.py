"""``/web`` (WEB-COMMAND): the chat's way to the web app. It answers with the address serve itself builds
(``config.serve_site``, what ``serve.auth.site`` returns), says plainly when serve has no public host, and never sends
a pairing link, a code or a token: the chat's history is not a place for a secret."""
import tempfile
import unittest
from pathlib import Path

from helpers import CleanEnv, make_cfg

from journal import commands, config
from journal.serve import auth


class WebCommandTest(CleanEnv):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = make_cfg(Path(tmp.name))

    def reply(self, **serve):
        self.cfg.serve.update(serve)
        return commands.web_reply(self.cfg)

    def test_the_address_is_the_one_serve_builds(self):
        got = self.reply(host="journal.example.org")
        self.assertEqual(got.splitlines()[0], "Your journal on the web: https://journal.example.org")
        self.assertEqual(auth.site(self.cfg), "https://journal.example.org")
        got = self.reply(public_port=8443)
        self.assertIn("https://journal.example.org:8443", got)
        self.assertEqual(auth.site(self.cfg), config.serve_site(self.cfg))
        self.assertEqual(self.reply(public_port=443).splitlines()[0],      # 443 spelled out: no port
                         "Your journal on the web: https://journal.example.org")

    def test_with_no_host_it_says_so_plainly(self):
        self.assertEqual(self.reply(host=""), "The web app isn't set up here: serve.host isn't set, so there's no "
                                              "address to give.")
        self.assertNotIn("http", self.reply(host=None))   # never the loopback bind address

    def test_a_host_that_is_not_a_bare_name_is_not_given_out(self):
        for bad in ("https://journal.example.org", "journal.example.org:8443", "a b", 5, "journal.example.org#x",
                    "journal.example.org?x", "me@journal.example.org", "[::1]", "a\\b"):
            got = self.reply(host=bad)
            self.assertIn("isn't usable", got, bad)
            self.assertNotIn("Your journal on the web", got)

    def test_it_never_sends_a_pairing_link_or_a_token(self):
        self.cfg.serve["token_env"] = "JOURNAL_SERVE_TOKEN_TEST"
        import os
        os.environ["JOURNAL_SERVE_TOKEN_TEST"] = "s3cret-token-value"
        self.addCleanup(os.environ.pop, "JOURNAL_SERVE_TOKEN_TEST", None)
        for serve in ({"host": "journal.example.org"},
                      {"host": "journal.example.org", "socket": "/run/j.sock", "tailscale_users": ["me@example.org"]}):
            got = self.reply(**serve)
            self.assertNotIn("/pair", got)
            self.assertNotIn("#", got)
            self.assertNotIn("s3cret", got)
        self.assertFalse(list(Path(self.cfg.state_dir).glob("serve-pairings.json")))   # no pairing code was made

    def test_how_to_sign_in_follows_the_setup(self):
        self.assertIn("journal serve pair", self.reply(host="journal.example.org"))
        got = self.reply(socket="/run/j.sock", tailscale_users=["me@example.org"])
        self.assertIn("signed in to Tailscale", got)
        self.assertNotIn("journal serve pair", got)

    def test_it_is_not_answered_before_the_platforms_gating(self):
        """The dispatch hook runs before Hermes checks who may run a command (``user_allowed_commands``); /web takes
        Hermes' own command path, so that gating applies to it (CodeRabbit, earlier PR 84)."""
        self.assertIsNone(commands.routed_program("/web"))
        self.assertIsNone(commands.run_program_command(self.cfg, "web", None))

    def test_it_is_a_command_like_the_others(self):
        spec = next(s for s in commands.specs() if s.name == "web")
        self.assertTrue(spec.needs_config)
        self.cfg.serve["host"] = "journal.example.org"
        self.assertEqual(spec.run(self.cfg, "", None), commands.web_reply(self.cfg))
        self.assertIn("isn't configured", spec.unconfigured("", RuntimeError("x")))
        self.assertIn("web", config.RESERVED_COMMANDS)
        self.assertNotIn("web", config.HERMES_COMMANDS)
        self.assertTrue(config.opt_out_problem("/web"))


if __name__ == "__main__":
    unittest.main()
