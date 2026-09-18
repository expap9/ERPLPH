"""ชุดทดสอบความถูกต้องของการทำความสะอาดชื่อยาและชื่อคู่ค้า (name_cleaner)
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import name_cleaner  # noqa: E402


class TestNameCleaner(unittest.TestCase):
    def test_thai_vowel_duplicate_consonant(self):
        """กรณีพยัญชนะไทยถูกทำสำเนาไว้หน้าสระนำ (เ, แ, โ, ใ, ไ)"""
        self.assertEqual(name_cleaner.clean_name("กโกสินทร์เวชภัณฑ์"), "โกสินทร์เวชภัณฑ์")
        self.assertEqual(name_cleaner.clean_name("จเจ ที เวิลด์ เทค"), "เจ ที เวิลด์ เทค")
        self.assertEqual(name_cleaner.clean_name("คเค เค เอ็น สมาร์ท"), "เค เค เอ็น สมาร์ท")
        self.assertEqual(name_cleaner.clean_name("บไบโอคอททอน"), "ไบโอคอททอน")
        self.assertEqual(name_cleaner.clean_name("ชเชียงใหม่"), "เชียงใหม่")

    def test_thai_consonant_duplicate(self):
        """กรณีพยัญชนะไทยตัวแรกซ้ำกันโดยตรง"""
        self.assertEqual(name_cleaner.clean_name("ฟฟิลิปส์ (ประเทศไทย)"), "ฟิลิปส์ (ประเทศไทย)")
        self.assertEqual(name_cleaner.clean_name("รรวยแน่"), "รวยแน่")
        self.assertEqual(name_cleaner.clean_name("ซซิลลิค ฟาร์มา"), "ซิลลิค ฟาร์มา")
        self.assertEqual(name_cleaner.clean_name("บบริษัท บางกอก"), "บริษัท บางกอก")

    def test_drug_leading_digits_duplicate(self):
        """กรณีตัวเลขตัวแรกซ้ำกันในชื่อยา/เวชภัณฑ์"""
        self.assertEqual(name_cleaner.clean_drug_name("33TC SYRUP"), "3TC SYRUP")
        self.assertEqual(name_cleaner.clean_drug_name("110%Glycerine"), "10%Glycerine")
        self.assertEqual(name_cleaner.clean_drug_name("66-MERCAPTOPURINE"), "6-MERCAPTOPURINE")
        self.assertEqual(name_cleaner.clean_drug_name("11.5% CAPD"), "1.5% CAPD")
        self.assertEqual(name_cleaner.clean_drug_name("44.25% CAPD"), "4.25% CAPD")
        self.assertEqual(name_cleaner.clean_drug_name("22.3% CAPD"), "2.3% CAPD")
        self.assertEqual(name_cleaner.clean_drug_name("775%Alcohol"), "75%Alcohol")
        self.assertEqual(name_cleaner.clean_drug_name("995% ALCOHOL"), "95% ALCOHOL")
        self.assertEqual(name_cleaner.clean_drug_name("00.5% Sodium hypochlorite"), "0.5% Sodium hypochlorite")
        self.assertEqual(name_cleaner.clean_drug_name("33 in 1 Total Parenteral"), "3 in 1 Total Parenteral")

    def test_genuine_abbreviations_preserved(self):
        """ชื่อย่อทางการแพทย์ที่ไม่ควรถูกตัดตัวอักษรซ้ำ"""
        self.assertEqual(name_cleaner.clean_drug_name("MMR VACCINE"), "MMR VACCINE")
        self.assertEqual(name_cleaner.clean_drug_name("DDAVP 0.1 MG/ML"), "DDAVP 0.1 MG/ML")
        self.assertEqual(name_cleaner.clean_drug_name("DDR RAM"), "DDR RAM")
        self.assertEqual(name_cleaner.clean_drug_name("LLETZ LOOP"), "LLETZ LOOP")
        self.assertEqual(name_cleaner.clean_drug_name("SS AGAR"), "SS AGAR")
        self.assertEqual(name_cleaner.clean_drug_name("SSC CROWN"), "SSC CROWN")
        self.assertEqual(name_cleaner.clean_drug_name("TTC NAIL"), "TTC NAIL")
        self.assertEqual(name_cleaner.clean_drug_name("CC1 PREDILUTE"), "CC1 PREDILUTE")

    def test_vendor_name_cleaning(self):
        """การจัดรูปแบบชื่อบริษัทคู่ค้าและเครื่องหมาย backslash"""
        self.assertEqual(
            name_cleaner.clean_vendor_name("กโกสินทร์เวชภัณฑ์ จำกัด\\บริษัท"),
            "โกสินทร์เวชภัณฑ์ จำกัด บริษัท"
        )
        self.assertEqual(
            name_cleaner.clean_vendor_name("รรวยแน่\\หจก."),
            "รวยแน่ หจก."
        )
        self.assertEqual(
            name_cleaner.clean_vendor_name("ฟฟิลิปส์ (ประเทศไทย) จำกัด\\บริษัท"),
            "ฟิลิปส์ (ประเทศไทย) จำกัด บริษัท"
        )
        self.assertEqual(
            name_cleaner.clean_vendor_name("จเจ ที เวิลด์ เทค จำกัด\\บริษัท"),
            "เจ ที เวิลด์ เทค จำกัด บริษัท"
        )
        self.assertEqual(
            name_cleaner.clean_vendor_name("คเค เค เอ็น สมาร์ท เซอร์วิส จำกัด\\บริษัท"),
            "เค เค เอ็น สมาร์ท เซอร์วิส จำกัด บริษัท"
        )
        # ตรวจสอบ non-breaking space
        self.assertEqual(
            name_cleaner.clean_vendor_name("บริษัท\xa0ทดสอบ\xa0จำกัด"),
            "บริษัท ทดสอบ จำกัด"
        )

    def test_empty_and_edge_cases(self):
        """กรณีค่าว่างหรือชนิดข้อมูลพิเศษ"""
        self.assertEqual(name_cleaner.clean_drug_name(""), "")
        self.assertEqual(name_cleaner.clean_drug_name(None), "")
        self.assertEqual(name_cleaner.clean_vendor_name(""), "")
        self.assertEqual(name_cleaner.clean_vendor_name(None), "")
        self.assertEqual(name_cleaner.clean_name("A"), "A")
        self.assertEqual(name_cleaner.clean_name("AB"), "AB")


if __name__ == "__main__":
    unittest.main()
