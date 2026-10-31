![Calling mom: Thetis rises from the sea to answer Achilles, Mount Olympus far above](assets/calling-mom-banner.jpg)

<sub>*Homer, Iliad I: the hero Achilles is stuck and calls his mother, the sea goddess Thetis, who rises from the sea to help; only once does she climb to Olympus to ask Zeus. The plugin works the same way. Your **main model** is Achilles: it does the work. When it is stuck or about to do something risky, it **calls mom** (Thetis), a good, affordable model that answers that one question, and she does so often. Only in a rare high-stakes case does mom say "this is too big for me" and send the question up to **olymp** (Zeus), the strongest and most expensive model, who is asked rarely and has a hard limit.*</sub>

# Calling mom

[![ci](https://github.com/SlyBase/calling-mom/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SlyBase/calling-mom/actions/workflows/ci.yml)

**Let a cheap model run your Kanban board. It calls mom when it needs help, and Olymp only when it really matters.**

A [Hermes](https://hermes-agent.nousresearch.com) plugin. Your everyday model (a local model, DeepSeek, Sonnet) does the work.
When it gets stuck or is about to do something risky, a stronger model looks over its shoulder. You pay for the big model
only in the rare moments it is worth it.

## Who is who

| | What it is | How often it is asked |
|---|---|---|
| **Your main model** | The model that works the cards. | All the time. It is the worker. |
| **Mom** | Your everyday helper. A good, affordable model (for example Sonnet or DeepSeek). | Often: whenever the main model is stuck, unsure, or about to do something risky. |
| **Olymp** | The expensive heavyweight (for example Opus). | Rarely. Only when Mom herself says "this one is too big for me". |

The main model never decides for itself that it needs help. A weak model cannot tell when it is out of its depth,
so the plugin watches for **signals** instead.

## When is Mom called?

| Signal | Who sends it | What Mom does |
|---|---|---|
| The main model **gives up** on a card ("capability" block), or the card keeps **crashing** | the main model / the board | The whole card is handed to Mom's model, once. |
| The **same error 3 times in a row** | the main model | A short tip: probable cause, next steps. |
| The **same command 3 times** (going in circles) | the main model | A short tip to get out of the loop. |
| **Broken tool calls 3 times** | the main model | A short tip. |
| A **risky command or file** (merge, release, force push, `terraform apply`, `kubectl delete`, `DROP TABLE`, `rm -rf` of broad folders, secrets, ...) | the main model | A review: the biggest risk, what to double-check. |
| A command on **another machine** (`ssh`, `scp`, `pct`, `qm`) | the main model | One short look per card. |
| **`SECOND-OPINION: <question>`** as a card comment (or the `second_opinion` tool) | the main model | Mom answers the question. |
| It says **"done"** | the main model | A check: does the result match what the card asked? Missing parts go back once. |
| Card marker **`[crit]`** | you | The card runs on Mom's model from the start. |
| Card marker **`[review]`** | you | A separate reviewer reads the result. |
| A **`verify: <command>`** line on the card | you | The command must pass before the card can finish. No model needed. |

Mom's answer appears **in the card** as a comment and is shown to the main model, which keeps working.

## When is Olymp called?

**Almost never.** Olymp is asked only if all of this is true:

1. The question is a **risky change** or a **`SECOND-OPINION`**. Stuck errors, loops and "done" checks never go to Olymp.
2. Mom answered first and wrote **`ESCALATE: <reason>`**: a high-stakes case she cannot judge as safe, an irreversible production or public action without a way back.
3. The limits are not used up: **1 Olymp question per card and 6 per day**.

Everything else, including routine `ssh` calls and ordinary reviews, is settled by Mom. If Olymp is down or the limit is
reached, the card simply carries on without his advice. (Advice arrives right after the command ran: it warns, it cannot undo.)

## Quick start

```bash
hermes plugins install SlyBase/calling-mom
hermes plugins enable calling-mom
hermes calling-mom setup     # pick Mom and Olymp from the providers and models you already have
hermes calling-mom doctor    # one real test call to each, tells you what is missing
```

`setup` shows a list of your configured providers and models (arrow keys; type `/` to filter by name), asks which
board may use the helpers, and writes exactly what you picked (it shows the summary first). Olymp stays off unless you choose one. Already
use *Advanced Delegation*? See [below](#optional-reuse-your-advanced-delegation-models).

No questions at all (scripts, provisioning, no terminal):

```bash
hermes calling-mom setup --mom openrouter/deepseek/deepseek-v4.1-flash --olymp none --boards '*'
hermes calling-mom setup --mom route:fast --olymp route:deep --no-cron
```

`--mom` and `--olymp` take `provider/model` or `route:NAME`; `--olymp none` switches Olymp off, leaving it out keeps
what is configured. Without a terminal, `setup` never waits: a missing flag ends it with exit code 2 and says what
to pass. `hermes calling-mom setup --preset subscription` still works too. `setup` also creates the sweep cron job
(`calling-mom: sweep - ...`, once; skip it with `--no-cron`).

### Optional: reuse your Advanced Delegation models

If you use the official [*Advanced Delegation*](https://hermes-agent.nousresearch.com/docs/plugins/advanced-delegation)
plugin, you already named your models there (`fast`, `deep`, `local` ...). `setup` reads those model routes and
offers them as the source for Mom and Olymp, so you do not pick provider and model a second time:

```bash
hermes calling-mom setup                                      # asks "one of your delegation routes, or by hand?"
hermes calling-mom setup --mom route:fast --olymp route:deep  # no questions
```

Good to know: the values are **copied once**, nothing stays linked, so changing a route later does not change
Mom or Olymp (run `setup` again for that). A route needs both a provider and a model; routes with only a
`base_url` are not offered. Without Advanced Delegation nothing changes, it is entirely optional.

| Command | What it does |
|---|---|
| `hermes calling-mom setup` | Choose Mom and Olymp from your own providers. Safe to repeat. |
| `hermes calling-mom status` | Who is Mom, who is Olymp, Olymp calls today, any failures. |
| `hermes calling-mom doctor` | Checks the setup and makes a real call to each model. Run it after every Hermes update. |

## What you will see in a card

```
[architecture/critical_change] run 68 · claude-sonnet-5-5 (claude-subscription-directsdk-experimental): 2719 in / 780 out
Biggest risk: the SSH keys now sit unencrypted in the scratch folder ... Double-check: delete them after the deploy ...
```

When Olymp answered, the comment says so: `claude-opus-5-5 (claude-subscription-directsdk-experimental, olymp)`.
If a helper cannot be reached (dead key, not logged in) you get **one** comment, never silence:
`[advisor unavailable] ... Check the setup: hermes calling-mom doctor`.

## Your money and your data

- **Nothing leaves your machine until you say so.** Helpers are off until you enable them and name a board (or put `[advisor]` on a card).
- **`[local-only]` on a card always wins**: nothing from that card is sent anywhere.
- Secrets (tokens, keys, passwords, credentials in URLs) are **blanked out** before anything is sent.
- Olymp is limited by **number of calls**, so a subscription never surprises you. Mom has a daily dollar and token cap.
- The plugin never changes your config by itself. Only `setup` does, and only with the values you picked.

More in [SECURITY.md](SECURITY.md).

## Minimal configuration

`setup` writes this for you. By hand, in `config.yaml`:

```yaml
calling_mom:
  mom:   {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
  olymp: {provider: claude-subscription-directsdk-experimental, model: claude-opus-5-5}   # leave empty for no Olymp
  advisor: {enabled: true, boards: [my-board]}
```

Hermes only lets a plugin use a model you have granted (`plugins.entries.calling-mom.llm.*`); `setup` does that too.
Kill switch: set `CALLING_MOM=off`. Every other option is in the [technical reference](docs/TECHNICAL.md).

## Something wrong?

| You see | Try |
|---|---|
| `[advisor unavailable]` in a card | `hermes calling-mom doctor` names the cause (key limit, login, missing grant). |
| Nothing happens at all | `hermes calling-mom status`: is a board opted in? Is the advisor on? |
| Two sweep cron jobs | `doctor` lists them. `setup` never creates a second one. |

## More

- [Technical reference](docs/TECHNICAL.md): all options, metrics, how the risk list was built, what was measured, development and release
- [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md)

MIT licensed.
