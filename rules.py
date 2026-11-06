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

Two tiers. **critical** = irreversible or public, reviewed with its own budget; since 1.4 it is asked BEFORE it runs
(pre-flight). It includes quitting an app a person may be using and copying into a Library folder (1.5, audit t_de8aec31).
**watch** = privileged WRITE access to another machine (ssh, pct, qm): worth one look, but it must never use up the budget
of the critical tier. A command that only reads (``ls``, ``cat``, ``grep``, ``pgrep``...) is no watch at all (``read_only``).
Add your own with ``categories.architecture.commands_extra`` (regexes); nothing here needs editing.
"""
import re
import shlex

_FLAGS = r"(?:\s+-{1,2}[\w=./:@-]+(?:\s+[\w=./:@-]+)?){0,8}"  # kubectl -n prod / --context p / git -C dir (loose)


def _gap(stop, chars=r"[^\n]"):
    """Up to 256 characters of one line that do not contain another `stop` word. Bounded, so a long line cannot make
    the regex slow; stopping at the next `stop` word, so each start scans only to its neighbour (linear, not cubic)."""
    return rf"(?:(?!\b(?:{stop})\b){chars}){{0,256}}"


NO_SEP = r"[^\n;&|]"  # one simple command
_COPY = "cp|mv|rsync|scp|ditto|ln"
# A Library folder (the person's, or the system's) as a target: `~/Library/`, `$HOME/Library/`, `/Users/me/Library/`,
# `/Library/`, also after `host:`. It must follow at least one other argument, so `cp ~/Library/a /tmp/` (a read) is no hit.
_LIBRARY = r"\s(?:[\w.@-]{1,64}:)?[\"']?(?:~|\$HOME|\$\{HOME\}|/Users/[\w.-]{1,64})?/Library/"

_GH_WRITE = r"(?:-X\s*|--request[= ]\s*|--method[= ]\s*)(?:PUT|POST|PATCH|DELETE)\b"

CRITICAL_FILES = [
    r"\.tf$", r"migrations?/", r"(^|/)Dockerfile$", r"docker-compose", r"\.github/workflows/",
    r"(^|/)(k8s|kubernetes|manifests|helm|charts)/.*\.ya?ml$", r"(^|/)(ansible|playbooks)/.*\.ya?ml$",
]

CURL = "curl|wget"
NO_PIPE = r"[^\n|;]"

CRITICAL_COMMANDS = [
    # --- download and execute -------------------------------------------------------------------------
    rf"\b(?:curl|wget)\b{_gap(CURL, NO_PIPE)}\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", r"\bbash\s+<\(\s*(?:curl|wget)\b",
    # --- git: public or irreversible ----------------------------------------------------------------
    rf"\bgit\b{_gap('git')}\bpush\b{_gap('git')}(?:--force\b|\s-f\b|--delete\b|\s:\S)", rf"\bgit\b{_gap('git')}\bpush\b{_gap('git')}\b(?:main|master)\b",
    r"\bgit\s+tag\b(?!\s+(?:-l\b|--list\b))\s+\S",
    r"\bgit\s+reset\s+--hard\b", r"\bgit\s+checkout\s+--\s+\.", r"\bgit\s+restore\s+\.(?:\s|$)",
    r"\bgit\s+clean\s+-\w*f", r"\bgit\s+stash\s+(?:drop|clear)\b", rf"\bgit\s+commit\b{_gap('git')}--amend\b",
    r"\bgit\s+branch\s+(?-i:-D)\b", r"\bgit\s+remote\s+(?:add|set-url)\b",
    # --- GitHub: merge, release, bot commands, access ----------------------------------------------
    r"\bgh\s+pr\s+(?:merge|close)\b", r"\bgh\s+release\s+(?:create|delete|upload|edit)\b", r"\bgh\s+workflow\s+run\b",
    r"\bgh\s+repo\s+(?:delete|fork|create|edit|archive)\b", r"\bgh\s+(?:issue|pr)\s+comment\b",
    rf"\bgh\s+api\b{_gap('gh')}" + _GH_WRITE, rf"\bgh\s+api\b{_gap('gh')}/(?:merge|releases|comments|dispatches|collaborators)\b",
    rf"\b(?:curl|wget)\b{_gap(CURL)}" + _GH_WRITE + rf"{_gap(CURL)}api\.github\.com",
    rf"\b(?:curl|wget)\b{_gap(CURL)}api\.github\.com{_gap(CURL)}" + _GH_WRITE,
    # --- infrastructure ----------------------------------------------------------------------------
    r"\b(?:terraform|tofu)\s+(?:apply|destroy)\b", r"\b(?:pulumi|cdk|terragrunt)\s+destroy\b",
    r"\bkubectl\b" + _FLAGS + r"\s+(?:apply|delete|replace|drain|cordon|exec|port-forward)\b",
    rf"\bkubectl\b{_gap('kubectl')}\bcreate\s+secret\b", r"\bhelm\s+(?:install|upgrade|uninstall|delete|rollback)\b",
    r"\bansible-playbook\b", r"\bdocker\s+(?:system|volume|image)\s+(?:prune|rm)\b", rf"\bdocker\s+compose\s+down\b{_gap('docker')}\s-v\b",
    # --- data and disks ------------------------------------------------------------------------------
    r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", r"\bTRUNCATE\s+TABLE\b", r"\bzfs\s+destroy\b", r"\bzpool\s+destroy\b",
    r"\bmkfs(?:\.\w+)?\b", r"\bwipefs\b", r"\bdd\s+if=\S+\s+of=/dev/",
    # --- cloud, secrets, access, DNS/TLS -----------------------------------------------------------
    r"\baws\s+s3\s+(?:rm|rb)\b", rf"\bgsutil\b{_gap('gsutil')}\brm\s+-r", r"\baws\s+iam\s+(?:attach|put|create|delete|update)\w*",
    r"\baz\s+role\s+assignment\b", rf"\bgcloud\b{_gap('gcloud')}\badd-iam-policy-binding\b",
    r"\baws\s+secretsmanager\s+(?:put|delete|create|update)\w*", r"\bvault\s+(?:kv\s+)?(?:put|delete|destroy)\b",
    r"\binfisical\s+secrets\s+(?:set|delete)\b", r"\bcertbot\b", rf"\broute53\b{_gap('route53')}\bchange-resource-record-sets\b",
    # --- recursive delete of a broad or unverifiable target (not `rm -f one-file`, not `rm -rf node_modules`) ---
    r"\brm\s+(?:-\w+\s+)*-\w*[rRf]\w*\s+(?:-\w+\s+)*(?:--\s+)?[\"']?(?:/|~|\$|\.\.?(?:/|\s|$)|\*)",
    # --- quit an app a person may be using; copy or link into a Library folder (also over ssh) ---------------
    rf"\bosascript\b{_gap('osascript', r'[\s\S]')}\bquit\b", r"\b(?:killall|pkill)\b",
    rf"\b(?:{_COPY})\b(?:\s+-{{1,2}}[\w=./:@-]{{1,64}}){{0,8}}\s+(?!-)\S{{1,256}}{_gap(_COPY, NO_SEP)}{_LIBRARY}",
    # --- guards switched off, tunnels, instance metadata --------------------------------------------
    rf"\bcurl\b{_gap(CURL)}\s(?:-k|--insecure)\b", r"--(?:no-verify|dangerously-skip-permissions|no-sandbox|yes-always)\b",
    r"\bngrok\b", r"\bcloudflared\s+tunnel\b", rf"\bssh\b{_gap('ssh')}\s-R\b", r"169\.254\.169\.254",
]

WATCH_COMMANDS = [r"\b(?:ssh|scp|rsync|pct|qm)\s"]

# What only looks: the first word of every simple command must be one of these (a second word where it matters).
_READ = frozenset("""pgrep ps ls cat head tail grep egrep fgrep wc echo printf pwd id whoami uname date df du stat file which
    sort uniq cut tr basename dirname readlink hostname uptime sw_vers free lsblk lscpu journalctl dmesg sleep true false test [ cd for done fi case esac in find sed awk curl wget""".split())
_READ_SUB = {"pct": {"list", "status", "config"}, "qm": {"list", "status", "config"}}
_SSH_VALUE = frozenset("-b -c -D -E -e -F -I -i -J -L -l -m -O -o -p -Q -R -S -W -w".split())  # options that take a value
_SEGMENT_START = frozenset({"do", "then", "else", "elif", "if", "while", "!", "{"})
_WRITE_FLAGS = {"sed": re.compile(r"^-\w*i"), "find": re.compile(r"^-(?:delete|exec|execdir|ok|fprint\w*|fls)$"),
                "curl": re.compile(r"^(?:-\w*[oOdTFX]|--(?:output|remote-name|data\S*|upload-file|form|request)\b)"),
                "wget": re.compile(r"^(?:-\w*[OP]|--(?:output-document|directory-prefix|post-data|post-file)\b)")}
_HARMLESS_REDIRECT = re.compile(r"(?:\d|&)?>>?\s{0,4}(?:/dev/null|&\d)\b")  # `2>/dev/null`, `>/dev/null`, `2>&1`


def read_only(command, _depth=0) -> bool:
    """True when ``command`` (a shell line, also through ``ssh host '...'``, ``pct exec N -- ...``, ``timeout 60 ...``)
    only looks: every simple command starts with a reading word, no output goes to a file, nothing else runs.
    Unknown, unparseable or longer than 4000 characters is not read-only (a review is cheaper than a missed write)."""
    text = str(command or "")
    if not text.strip() or len(text) > 4000 or _depth > 3:  # ponytail: ceiling; a longer line is always reviewed
        return False
    text = _HARMLESS_REDIRECT.sub(" ", text.replace("\n", " ; "))
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split, lex.commenters = True, ""  # a `#` is a word here: a comment must not hide a later write
    try:
        words = list(lex)
    except ValueError:
        return False
    if any(">" in w for w in words):  # a redirect that survived writes a file
        return False
    segments, current = [], []
    for w in words:
        if set(w) <= set("();|&") and w:  # a separator
            segments.append(current)
            current = []
        else:
            current.append(w)
    return all(_segment_reads(seg, _depth) for seg in segments + [current])


def _segment_reads(words, depth) -> bool:
    while words and (re.fullmatch(r"\w+=\S*", words[0]) or words[0] in _SEGMENT_START | {"time"}):
        words = words[1:]
    if not words:  # empty (`;;`, `$(`...): nothing runs here
        return True
    head, rest = words[0], words[1:]
    if head == "timeout" and rest and re.fullmatch(r"\d+[smhd]?", rest[0]):
        return _segment_reads(rest[1:], depth) if rest[1:] else False
    if head == "ssh":
        args = list(rest)
        while args and args[0].startswith("-"):
            args = args[2:] if args[0] in _SSH_VALUE else args[1:]
        return len(args) > 1 and read_only(" ".join(args[1:]), depth + 1)  # args[0] is the host
    if head == "pct" and rest[:1] == ["exec"]:
        return "--" in rest and read_only(" ".join(rest[rest.index("--") + 1:]), depth + 1)
    if head in _READ_SUB:
        return rest[:1] != [] and rest[0] in _READ_SUB[head]
    flag = _WRITE_FLAGS.get(head)
    return head in _READ and not (flag and any(flag.search(a) for a in rest))


_CRITICAL = [re.compile(p, re.I) for p in CRITICAL_COMMANDS]
_WATCH = [re.compile(p, re.I) for p in WATCH_COMMANDS]
_FILES = [re.compile(p, re.I) for p in CRITICAL_FILES]


# Questions only the owner can answer (WP3): when the question or the card touches one, Mom never answers, she may
# only write a brief. Words are matched whole, so "tokenizer" is not a credential. Deliberately broad: a false hit
# costs the owner one question, a miss lets Mom decide something that is not hers.
HUMAN_TOPICS = {
    "credentials": [r"\b(?:passwords?|passphrases?|credentials?|secrets?|api[ _-]?keys?|access[ _-]?tokens?|auth[ _-]?tokens?|"
                    r"private[ _-]?keys?|ssh[ _-]?keys?|2fa|mfa|one-time (?:code|password))\b"],
    "money": [r"\b(?:pay|payments?|invoices?|billing|purchase|buy|refunds?|credit card|iban|wire transfer)\b",
              r"[$€£]\s?\d", r"\b\d+(?:[.,]\d+)?\s?(?:usd|eur|dollars?|euros?)\b"],
    "legal": [r"\b(?:contracts?|legal|lawyers?|attorney|nda|gdpr|terms of service|license agreements?|liability|lawsuit)\b"],
    "personal_data": [r"\b(?:personal data|pii|home address(?:es)?|passports?|social security|medical records?|health data|"
                      r"date of birth)\b"],
    "publishing": [r"\b(?:publish|announce|tweet|press release|on my behalf|in my name|in your name|on your behalf)\b",
                   r"\bpost\b[^\n]{0,40}\b(?:on|to)\s+(?:x|twitter|linkedin|reddit|hacker news|mastodon)\b",
                   r"\bsend\b[^\n]{0,20}\be-?mails?\s+to\b"],
}
_HUMAN = {topic: [re.compile(p, re.I) for p in patterns] for topic, patterns in HUMAN_TOPICS.items()}


def human_topic(text):
    """Name of the first owner-only topic in ``text`` (see ``HUMAN_TOPICS``), or None."""
    text = str(text or "")
    return next((topic for topic, rx in _HUMAN.items() if any(r.search(text) for r in rx)), None)


def classify(command, extra=(), critical=None, watch=None):
    """``"critical"``, ``"watch"`` or None for a shell command line. ``critical``/``watch`` replace the defaults
    (regex strings); ``extra`` adds critical regexes."""
    text = str(command or "")
    crit = _CRITICAL if critical is None else [re.compile(p, re.I) for p in critical]
    crit = crit + [re.compile(p, re.I) for p in extra]
    if any(r.search(text) for r in crit):
        return "critical"
    wat = _WATCH if watch is None else [re.compile(p, re.I) for p in watch]
    return "watch" if any(r.search(text) for r in wat) and not read_only(text) else None


def is_critical_file(path, patterns=None):
    rx = _FILES if patterns is None else [re.compile(p, re.I) for p in patterns]
    return any(r.search(str(path or "")) for r in rx)
