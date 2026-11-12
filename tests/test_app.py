"""The installable app and the theme: manifest, icons, the service worker the build writes, and the head of every page."""

import json
import os
import re
import struct
import unittest
from pathlib import Path
from unittest import mock

from helpers import HERE, build_fixture, build_site, config

ROOT = HERE.parent


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def sw_value(sw, name):
    return json.loads(re.search(rf"^const {name} = (.*?);", sw, re.M).group(1))


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.paper, cls.out, _ = build_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_manifest_makes_the_paper_installable(self):
        m = json.loads((self.out / "manifest.webmanifest").read_text())
        self.assertEqual(m["name"], "The Test Gazette")
        self.assertEqual(m["short_name"], "The Test Gazette")  # a renamed paper is not called PR Post
        self.assertEqual((m["start_url"], m["scope"], m["id"]), ("/", "/", "/"))
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["theme_color"], "#f8f7f3")
        self.assertEqual(m["background_color"], "#e9e8e3")
        self.assertEqual(m["user_preferences"]["color_scheme_dark"], {"theme_color": "#18181c", "background_color": "#0f0f11"})
        icons = {(i["sizes"], i["purpose"]): i["src"] for i in m["icons"]}
        self.assertEqual(set(icons), {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")})
        for (sizes, _), src in icons.items():
            w, h = png_size(self.out / src.lstrip("/"))
            self.assertEqual(f"{w}x{h}", sizes, src)
        self.assertEqual(png_size(self.out / "assets/icons/apple-touch-icon.png"), (180, 180))
        self.assertEqual(png_size(self.out / "assets/icons/favicon-32.png"), (32, 32))
        self.assertTrue((self.out / "assets/icons/icon.svg").read_text().startswith("<svg"))
        self.assertNotIn(chr(0x2014), (self.out / "manifest.webmanifest").read_text())

    def test_every_page_has_the_head_the_app_needs(self):
        pages = list(self.out.rglob("*.html"))
        self.assertGreater(len(pages), 4)
        for page in pages:
            head = page.read_text().split("</head>")[0]
            name = page.relative_to(self.out)
            self.assertIn('<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">', head, name)
            self.assertIn('<link rel="apple-touch-icon" href="/assets/icons/apple-touch-icon.png">', head, name)
            self.assertIn('<meta name="apple-mobile-web-app-title" content="The Test Gazette">', head, name)
            self.assertEqual(len(re.findall(r'<meta name="theme-color" content="#[0-9a-f]{6}" media="\(prefers-color-scheme: (light|dark)\)">', head)), 2, name)
            # theme.js blocks: it must run before the stylesheet paints anything, and after the metas it updates.
            theme = re.search(r'<script src="/assets/theme\.js\?v=[0-9a-f]{10}"></script>', head)
            self.assertTrue(theme, name)
            self.assertLess(head.index('name="theme-color"'), theme.start(), name)
            self.assertLess(theme.start(), head.index("/assets/style.css"), name)
            body = page.read_text()
            self.assertIn('<fieldset class="theme-pick">', body, name)
            self.assertIn('id="install"', body, name)
            self.assertRegex(body, r'<script src="/assets/pwa\.js\?v=[0-9a-f]{10}" defer></script>')

    def test_the_install_card_starts_hidden(self):
        front = (self.out / "index.html").read_text()
        card = re.search(r'<aside class="install"[^>]*>', front).group(0)
        self.assertIn(" hidden", card)

    def test_service_worker_is_filled_in(self):
        sw = (self.out / "sw.js").read_text()
        self.assertNotIn("__", re.sub(r"//.*", "", sw).replace("__proto__", ""))
        self.assertRegex(sw, r'const BUILD = "[0-9a-f]{16}";')
        shell = sw_value(sw, "SHELL")
        pages = sw_value(sw, "PAGES")
        for rel in shell + pages + ["offline.html"]:
            path = rel.split("?")[0]
            target = self.out / (path + "index.html" if path.endswith("/") or path == "./" else path)
            self.assertTrue(target.resolve().is_file(), rel)
        self.assertIn("manifest.webmanifest", shell)
        self.assertTrue(any(s.startswith("assets/style.css?v=") for s in shell))
        self.assertEqual(pages[:2], ["./", "archive/"])
        self.assertIn("p/octo/", pages)
        self.assertEqual(sw_value(sw, "FONT_CSS"), build_site.FONTS)
        self.assertNotIn("http://", "".join(shell + pages))
        self.assertTrue(all(not p.startswith("/") for p in shell + pages), "entries are relative to the scope")

    def test_build_id_changes_only_when_the_site_does(self):
        bid = lambda out: re.search(r'const BUILD = "(\w+)"', (out / "sw.js").read_text()).group(1)
        again = Path(self.tmp.name) / "again"
        build_site.build(self.paper, again)
        self.assertEqual(bid(again), bid(self.out))
        wl = self.paper / "watchlist.toml"
        original = wl.read_text()
        self.addCleanup(wl.write_text, original)
        wl.write_text(original.replace('motto = "Testing, testing"', 'motto = "Still testing"'))
        changed = Path(self.tmp.name) / "changed"
        build_site.build(self.paper, changed)
        self.assertNotEqual(bid(changed), bid(self.out))

    def test_the_offline_page_is_built(self):
        page = (self.out / "offline.html").read_text()
        self.assertIn("You are offline", page)
        self.assertIn('href="/"', page)

    def test_colors_match_the_stylesheet_and_theme_script(self):
        css = (ROOT / "site/assets/style.css").read_text()
        light = css[css.index(":root {"):]
        dark = css[css.index(':root[data-theme="dark"] {'):]
        token = lambda block, name: re.search(rf"--{name}:\s*(#[0-9a-f]{{6}})", block).group(1)
        for theme, block in (("light", light), ("dark", dark)):
            self.assertEqual(build_site.COLORS[theme]["sheet"], token(block, "sheet"), theme)
            self.assertEqual(build_site.COLORS[theme]["paper"], token(block, "paper"), theme)
        # theme.js takes its colors from the theme-color metas, so the build is the one place they live.
        self.assertNotRegex((ROOT / "site/assets/theme.js").read_text(), r"#[0-9a-fA-F]{6}")


class AppUnderABasePath(unittest.TestCase):
    def test_scope_and_start_url_follow_the_base_path(self):
        with mock.patch.dict(os.environ, {"PR_POST_BASE_PATH": "/pr-post"}):
            tmp, _, out, _ = build_fixture()
        self.addCleanup(tmp.cleanup)
        m = json.loads((out / "manifest.webmanifest").read_text())
        self.assertEqual((m["start_url"], m["scope"], m["id"]), ("/pr-post/", "/pr-post/", "/pr-post/"))
        self.assertTrue(all(i["src"].startswith("/pr-post/assets/icons/") for i in m["icons"]))
        front = (out / "index.html").read_text()
        self.assertIn('<link rel="manifest" href="/pr-post/manifest.webmanifest" crossorigin="use-credentials">', front)
        # sw.js sits at the base path; its lists stay relative, so they resolve inside the scope.
        self.assertTrue((out / "sw.js").is_file())
        self.assertTrue(all(not p.startswith("/") for p in sw_value((out / "sw.js").read_text(), "PAGES")))


class ShortName(unittest.TestCase):
    def test_default_and_custom_short_names(self):
        self.assertEqual(config.parse("")["site"]["short_name"], "PR Post")
        self.assertEqual(config.parse('[site]\ntitle = "My Paper"\n')["site"]["short_name"], "My Paper")
        self.assertEqual(config.parse('[site]\ntitle = "My Paper"\nshort_name = "Mine"\n')["site"]["short_name"], "Mine")
        with self.assertRaises(config.ConfigError):
            config.parse('[site]\nshort_name = "' + "x" * 31 + '"\n')


if __name__ == "__main__":
    unittest.main()
