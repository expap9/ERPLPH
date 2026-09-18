"""เผยแพร่ที่ 172.16.13.102/ERPLPH ผ่าน IIS reverse proxy (คู่กับ /stock ของ Stock5)

IIS ส่งคำขอมาที่แบ็กเอนด์พร้อม prefix "/ERPLPH" ติดไปด้วยเสมอ (ดู web.config ตัวอย่าง
ของ Stock5App/DIMOPH) แอปต้องแกะ prefix ออกเองทั้งฝั่งเราต์ (PrefixMiddleware) และ
ฝั่งลิงก์ที่ฝังไว้ตรง ๆ ในหน้า Jinja (href="/..." ที่ไม่ได้ใช้ url_for()) มิฉะนั้นเมนู
ในเว็บจะเด้งไปหน้า root ของเซิร์ฟเวอร์แทนที่จะอยู่ใต้ /ERPLPH
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import web  # noqa: E402


class PrefixAbsoluteRefsTests(unittest.TestCase):
    """ทดสอบตัวเติม prefix ให้ href/src/action และ fetch()/location.href แบบ unit ล้วน"""

    def test_prefixes_html_attributes(self):
        html = '<a href="/overview">x</a><form action="/items"><img src="/logo.png">'
        result = web._prefix_absolute_refs(html, "/ERPLPH")
        self.assertIn('href="/ERPLPH/overview"', result)
        self.assertIn('action="/ERPLPH/items"', result)
        self.assertIn('src="/ERPLPH/logo.png"', result)

    def test_prefixes_js_fetch_and_navigation(self):
        html = ("<script>fetch(`/api/substores/daily-cut`);"
                "fetch('/api/drugs/search');"
                "location.href='/login';"
                "window.location.href = \"/overview\";</script>")
        result = web._prefix_absolute_refs(html, "/ERPLPH")
        self.assertIn("fetch(`/ERPLPH/api/substores/daily-cut`)", result)
        self.assertIn("fetch('/ERPLPH/api/drugs/search')", result)
        self.assertIn("location.href='/ERPLPH/login'", result)
        self.assertIn('window.location.href = "/ERPLPH/overview"', result)

    def test_does_not_touch_protocol_relative_or_external_urls(self):
        html = '<a href="//cdn.example.com/x.js">x</a><a href="https://stock5/">y</a>'
        result = web._prefix_absolute_refs(html, "/ERPLPH")
        self.assertEqual(result, html, "ห้ามแตะ // (protocol-relative) หรือ URL เต็มรูปแบบ")

    def test_does_not_double_prefix_already_prefixed_paths(self):
        """หน้า Angular ที่ _angular_index() แก้ <base href> ไปก่อนแล้วต้องไม่โดนเติมซ้ำ"""
        html = '<base href="/ERPLPH/">'
        result = web._prefix_absolute_refs(html, "/ERPLPH")
        self.assertEqual(result, html)



class PrefixMiddlewareEndToEndTests(unittest.TestCase):
    """เรียกผ่าน test_client() จริง ให้ครอบคลุมทั้ง PrefixMiddleware และ after_request ด้วยกัน"""

    def setUp(self):
        web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def test_request_without_prefix_is_unaffected(self):
        response = self.client.get("/overview")
        html = response.get_data(as_text=True)
        self.assertNotIn("/ERPLPH/overview", html, "ไม่ผ่าน proxy ต้องไม่มี prefix ปนอยู่")

    def test_request_with_erplph_prefix_gets_rewritten_links(self):
        response = self.client.get("/ERPLPH/overview")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/ERPLPH/overview"', html)
        self.assertNotIn('href="/overview"', html, "ลิงก์เดิมต้องถูกเติม prefix ไปหมดแล้ว ไม่เหลือของเก่า")

    def test_bare_prefix_without_trailing_slash_redirects(self):
        response = self.client.get("/ERPLPH", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/ERPLPH/"))

    def test_lowercase_prefix_also_works(self):
        response = self.client.get("/erplph/overview")
        self.assertEqual(response.status_code, 200)

    def test_hardcoded_redirects_keep_the_prefix(self):
        """/dashboard เดิม redirect("/overview") ตรง ๆ (ไม่ได้ใช้ url_for) ก็ต้องไม่หลุด prefix"""
        for path, expected_location in (
            ("/ERPLPH/dashboard", "/ERPLPH/overview"),
            ("/ERPLPH/purchasing", "/ERPLPH/procure-to-pay"),
            ("/ERPLPH/drug-search", "/ERPLPH/items"),
            ("/ERPLPH/drugs/1000", "/ERPLPH/items/1000"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], expected_location)

    def test_url_for_based_redirect_is_not_double_prefixed(self):
        """/daily-stock ใช้ url_for() อยู่แล้ว ซึ่งเติม prefix ให้เองจาก SCRIPT_NAME"""
        response = self.client.get("/ERPLPH/daily-stock", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/ERPLPH/substores?tab=daily")


if __name__ == "__main__":
    unittest.main()
