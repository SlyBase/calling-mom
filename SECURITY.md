# Security

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting
(Security tab -> "Report a vulnerability") of this repository. You get an answer within a few days.

## Threat model

The plugin runs inside a Hermes Kanban worker and talks to LLM providers. Assets and how they are protected:

| Threat | Why it matters | Control |
|---|---|---|
| **Card text is untrusted.** It can come from mail, web pages or other agents. | A card could ask the plugin to run a command or leak code. | `verify:` runs only for cards created by `verify.trusted_creators`, never through a shell (`shlex`), only commands whose first word is in `verify.allow`, with a scrubbed environment, on a background thread in its own process group (killed after `verify.timeout_s`, default 600 s). `python3 -c` is still arbitrary code: the creator check is the real control. |
| **Code and logs leave the machine** when the advisor asks a remote model. | Secrets in tool output or files. | The advisor is **off** until you enable it AND opt a board or card in. Every prompt passes Hermes' `redact_for_egress` plus a URL-credential scrub; `EgressRedactionTest` pins the shapes (GitHub/OpenAI/Anthropic/OpenRouter/AWS keys, bearer headers, password assignments, `scheme://user:pass@host`). Board tool results are filtered out. `[local-only]` on a card always wins. The redactor is pattern based: an unusual secret format can pass, so do not enable the advisor on boards that handle secrets in plain text. |
| **Pre-flight** asks a remote model before a critical command runs. | A command (or card text) could leak into the prompt, or a model could block a needed command. | Pre-flight is active **only where the advisor is** (board opt-in or card marker) and never on `[local-only]` cards, non-critical commands, or outside Kanban workers. The card and command are redacted before the question leaves. The log stores only the command hash, not the command. A HOLD is a persistent block that only a human release lifts — a model never releases its own hold; a timeout, a down provider, an unparseable answer or the cap lets the command run (fail-open). |
| **Mom answers worker questions** (`calling_mom.answer`, off by default). | With it on, a model answers a question a worker asked a person and **unblocks the card**; a wrong answer steers the worker, and the question, the card text and the last comments go to a remote model. | Own switch and own board list (`answer.enabled`, `answer.boards`), separate from the advisor. Only the worker's own `needs_input` block counts (a block by a person, or one Hermes re-kinded, is skipped). The fast path decides without a model: `[local-only]` and `[human]` cards are skipped; a question or card that matches the critical risk list or `rules.HUMAN_TOPICS` (credentials and secrets, payments and money, legal, personal data, publishing in the owner's name) and a card with an active pre-flight HOLD **never get an answer**, at most a brief for the person. The prompt is redacted like every other. At most `answer.max_per_card` per card and the advisor's daily caps; Olymp 1 per card and 6 per day. Error or timeout: the card stays blocked and unchanged, no retry. Mom can never release a HOLD: `answer_card(allow_once=True)` is for a person only (CLI `hermes calling-mom answer`). The log stores outcome, reason, counts and ids, never the question or the answer. |
| **Model override** lets a plugin call any model on your credentials. | Cost and data exposure. | Hermes grants it only through `plugins.entries.calling-mom.llm.allowed_models/allowed_providers`. `hermes calling-mom setup` only ever **adds** entries and shows what it writes. |
| **Runaway spend.** | A loop could call the expensive model. | Olymp: hard limits per card and per day (calls). Mom: per-card budgets, daily USD and token caps. Failures are fail-open (the worker continues). |
| **Observation log** | A log is a data store. | It stores hashes, counts and tool names, no content. |
| **Config** | A plugin that edits config is a risk. | Only the commands you run yourself (`setup`) write config, with the values you picked. Hooks never do. |

Out of scope: the security of Hermes itself and of the providers you configure.
