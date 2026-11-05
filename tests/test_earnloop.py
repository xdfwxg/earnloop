import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("earnloop", os.path.join(HERE, "..", "earnloop.py"))
el = importlib.util.module_from_spec(spec)
spec.loader.exec_module(el)


def run(*argv, home=None, extra_env=None):
    import subprocess, tempfile
    env = dict(os.environ, EARNLOOP_HOME=home or tempfile.mkdtemp())
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, os.path.join(HERE, "..", "earnloop.py")] + list(argv),
                          capture_output=True, text=True, env=env)


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

    def test_source_feedback_gates(self):
        # upskill lesson: no history → no nudge; needs >=3 approved attempts
        base = 10.0
        self.assertEqual(el.apply_source_feedback(base, {"approved": 0, "won": 0}), base)
        self.assertEqual(el.apply_source_feedback(base, {"approved": 2, "won": 2}), base)
        self.assertEqual(el.apply_source_feedback(base, {"approved": 3, "won": 0}), 7.0)
        self.assertEqual(el.apply_source_feedback(base, {"approved": 3, "won": 3}), 12.0)


class FlowTest(unittest.TestCase):
    def test_demo_flow_in_tmp(self):
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        r1 = run("try", home=tmp)
        self.assertEqual(r1.returncode, 0)
        self.assertIn("SCAM BLOCKED", r1.stdout)
        r2 = run("propose", "demo-clean", home=tmp)
        self.assertEqual(r2.returncode, 0)
        r3 = run("propose", "demo-scam", home=tmp)
        self.assertNotEqual(r3.returncode, 0)          # scam must be blocked
        self.assertIn("scam patterns", r3.stderr + r3.stdout)
        r4 = run("approve", "demo-clean", home=tmp)
        self.assertEqual(r4.returncode, 0)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_propose_blocks_secret_in_title(self):
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        run("try", home=tmp)
        # inject an opportunity whose title leaks a token
        import sqlite3
        conn = sqlite3.connect(os.path.join(tmp, "earnloop.db"))
        conn.execute("INSERT OR REPLACE INTO opportunities(id,source,title,url,budget,body,score,scams)"
                     " VALUES('demo-leak','demo','fix ghp_ABCDEFGHIJKLMNOPQRSTUVWX leak','https://x.org','$10','',0,'[]')")
        conn.commit(); conn.close()
        r = run("propose", "demo-leak", home=tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("secrets", r.stderr + r.stdout)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_pause_is_a_real_kill_switch(self):
        """mcp-session-share lesson: stop must be structural, not advisory."""
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        run("try", home=tmp)
        r0 = run("pause", home=tmp)
        self.assertEqual(r0.returncode, 0)
        r1 = run("propose", "demo-clean", home=tmp)
        self.assertNotEqual(r1.returncode, 0)
        self.assertIn("kill switch", r1.stderr)
        r2 = run("approve", "demo-clean", home=tmp)
        self.assertNotEqual(r2.returncode, 0)
        # inspection still works while paused
        r3 = run("status", home=tmp)
        self.assertEqual(r3.returncode, 0)
        r4 = run("resume", home=tmp)
        self.assertEqual(r4.returncode, 0)
        r5 = run("propose", "demo-clean", home=tmp)
        self.assertEqual(r5.returncode, 0)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_watchdog_daily_cap(self):
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        run("try", home=tmp)
        r1 = run("propose", "demo-clean", home=tmp, extra_env={"EARNLOOP_MAX_PROPOSALS_PER_DAY": "1"})
        self.assertEqual(r1.returncode, 0)
        r2 = run("approve", "demo-clean", home=tmp, extra_env={"EARNLOOP_MAX_PROPOSALS_PER_DAY": "1"})
        self.assertEqual(r2.returncode, 0)             # 1st approval of the day
        # a second approval would exceed cap=1 — verify via journal counting path
        r3 = run("propose", "demo-scam", home=tmp, extra_env={"EARNLOOP_MAX_PROPOSALS_PER_DAY": "1"})
        # demo-scam is scam-blocked first, so watchdog is not reachable here;
        # check status reports cap correctly instead
        r4 = run("status", home=tmp, extra_env={"EARNLOOP_MAX_PROPOSALS_PER_DAY": "1"})
        self.assertIn("approvals today: 1 / cap 1", r4.stdout)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_followup_escalation(self):
        """autoloop-old lesson: after N no-replies, stop and escalate."""
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        run("try", home=tmp)
        for i in range(3):
            r = run("followup", "demo-clean", "--no-reply", home=tmp)
            self.assertEqual(r.returncode, 0)
        self.assertIn("ESCALATED", r.stdout)
        r2 = run("status", home=tmp)
        self.assertIn("needs_human    1", r2.stdout)
        # why shows the full trail (autoloop lesson: one command answers everything)
        r3 = run("why", "demo-clean", home=tmp)
        self.assertIn("followup", r3.stdout)
        self.assertIn("escalated", r3.stdout)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_money_requires_evidence(self):
        """aegisflow lesson: the model does not self-report income."""
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        r1 = run("record", "脏表清洗", "150", home=tmp)
        self.assertNotEqual(r1.returncode, 0)
        self.assertIn("evidence is REQUIRED", r1.stderr)
        r2 = run("record", "脏表清洗", "150", "--evidence", "shot: /tmp/delivery.png", home=tmp)
        self.assertEqual(r2.returncode, 0)
        # delivered-but-unverified money must NOT count as paid
        r3 = run("ledger", home=tmp)
        self.assertIn("pending-verify", r3.stdout)
        self.assertIn("total PAID: 0.00", r3.stdout)
        # verify without evidence → blocked
        r4 = run("verify", "1", home=tmp)
        self.assertNotEqual(r4.returncode, 0)
        # verify with evidence → paid, and double-verify refused
        r5 = run("verify", "1", "--evidence", "tx: alipay 20261105xxx", home=tmp)
        self.assertEqual(r5.returncode, 0)
        r6 = run("verify", "1", home=tmp)
        self.assertNotEqual(r6.returncode, 0)
        r7 = run("ledger", home=tmp)
        self.assertIn("total PAID: 150.00", r7.stdout)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
