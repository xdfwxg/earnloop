#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
earnloop — the earning loop for AI agents.

Find paid work, verify it is real, quote it, close it, deliver it, and keep
an honest ledger. Zero dependencies: Python 3.8+ stdlib only.

    earnloop scan                 pull opportunities from every enabled source
    earnloop propose <id>         stage an outreach draft (never sends by itself)
    earnloop approve <id>         approve a staged proposal (human consent)
    earnloop status               what is happening right now
    earnloop ledger               money in / money expected, nothing hidden
    earnloop try                  offline demo, no network needed

Design rules (learned the hard way):
  1. Same state + same input = same decision. All randomness/time are injected.
  2. The agent NEVER sends money, NEVER promises a date, NEVER fakes identity.
  3. Nothing leaves the machine without a human approval record.
  4. Scam patterns live in SCAMS.md and are enforced in code, not in docs.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

VERSION = "0.1.0"
HOME = os.environ.get("EARNLOOP_HOME") or os.path.expanduser("~/.earnloop")
DB_PATH = os.path.join(HOME, "earnloop.db")

# --------------------------------------------------------------------------
# scam defense — every rule here is a real scam we (or the community) paid for
# --------------------------------------------------------------------------
SCAM_PATTERNS = [
    # (id, compiled regex on title+body, reason)
    ("unfunded", re.compile(r"every bounty is (unfunded|零奖金)|no cash prize", re.I),
     "仓库自述悬赏无资金"),
    ("placeholder-repo", re.compile(r"(discord\.gg/xxxx|replace.?with.?your|TODO:?\s*add link)", re.I),
     "README 含占位符，仓库未完成"),
    ("pay-to-work", re.compile(r"(先.{0,6}(充值|押金|保证金))|(deposit.{0,20}(before|first))|(训练.{0,4}费)", re.I),
     "要求先付钱才能开工"),
    ("off-platform-escrow", re.compile(r"(加.{0,3}(微信|QQ|telegram).{0,10}(私聊|转账))|(contact.{0,20}(whatsapp|telegram).{0,15}(payment|pay))", re.I),
     "引导脱离平台私下交易"),
    ("vague-huge-budget", re.compile(r"budget.{0,10}(unlimited|no limit|面议.{0,8}上不封顶)", re.I),
     "预算夸张不现实"),
]

SCAM_REPUTATION = {
    # flags that, in aggregate, mark a source as untrustworthy
    "mass_claim_zero_award": "大量随机名账号认领、0 派单记录",
    "new_repo_no_readme": "新仓库且无 README",
}


def scam_check(text):
    """Return list of scam hits for a title+body string."""
    hits = []
    for sid, pattern, reason in SCAM_PATTERNS:
        if pattern.search(text or ""):
            hits.append({"id": sid, "reason": reason})
    return hits


# --------------------------------------------------------------------------
# storage — one sqlite file, human-inspectable
# --------------------------------------------------------------------------
def db():
    os.makedirs(HOME, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS opportunities(
            id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
            budget TEXT, body TEXT, score REAL, scams TEXT,
            staged_at REAL, approved_at REAL, sent_at REAL,
            outcome TEXT DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            item TEXT, amount REAL, currency TEXT, status TEXT,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS approvals(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            opportunity_id TEXT, action TEXT, decided_by TEXT DEFAULT 'human'
        );
        """
    )
    return conn


def now():
    """Injectable clock slot (tests monkeypatch this)."""
    return time.time()


# --------------------------------------------------------------------------
# scoring — budget up, competition down, scams kill
# --------------------------------------------------------------------------
def score_opp(budget_text, body, competitor_hints=0):
    b = 0.0
    m = re.search(r"[\$￥¥€]\s?([0-9][0-9,.]*)", budget_text or "")
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
            b = v if "$" in (budget_text or "￥") else v / 7.2
        except ValueError:
            pass
    s = min(b / 50.0, 20.0)          # budget: cap at 20 pts
    s += 5.0                          # every listing starts with base credit
    s -= min(competitor_hints * 0.5, 10.0)   # competition penalty
    return round(s, 2)


# --------------------------------------------------------------------------
# sources — each returns list of dicts; network failures degrade gracefully
# --------------------------------------------------------------------------
def source_botstreet(cfg_path=None):
    cfg = {}
    path = cfg_path or os.path.expanduser("~/打工/chihiro-agent/accounts/botstreet.env")
    if not os.path.exists(path):
        return []
    for line in open(path, encoding="utf-8"):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            cfg[k] = v
    if "agentId" not in cfg:
        return []
    try:
        req = urllib.request.Request(
            cfg.get("base", "https://botstreet.io/api/v1") + "/posts?contentType=DEMAND&sort=newest&limit=30",
            headers={"x-agent-id": cfg["agentId"], "x-agent-key": cfg["agentKey"]})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        items = d.get("data") or []
        out = []
        for p in items:
            out.append({
                "id": "bs-" + str(p.get("id")),
                "source": "botstreet",
                "title": p.get("title") or "",
                "url": "https://botstreet.io/post/" + str(p.get("id")),
                "budget": str(p.get("reward") or p.get("budget") or "面议"),
                "body": (p.get("content") or "")[:800],
            })
        return out
    except Exception as e:
        sys.stderr.write("source botstreet degraded: %s\n" % e)
        return []


SOURCES = {"botstreet": source_botstreet}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_scan(args):
    conn = db()
    rows, degraded = [], 0
    for name in (args.sources.split(",") if args.sources else SOURCES.keys()):
        fn = SOURCES.get(name)
        if not fn:
            continue
        got = fn()
        if got == [] :
            degraded += 1
        rows.extend(got)
    added = 0
    for r in rows:
        text = (r.get("title", "") + " " + r.get("body", ""))
        scams = scam_check(text)
        s = score_opp(r.get("budget", ""), r.get("body", ""))
        if scams:
            s = 0.0
        cur = conn.execute("SELECT id FROM opportunities WHERE id=?", (r["id"],)).fetchone()
        if cur:
            continue
        conn.execute(
            "INSERT INTO opportunities(id,source,title,url,budget,body,score,scams,staged_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (r["id"], r["source"], r["title"], r["url"], r["budget"],
             r["body"], s, json.dumps(scams, ensure_ascii=False), now()))
        added += 1
    conn.commit()
    top = conn.execute(
        "SELECT id,source,score,title,budget,scams FROM opportunities"
        " WHERE outcome='open' AND approved_at IS NULL ORDER BY score DESC LIMIT ?",
        (args.top,)).fetchall()
    for t in top:
        flag = " ⚠️%s" % t["scams"] if t["scams"] not in ("[]", "", None) and t["scams"] != "[]" else ""
        print("%6.2f  %-9s %-34s %s%s" % (t["score"], t["source"], t["id"], t["title"][:40], flag))
    print("\n%d new, %d sources degraded. staged→propose, nothing is sent automatically." % (added, degraded))


def cmd_propose(args):
    conn = db()
    row = conn.execute("SELECT * FROM opportunities WHERE id=?", (args.id,)).fetchone()
    if not row:
        sys.exit("no such opportunity: %s" % args.id)
    if json.loads(row["scams"] or "[]"):
        sys.exit("blocked: this opportunity matched scam patterns %s — see SCAMS.md" % row["scams"])
    draft = ("你好，看到你的需求「%s」。我的方案：先免费出最小样稿/体检报告，"
             "你验证后再谈价；数据不出本机、附来源与可复现脚本；验收不合格免费返工。"
             " %s") % (row["title"], row["url"])
    conn.execute("UPDATE opportunities SET staged_at=? WHERE id=?", (now(), args.id))
    conn.execute("INSERT INTO approvals(ts,opportunity_id,action) VALUES(?,?,?)",
                 (now(), args.id, "staged"))
    conn.commit()
    print("staged (NOT sent). draft:\n---\n%s\n---" % draft)
    print("run: earnloop approve %s   # human consent required before sending" % args.id)


def cmd_approve(args):
    conn = db()
    row = conn.execute("SELECT * FROM opportunities WHERE id=?", (args.id,)).fetchone()
    if not row:
        sys.exit("no such opportunity: %s" % args.id)
    if not row["staged_at"]:
        sys.exit("propose first: earnloop propose %s" % args.id)
    conn.execute("UPDATE opportunities SET approved_at=? WHERE id=?", (now(), args.id))
    conn.execute("INSERT INTO approvals(ts,opportunity_id,action) VALUES(?,?,?)",
                 (now(), args.id, "approved_by_human"))
    conn.commit()
    print("approved by human at %s. The agent may now send via the channel plugin." % time.strftime("%F %T"))


def cmd_status(args):
    conn = db()
    for state, label in [("open", "open"), ("won", "WON"), ("lost", "lost")]:
        n = conn.execute("SELECT COUNT(*) c FROM opportunities WHERE outcome=?", (state,)).fetchone()["c"]
        print("%-8s %d" % (label, n))
    pend = conn.execute("SELECT COUNT(*) c FROM opportunities WHERE staged_at IS NOT NULL AND approved_at IS NULL").fetchone()["c"]
    print("staged (waiting human): %d" % pend)


def cmd_ledger(args):
    conn = db()
    rows = conn.execute("SELECT * FROM ledger ORDER BY ts DESC LIMIT 20").fetchall()
    if not rows:
        print("(empty — every entry here is a real payment, never a promise)")
    for r in rows:
        print("%s  %-28s %8.2f %s  %s" % (time.strftime("%F", time.localtime(r["ts"])),
              r["item"][:28], r["amount"], r["currency"], r["status"]))
    won = conn.execute("SELECT SUM(amount) s FROM ledger WHERE status='paid'").fetchone()["s"] or 0
    print("\ntotal paid: %.2f (honest number, zero inflation)" % won)


def cmd_try(args):
    """Offline demo: injects two opportunities (one clean, one scam) — no network."""
    conn = db()
    demo = [
        {"id": "demo-clean", "source": "demo", "title": "清洗 300 行客户表并合并两部门数据",
         "url": "https://example.org/clean", "budget": "￥150",
         "body": "把两个部门的表合并成一个，同单去重，日期统一格式。"},
        {"id": "demo-scam", "source": "demo", "title": "高薪急聘 加微信私聊 先交保证金",
         "url": "https://example.org/scam", "budget": "budget unlimited",
         "body": "先交保证金再开工，加微信转账，budget no limit"},
    ]
    for r in demo:
        scams = scam_check(r["title"] + " " + r["body"])
        s = 0.0 if scams else score_opp(r["budget"], r["body"])
        conn.execute("INSERT OR REPLACE INTO opportunities(id,source,title,url,budget,body,score,scams,staged_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?)",
                     (r["id"], r["source"], r["title"], r["url"], r["budget"], r["body"],
                      s, json.dumps(scams, ensure_ascii=False), now()))
    conn.commit()
    for row in conn.execute("SELECT id,score,title,scams FROM opportunities WHERE source='demo' ORDER BY score DESC"):
        verdict = "SCAM BLOCKED ⚠️" if row["scams"] != "[]" else "clean"
        print("%6.2f  %-32s %s" % (row["score"], row["title"][:32], verdict))
    print("\nuse `earnloop propose demo-clean` → `earnloop approve demo-clean` to walk the full consent flow.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="earnloop", description="the earning loop for AI agents")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--sources"); p.add_argument("--top", type=int, default=10); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("propose"); p.add_argument("id"); p.set_defaults(fn=cmd_propose)
    p = sub.add_parser("approve"); p.add_argument("id"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("ledger"); p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("try"); p.set_defaults(fn=cmd_try)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
