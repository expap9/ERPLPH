"""ตารางรหัสหน่วยงาน — รหัสที่ไม่รู้จักต้องไม่ทำให้แถวหายจากรายงาน"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import departments  # noqa: E402


SAMPLE = {
    "source": "SSBSTOCK.dbo.SYSCONFIG",
    "entries": [
        {"level": "division", "path": ["208", "", ""], "name": "กลุ่มงานเภสัชกรรม"},
        {"level": "division", "path": ["209", "", ""], "name": "กลุ่มการพยาบาล"},
        {"level": "division", "path": ["11", "", ""], "name": "((ยกเลิก)งานบริหารและธุรการ"},
        {"level": "dept", "path": ["209", "07", ""], "name": "งานจ่ายกลาง"},
        {"level": "section", "path": ["209", "07", "01"], "name": "จ่ายกลาง OPD"},
    ],
}


class CodeShapeTests(unittest.TestCase):
    """ช่อง CODE ของ SYSCONFIG รวมรหัสแม่ไว้ด้วย โดยเติมช่องว่างให้ชั้นละ 6 ตัวอักษร"""

    def test_each_level_is_split_at_the_fixed_width(self):
        self.assertEqual(departments.split_code("208"), ("208", "", ""))
        self.assertEqual(departments.split_code("209   01"), ("209", "01", ""))
        self.assertEqual(departments.split_code("209   07    01"), ("209", "07", "01"))
        self.assertEqual(departments.split_code("000   017   267"), ("000", "017", "267"))

    def test_a_level_below_a_blank_one_cannot_identify_anything(self):
        self.assertEqual(departments.path_of("", "02", "02"), ("", "", ""))
        self.assertEqual(departments.path_of("208", "", "02"), ("208", "", ""))

    def test_surrounding_space_does_not_make_a_different_department(self):
        self.assertEqual(departments.path_of(" 208 ", " 02 "), ("208", "02", ""))

    def test_retired_departments_are_recognised_by_their_name(self):
        self.assertTrue(departments.is_retired("((ยกเลิก)งานบริหารและธุรการ"))
        self.assertTrue(departments.is_retired("((ยกเลิกใช้ 30704 แทน)กลุ่มนโยบายและแผนงาน"))
        self.assertFalse(departments.is_retired("กลุ่มงานเภสัชกรรม"))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "departments.json"
        self.path.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
        patcher = patch.object(departments, "REGISTRY_PATH", str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)
        departments.reload()
        self.addCleanup(departments.reload)

    def test_a_known_code_reads_as_its_thai_name(self):
        self.assertEqual(departments.name_of("208"), "กลุ่มงานเภสัชกรรม")
        self.assertEqual(departments.name_of("209", "07", "01"), "จ่ายกลาง OPD")

    def test_an_unknown_code_keeps_its_code_instead_of_disappearing(self):
        """ผังองค์กรเปลี่ยนได้ตลอด ถ้าไม่รู้จักแล้วทิ้งแถว ยอดรวมจะไม่ตรงโดยไม่มีใครรู้"""
        self.assertEqual(departments.name_of("777", "01"), "777-01")
        self.assertEqual(departments.name_of(""), "(ไม่ระบุหน่วยงาน)")

    def test_the_full_name_reads_from_the_top_down(self):
        self.assertEqual(departments.full_name("209", "07", "01"),
                         "กลุ่มการพยาบาล › งานจ่ายกลาง › จ่ายกลาง OPD")

    def test_children_are_one_level_below_the_parent_only(self):
        self.assertEqual([found.name for found in departments.children_of("209")],
                         ["งานจ่ายกลาง"])
        self.assertEqual([found.name for found in departments.children_of("209", "07")],
                         ["จ่ายกลาง OPD"])
        self.assertEqual(len(departments.children_of()), 3, "ชั้นบนสุดคือกลุ่มงานทั้งหมด")

    def test_the_page_can_say_which_database_the_names_came_from(self):
        """สองฐานให้ชื่อไม่ตรงกันในหลายรหัส หน้าจอจึงต้องบอกว่ายึดฐานไหน"""
        self.assertEqual(departments.source(), "SSBSTOCK.dbo.SYSCONFIG")


class MissingRegistryTests(unittest.TestCase):
    def test_a_missing_file_does_not_stop_the_page_from_opening(self):
        with patch.object(departments, "REGISTRY_PATH", "config/ยังไม่มีไฟล์นี้.json"):
            departments.reload()
            self.addCleanup(departments.reload)
            self.assertEqual(departments.name_of("208"), "208")
            self.assertEqual(departments.source(), "")


class RealRegistryTests(unittest.TestCase):
    """สำเนาจริงที่อยู่ในโปรเจกต์ ต้องอ่านได้และมีหน่วยงานที่ผู้ใช้ยกตัวอย่างไว้"""

    def setUp(self):
        departments.reload()
        self.addCleanup(departments.reload)
        if not (ROOT / departments.REGISTRY_PATH).is_file():
            self.skipTest("ยังไม่ได้สร้าง config/departments.json")

    def test_the_departments_the_user_named_are_all_present(self):
        # จากภาพหน้าจอที่ผู้ใช้ส่งมา 17 ก.ย. 2569 และตัวอย่างที่ยกในโจทย์
        self.assertEqual(departments.name_of("208"), "กลุ่มงานเภสัชกรรม")
        self.assertEqual(departments.name_of("306"), "กลุ่มงานโภชนศาสตร์")
        self.assertEqual(departments.name_of("203"), "กลุ่มงานรังสีวิทยา")
        self.assertEqual(departments.name_of("202"), "กลุ่มงานวิสัญญีวิทยา")
        self.assertIn("พัสดุ", departments.name_of("305"))

    def test_names_are_cleaned_of_the_repeated_leading_letter(self):
        """ชื่อในฐานข้อมูลมีอักขระตัวแรกซ้ำเหมือนชื่อยา เช่น 'กกลุ่มงานเภสัชกรรม'"""
        for found in departments.children_of():
            if found.retired:
                continue
            with self.subTest(code=found.path[0]):
                self.assertNotEqual(found.name[:1], found.name[1:2],
                                    f"ชื่อยังมีตัวอักษรซ้ำ: {found.name}")


if __name__ == "__main__":
    unittest.main()
