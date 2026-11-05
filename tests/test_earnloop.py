import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("earnloop", os.path.join(HERE, "..", "earnloop.py"))
el = importlib.util.module_from_spec(spec)
spec.loader.exec_module(el)


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


class ScoreTest(unittest.TestCase):
    def test_budget_scores(self):
        self.assertGreater(el.score_opp("$300", ""), el.score_opp("$50", ""))
        self.assertEqual(el.score_opp("", "no budget info"), 5.0)

    def test_competition_penalizes(self):
        self.assertGreater(el.score_opp("$100", "", 0), el.score_opp("$100", "", 20))


class FlowTest(unittest.TestCase):
    def test_demo_flow_in_tmp(self):
        import subprocess, tempfile, shutil
        tmp = tempfile.mkdtemp()
        env = dict(os.environ, EARNLOOP_HOME=tmp)
        py = sys.executable
        exe = os.path.join(HERE, "..", "earnloop.py")
        r1 = subprocess.run([py, exe, "try"], capture_output=True, text=True, env=env)
        self.assertEqual(r1.returncode, 0)
        self.assertIn("SCAM BLOCKED", r1.stdout)
        r2 = subprocess.run([py, exe, "propose", "demo-clean"], capture_output=True, text=True, env=env)
        self.assertEqual(r2.returncode, 0)
        r3 = subprocess.run([py, exe, "propose", "demo-scam"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r3.returncode, 0)          # scam must be blocked
        self.assertIn("scam patterns", r3.stderr + r3.stdout)
        r4 = subprocess.run([py, exe, "approve", "demo-clean"], capture_output=True, text=True, env=env)
        self.assertEqual(r4.returncode, 0)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
