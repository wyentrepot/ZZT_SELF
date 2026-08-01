import unittest
from pathlib import Path


STATIC_DIR = Path("hplc_web/static")


class UiLayoutTests(unittest.TestCase):
    def test_workspace_is_bounded_to_viewport_with_internal_table_scroll(self):
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".data-section", css)
        self.assertIn("height: calc(100vh", css)
        self.assertIn("overflow-y: auto", css)

    def test_hidden_debugger_cannot_be_overridden_by_component_display(self):
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)

    def test_page_keeps_file_workspace_and_detail_inspector(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="operation-panel"', html)
        self.assertIn('class="table-shell"', html)
        self.assertIn('class="detail-panel"', html)


if __name__ == "__main__":
    unittest.main()
