# Security

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting
(Security tab -> "Report a vulnerability") of this repository. You get an answer within a few days.

## Threat model

The plugin runs inside a Hermes Kanban worker and talks to LLM providers. Assets and how they are protected:

| Threat | Why it matters | Control |
|---|---|---|
| **Card text is untrusted.** It can come from mail, web pages or other agents. | A card could ask the plugin to run a command or leak code. | `verify:` runs only for cards created by `verify.trusted_creators`, never through a shell (`shlex`), only commands whose first word is in `verify.allow`, with a scrubbed environment. `python3 -c` is still arbitrary code: the creator check is the real control. |
| **Code and logs leave the machine** when the advisor asks a remote model. | Secrets in tool output or files. | The advisor is **off** until you enable it AND opt a board or card in. Every prompt passes Hermes' `redact_for_egress` plus a URL-credential scrub; `EgressRedactionTest` pins the shapes (GitHub/OpenAI/Anthropic/OpenRouter/AWS keys, bearer headers, password assignments, `scheme://user:pass@host`). Board tool results are filtered out. `[local-only]` on a card always wins. The redactor is pattern based: an unusual secret format can pass, so do not enable the advisor on boards that handle secrets in plain text. |
| **Model override** lets a plugin call any model on your credentials. | Cost and data exposure. | Hermes grants it only through `plugins.entries.calling-mom.llm.allowed_models/allowed_providers`. `hermes calling-mom setup` only ever **adds** entries and shows what it writes. |
| **Runaway spend.** | A loop could call the expensive model. | Olymp: hard limits per card and per day (calls). Mom: per-card budgets, daily USD and token caps. Failures are fail-open (the worker continues). |
| **Observation log** | A log is a data store. | It stores hashes, counts and tool names, no content. |
| **Config** | A plugin that edits config is a risk. | Only the commands you run yourself (`setup`) write config, after confirmation. Hooks never do. |

Out of scope: the security of Hermes itself and of the providers you configure.
