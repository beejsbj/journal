"""The repo tree against Hermes' plugin install scanner: nothing in it may be a CRITICAL finding.

Hermes scans a plugin before installing it (`tools/plugin_guard.py` walking the tree, the patterns of
`tools/skills_guard.py`). One CRITICAL finding is a "dangerous" verdict, a hard block that `--force` does not override,
so a doc example or a lockfile hash that merely looks like an attack stops a deploy cold. It did once: a
`curl ... | python3 -m json.tool` line in DEPLOY.md, and the sha512 integrity of `workbox-cacheable-response` 7.4.1 in
`web/package-lock.json`, which contains the letters x-m-r-i-g, in mixed case, and matches the crypto-mining pattern (case-insensitive).

This test walks the tree the way plugin_guard does and applies the CRITICAL-severity regexes, so such a line fails here,
with file:line, before it reaches a deploy.

KEEP IN SYNC WITH HERMES. `CRITICAL_PATTERNS` is copied from `THREAT_PATTERNS` (severity "critical") in
`hermes-agent/tools/skills_guard.py`, as shipped with scanner version `plugin-guard-v11`
(`PLUGIN_SCANNER_VERSION` in `tools/plugin_guard.py`). When Hermes updates, refresh the list and the version, and
re-run the real scanner on a clone (`scan_plugin` with HERMES_DISABLE_LAZY_INSTALLS=1 and a temporary HERMES_HOME).

What mirrors plugin_guard: the excluded directories, symlinks skipped, the scannable extensions, utf-8-sig reading,
case-insensitive per-line matching. Test trees (`tests/`, `fixtures/`, `test_*.py`, ...) are skipped: plugin_guard steps
their findings down one severity (hostile strings are fixtures there), so they cannot make a "dangerous" verdict. Two
things are stricter than the scanner on purpose: no docstring skipping, and no per-file demotions (CI workflows,
prose docs): a match there is reported even where Hermes would lower it.
"""
import re
import unittest
from pathlib import Path

from helpers import ROOT

SCANNER_VERSION = "plugin-guard-v11"

# tools/plugin_guard.py EXCLUDED_DIRS
EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox"}

# tools/skills_guard.py SCANNABLE_EXTENSIONS (a file named SKILL.md is scanned too)
SCANNABLE_EXTENSIONS = {
    ".md", ".txt", ".py", ".sh", ".bash", ".js", ".ts", ".rb", ".yaml", ".yml", ".json", ".toml",
    ".cfg", ".ini", ".conf", ".html", ".css", ".xml", ".tex", ".r", ".jl", ".pl", ".php"}

# tools/plugin_guard_context.py: TEST_TREE_DIRS, _TEST_DIRS_ANY_DEPTH, _TEST_FILE_NAME (is_test_tree)
TEST_TREE_DIRS = {"tests", "test", "testing", "spec", "specs", "fixtures"}
TEST_DIRS_ANY_DEPTH = {"__tests__", "__fixtures__", "__mocks__"}
TEST_FILE_NAME = re.compile(
    r"^(?:tests?_[^/]*|[^/]*_tests?\.[^./]+|[^/]*\.(?:test|spec)\.[^./]+)$", re.IGNORECASE)

# (regex, pattern id): every THREAT_PATTERNS entry of severity "critical" in tools/skills_guard.py, plugin-guard-v11.
CRITICAL_PATTERNS = [
    (r"""curl\s+(?![^\n]*https?://(?:localhost|127\.0\.0\.1|\[::1\]))[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\b""",
     "env_exfil_curl"),
    (r"""wget\s+(?![^\n]*https?://(?:localhost|127\.0\.0\.1|\[::1\]))[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\b""",
     "env_exfil_wget"),
    (r"""fetch\s*\((?![^\n]*https?://(?:localhost|127\.0\.0\.1|\[::1\]))[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD)S?\b""",
     "env_exfil_fetch"),
    (r"""httpx?\.(get|post|put|patch)\s*\((?![^\n]*https?://(?:localhost|127\.0\.0\.1|\[::1\]))[^\n]*(KEY|TOKEN|SECRET|PASSWORD)""",
     "env_exfil_httpx"),
    (r"""requests\.(get|post|put|patch)\s*\((?![^\n]*https?://(?:localhost|127\.0\.0\.1|\[::1\]))[^\n]*(KEY|TOKEN|SECRET|PASSWORD)""",
     "env_exfil_requests"),
    (r"""\$HOME/\.hermes/\.env|\~/\.hermes/\.env""",
     "hermes_env_access"),
    (r"""cat\s+(?!>)[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)""",
     "read_secrets_file"),
    (r"""\b(?:readFile(?:Sync)?|readTextFile)\s*\(\s*["\'][^"\'\n]*(?:\.ssh[/\\]id_(?:rsa|ed25519|ecdsa|dsa)(?!\.pub)|\.env\b|credentials\b|\.netrc\b|\.pgpass\b|\.npmrc\b|\.pypirc\b)[^"\'\n]*["\']""",
     "js_read_secrets_file"),
    (r"""\bopen\s*\(\s*(?:os\.path\.expanduser\s*\(\s*)?["\'][^"\'\n]*(?:\.ssh[/\\]id_(?:rsa|ed25519|ecdsa|dsa)(?!\.pub)|\.env\b|credentials\b|\.netrc\b|\.pgpass\b|\.npmrc\b|\.pypirc\b)[^"\'\n]*["\']\s*\)?(?!\s*\)?\s*,\s*["\'][^"\']*[wax][^"\']*["\'])(?![^\n]*\bmode\s*=\s*["\'][^"\']*[wax])|\bPath\s*\(\s*(?:os\.path\.expanduser\s*\(\s*)?["\'][^"\'\n]*(?:\.ssh[/\\]id_(?:rsa|ed25519|ecdsa|dsa)(?!\.pub)|\.env\b|credentials\b|\.netrc\b|\.pgpass\b|\.npmrc\b|\.pypirc\b)[^"\'\n]*["\']\s*\)?\s*\)\.(?:read_text|read_bytes|readlines|readline)\s*\(""",
     "py_read_secrets_file"),
    (r"""(?-i:ENV)\[.*(?:KEY|TOKEN|SECRET|PASSWORD)""",
     "ruby_env_secret"),
    (r"""(?<![-/])\b(?:dig|nslookup)\s+(?:[-+@]\S*(?:\s+[^\s$"\'-][^\s$]*)?\s+)*["\']?[^\s"\'$()]*\$(?:\{[^}\s]*\}|\([^\n]*?\)+|\w+)[\w-]*["\']?\.[a-z]|(?<![-/])\bhost\s+(?:[-+@]\S*(?:\s+[^\s$"\'-][^\s$]*)?\s+)*["\']?[^\s"\'$()]*\$(?:\{[^}\s]*\}|\([^\n]*?\)+|\w+)[\w-]*\.[a-z]|(?:^|[;&|]|\$\(|\b(?:do|then|else)\b)\s*host\s+(?:[-+@]\S*(?:\s+[^\s$"\'-][^\s$]*)?\s+)*["\']?\$""",
     "dns_exfil"),
    (r""">\s*/tmp/[^\s]*\s*&&\s*(curl|wget|nc|python)""",
     "tmp_staging"),
    (r"""ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions""",
     "prompt_injection_ignore"),
    (r"""system\s+(?:\w+\s+)*prompt\s+(?:\w+\s+)*override""",
     "sys_prompt_override"),
    (r"""disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)""",
     "disregard_rules"),
    (r"""act\s+as\s+(if|though)\s+(?:\w+\s+)*you\s+(?:\w+\s+)*(have\s+no|don\'t\s+have)\s+(?:\w+\s+)*(restrictions|limits|rules)""",
     "bypass_restrictions"),
    (r"""translate\s+.*\s+into\s+.*\s+and\s+(execute|run|eval)""",
     "translate_execute"),
    (r"""rm\s+-rf\s+((?:[^\s;&|<>#][^\s;&|<>]*\s+)+?)?/(?(1)(?!/(?:\s|$)))(?:(?!(?:tmp|var/tmp|dev/shm|run|var/lib/apt/lists|var/cache/(?:apt|apk|yum|dnf))(?:\b|/))|(?:tmp|var/tmp|dev/shm|run|var/lib/apt/lists|var/cache/(?:apt|apk|yum|dnf))/(?:[^/\s]*/)*\.\.(?=/|[\s;&|]|$))""",
     "destructive_root_rm"),
    (r"""(?<![A-Za-z0-9])rm\s+(-[^\s]*)?r.*(?:\$HOME|~[/\s*]|~$)|(?<![A-Za-z0-9])rmdir\s+.*(?:\$HOME|~[/\s*]|~$)""",
     "destructive_home_rm"),
    (r""">\s*/etc/""",
     "system_overwrite"),
    (r"""\bmkfs\b""",
     "format_filesystem"),
    (r"""\bdd\s+.*if=.*of=/dev/""",
     "disk_overwrite"),
    (r"""truncate\s+-s\s*0\s+/""",
     "truncate_system"),
    (r"""authorized_keys""",
     "ssh_backdoor"),
    (r"""/etc/sudoers|visudo""",
     "sudoers_mod"),
    (r"""\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b[^\n]*\b(?:tcp|udp|openssl|ssl|exec|system|pty|unix)[\w-]*:""",
     "reverse_shell"),
    (r"""/bin/(?:bash|sh|zsh|ksh|dash)\b\s+-i\s+.*>/dev/tcp/""",
     "bash_reverse_shell"),
    (r"""python[23]?\s+-c\s+["\']import\s+socket""",
     "python_socket_oneliner"),
    (r"""echo\s+[^\n]*\|\s*(?:(?:bash|sh|zsh|ksh|dash)\b|python|perl|ruby|node)""",
     "echo_pipe_exec"),
    (r"""/etc/passwd|/etc/shadow""",
     "system_passwd_access"),
    (r"""xmrig|stratum\+tcp|monero|coinhive|cryptonight""",
     "crypto_mining"),
    (r"""curl\s+[^|\s][^\n]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:bash|sh|zsh|ksh|dash)\b""",
     "curl_pipe_shell"),
    (r"""wget\s+[^\n]*-O\s*-\s*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:bash|sh|zsh|ksh|dash)\b""",
     "wget_pipe_shell"),
    (r"""curl\s+[^|\s][^\n]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?python""",
     "curl_pipe_python"),
    (r"""(?<![a-z])set(?:e|re|res)?[ug]id\s*\(|\bcap_set[ug]id\b|\bchmod\s+(?:-\w+\s+)*(?:[ugoa]*[+=][rwxXt]*s|0?[2-7][0-7]{3}\b)""",
     "setuid_setgid"),
    (r"""NOPASSWD""",
     "nopasswd_sudo"),
    (r"""chmod\s+[u+]?s""",
     "suid_bit"),
    (r"""^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\bwrit(?:e|es|ing)\b|\bwritten\b|\bedit(?:s|ed|ing)?\b|\bmodif(?:y|ies|ied|ying|ication)s?\b|\bupdat(?:e|es|ed|ing)\b|\bappend(?:s|ed|ing)?\b|\bprepend(?:s|ed|ing)?\b|\binject(?:s|ed|ing)?\b|\boverwrit(?:e|es|ing)\b|\boverwritten\b|\breplac(?:e|es|ed|ing)\b|\balter(?:s|ed|ing)?\b|\badd(?:s|ed|ing)\b)[^\n,]{0,80}?(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)\b|(?:\byou\s+(?:must|should|need\s+to)\s+|\bplease\s+|\bmake\s+sure\s+(?:to\s+|you\s+)|\bbe\s+sure\s+to\s+)(?:\bwrit(?:e|es|ing)\b|\bwritten\b|\bedit(?:s|ed|ing)?\b|\bmodif(?:y|ies|ied|ying|ication)s?\b|\bupdat(?:e|es|ed|ing)\b|\bappend(?:s|ed|ing)?\b|\bprepend(?:s|ed|ing)?\b|\binject(?:s|ed|ing)?\b|\boverwrit(?:e|es|ing)\b|\boverwritten\b|\breplac(?:e|es|ed|ing)\b|\balter(?:s|ed|ing)?\b|\badd(?:s|ed|ing)\b)[^\n,]{0,80}?(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)\b""",
     "agent_config_mod"),
    (r"""(?:>>|[\w"\'`)\]]\s*>)\s*[~\w./-]*(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)(?!\.?\w)|\bsed\b[^\n]*\s(?:-[A-Za-z]*i[A-Za-z]*|--in-place)\b[^\n]*(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)(?!\.?\w)|\btee\s+(?:-a\s+)?[~\w./"\'-]*(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)(?!\.?\w)|\b(?:cp|mv)\s+[^\s|;&]+\s+[^\n|;&]{0,40}?(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)(?!\.?\w)""",
     "agent_config_mod_shell"),
    (r"""(?:>>|[\w"\'`)\]]\s*>)\s*[~\w./-]*\.hermes/(?:config\.yaml|SOUL\.md)(?!\.?\w)|\bsed\b[^\n]*\s(?:-[A-Za-z]*i[A-Za-z]*|--in-place)\b[^\n]*\.hermes/(?:config\.yaml|SOUL\.md)(?!\.?\w)|\btee\s+(?:-a\s+)?[~\w./"\'-]*\.hermes/(?:config\.yaml|SOUL\.md)(?!\.?\w)|\b(?:cp|mv)\s+[^\s|;&]+\s+[^\n|;&]{0,40}?\.hermes/(?:config\.yaml|SOUL\.md)(?!\.?\w)""",
     "hermes_config_mod_shell"),
    (r"""(?:>>|[\w"\'`)\]]\s*>)\s*[~\w./-]*\.(?:claude/settings|codex/config)[\w.]*(?!\.?\w)|\bsed\b[^\n]*\s(?:-[A-Za-z]*i[A-Za-z]*|--in-place)\b[^\n]*\.(?:claude/settings|codex/config)[\w.]*(?!\.?\w)|\btee\s+(?:-a\s+)?[~\w./"\'-]*\.(?:claude/settings|codex/config)[\w.]*(?!\.?\w)|\b(?:cp|mv)\s+[^\s|;&]+\s+[^\n|;&]{0,40}?\.(?:claude/settings|codex/config)[\w.]*(?!\.?\w)""",
     "other_agent_config_mod_shell"),
    (r"""(?:api[_-]?key|token|secret|password)\s*[=:]\s*["\'](?!(?-i:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["\'])[A-Za-z0-9+/=_-]{20,}""",
     "hardcoded_secret"),
    (r"""(?-i:-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----)""",
     "embedded_private_key"),
    (r"""(?-i:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,})""",
     "github_token_leaked"),
    (r"""(?-i:sk-[A-Za-z0-9]{20,})""",
     "openai_key_leaked"),
    (r"""(?-i:sk-ant-[A-Za-z0-9_-]{90,})""",
     "anthropic_key_leaked"),
    (r"""(?-i:AKIA[0-9A-Z]{16})""",
     "aws_access_key_leaked"),
    (r"""(?-i:glpat-[A-Za-z0-9_\-]{20,})""",
     "gitlab_token_leaked"),
    (r"""\bDAN\s+mode\b|Do\s+Anything\s+Now""",
     "jailbreak_dan"),
    (r"""\bdeveloper\s+mode\b.*\benabled?\b""",
     "jailbreak_dev_mode"),
    (r"""(respond|answer|reply)\s+without\s+(?:\w+\s+)*(restrictions|limitations|filters|safety)""",
     "remove_filters"),
]

COMPILED = [(re.compile(pattern, re.IGNORECASE), pid) for pattern, pid in CRITICAL_PATTERNS]


def is_test_tree(rel: str) -> bool:
    parts = Path(rel).parts
    return (
        (len(parts) > 1 and parts[0].lower() in TEST_TREE_DIRS)
        or any(part.lower() in TEST_DIRS_ANY_DEPTH for part in parts[:-1])
        or TEST_FILE_NAME.match(parts[-1]) is not None
    )


def scanned_files(root: Path):
    """(path, "a/b/c") for each file plugin_guard would scan: not excluded, not a symlink, a scannable extension."""
    for f in sorted(root.rglob("*")):
        rel_parts = f.relative_to(root).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if not f.is_file() or f.is_symlink():
            continue
        if f.suffix.lower() not in SCANNABLE_EXTENSIONS and f.name != "SKILL.md":
            continue
        rel = "/".join(rel_parts)
        if not is_test_tree(rel):
            yield f, rel


def critical_findings(root: Path) -> list[str]:
    found = []
    for f, rel in scanned_files(root):
        try:
            lines = f.read_text(encoding="utf-8-sig").split("\n")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, start=1):
            for pattern, pid in COMPILED:
                if pattern.search(line):
                    text = line.strip()
                    found.append(f"{rel}:{number}: {pid}: {text if len(text) <= 100 else text[:97] + '...'}")
    return found


class InstallScanTest(unittest.TestCase):
    def test_the_tree_has_no_critical_finding(self):
        found = critical_findings(ROOT)
        self.assertEqual(
            found, [],
            "Hermes' plugin install scanner would block this tree as dangerous (no --force):\n" + "\n".join(found))

    def test_the_scan_sees_the_files_it_is_meant_to_see(self):
        rels = {rel for _, rel in scanned_files(ROOT)}
        self.assertIn("DEPLOY.md", rels)
        self.assertIn("web/package-lock.json", rels)
        self.assertFalse({r for r in rels if r.startswith(("tests/", "web/node_modules/", ".git/"))})

    def test_the_patterns_catch_what_once_blocked_a_deploy(self):
        def hits(line):
            return {pid for pattern, pid in COMPILED if pattern.search(line)}

        self.assertIn("curl_pipe_python", hits("curl -sS $B/manifest.webmanifest | python3 -m json.tool"))
        integrity_of_7_4_1 = '"integrity": "sha512-8xaFoJdDc2Ojrlbb...' + "XM" + 'rIG..."'  # split, so this file is clean too
        self.assertTrue(hits(integrity_of_7_4_1), "the mining pattern is case-insensitive")
        self.assertEqual(hits("curl -sS $B/manifest.webmanifest | jq -c '{start_url,scope}'"), set())


if __name__ == "__main__":
    unittest.main()
