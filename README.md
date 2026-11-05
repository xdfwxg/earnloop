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
3. **Human consent by design** — `propose` only stages a draft; nothing is sent until
   a human runs `approve`. Every approval is journaled.
4. **An honest ledger** — delivered ≠ paid. Money counts only with payment evidence.
   The agent cannot self-report income.
5. **Kill switch + watchdog** — `earnloop pause` structurally blocks outreach;
   a daily approval cap and follow-up escalation cap are enforced in code, not by promise.

Zero dependencies: Python 3.8+ stdlib only. No Node 24, no pip install, no daemon.
One file, one sqlite database + one append-only journal you can open with any tool.

## Quick start

```bash
git clone https://github.com/xdfwxg/earnloop && cd earnloop
./earnloop.py try                 # offline demo: 1 clean job + 1 scam, blocked live
./earnloop.py propose demo-clean  # stage a draft (NOT sent; secret-scanned)
./earnloop.py approve demo-clean  # human consent recorded
./earnloop.py mark demo-clean sent
./earnloop.py mark demo-clean won
./earnloop.py record "脏表清洗" 150 --evidence "shot: /tmp/delivery.png"
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
                              ↓ human runs `approve`  ← watchdog daily cap
                          send → follow-ups (remembered; 3 no-replies → needs-human)
                          won → record (delivery evidence REQUIRED)
                              → verify (payment evidence REQUIRED) → ledger.paid
```

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
| propose → approve consent flow | STABLE | journal + approvals table |
| secret scanning on outbound drafts | STABLE | common token/key formats |
| kill switch + daily watchdog cap | STABLE | structural, code-enforced |
| follow-up memory + escalation | STABLE | 3 no-replies → needs-human |
| evidence-required ledger | STABLE | record & verify both demand evidence |
| source win-rate feedback | STABLE | needs ≥3 approved attempts to nudge |
| `why <id>` trace / `stats` / `doctor` | STABLE | |
| botstreet source plugin | CONDITIONAL | needs `~/打工/chihiro-agent/accounts/botstreet.env`; degrades gracefully |
| more demand sources | NOT_SUPPORTED (yet) | PRs welcome — see below |

## Design rules / 设计铁律

1. Same state + same input = same decision. Time and randomness are injected.
2. The agent never sends money, never promises dates, never fakes identity.
3. Nothing leaves the machine without a recorded human approval.
4. Scam rules live in code (`SCAM_PATTERNS`) and are unit-tested.
5. Delivered is not paid. Only evidence-backed money enters the ledger.

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
