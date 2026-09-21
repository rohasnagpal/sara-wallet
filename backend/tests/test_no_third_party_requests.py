"""Opening Sara must not contact any third party: fonts are served from the
app itself, and the page loads no external script, stylesheet, image or font."""
import os
import re
import unittest

from fastapi.testclient import TestClient

import main

INDEX = os.path.join(os.path.dirname(main.__file__), "..", "index.html")


class NoThirdPartyRequestsTests(unittest.TestCase):
    def setUp(self):
        with open(INDEX, encoding="utf-8") as f:
            self.html = f.read()
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8888")

    def test_the_page_loads_no_external_resources(self):
        tags = re.findall(r'<(?:link|script|img|iframe|source|video|audio)\b[^>]*\b(?:href|src)\s*=\s*["\']https?://[^"\']+', self.html, re.I)
        css_urls = re.findall(r'url\(\s*["\']?https?://[^)]+', self.html, re.I) + re.findall(r'@import\s+(?:url\()?["\']?https?://', self.html, re.I)
        self.assertEqual(tags + css_urls, [])
        self.assertNotIn("fonts.googleapis.com", self.html)
        self.assertNotIn("fonts.gstatic.com", self.html)

    def test_fonts_are_served_by_the_app(self):
        css = self.client.get("/fonts/fonts.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("DM Sans", css.text)
        self.assertIn("Space Mono", css.text)
        files = re.findall(r"url\('(/fonts/[^']+\.woff2)'\)", css.text)
        self.assertGreaterEqual(len(files), 6)
        for path in files:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_security_policy_allows_only_this_origin_for_styles_and_fonts(self):
        csp = self.client.get("/fonts/fonts.css").headers["Content-Security-Policy"]
        self.assertIn("font-src 'self'", csp)
        self.assertNotIn("google", csp)
        self.assertNotIn("gstatic", csp)

    def test_font_licences_ship_with_the_fonts(self):
        fonts = os.path.join(os.path.dirname(main.__file__), "fonts")
        for name in ("DM-Sans-OFL.txt", "Space-Mono-OFL.txt"):
            self.assertTrue(os.path.exists(os.path.join(fonts, name)), name)


if __name__ == "__main__":
    unittest.main()
