"""Default risk lists: which tool calls deserve a second look. Pure data and two tiny functions.

Where the list comes from (nothing here is invented from scratch):

* Claude Code's auto-mode classifier publishes what it blocks by default (code.claude.com/docs/en/permission-modes,
  "What the classifier blocks by default"): download-and-execute, production deploys, force push, destructive git,
  ``terraform destroy`` and friends, secret-manager writes, IAM grants, merging unapproved PRs, bot commands in
  comments, flags that disarm a guard (``--insecure``), tunnels, the cloud metadata endpoint.
* OpenAI Codex (codex-rs/shell-command ``is_dangerous_command``) flags forced ``rm`` and looks through
  ``sudo``/``env``/``bash -c`` wrappers; Claude's docs warn that prefix rules miss ``/usr/bin/curl`` and
  ``git -C . push``. We match a regex anywhere in the command text, so wrappers and paths cannot hide a hit.
* Real worker runs: ``curl -X PUT .../merge`` and ``curl -X POST .../dispatches`` bypassed a ``gh``-only list.

Two tiers. **critical** = irreversible or public, reviewed with its own budget. **watch** = privileged access to
another machine (ssh, pct, qm): worth one look, but it must never use up the budget of the critical tier.
Add your own with ``categories.architecture.commands_extra`` (regexes); nothing here needs editing.
"""
import re

_FLAGS = r"(?:\s+-{1,2}[\w=./:@-]+(?:\s+[\w=./:@-]+)?)*"  # kubectl -n prod / --context p / git -C dir (loose)
_GH_WRITE = r"(?:-X\s*|--request[= ]\s*|--method[= ]\s*)(?:PUT|POST|PATCH|DELETE)\b"

CRITICAL_FILES = [
    r"\.tf$", r"migrations?/", r"(^|/)Dockerfile$", r"docker-compose", r"\.github/workflows/",
    r"(^|/)(k8s|kubernetes|manifests|helm|charts)/.*\.ya?ml$", r"(^|/)(ansible|playbooks)/.*\.ya?ml$",
]

CRITICAL_COMMANDS = [
    # --- download and execute -------------------------------------------------------------------------
    r"\b(?:curl|wget)\b[^\n|;]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", r"\bbash\s+<\(\s*(?:curl|wget)\b",
    # --- git: public or irreversible ----------------------------------------------------------------
    r"\bgit\b.*\bpush\b.*(?:--force\b|\s-f\b|--delete\b|\s:\S)", r"\bgit\b.*\bpush\b.*\b(?:main|master)\b",
    r"\bgit\s+tag\b(?!\s+(?:-l\b|--list\b))\s+\S",
    r"\bgit\s+reset\s+--hard\b", r"\bgit\s+checkout\s+--\s+\.", r"\bgit\s+restore\s+\.(?:\s|$)",
    r"\bgit\s+clean\s+-\w*f", r"\bgit\s+stash\s+(?:drop|clear)\b", r"\bgit\s+commit\b.*--amend\b",
    r"\bgit\s+branch\s+(?-i:-D)\b", r"\bgit\s+remote\s+(?:add|set-url)\b",
    # --- GitHub: merge, release, bot commands, access ----------------------------------------------
    r"\bgh\s+pr\s+(?:merge|close)\b", r"\bgh\s+release\s+(?:create|delete|upload|edit)\b", r"\bgh\s+workflow\s+run\b",
    r"\bgh\s+repo\s+(?:delete|fork|create|edit|archive)\b", r"\bgh\s+(?:issue|pr)\s+comment\b",
    r"\bgh\s+api\b.*" + _GH_WRITE, r"\bgh\s+api\b.*/(?:merge|releases|comments|dispatches|collaborators)\b",
    r"\b(?:curl|wget)\b[^\n]*" + _GH_WRITE + r"[^\n]*api\.github\.com",
    r"\b(?:curl|wget)\b[^\n]*api\.github\.com[^\n]*" + _GH_WRITE,
    # --- infrastructure ----------------------------------------------------------------------------
    r"\b(?:terraform|tofu)\s+(?:apply|destroy)\b", r"\b(?:pulumi|cdk|terragrunt)\s+destroy\b",
    r"\bkubectl\b" + _FLAGS + r"\s+(?:apply|delete|replace|drain|cordon|exec|port-forward)\b",
    r"\bkubectl\b.*\bcreate\s+secret\b", r"\bhelm\s+(?:install|upgrade|uninstall|delete|rollback)\b",
    r"\bansible-playbook\b", r"\bdocker\s+(?:system|volume|image)\s+(?:prune|rm)\b", r"\bdocker\s+compose\s+down\b.*\s-v\b",
    # --- data and disks ------------------------------------------------------------------------------
    r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", r"\bTRUNCATE\s+TABLE\b", r"\bzfs\s+destroy\b", r"\bzpool\s+destroy\b",
    r"\bmkfs(?:\.\w+)?\b", r"\bwipefs\b", r"\bdd\s+if=\S+\s+of=/dev/",
    # --- cloud, secrets, access, DNS/TLS -----------------------------------------------------------
    r"\baws\s+s3\s+(?:rm|rb)\b", r"\bgsutil\b.*\brm\s+-r", r"\baws\s+iam\s+(?:attach|put|create|delete|update)\w*",
    r"\baz\s+role\s+assignment\b", r"\bgcloud\b.*\badd-iam-policy-binding\b",
    r"\baws\s+secretsmanager\s+(?:put|delete|create|update)\w*", r"\bvault\s+(?:kv\s+)?(?:put|delete|destroy)\b",
    r"\binfisical\s+secrets\s+(?:set|delete)\b", r"\bcertbot\b", r"\broute53\b.*\bchange-resource-record-sets\b",
    # --- recursive delete of a broad or unverifiable target (not `rm -f one-file`, not `rm -rf node_modules`) ---
    r"\brm\s+(?:-\w+\s+)*-\w*[rRf]\w*\s+(?:-\w+\s+)*(?:--\s+)?[\"']?(?:/|~|\$|\.\.?(?:/|\s|$)|\*)",
    # --- guards switched off, tunnels, instance metadata --------------------------------------------
    r"\bcurl\b[^\n]*\s(?:-k|--insecure)\b", r"--(?:no-verify|dangerously-skip-permissions|no-sandbox|yes-always)\b",
    r"\bngrok\b", r"\bcloudflared\s+tunnel\b", r"\bssh\b[^\n]*\s-R\b", r"169\.254\.169\.254",
]

WATCH_COMMANDS = [r"\b(?:ssh|scp|rsync|pct|qm)\s"]

_CRITICAL = [re.compile(p, re.I) for p in CRITICAL_COMMANDS]
_WATCH = [re.compile(p, re.I) for p in WATCH_COMMANDS]
_FILES = [re.compile(p, re.I) for p in CRITICAL_FILES]


def classify(command, extra=(), critical=None, watch=None):
    """``"critical"``, ``"watch"`` or None for a shell command line. ``critical``/``watch`` replace the defaults
    (regex strings); ``extra`` adds critical regexes."""
    text = str(command or "")
    crit = _CRITICAL if critical is None else [re.compile(p, re.I) for p in critical]
    crit = crit + [re.compile(p, re.I) for p in extra]
    if any(r.search(text) for r in crit):
        return "critical"
    wat = _WATCH if watch is None else [re.compile(p, re.I) for p in watch]
    return "watch" if any(r.search(text) for r in wat) else None


def is_critical_file(path, patterns=None):
    rx = _FILES if patterns is None else [re.compile(p, re.I) for p in patterns]
    return any(r.search(str(path or "")) for r in rx)
