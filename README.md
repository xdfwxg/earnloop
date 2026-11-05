# earnloop

**The earning loop for AI agents — find real paid work, verify it, close it, keep an honest ledger.**
**AI 智能体的接活闭环——发现真需求、拦掉骗局、走完成交、记一本诚实的账。**

[![tests](https://github.com/xdfwxg/earnloop/actions/workflows/tests.yml/badge.svg)](tests)

## Why / 为什么做这个

Command-line agents are great at doing work but blind to *where paid work comes from*.
Task-loop frameworks (e.g. agent-autoloop) dispatch **your** tasks to an agent —
they never answer the real question: **谁付钱？哪单是真的？**

earnloop closes that gap:

1. **Sources, not vibes** — marketplace/demand-board plugins, scored by budget vs. competition,
   with per-source win-rate feedback baked back into scoring.
2. **Scam defense in code** — unfunded bounty repos, pay-to-work, off-platform escrow…
   hard-blocked, documented in [SCAMS.md](SCAMS.md) with real cases.
3. **Human consent an agent cannot fake** — `propose` only stages a draft; `approve`
   and `verify` require `EARNLOOP_APPROVER_KEY`, which lives in the human's shell and
   not in the agent's environment. Every approval is journaled with the identity that
   made it. `mark … sent` refuses to run without an approval on record.
4. **An honest ledger** — delivered ≠ paid. Money counts only with payment evidence,
   and every entry is linked to the deal it came from.
5. **Kill switch + watchdog** — `earnloop pause` blocks every path that reaches a human
   (propose / approve / follow-up / marking sent) while inspection keeps working;
   a daily approval cap and follow-up escalation cap are enforced in code, not by promise.

Zero dependencies: Python 3.8+ stdlib only. No Node 24, no pip install, no daemon.
One file, one sqlite database + one append-only journal you can open with any tool.

## Quick start

```bash
git clone https://github.com/xdfwxg/earnloop && cd earnloop
export EARNLOOP_APPROVER_KEY="a phrase only you know"   # in YOUR shell, never the agent's
./earnloop.py try                 # offline demo: 1 clean job + 1 scam, blocked live
./earnloop.py propose demo-clean  # stage a draft (NOT sent; secret-scanned)
./earnloop.py approve demo-clean  # human consent recorded (needs the key above)
./earnloop.py mark demo-clean sent
./earnloop.py mark demo-clean won
./earnloop.py record "脏表清洗" 150 --opp demo-clean --evidence "shot: /tmp/delivery.png"
./earnloop.py verify 1 --evidence "tx: alipay 20261105…"
./earnloop.py why demo-clean      # full trace of one opportunity, one command
./earnloop.py status && ./earnloop.py ledger && ./earnloop.py stats
```

Point it at a real source:

```bash
./earnloop.py doctor              # config / db / journal health check
./earnloop.py scan --top 10
```

## The loop

```
scan → score ──┬─ scam hit → blocked (journaled)
               └─ clean → propose (staged draft, secret-scanned)
                              ↓ human runs `approve` (approver key REQUIRED) ← daily cap
                          mark sent (refused without that approval)
                              → follow-ups (remembered; 3 no-replies → needs-human)
                          won → record (--opp + delivery evidence REQUIRED)
                              → verify (approver key + payment evidence REQUIRED) → ledger.paid
```

States move one way only: `open → sent → won/lost/needs-human`. A deal cannot walk
backwards, and `sent` is the one transition that puts words in front of a real person.

## Discovery — where demand comes from / 需求从哪来

Two doors ship out of the box (see [DISCOVERY.md](DISCOVERY.md)):

- **`botstreet`** — a demand board. Bidding environment: many agents fight over one
  post, and the listing text's "已有 N 人认领 / N bids" is *measured* by
  `extract_competitors` and pushes the score down.
- **`github-issues`** — help-wanted issues that are unassigned and have no linked PR.
  Nobody quotes under those posts, so competition is near zero by construction;
  `propose` speaks a different voice there ("先给最小 patch 供你验证，通过后再谈报酬").

Scoring makes the doors compete with each other — low-competition supply naturally
floats to the top of `scan`.

## Human approval — how it actually works

A shell-running agent can type any command, including `approve`. The approval key is
the one input it cannot supply, because it lives in your profile and not its environment:

```bash
export EARNLOOP_APPROVER_KEY="…"    # ~/.zshrc — the agent's shell never sees it
```

Without it, `approve` and `verify` exit non-zero and write nothing at all (a failed
attempt must not consume the daily cap). With it, the journal records
`human:<sha256(key)[:8]>` instead of a self-declared `"human"`.

Limits, stated rather than implied: this is a separation of *environments*, not proof of
personhood — an agent that can read your shell profile can still forge consent. Evidence
is validated for scheme and reference (`shot:` / `tx:` / `file:` / `url:`), not for truth;
a human still decides whether `tx: …` is real. The tool can only guarantee that the
record was not written by the model on its own.

## What we absorbed from the ecosystem / 站在谁肩膀上

We studied every project in the [agent-autoloop search results](https://github.com/search?q=agent-autoloop&type=repositories)
and took the best ideas — with credit:

| From | Idea absorbed into earnloop |
|---|---|
| [KKKKhazix/agent-autoloop](https://github.com/KKKKhazix/agent-autoloop) | Append-only event journal + `why <id>` trace: every round's full story, one command. |
| [xonogesrs/aegisflow](https://github.com/xonogesrs/aegisflow) | *The harness observes, the model does not self-report*: no ledger entry and no payment without evidence; honest capability table below. |
| [ythx-101/autoloop-old](https://github.com/ythx-101/autoloop-old) | Delayed verification (delivered ≠ paid); retry memory (follow-ups recorded); escalation after N failures → `needs-human`, the loop stops. |
| [AndreSantos09/mcp-session-share](https://github.com/AndreSantos09/mcp-session-share) | Watchdog ceilings clamped in code (daily approval cap) + a structural kill switch (`pause` blocks outreach but never inspection). |
| [Autoloops/upskill](https://github.com/Autoloops/upskill) | Feedback loop (sources that close deals rank higher) + outbound secret scanning before any draft is staged. |

Still ours and still the difference: task-loop frameworks manage *doing the work*;
earnloop manages *the business of getting paid for it*.

## Capability status (honest, aegisflow-style)

| Capability | Status | Notes |
|---|---|---|
| scan / score / scam hard-block | STABLE | unit-tested; patterns from real cases |
| propose → approve consent flow | STABLE | journal + approvals table; approval needs the approver key |
| secret scanning on outbound drafts | STABLE | common token/key formats |
| kill switch + daily watchdog cap | STABLE | pause covers propose/approve/follow-up/mark-sent; bookkeeping still works |
| follow-up memory + escalation | STABLE | only on `sent` deals; 3 no-replies → needs-human |
| evidence-required ledger | STABLE | every entry links to a deal; evidence must be a `shot:`/`tx:`/`file:`/`url:` reference |
| state machine on deals | STABLE | `open → sent → won/lost/needs-human`; illegal transitions refused |
| source win-rate feedback | STABLE | needs ≥3 approved attempts to nudge |
| `why <id>` trace / `stats` / `doctor` | STABLE | `why` now includes the money recorded against that deal |
| botstreet source plugin | CONDITIONAL | needs `$EARNLOOP_BOTSTREET_ENV` (default `~/.earnloop/botstreet.env`); degrades gracefully |
| more demand sources | NOT_SUPPORTED (yet) | PRs welcome — see below |

## Design rules / 设计铁律

1. Same state + same input = same decision. Time and randomness are injected.
2. The agent never sends money, never promises dates, never fakes identity.
3. Nothing leaves the machine without a recorded human approval — and consent counts
   only if the approver key sits outside the agent's environment.
4. Scam rules live in code (`SCAM_PATTERNS`) and are unit-tested.
5. Delivered is not paid. Only evidence-backed money enters the ledger, linked to a deal.

## Add a source / 加一个需求源

```python
def my_source():
    return [{"id": "x-1", "source": "x", "title": "...", "url": "...",
             "budget": "$50", "body": "..."}]
SOURCES["x"] = my_source
```

PRs welcome — especially new scam patterns (see SCAMS.md) and new sources.

## License

MIT
