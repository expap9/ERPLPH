"""การรับรองกติกาหน่วยย้อนหลัง: ต้องมีหลักฐานจาก git ครบ มิฉะนั้นปฏิเสธ

การรับรองผิดแปลว่าสถานะสอบทานที่เก็บไว้อาจเป็นของกติกาเก่า แต่ระบบจะเชื่อว่าเป็น
ของปัจจุบัน และจะไม่ดึงใหม่อีกเลย จึงต้องปฏิเสธทุกกรณีที่หลักฐานไม่ครบ
"""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

_spec = importlib.util.spec_from_file_location("adopt_unit_digests",
                                               ROOT / "scripts" / "adopt_unit_digests.py")
adopt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adopt)

PULLED_FROM = datetime(2026, 9, 11, 3, 30, tzinfo=timezone.utc)


def fake_git(tracked=adopt.EVIDENCE_FILES, dirty="", last="0b2afc6aa 2026-09-11T08:53:49+07:00"):
    def run(_home, *args):
        if args[0] == "ls-files":
            return "\n".join(tracked)
        if args[0] == "status":
            return dirty
        if args[0] == "log":
            return last
        raise AssertionError(args)
    return run


class EvidenceTests(unittest.TestCase):
    def test_clean_files_committed_before_the_pull_are_accepted(self):
        ok, notes = adopt.evidence(PULLED_FROM, Path("."), fake_git())
        self.assertTrue(ok, notes)

    def test_uncommitted_edits_to_the_rules_are_refused(self):
        ok, _ = adopt.evidence(PULLED_FROM, Path("."),
                               fake_git(dirty=" M new data/baseunit_0369.xlsx"))
        self.assertFalse(ok)

    def test_rules_committed_after_the_pull_are_refused(self):
        ok, _ = adopt.evidence(PULLED_FROM, Path("."),
                               fake_git(last="abc1234 2026-09-11T19:00:00+07:00"))
        self.assertFalse(ok)

    def test_untracked_rule_files_are_refused(self):
        # ไฟล์นอก git ไม่มีประวัติเนื้อหาให้พิสูจน์
        ok, _ = adopt.evidence(PULLED_FROM, Path("."),
                               fake_git(tracked=adopt.EVIDENCE_FILES[:-1]))
        self.assertFalse(ok)

    def test_no_git_means_no_evidence(self):
        def broken(*_args):
            raise subprocess.CalledProcessError(128, "git")
        ok, _ = adopt.evidence(PULLED_FROM, Path("."), broken)
        self.assertFalse(ok)

    def test_every_file_that_shapes_the_check_is_part_of_the_evidence(self):
        for name in ("app/lot_reconciliation.py", "app/monitor_units.py", "app/special_units.py",
                     "app/confirmed_units.py", "new data/baseunit_0369.xlsx"):
            self.assertIn(name, adopt.EVIDENCE_FILES)


if __name__ == "__main__":
    unittest.main()
