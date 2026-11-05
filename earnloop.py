#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
earnloop — the earning loop for AI agents.

Find paid work, verify it is real, quote it, close it, deliver it, and keep
an honest ledger. Zero dependencies: Python 3.8+ stdlib only.

    earnloop scan                 pull opportunities from every enabled source
    earnloop propose <id>         stage an outreach draft (never sends by itself)
    earnloop approve <id>         approve a staged proposal (approver key REQUIRED)
    earnloop mark <id> <state>    sent / won / lost / needs-human
    earnloop followup <id>        record a follow-up (3 no-replies → needs-human)
    earnloop record <item> <amt> --opp <id>   ledger entry (evidence REQUIRED)
    earnloop verify <ledger-id>   money received (approver key + evidence REQUIRED)
    earnloop why <id>             full trace of one opportunity (event journal)
    earnloop stats                per-source win rates (feedback into scoring)
    earnloop pause / resume       kill switch: block new outreach, keep inspection
    earnloop doctor               check config, db, journal health
    earnloop status [--json]      what is happening right now
    earnloop ledger               money in / money expected, nothing hidden
    earnloop try                  offline demo, no network needed

Design rules (learned the hard way):
  1. Same state + same input = same decision. All randomness/time are injected.
  2. The agent NEVER sends money, NEVER promises a date, NEVER fakes identity.
  3. Nothing leaves the machine without a human approval record.
  4. Scam patterns live in SCAMS.md and are enforced in code, not in docs.
  5. Delivered is not paid. Money is counted only with payment evidence.
  6. Consent only counts as human if the approver key lives outside the agent's
     environment. A shell-running agent can type any command — not that key.

Ideas absorbed from the ecosystem (credit where due):
  - KKKKhazix/agent-autoloop: append-only event journal + `why` trace
  - xonogesrs/aegisflow:     evidence over self-report; honest status table
  - ythx-101/autoloop-old:   delayed verification; retry memory; escalation
  - AndreSantos09/mcp-session-share: watchdog ceilings + always-available stop
  - Autoloops/upskill:       source feedback loop; outbound secret scanning
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.parse

VERSION = "0.2.2"
HOME = os.environ.get("EARNLOOP_HOME") or os.path.expanduser("~/.earnloop")
DB_PATH = os.path.join(HOME, "earnloop.db")
JOURNAL_PATH = os.path.join(HOME, "events.jsonl")
PAUSE_FLAG = os.path.join(HOME, "PAUSED")

# The approver key belongs in the human's own shell profile. It is deliberately
# NOT read from any config file this tool writes, so an agent that can run
# `earnloop` still cannot manufacture a human approval.
APPROVER_KEY_ENV = "EARNLOOP_APPROVER_KEY"

# demand source credentials live outside the repo, path configurable
BOTSTREET_ENV = (os.environ.get("EARNLOOP_BOTSTREET_ENV")
                 or os.path.join(HOME, "botstreet.env"))

# watchdog ceilings (lesson from mcp-session-share: server-clamped limits,
# not agent-honored promises)
MAX_PROPOSALS_PER_DAY = int(os.environ.get("EARNLOOP_MAX_PROPOSALS_PER_DAY", "10"))
FOLLOWUP_ESCALATION = int(os.environ.get("EARNLOOP_FOLLOWUP_ESCALATION", "3"))

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

# outbound secret scan (lesson from upskill: scan payloads before they leave
# the machine — a draft that leaks credentials must never be staged)
SECRET_PATTERNS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "API key (sk-…)"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"), "credential key=value"),
]

SCAM_REPUTATION = {
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


def secret_check(text):
    """Return list of (kind, matched) secrets found in outbound text."""
    out = []
    for pat, kind in SECRET_PATTERNS:
        m = pat.search(text or "")
        if m:
            out.append((kind, m.group(0)[:6] + "…"))
    return out


# --------------------------------------------------------------------------
# human identity + evidence — the two things an agent must not be able to fake
# (aegisflow lesson: authority and evidence live outside the model's process)
# --------------------------------------------------------------------------
EVIDENCE_RE = re.compile(r"^(shot|tx|file|url):\s*(\S.{2,})$")


def approver_identity():
    """Return an identity string for the human approver, or None if no key."""
    key = os.environ.get(APPROVER_KEY_ENV, "").strip()
    if not key:
        return None
    return "human:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def require_human(action):
    """Refuse to record consent that the agent itself could have produced."""
    who = approver_identity()
    if not who:
        sys.exit("blocked: `%s` records human consent, so it needs %s set in YOUR "
                 "shell (not in the agent's environment). "
                 "export %s=\"<a phrase only you know>\" then retry."
                 % (action, APPROVER_KEY_ENV, APPROVER_KEY_ENV))
    return who


def check_evidence(evidence, kind):
    """Evidence must be a reference someone else can re-check, not a claim."""
    if not evidence:
        sys.exit("blocked: --evidence is REQUIRED for %s "
                 "(e.g. `tx: alipay 20261105…`, `shot: ./delivery.png`). "
                 "No evidence, no record — self-reported facts are fiction." % kind)
    m = EVIDENCE_RE.match(evidence.strip())
    if not m:
        sys.exit("blocked: evidence must name a scheme so it can be re-checked — "
                 "shot: / tx: / file: / url:. got: %r" % evidence)
    scheme, ref = m.group(1), m.group(2).strip()
    if scheme == "file" and not os.path.exists(os.path.expanduser(ref)):
        sys.exit("blocked: evidence file does not exist: %s" % ref)
    return evidence.strip()


# --------------------------------------------------------------------------
# event journal — append-only, one JSON object per line (autoloop lesson:
# any round's history must be answerable with one command)
# --------------------------------------------------------------------------
def journal_write(event_type, opp_id=None, detail=""):
    os.makedirs(HOME, exist_ok=True)
    rec = {"ts": now(), "type": event_type, "opp": opp_id, "detail": detail}
    with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def journal_read():
    if not os.path.exists(JOURNAL_PATH):
        return []
    out = []
    with open(JOURNAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # tolerate a torn last line after a crash
    return out


def journal_for(opp_id):
    return [e for e in journal_read() if e.get("opp") == opp_id]


def proposals_today():
    """Count approvals issued today (local calendar day)."""
    today = time.strftime("%Y-%m-%d", time.localtime(now()))
    n = 0
    for e in journal_read():
        if e.get("type") == "approved" and time.strftime("%Y-%m-%d", time.localtime(e["ts"])) == today:
            n += 1
    return n


def guard_paused():
    """Kill switch. Blocks every path that can reach a human, never inspection."""
    if os.path.exists(PAUSE_FLAG):
        sys.exit("blocked: kill switch is ON (PAUSED). run `earnloop resume` first.")


def guard_outreach(action, opp_id):
    """Kill switch + watchdog gate, for paths that allocate a NEW outreach."""
    guard_paused()
    if proposals_today() >= MAX_PROPOSALS_PER_DAY:
        journal_write("watchdog_block", opp_id,
                      "daily cap %d reached" % MAX_PROPOSALS_PER_DAY)
        sys.exit("blocked: watchdog — already approved %d proposals today (cap %d). "
                 "raise EARNLOOP_MAX_PROPOSALS_PER_DAY or wait." % (proposals_today(), MAX_PROPOSALS_PER_DAY))
    journal_write(action, opp_id)


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
            opportunity_id TEXT,
            item TEXT, amount REAL, currency TEXT, status TEXT,
            note TEXT, evidence TEXT
        );
        CREATE TABLE IF NOT EXISTS approvals(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
            opportunity_id TEXT, action TEXT, decided_by TEXT
        );
        """
    )
    # migrations (tolerate re-runs)
    try:
        conn.execute("ALTER TABLE opportunities ADD COLUMN attempts INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE opportunities ADD COLUMN last_note TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE ledger ADD COLUMN opportunity_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def now():
    """Injectable clock slot (tests monkeypatch this)."""
    return time.time()


# --------------------------------------------------------------------------
# scoring — budget up, competition down, scams kill, source history nudges
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


def source_stats(conn):
    """Per-source funnel: listed / approved / won. (upskill lesson: skills
    that work should rise; sources that close should rank higher.)"""
    rows = conn.execute(
        "SELECT source, COUNT(*) n,"
        " SUM(CASE WHEN approved_at IS NOT NULL THEN 1 ELSE 0 END) ap,"
        " SUM(CASE WHEN outcome='won' THEN 1 ELSE 0 END) wn"
        " FROM opportunities GROUP BY source").fetchall()
    return {r["source"]: {"listed": r["n"], "approved": r["ap"] or 0, "won": r["wn"] or 0}
            for r in rows}


def apply_source_feedback(score, stats):
    """Nudge score by this source's real win history. Conservative:
    needs >=3 approved attempts before it can move the needle at all."""
    st = stats or {}
    if st.get("approved", 0) < 3:
        return score
    win = st["won"] / float(st["approved"])
    if win >= 0.3:
        return round(score * 1.2, 2)
    if win == 0.0:
        return round(score * 0.7, 2)
    return score


# --------------------------------------------------------------------------
# sources — each returns list of dicts; network failures degrade gracefully
# --------------------------------------------------------------------------
def source_botstreet(cfg_path=None):
    cfg = {}
    path = cfg_path or BOTSTREET_ENV
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


# competitor signals actually measured in listing text (review lesson: the
# README claimed budget-vs-competition scoring while competition was never read)
COMPETITOR_RE = re.compile(
    r"(\d{1,4})\s*(?:人|位|个)?\s*(?:已经?)?\s*(?:认领|投标|报名|申请|接单|bids?|applicants?|claimed)", re.I)


def extract_competitors(text):
    """Best (max) competitor count found in a listing's title+body; 0 if none."""
    n = 0
    for m in COMPETITOR_RE.finditer(text or ""):
        try:
            n = max(n, int(m.group(1)))
        except ValueError:
            continue
    return n


# --------------------------------------------------------------------------
# discovery source: GitHub help-wanted issues (the low-competition door).
# A bidding board is an auction: N people fight over one post. A help-wanted
# issue is a door nobody knocks on — the asker wants a fix, most readers only
# lurk demand boards, so competition is near zero by construction. See DISCOVERY.md.
# --------------------------------------------------------------------------
def source_github_issues(cfg=None):
    # lesson from the first live run: `-assigned` is not a valid search
    # qualifier and silently poisons the whole query (0 hits / 422);
    # `no:assignee` is the correct form.
    q = ('label:help-wanted state:open type:issue -linked:pr '
         'no:assignee created:>={since}').format(
             since=time.strftime("%Y-%m-%d", time.localtime(now() - 30 * 86400)))
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "earnloop"}
    tok = cfg or os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = "Bearer " + tok
    try:
        url = ("https://api.github.com/search/issues?per_page=20&sort=created&order=desc&q="
               + urllib.parse.quote(q))
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=20).read().decode())
        out = []
        for it in d.get("items", []):
            body = (it.get("body") or "")[:800]
            out.append({
                "id": "gh-issue-" + str(it.get("id")),
                "source": "github-issues",
                "title": it.get("title") or "",
                "url": it.get("html_url") or "",
                # issues carry no bounty by default — price is set by us, not bid
                "budget": "help-wanted",
                "body": body,
            })
        return out
    except Exception as e:
        sys.stderr.write("source github-issues degraded: %s\n" % e)
        return []


SOURCES["github-issues"] = source_github_issues


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
        if got == []:
            degraded += 1
        rows.extend(got)
    stats = source_stats(conn)
    added = 0
    for r in rows:
        text = (r.get("title", "") + " " + r.get("body", ""))
        scams = scam_check(text)
        rivals = extract_competitors(text)
        s = score_opp(r.get("budget", ""), r.get("body", ""), rivals)
        if scams:
            s = 0.0
        else:
            s = apply_source_feedback(s, stats.get(r.get("source", ""), {}))
        cur = conn.execute("SELECT id FROM opportunities WHERE id=?", (r["id"],)).fetchone()
        if cur:
            continue
        conn.execute(
            "INSERT INTO opportunities(id,source,title,url,budget,body,score,scams,staged_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (r["id"], r["source"], r["title"], r["url"], r["budget"],
             r["body"], s, json.dumps(scams, ensure_ascii=False), now()))
        journal_write("scanned", r["id"], "score=%.2f source=%s rivals=%d" % (s, r.get("source", "?"), rivals))
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
        journal_write("blocked_scam", args.id, row["scams"])
        sys.exit("blocked: this opportunity matched scam patterns %s — see SCAMS.md" % row["scams"])
    if args.draft:
        # personalized draft supplied by the agent — still passes the same gates
        draft = open(os.path.expanduser(args.draft), encoding="utf-8").read().strip()
    elif row["source"] == "github-issues":
        draft = ("看到这个 issue。我可以接手修复：先给一个最小可复现/最小 patch 供你验证，"
                 "通过后再谈报酬；全程在公开仓库可见，验收不合格不收费。 %s") % row["url"]
    else:
        draft = ("你好，看到你的需求「%s」。我的方案：先免费出最小样稿/体检报告，"
                 "你验证后再谈价；数据不出本机、附来源与可复现脚本；验收不合格免费返工。"
                 " %s") % (row["title"], row["url"])
    secrets = secret_check(draft)
    if secrets:
        journal_write("blocked_secret", args.id, str(secrets))
        sys.exit("blocked: outbound draft contains secrets %s — remove them before staging."
                 % [k for k, _ in secrets])
    guard_outreach("staged", args.id)
    conn.execute("UPDATE opportunities SET staged_at=? WHERE id=?", (now(), args.id))
    conn.execute("INSERT INTO approvals(ts,opportunity_id,action,decided_by) VALUES(?,?,?,?)",
                 (now(), args.id, "staged", "agent"))
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
    if row["approved_at"]:
        sys.exit("already approved at %s — refusing to double-count consent."
                 % time.strftime("%F %T", time.localtime(row["approved_at"])))
    who = require_human("approve")      # identity first: nothing is journaled on a failed attempt
    guard_outreach("approved", args.id)
    conn.execute("UPDATE opportunities SET approved_at=? WHERE id=?", (now(), args.id))
    conn.execute("INSERT INTO approvals(ts,opportunity_id,action,decided_by) VALUES(?,?,?,?)",
                 (now(), args.id, "approved_by_human", who))
    conn.commit()
    print("approved by %s at %s. The agent may now send via the channel plugin."
          % (who, time.strftime("%F %T")))


# a deal may not walk backwards, and `sent` is the one transition that reaches
# a real person — so it needs both the kill switch and a recorded approval
ALLOWED_TRANSITIONS = {
    "open": {"sent"},
    "sent": {"won", "lost", "needs-human"},
    "needs-human": {"won", "lost"},
}


def cmd_mark(args):
    """autoloop-old lesson: retry memory + escalation. Every follow-up that
    goes unanswered is remembered; after N, the loop stops and escalates."""
    conn = db()
    if args.state not in ("sent", "won", "lost", "needs-human"):
        sys.exit("state must be one of: sent, won, lost, needs-human")
    row = conn.execute("SELECT * FROM opportunities WHERE id=?", (args.id,)).fetchone()
    if not row:
        sys.exit("no such opportunity: %s" % args.id)
    current = row["outcome"] or "open"
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if args.state not in allowed:
        sys.exit("blocked: %s → %s is not a legal transition (from `%s` allowed: %s)"
                 % (current, args.state, current,
                    ", ".join(sorted(allowed)) or "none — won/lost are terminal"))
    if args.state == "sent":
        if not row["approved_at"]:
            journal_write("blocked_unapproved_send", args.id)
            sys.exit("blocked: %s has no human approval on record. `mark sent` is the "
                     "moment the draft reaches a real person — run `earnloop approve %s` first."
                     % (args.id, args.id))
        guard_paused()
    conn.execute("UPDATE opportunities SET outcome=?, sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END WHERE id=?",
                 (args.state, args.state, now(), args.id))
    journal_write("marked", args.id, args.state)
    conn.commit()
    print("%s → %s" % (args.id, args.state))


def cmd_followup(args):
    conn = db()
    row = conn.execute("SELECT * FROM opportunities WHERE id=?", (args.id,)).fetchone()
    if not row:
        sys.exit("no such opportunity: %s" % args.id)
    guard_paused()          # a follow-up contacts a human: the kill switch must cover it
    if (row["outcome"] or "open") != "sent":
        sys.exit("blocked: %s is in state `%s` — follow-ups only apply to `sent`."
                 % (args.id, row["outcome"] or "open"))
    attempts = (row["attempts"] or 0) + 1
    note = args.note or ("no-reply" if args.no_reply else "reply")
    outcome = row["outcome"]
    if args.no_reply and attempts >= FOLLOWUP_ESCALATION and outcome not in ("won", "lost"):
        outcome = "needs-human"
        journal_write("escalated", args.id, "%d unanswered follow-ups" % attempts)
    conn.execute("UPDATE opportunities SET attempts=?, last_note=?, outcome=? WHERE id=?",
                 (attempts, note, outcome, args.id))
    journal_write("followup", args.id, "%d: %s" % (attempts, note))
    conn.commit()
    line = "%s follow-up #%d (%s)" % (args.id, attempts, note)
    if outcome == "needs-human":
        line += "  → ESCALATED to needs-human: stop contacting, a human decides next step."
    print(line)


def cmd_record(args):
    """aegisflow lesson: the model does not self-report money. A ledger entry
    cannot exist without delivery evidence, and cannot exist without a deal."""
    if not args.opp:
        sys.exit("blocked: --opp is REQUIRED — money that belongs to no deal is not "
                 "a ledger entry, it is a story.")
    evidence = check_evidence(args.evidence, "record")
    conn = db()
    if not conn.execute("SELECT id FROM opportunities WHERE id=?", (args.opp,)).fetchone():
        sys.exit("no such opportunity: %s" % args.opp)
    cur = conn.execute(
        "INSERT INTO ledger(ts,opportunity_id,item,amount,currency,status,note,evidence)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (now(), args.opp, args.item, args.amount, args.currency, "pending-verify",
         args.note or "", evidence))
    journal_write("recorded", args.opp,
                  "ledger#%d %s %.2f" % (cur.lastrowid, args.item, args.amount))
    conn.commit()
    print("ledger#%d recorded against %s as pending-verify. Money counts only after "
          "`earnloop verify %d`." % (cur.lastrowid, args.opp, cur.lastrowid))


def cmd_verify(args):
    """autoloop-old lesson: delayed verification. Delivered ≠ paid."""
    who = require_human("verify")       # payment confirmation is a human fact
    evidence = check_evidence(args.evidence, "verify")
    conn = db()
    row = conn.execute("SELECT * FROM ledger WHERE id=?", (args.ledger_id,)).fetchone()
    if not row:
        sys.exit("no such ledger entry: %d" % args.ledger_id)
    if row["status"] == "paid":
        sys.exit("already verified (paid) — refuse to double-count.")
    merged = "%s | paid: %s" % (row["evidence"] or "-", evidence)
    conn.execute("UPDATE ledger SET status='paid', evidence=?, note=? WHERE id=?",
                 (merged, (row["note"] + " | verified: " + (args.note or "")).strip(" |"),
                  args.ledger_id))
    journal_write("verified", row["opportunity_id"],
                  "ledger#%d paid %.2f %s by %s" % (args.ledger_id, row["amount"],
                                                    row["currency"], who))
    conn.commit()
    print("ledger#%d verified as PAID by %s (opportunity %s). The honest total just moved."
          % (args.ledger_id, who, row["opportunity_id"] or "unlinked"))


def cmd_why(args):
    """autoloop lesson: any round's full story, one command."""
    rows = journal_for(args.id)
    conn = db()
    opp = conn.execute("SELECT id,source,title,outcome,attempts,last_note,score,scams FROM opportunities WHERE id=?",
                       (args.id,)).fetchone()
    if opp:
        print("opportunity %s  [%s]  score=%.2f  attempts=%d  last=%s" %
              (opp["id"], opp["outcome"], opp["score"] or 0, opp["attempts"] or 0, opp["last_note"] or "-"))
        print("  title: %s" % opp["title"])
        if opp["scams"] not in ("[]", "", None):
            print("  scams: %s" % opp["scams"])
        money = conn.execute(
            "SELECT id,amount,currency,status,evidence FROM ledger"
            " WHERE opportunity_id=? ORDER BY ts", (args.id,)).fetchall()
        if money:
            print("  money: %d ledger entr%s" % (len(money), "y" if len(money) == 1 else "ies"))
            for m in money:
                print("    ledger#%-4d %8.2f %s  %-14s ev:%s"
                      % (m["id"], m["amount"], m["currency"], m["status"],
                         (m["evidence"] or "")[:40]))
        else:
            print("  money: none recorded against this deal yet")
    else:
        print("(not in db: %s)" % args.id)
    if not rows:
        print("(no journal events)")
    for e in rows:
        print("%s  %-16s %s" % (time.strftime("%F %T", time.localtime(e["ts"])), e["type"], e.get("detail", "")))


def cmd_stats(args):
    conn = db()
    stats = source_stats(conn)
    if not stats:
        print("(no data yet — run scan)")
    print("%-12s %7s %9s %5s %7s" % ("source", "listed", "approved", "won", "winrate"))
    for src, st in sorted(stats.items(), key=lambda kv: -kv[1]["listed"]):
        wr = ("%.0f%%" % (100.0 * st["won"] / st["approved"])) if st["approved"] else "-"
        print("%-12s %7d %9d %5d %7s" % (src, st["listed"], st["approved"], st["won"], wr))
    print("\nsources with >=3 approved attempts feed back into scan scoring (up/down nudge).")


def cmd_pause(args):
    os.makedirs(HOME, exist_ok=True)
    open(PAUSE_FLAG, "w").write(time.strftime("%F %T"))
    journal_write("paused")
    print("kill switch ON — propose/approve/followup/mark-sent are blocked; "
          "inspection (status/ledger/why/doctor) still works.")


def cmd_resume(args):
    if os.path.exists(PAUSE_FLAG):
        os.remove(PAUSE_FLAG)
        journal_write("resumed")
        print("kill switch OFF — outreach unblocked.")
    else:
        print("not paused.")


def cmd_doctor(args):
    checks = []
    try:
        os.makedirs(HOME, exist_ok=True)
        writable = os.access(HOME, os.W_OK)
    except OSError:
        writable = False
    checks.append(("home writable (%s)" % HOME, writable))
    try:
        conn = db()
        conn.execute("SELECT 1 FROM opportunities LIMIT 1").fetchall()
        checks.append(("db schema", True))
    except Exception as e:
        checks.append(("db schema: %s" % e, False))
    checks.append(("journal file", os.path.exists(JOURNAL_PATH)))
    checks.append(("paused", os.path.exists(PAUSE_FLAG)))
    for name in SOURCES:
        src_ok = True
        if name == "botstreet":
            src_ok = os.path.exists(BOTSTREET_ENV)
        if name == "github-issues" and not os.environ.get("GITHUB_TOKEN"):
            checks.append(("source github-issues: no GITHUB_TOKEN (rate-limited to ~10 req/min)", "WARN"))
            continue
        checks.append(("source %s configured" % name, src_ok))
    checks.append(("approver key (%s)" % APPROVER_KEY_ENV, approver_identity() is not None))
    bad = 0
    for name, ok in checks:
        if ok is True:
            mark = "OK "
        elif ok == "WARN" or (name == "paused" and ok is False):
            mark = "WARN"
        else:
            mark = "FAIL"
        if ok is False and name != "paused":
            bad += 1
        print("[%s] %s%s" % (mark, name, (" — %s" % ok) if isinstance(ok, str) else ""))
    print("\ncap: proposals/day=%d, follow-up escalation=%d"
          % (MAX_PROPOSALS_PER_DAY, FOLLOWUP_ESCALATION))
    print("%d failure(s). " % bad + ("kill switch is ON." if os.path.exists(PAUSE_FLAG) else "kill switch off."))


def cmd_status(args):
    conn = db()
    stats = {
        "open": conn.execute("SELECT COUNT(*) c FROM opportunities WHERE outcome='open'").fetchone()["c"],
        "won": conn.execute("SELECT COUNT(*) c FROM opportunities WHERE outcome='won'").fetchone()["c"],
        "lost": conn.execute("SELECT COUNT(*) c FROM opportunities WHERE outcome='lost'").fetchone()["c"],
        "needs_human": conn.execute("SELECT COUNT(*) c FROM opportunities WHERE outcome='needs-human'").fetchone()["c"],
        "staged_waiting_human": conn.execute(
            "SELECT COUNT(*) c FROM opportunities WHERE staged_at IS NOT NULL AND approved_at IS NULL").fetchone()["c"],
        "ledger_pending_verify": conn.execute("SELECT COUNT(*) c FROM ledger WHERE status='pending-verify'").fetchone()["c"],
        "paused": os.path.exists(PAUSE_FLAG),
        "approvals_today": proposals_today(),
        "daily_cap": MAX_PROPOSALS_PER_DAY,
    }
    if getattr(args, "json", False):
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    for state in ("open", "won", "lost", "needs_human"):
        print("%-14s %d" % (state, stats[state]))
    print("staged (waiting human): %d" % stats["staged_waiting_human"])
    print("ledger pending-verify: %d" % stats["ledger_pending_verify"])
    print("approvals today: %d / cap %d  %s" % (stats["approvals_today"], stats["daily_cap"],
                                                " [PAUSED]" if stats["paused"] else ""))


def cmd_ledger(args):
    conn = db()
    rows = conn.execute("SELECT * FROM ledger ORDER BY ts DESC LIMIT 20").fetchall()
    if not rows:
        print("(empty — every entry here is a real payment, never a promise)")
    for r in rows:
        print("%s  #%-4d %-14s %-24s %8.2f %s  %-14s ev:%s" % (
            time.strftime("%F", time.localtime(r["ts"])), r["id"],
            (r["opportunity_id"] or "-")[:14], r["item"][:24],
            r["amount"], r["currency"], r["status"], (r["evidence"] or "")[:20]))
    paid = conn.execute("SELECT SUM(amount) s FROM ledger WHERE status='paid'").fetchone()["s"] or 0
    pend = conn.execute("SELECT SUM(amount) s FROM ledger WHERE status='pending-verify'").fetchone()["s"] or 0
    print("\ntotal PAID: %.2f (verified with evidence — the only number that matters)" % paid)
    print("pending-verify: %.2f (delivered, not yet confirmed — never counted as paid)" % pend)


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
    print("then: mark sent → won → record (evidence!) → verify (evidence!) → why demo-clean")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="earnloop", description="the earning loop for AI agents")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--sources"); p.add_argument("--top", type=int, default=10); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("propose"); p.add_argument("id"); p.add_argument("--draft", help="path to a personalized draft file"); p.set_defaults(fn=cmd_propose)
    p = sub.add_parser("approve"); p.add_argument("id"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("mark"); p.add_argument("id"); p.add_argument("state"); p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("followup"); p.add_argument("id"); p.add_argument("--no-reply", action="store_true"); p.add_argument("--note"); p.set_defaults(fn=cmd_followup)
    p = sub.add_parser("record"); p.add_argument("item"); p.add_argument("amount", type=float)
    p.add_argument("--opp", required=True); p.add_argument("--currency", default="CNY")
    p.add_argument("--note"); p.add_argument("--evidence"); p.set_defaults(fn=cmd_record)
    p = sub.add_parser("verify"); p.add_argument("ledger_id", type=int); p.add_argument("--note"); p.add_argument("--evidence"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("why"); p.add_argument("id"); p.set_defaults(fn=cmd_why)
    p = sub.add_parser("stats"); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("pause"); p.set_defaults(fn=cmd_pause)
    p = sub.add_parser("resume"); p.set_defaults(fn=cmd_resume)
    p = sub.add_parser("doctor"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("status"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("ledger"); p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("try"); p.set_defaults(fn=cmd_try)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
