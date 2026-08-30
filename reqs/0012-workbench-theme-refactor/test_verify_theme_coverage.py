import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("verify-theme-coverage.js")


class ThemeCoverageGateTests(unittest.TestCase):
    def run_gate(self):
        return subprocess.run(
            ["node", str(SCRIPT)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8",
        )

    def run_gate_at(self, root):
        return subprocess.run(
            ["node", str(SCRIPT), str(root)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8",
        )

    def run_body_theme_gate_at(self, root):
        """只跑 body-theme 断言 —— 排除遗留方言/裸色值等检查的干扰，
        确保反例是被新断言本身抓到的，而不是被别的检查顺带命中。"""
        return subprocess.run(
            ["node", str(SCRIPT), str(root), "--only=body-theme"], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8",
        )

    # body-theme 断言的 fixture 基线：两套主题 + 随主题变化的 --color-bg-canvas。
    THEMED_TOKENS = (
        ':root { --theme-registry: "midnight|墨夜深色|🌙,daylight|晴昼浅色|☀️";'
        " --p-teal-500:#1; --p-amber-500:#2;"
        " --p-slate-900:#0a1628; --p-slate-50:#f4f6f9;"
        " --color-bg-canvas: var(--p-slate-900); }\n"
        'html[data-theme="midnight"] { --color-dir-rx: var(--p-teal-500);'
        " --color-dir-tx: var(--p-amber-500); --color-bg-canvas: var(--p-slate-900); }\n"
        'html[data-theme="daylight"] { --color-dir-rx: var(--p-teal-500);'
        " --color-dir-tx: var(--p-amber-500); --color-bg-canvas: var(--p-slate-50); }\n"
    )
    SAFE_BOOT = ('<script>var THEME_KEYS=["midnight","daylight"];'
                 'var saved=localStorage.getItem("wb-theme");'
                 'if (THEME_KEYS.includes(saved)) {'
                 ' document.documentElement.setAttribute("data-theme", saved); }</script>')

    def build_themed_fixture(self, static, body_css, link_tokens=True, tokens=None):
        """造一套 10 页 fixture；body_css 写进每页的 <style>。"""
        (static / "pages").mkdir(parents=True, exist_ok=True)
        (static / "tokens-v2.css").write_text(tokens or self.THEMED_TOKENS, encoding="utf-8")
        entries = [("workbench", "workbench.html"), ("serial", "pages/serial.html"),
                   ("module", "pages/module.html"), ("listener", "pages/listener.html"),
                   ("simcon", "pages/simcon.html"), ("trace", "pages/trace.html"),
                   ("dict", "pages/dict.html"), ("scenario", "pages/scenario.html"),
                   ("maintenance", "pages/maintenance.html")]
        (static / "app.js").write_text("const PAGES = [\n" + "\n".join(
            f'{{ id: "{key}", src: "/static/{file}" }},' for key, file in entries) + "\n];", encoding="utf-8")
        link = '<link rel="stylesheet" href="/static/tokens-v2.css">' if link_tokens else ""
        for _, file in [("outer", "index.html"), *entries]:
            page = static / file
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("<!doctype html><head>" + link + self.SAFE_BOOT + "</head>"
                            "<body><style>" + body_css + "</style></body>", encoding="utf-8")

    def test_gate_passes_on_current_codebase(self):
        # 这条验的是「真实仓库当前应当通过门禁」。
        # 它原先叫 test_gate_reports_..._current_issues 并断言 returncode != 0，
        # 那个前提成立于侦听台未接入的年代（当时全仓 206 issues）。
        # REQS-0012 阶段 3 收尾后全仓 0 issues，前提反了 —— 是好消息把测试跑挂，不是回归。
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("主题覆盖率：10 个生产页面", result.stdout)
        self.assertIn("0 issues", result.stdout)

    def test_success_path_has_exact_output(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            (static / "pages").mkdir(parents=True)
            # body-theme 断言要求：--theme-registry 至少两套主题 + 随主题变化的背景 token。
            # 两者缺一，那 10 个假页面都会报错，这条「成功路径」测试就名不副实了。
            (static / "tokens-v2.css").write_text(
                ':root { --theme-registry: "midnight|墨夜深色|🌙,daylight|晴昼浅色|☀️";'
                " --p-teal-500:#1; --p-amber-500:#2;"
                " --p-slate-900:#0a1628; --p-slate-50:#f4f6f9; }\n"
                'html[data-theme="midnight"] { --color-dir-rx: var(--p-teal-500); --color-dir-tx: var(--p-amber-500);'
                " --color-bg-canvas: var(--p-slate-900); }\n"
                'html[data-theme="daylight"] { --color-dir-rx: var(--p-teal-500); --color-dir-tx: var(--p-amber-500);'
                " --color-bg-canvas: var(--p-slate-50); }\n", encoding="utf-8")
            entries = [
                ("workbench", "workbench.html"), ("serial", "pages/serial.html"),
                ("module", "pages/module.html"), ("listener", "pages/listener.html"),
                ("simcon", "pages/simcon.html"), ("trace", "pages/trace.html"),
                ("dict", "pages/dict.html"), ("scenario", "pages/scenario.html"),
                ("maintenance", "pages/maintenance.html"),
            ]
            app = "const PAGES = [\n" + "\n".join(
                f'{{ id: "{key}", src: "/static/{file}" }},' for key, file in entries) + "\n];"
            (static / "app.js").write_text(app + '\nconst DEMO = {src: "/static/preview/demo.html"};', encoding="utf-8")
            boot = ('<script>var THEME_KEYS=["midnight","daylight"];'
                    'var saved=localStorage.getItem("wb-theme");'
                    'if (THEME_KEYS.includes(saved)) { document.documentElement.setAttribute("data-theme", saved); }'
                    '</script>')
            for _, file in [("outer", "index.html"), *entries]:
                page = static / file
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text('<!doctype html><head><link rel="stylesheet" href="/static/tokens-v2.css">'
                                + boot + '</head><body><style>body{background:var(--color-bg-canvas)}'
                                '.rx{color:var(--color-dir-rx)}.tx{color:var(--color-dir-tx)}</style></body>', encoding="utf-8")
            result = self.run_gate_at(temp)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "主题覆盖率：10 个生产页面 / 0 issues\n")

    # ---------- body-theme：切换 data-theme 后 body 背景色必须变化 ----------

    def test_body_theme_gate_accepts_a_theme_reactive_canvas(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "body{background:var(--color-bg-canvas)}")
            result = self.run_body_theme_gate_at(temp)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("0 issues", result.stdout)

    def test_body_theme_gate_resolves_the_l3_to_l1_token_chain(self):
        tokens = self.THEMED_TOKENS + ":root, html[data-theme] { --frame-bg: var(--color-bg-canvas); }\n"
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "body{background:var(--frame-bg)}", tokens=tokens)
            result = self.run_body_theme_gate_at(temp)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("0 issues", result.stdout)

    def test_body_theme_gate_rejects_a_hard_coded_background(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "body{background:#0a1628}")
            result = self.run_body_theme_gate_at(temp)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("切换 data-theme 背景不变", result.stdout)

    def test_body_theme_gate_rejects_a_primitive_bypassing_the_semantic_layer(self):
        # 越层直引 L1 primitive：它是纯色值，不随主题变。
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "body{background:var(--p-slate-900)}")
            result = self.run_body_theme_gate_at(temp)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("切换 data-theme 背景不变", result.stdout)

    def test_body_theme_gate_rejects_a_token_reference_without_the_token_file(self):
        # 最有价值的一条反例：页面引用了 token 却没引入 tokens-v2.css，
        # 运行时变量为空 —— 看起来接入了，实际切换主题纹丝不动。
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "body{background:var(--color-bg-canvas)}", link_tokens=False)
            result = self.run_body_theme_gate_at(temp)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("看起来接入了 token", result.stdout)

    def test_body_theme_gate_rejects_a_theme_override_collapsing_to_one_color(self):
        # 高特异性主题块把 daylight 覆盖回 midnight 的色值。
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(
                static,
                "body{background:var(--color-bg-canvas)}"
                'html[data-theme="daylight"] body{background:var(--p-slate-900)}')
            result = self.run_body_theme_gate_at(temp)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("切换 data-theme 背景不变", result.stdout)

    def test_body_theme_gate_rejects_a_page_with_no_background_at_all(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, ".panel{color:var(--color-bg-canvas)}")
            result = self.run_body_theme_gate_at(temp)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("均无 background 声明", result.stdout)

    def test_body_theme_gate_falls_back_to_html_when_body_has_no_background(self):
        # 根元素背景会传播到画布，所以只给 html 设底色是合法写法。
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "html{background:var(--color-bg-canvas)}")
            result = self.run_body_theme_gate_at(temp)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("底色由 html 承载", result.stdout)

    def test_body_theme_gate_flags_color_mix_overlays_for_manual_review(self):
        # 真实仓库里 module-serial 就是这个形态：渐变光晕含 color-mix，
        # 底色层可判定（确实随主题变），但整体观感必须单列出来人工确认。
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(
                static,
                "body{background:radial-gradient(circle,"
                " color-mix(in srgb, var(--color-bg-canvas) 20%, transparent), transparent 30rem),"
                " var(--color-bg-canvas)}")
            result = self.run_body_theme_gate_at(temp)
            self.assertIn("需人工确认", result.stdout)
            self.assertIn("color-mix() 无法静态求值", result.stdout)

    def test_body_theme_gate_never_silently_passes_an_unevaluable_color(self):
        # color-mix 就是背景色本身时，绝不能伪装成通过 ——
        # 它只是不阻塞构建，但必须挂进「需人工确认」清单。
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(
                static, "body{background:color-mix(in srgb, var(--color-bg-canvas) 80%, transparent)}")
            result = self.run_body_theme_gate_at(temp)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("需人工确认", result.stdout)
            self.assertIn("color-mix() 无法静态求值", result.stdout)
            self.assertIn("另有", result.stdout)

    def test_body_theme_gate_refuses_to_run_without_a_theme_registry(self):
        bare = (":root { --p-slate-900:#0a1628; --p-slate-50:#f4f6f9;"
                " --color-bg-canvas: var(--p-slate-900); }\n"
                'html[data-theme="daylight"] { --color-bg-canvas: var(--p-slate-50); }\n')
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            self.build_themed_fixture(static, "body{background:var(--color-bg-canvas)}", tokens=bare)
            result = self.run_body_theme_gate_at(temp)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("至少需要两套主题", result.stdout)

    def test_direction_contract_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            (static / "pages").mkdir(parents=True)
            (static / "app.js").write_text('const PAGES=[{src:"/static/a.html"}];', encoding="utf-8")
            (static / "tokens-v2.css").write_text(
                ':root{--p-teal-500:#1;--p-amber-500:#2;} html[data-theme="midnight"]{--color-dir-rx:var(--p-amber-500);--color-dir-tx:var(--p-teal-500)} html[data-theme="daylight"]{--color-dir-rx:var(--p-amber-500);--color-dir-tx:var(--p-teal-500)}', encoding="utf-8")
            boot = '<script>var keys=["midnight","daylight"];var saved=localStorage.getItem("wb-theme");var theme=keys.indexOf(saved)>-1?saved:keys[0];document.documentElement.setAttribute("data-theme",theme)</script>'
            (static / "index.html").write_text('<head><link rel="stylesheet" href="/static/tokens-v2.css">' + boot + '</head>', encoding="utf-8")
            (static / "a.html").write_text('<head><link rel="stylesheet" href="/static/tokens-v2.css">' + boot + '</head>', encoding="utf-8")
            result = self.run_gate_at(temp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("P5", result.stdout)

    def test_unrelated_includes_cannot_authorize_saved_theme(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            (static / "pages").mkdir(parents=True)
            entries = [f"pages/p{index}.html" for index in range(9)]
            (static / "app.js").write_text("const PAGES=[" + ",".join(
                f'{{src:"/static/{file}"}}' for file in entries) + "];", encoding="utf-8")
            (static / "tokens-v2.css").write_text(
                ':root{--p-teal-500:#1;--p-amber-500:#2;} html[data-theme="midnight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)} html[data-theme="daylight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)}', encoding="utf-8")
            boot = '<script>var saved=localStorage.getItem("wb-theme");var unrelated=["saved"].includes(saved);document.documentElement.setAttribute("data-theme",saved)</script>'
            for file in ["index.html", *entries]:
                page = static / file
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text('<head><link rel="stylesheet" href="/static/tokens-v2.css">' + boot + '</head>', encoding="utf-8")
            result = self.run_gate_at(temp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("白名单", result.stdout)

    def test_nested_direction_selector_cannot_swap_rx_and_tx(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            (static / "pages").mkdir(parents=True)
            (static / "app.js").write_text('const PAGES=[{src:"/static/a.html"}];', encoding="utf-8")
            (static / "tokens-v2.css").write_text(
                ':root{--p-teal-500:#1;--p-amber-500:#2;} html[data-theme="midnight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)} html[data-theme="daylight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)}', encoding="utf-8")
            boot = '<script>var keys=["midnight","daylight"];var saved=localStorage.getItem("wb-theme");if (keys.includes(saved)) { document.documentElement.setAttribute("data-theme",saved); }</script>'
            html = '<head><link rel="stylesheet" href="/static/tokens-v2.css">' + boot + '</head><style>@media (min-width:1px){.rx{color:var(--color-dir-tx)}}</style>'
            (static / "index.html").write_text(html, encoding="utf-8")
            (static / "a.html").write_text(html, encoding="utf-8")
            result = self.run_gate_at(temp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RX selector", result.stdout)

    def test_every_saved_theme_assignment_must_be_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            (static / "pages").mkdir(parents=True)
            entries = [f"pages/p{index}.html" for index in range(9)]
            (static / "app.js").write_text("const PAGES=[" + ",".join(
                f'{{src:"/static/{file}"}}' for file in entries) + "];", encoding="utf-8")
            (static / "tokens-v2.css").write_text(
                ':root{--p-teal-500:#1;--p-amber-500:#2;} html[data-theme="midnight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)} html[data-theme="daylight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)}', encoding="utf-8")
            boot = ('<script>var THEME_KEYS=["midnight","daylight"];var saved=localStorage.getItem("wb-theme");'
                    'if (THEME_KEYS.includes(saved)) { document.documentElement.setAttribute("data-theme", saved); }'
                    'document.documentElement.setAttribute("data-theme", saved);</script>')
            for file in ["index.html", *entries]:
                page = static / file
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text('<head><link rel="stylesheet" href="/static/tokens-v2.css">' + boot + '</head>', encoding="utf-8")
            result = self.run_gate_at(temp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("每次", result.stdout)

    def test_indexof_comparison_must_bind_to_same_call(self):
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp) / "apps" / "workbench" / "static"
            (static / "pages").mkdir(parents=True)
            entries = [f"pages/p{index}.html" for index in range(9)]
            (static / "app.js").write_text("const PAGES=[" + ",".join(
                f'{{src:"/static/{file}"}}' for file in entries) + "];", encoding="utf-8")
            (static / "tokens-v2.css").write_text(
                ':root{--p-teal-500:#1;--p-amber-500:#2;} html[data-theme="midnight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)} html[data-theme="daylight"]{--color-dir-rx:var(--p-teal-500);--color-dir-tx:var(--p-amber-500)}', encoding="utf-8")
            boot = '<script>var keys=["midnight","daylight"];var saved=localStorage.getItem("wb-theme");if (keys.indexOf(saved) < 0 || other.indexOf("x") >= 0) { document.documentElement.setAttribute("data-theme",saved); }</script>'
            for file in ["index.html", *entries]:
                page = static / file
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text('<head><link rel="stylesheet" href="/static/tokens-v2.css">' + boot + '</head>', encoding="utf-8")
            result = self.run_gate_at(temp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("白名单", result.stdout)


if __name__ == "__main__":
    unittest.main()
