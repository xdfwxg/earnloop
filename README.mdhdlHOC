# earnloop

**The earning loop for AI agents — find real paid work, verify it, close it, keep an honest ledger.**
**AI 智能体的接活闭环——发现真需求、拦掉骗局、走完成交、记一本诚实的账。**

[![tests](https://github.com/OWNER/earnloop/actions/workflows/tests.yml/badge.svg)](tests)

## Why / 为什么做这个

Command-line agents are great at doing work but blind to *where paid work comes from*.
Task-loop frameworks (e.g. agent-autoloop) dispatch **your** tasks to an agent —
they never answer the real question: **谁付钱？哪单是真的？**

earnloop closes that gap with four things nobody else ships:

1. **Sources, not vibes** — marketplace/demand-board plugins, scored by budget vs. competition.
2. **Scam defense in code** — unfunded bounty repos, pay-to-work, off-platform escrow…
   hard-blocked, documented in [SCAMS.md](SCAMS.md) with real cases.
3. **Human consent by design** — `propose` only stages a draft; nothing is sent until
   a human runs `approve`. Every approval is recorded.
4. **An honest ledger** — `earnloop ledger` shows money actually received.
   No projections, no vanity numbers.

Zero dependencies: Python 3.8+ stdlib only. No Node 24, no pip install, no daemon.
One file, one sqlite database you can open with any tool.

## Quick start

```bash
git clone https://github.com/OWNER/earnloop && cd earnloop
./earnloop.py try                 # offline demo: 1 clean job + 1 scam, blocked live
./earnloop.py propose demo-clean  # stage a draft (NOT sent)
./earnloop.py approve demo-clean  # human consent recorded
./earnloop.py status && ./earnloop.py ledger
```

Point it at a real source:

```bash
export EARNLOOP_SOURCES=botstreet          # + your own plugin in SOURCES
./earnloop.py scan --top 10
```

## The loop

```
scan → score ──┬─ scam hit → blocked (recorded, why shown)
               └─ clean → propose (staged draft)
                              ↓ human runs `approve`
                          send via channel plugin → deliver → ledger.paid
```

## Design rules / 设计铁律

1. Same state + same input = same decision. Time and randomness are injected.
2. The agent never sends money, never promises dates, never fakes identity.
3. Nothing leaves the machine without a recorded human approval.
4. Scam rules live in code (`SCAM_PATTERNS`) and are unit-tested.

## Comparison / 与任务框架的差异

| | task-loop frameworks | earnloop |
|---|---|---|
| 派活执行 | ✅ 强项 | 交给你的 agent（任何能跑命令的） |
| **钱从哪来** | ❌ 不管 | ✅ 需求源插件 + 打分 |
| **防骗** | ❌ 无 | ✅ 代码级拦截 + 公开案例库 |
| **成交与记账** | ❌ 无 | ✅ propose/approve 流程 + ledger |
| 依赖 | Node 24 + TS 工程 | Python 3.8 标准库，单文件 |

两者互补：autoloop 管"干活的纪律"，earnloop 管"接活的生意"。

## Add a source / 加一个需求源

```python
def my_source():
    return [{"id": "x-1", "source": "x", "title": "...", "url": "...",
             "budget": "$50", "body": "..."}]
SOURCES["x"] = my_source
```

PRs welcome — especially new scam patterns (see SCAMS.md).

## License

MIT
