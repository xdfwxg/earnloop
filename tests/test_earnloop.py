import importlib.util
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("earnloop", os.path.join(HERE, "..", "earnloop.py"))
el = importlib.util.module_from_spec(spec)
spec.loader.exec_module(el)

# the key stands in for "something only the human's shell knows"
HUMAN_KEY = "test-only-approver-key"


def run(*argv, home=None, extra_env=None, human=False):
    """Run the CLI in a subprocess. human=True adds the approver key to the
    environment — i.e. simulates the human's own shell, not the agent's."""
    import subprocess, tempfile
    env = dict(os.environ, EARNLOOP_HOME=home or tempfile.mkdtemp())
    env.pop("EARNLOOP_APPROVER_KEY", None)
    if human:
        env["EARNLOOP_APPROVER_KEY"] = HUMAN_KEY
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, os.path.join(HERE, "..", "earnloop.py")] + list(argv),
                          capture_output=True, text=True, env=env)


def walk_to(tmp, state):
    """try → propose → approve → mark sent, using the human key."""
    run("try", home=tmp)
    run("propose", "demo-clean", home=tmp)
    run("approve", "demo-clean", home=tmp, human=True)
    if state in ("sent", "won", "lost", "needs-human"):
        run("mark", "demo-clean", "sent", home=tmp)
    if state in ("won", "lost", "needs-human"):
        run("mark", "demo-clean", state, home=tmp)


def journal(tmp):
    path = os.path.join(tmp, "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class ScamTest(unittest.TestCase):
    def test_pay_to_work(self):
        hits = el.scam_check("高薪急聘 先交保证金再开工")
        self.assertIn("pay-to-work", [h["id"] for h in hits])

    def test_off_platform(self):
        hits = el.scam_check("详情加微信私聊转账")
        self.assertIn("off-platform-escrow", [h["id"] for h in hits])

    def test_unfunded(self):
        hits = el.scam_check("At launch, every bounty is unfunded")
        self.assertIn("unfunded", [h["id"] for h in hits])

    def test_clean_not_flagged(self):
        self.assertEqual(el.scam_check("清洗客户表，按行去重，统一日期格式"), [])


class SecretTest(unittest.TestCase):
    """upskill lesson: scan outbound payloads before they leave the machine."""

    def test_github_token_detected(self):
        kinds = [k for k, _ in el.secret_check("my token is ghp_ABCDEFGHIJKLMNOPQRSTUVWX")]
        self.assertIn("GitHub token", kinds)

    def test_api_key_detected(self):
        kinds = [k for k, _ in el.secret_check("use sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")]
        self.assertIn("API key (sk-…)", kinds)

    def test_clean_draft_passes(self):
        self.assertEqual(el.secret_check("你好，看到你的需求，先出样稿再谈价。"), [])


class ScoreTest(unittest.TestCase):
    def test_budget_scores(self):
        self.assertGreater(el.score_opp("$300", ""), el.score_opp("$50", ""))
        self.assertEqual(el.score_opp("", "no budget info"), 5.0)

    def test_competition_penalizes(self):
        self.assertGreater(el.score_opp("$100", "", 0), el.score_opp("$100", "", 20))

    def test_competitors_actually_extracted(self):
        """review lesson: competition must be measured, not a dead parameter."""
        self.assertEqual(el.extract_competitors("已有12人认领，速来"), 12)
        self.assertEqual(el.extract_competitors("5 bids so far"), 5)
        self.assertEqual(el.extract_competitors("3 位已报名"), 3)
        self.assertEqual(el.extract_competitors("无人投标"), 0)

    def test_source_feedback_gates(self):
        # upskill lesson: no history → no nudge; needs >=3 approved attempts
        base = 10.0
        self.assertEqual(el.apply_source_feedback(base, {"approved": 0, "won": 0}), base)
        self.assertEqual(el.apply_source_feedback(base, {"approved": 2, "won": 2}), base)
        self.assertEqual(el.apply_source_feedback(base, {"approved": 3, "won": 0}), 7.0)
        self.assertEqual(el.apply_source_feedback(base, {"approved": 3, "won": 3}), 12.0)


class ConsentTest(unittest.TestCase):
    """aegisflow lesson: consent that the agent can manufacture is not consent."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_approve_requires_human_key(self):
        run("try", home=self.tmp)
        run("propose", "demo-clean", home=self.tmp)
        r = run("approve", "demo-clean", home=self.tmp)          # agent's shell: no key
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("EARNLOOP_APPROVER_KEY", r.stderr + r.stdout)
        self.assertNotIn("approved", [e["type"] for e in journal(self.tmp)],
                         "a failed approval must not consume the daily cap")

    def test_approve_records_who_approved(self):
        import sqlite3
        run("try", home=self.tmp)
        run("propose", "demo-clean", home=self.tmp)
        r = run("approve", "demo-clean", home=self.tmp, human=True)
        self.assertEqual(r.returncode, 0)
        conn = sqlite3.connect(os.path.join(self.tmp, "earnloop.db"))
        rows = conn.execute("SELECT decided_by FROM approvals WHERE action='approved_by_human'").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0][0].startswith("human:"))
        self.assertNotEqual(rows[0][0], "human", "identity must carry a key fingerprint")

    def test_approve_refuses_double_count(self):
        run("try", home=self.tmp)
        run("propose", "demo-clean", home=self.tmp)
        run("approve", "demo-clean", home=self.tmp, human=True)
        r = run("approve", "demo-clean", home=self.tmp, human=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already approved", r.stderr + r.stdout)

    def test_verify_requires_human_key(self):
        walk_to(self.tmp, "sent")
        run("record", "清洗", "150", "--opp", "demo-clean",
            "--evidence", "shot: ./delivery.png", home=self.tmp)
        r = run("verify", "1", "--evidence", "tx: alipay 20261105", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("EARNLOOP_APPROVER_KEY", r.stderr + r.stdout)


class KillSwitchTest(unittest.TestCase):
    """mcp-session-share lesson: stop must cover everything that reaches a human."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pause_blocks_followup(self):
        walk_to(self.tmp, "sent")
        run("pause", home=self.tmp)
        r = run("followup", "demo-clean", "--no-reply", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("kill switch", r.stderr)

    def test_pause_blocks_mark_sent(self):
        run("try", home=self.tmp)
        run("propose", "demo-clean", home=self.tmp)
        run("approve", "demo-clean", home=self.tmp, human=True)
        run("pause", home=self.tmp)
        r = run("mark", "demo-clean", "sent", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("kill switch", r.stderr)

    def test_pause_still_allows_recording_an_outcome(self):
        """Stopping outreach must not stop bookkeeping."""
        walk_to(self.tmp, "sent")
        run("pause", home=self.tmp)
        r = run("mark", "demo-clean", "won", home=self.tmp)
        self.assertEqual(r.returncode, 0)
        r2 = run("status", home=self.tmp)
        self.assertEqual(r2.returncode, 0)
        self.assertIn("won            1", r2.stdout)

    def test_pause_keeps_inspection(self):
        run("try", home=self.tmp)
        run("pause", home=self.tmp)
        for cmd in ("status", "ledger", "doctor", "stats"):
            r = run(cmd, home=self.tmp)
            self.assertEqual(r.returncode, 0, "%s must still work while paused" % cmd)


class FlowTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_demo_flow(self):
        r1 = run("try", home=self.tmp)
        self.assertEqual(r1.returncode, 0)
        self.assertIn("SCAM BLOCKED", r1.stdout)
        self.assertEqual(run("propose", "demo-clean", home=self.tmp).returncode, 0)
        r3 = run("propose", "demo-scam", home=self.tmp)
        self.assertNotEqual(r3.returncode, 0)
        self.assertIn("scam patterns", r3.stderr + r3.stdout)
        self.assertEqual(run("approve", "demo-clean", home=self.tmp, human=True).returncode, 0)
        self.assertEqual(run("mark", "demo-clean", "sent", home=self.tmp).returncode, 0)
        self.assertEqual(run("mark", "demo-clean", "won", home=self.tmp).returncode, 0)

    def test_propose_blocks_secret_in_title(self):
        import sqlite3
        run("try", home=self.tmp)
        conn = sqlite3.connect(os.path.join(self.tmp, "earnloop.db"))
        conn.execute("INSERT OR REPLACE INTO opportunities(id,source,title,url,budget,body,score,scams)"
                     " VALUES('demo-leak','demo','fix ghp_ABCDEFGHIJKLMNOPQRSTUVWX leak','https://x.org','$10','',0,'[]')")
        conn.commit(); conn.close()
        r = run("propose", "demo-leak", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("secrets", r.stderr + r.stdout)

    def test_mark_sent_requires_approval(self):
        """The consent flow must not be bypassable through another entry point."""
        import sqlite3
        run("try", home=self.tmp)
        run("propose", "demo-clean", home=self.tmp)
        r = run("mark", "demo-clean", "sent", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("approve", r.stderr + r.stdout)
        conn = sqlite3.connect(os.path.join(self.tmp, "earnloop.db"))
        outcome, sent_at = conn.execute(
            "SELECT outcome, sent_at FROM opportunities WHERE id='demo-clean'").fetchone()
        conn.close()
        self.assertEqual(outcome, "open")
        self.assertIsNone(sent_at)

    def test_illegal_transition_rejected(self):
        run("try", home=self.tmp)
        r = run("mark", "demo-clean", "won", home=self.tmp)       # open → won
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a legal transition", r.stderr + r.stdout)

    def test_followup_requires_a_sent_deal(self):
        run("try", home=self.tmp)
        r = run("followup", "demo-clean", "--no-reply", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only apply to `sent`", r.stderr + r.stdout)

    def test_watchdog_daily_cap(self):
        env = {"EARNLOOP_MAX_PROPOSALS_PER_DAY": "1"}
        run("try", home=self.tmp, extra_env=env)
        self.assertEqual(run("propose", "demo-clean", home=self.tmp, extra_env=env).returncode, 0)
        self.assertEqual(run("approve", "demo-clean", home=self.tmp, human=True,
                             extra_env=env).returncode, 0)
        r = run("status", home=self.tmp, extra_env=env)
        self.assertIn("approvals today: 1 / cap 1", r.stdout)

    def test_followup_escalation(self):
        """autoloop-old lesson: after N no-replies, stop and escalate."""
        walk_to(self.tmp, "sent")
        for _ in range(3):
            r = run("followup", "demo-clean", "--no-reply", home=self.tmp)
            self.assertEqual(r.returncode, 0)
        self.assertIn("ESCALATED", r.stdout)
        r2 = run("status", home=self.tmp)
        self.assertIn("needs_human    1", r2.stdout)
        r3 = run("why", "demo-clean", home=self.tmp)
        self.assertIn("followup", r3.stdout)
        self.assertIn("escalated", r3.stdout)


class LedgerTest(unittest.TestCase):
    """aegisflow lesson: the model does not self-report money."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_requires_a_deal(self):
        r = run("record", "脏表清洗", "150", "--evidence", "shot: ./d.png", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--opp", r.stderr + r.stdout)

    def test_record_requires_evidence(self):
        run("try", home=self.tmp)
        r = run("record", "脏表清洗", "150", "--opp", "demo-clean", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("evidence is REQUIRED", r.stderr + r.stdout)

    def test_evidence_must_be_a_reference_not_a_claim(self):
        run("try", home=self.tmp)
        r = run("record", "脏表清洗", "150", "--opp", "demo-clean",
                "--evidence", "我保证客户付了", home=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("scheme", r.stderr + r.stdout)
        r2 = run("record", "脏表清洗", "150", "--opp", "demo-clean",
                 "--evidence", "file: /nope/missing.png", home=self.tmp)
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("does not exist", r2.stderr + r2.stdout)

    def test_delivered_is_not_paid(self):
        walk_to(self.tmp, "sent")
        run("record", "脏表清洗", "150", "--opp", "demo-clean",
            "--evidence", "shot: /tmp/delivery.png", home=self.tmp)
        r = run("ledger", home=self.tmp)
        self.assertIn("pending-verify", r.stdout)
        self.assertIn("total PAID: 0.00", r.stdout)
        self.assertEqual(run("verify", "1", home=self.tmp, human=True).returncode != 0, True)

    def test_verify_moves_money_and_keeps_delivery_proof(self):
        import sqlite3
        walk_to(self.tmp, "sent")
        run("record", "脏表清洗", "150", "--opp", "demo-clean",
            "--evidence", "shot: /tmp/delivery.png", home=self.tmp)
        r = run("verify", "1", "--evidence", "tx: alipay 20261105", home=self.tmp, human=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("demo-clean", r.stdout)
        self.assertEqual(run("verify", "1", "--evidence", "tx: again", home=self.tmp,
                             human=True).returncode != 0, True)
        r2 = run("ledger", home=self.tmp)
        self.assertIn("total PAID: 150.00", r2.stdout)
        self.assertIn("demo-clean", r2.stdout)
        conn = sqlite3.connect(os.path.join(self.tmp, "earnloop.db"))
        ev = conn.execute("SELECT evidence FROM ledger WHERE id=1").fetchone()[0]
        conn.close()
        self.assertIn("delivery.png", ev)      # delivery proof survives verification
        self.assertIn("alipay", ev)            # payment proof is added next to it

    def test_why_shows_the_money_for_that_deal(self):
        walk_to(self.tmp, "sent")
        run("record", "脏表清洗", "150", "--opp", "demo-clean",
            "--evidence", "shot: /tmp/delivery.png", home=self.tmp)
        r = run("why", "demo-clean", home=self.tmp)
        self.assertIn("ledger#1", r.stdout)
        self.assertIn("150.00", r.stdout)
        self.assertIn("pending-verify", r.stdout)


class DiscoveryTest(unittest.TestCase):
    """the low-competition door: github-issues source + its own draft voice."""

    def test_issue_draft_and_agent_attribution(self):
        import shutil, sqlite3, tempfile
        tmp = tempfile.mkdtemp()
        run("try", home=tmp)
        conn = sqlite3.connect(os.path.join(tmp, "earnloop.db"))
        conn.execute("INSERT OR REPLACE INTO opportunities(id,source,title,url,budget,body,score,scams,staged_at,outcome)"
                     " VALUES('gh-1','github-issues','App crashes on empty csv',"
                     "'https://github.com/x/y/issues/1','help-wanted','',5,'[]',NULL,'open')")
        conn.commit(); conn.close()
        r = run("propose", "gh-1", home=tmp)
        self.assertEqual(r.returncode, 0)
        self.assertIn("最小", r.stdout)          # issue voice, not bidding voice
        conn = sqlite3.connect(os.path.join(tmp, "earnloop.db"))
        who = conn.execute("SELECT decided_by FROM approvals WHERE opportunity_id='gh-1'"
                           " AND action='staged'").fetchone()[0]
        conn.close()
        self.assertEqual(who, "agent")            # staging is the agent's act, never 'human'
        shutil.rmtree(tmp, ignore_errors=True)

    def test_doctor_warns_on_missing_github_token(self):
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None)
        env["EARNLOOP_HOME"] = tmp
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, "..", "earnloop.py"), "doctor"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("WARN", r.stdout)
        self.assertIn("GITHUB_TOKEN", r.stdout)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
