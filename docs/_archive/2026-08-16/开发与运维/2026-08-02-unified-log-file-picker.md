# Unified Log File Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one reliable log-file selection action that selects a local Windows file, fills the log-path field, and leaves the user ready to start indexing.

**Architecture:** The existing browser modal backed by `/api/fs/roots` and `/api/fs/list` is the single source of truth for selecting a file. Remove the separate Tkinter-based `/api/fs/pick` route and its front-end event path, because it silently degrades to an empty selection when the server-side GUI dialog cannot be shown.

**Tech Stack:** FastAPI, vanilla JavaScript, HTML/CSS, Python unittest.

## Global Constraints

- Keep the file-selection result as the Windows path in `#log-path`.
- Clicking `建立解析索引` must continue to POST that path to `/api/logs/open`.
- Do not add browser file uploads or temporary copies of the log.

---

### Task 1: Specify the unified selector surface

**Files:**
- Modify: `hplc_web/tests/test_ui_layout.py`
- Modify: `hplc_web/static/index.html`

**Interfaces:**
- Consumes: `#log-path`, existing `#file-picker` modal.
- Produces: One `#pick-button` with label `选择日志文件`, and no `#browse-button`.

- [ ] **Step 1: Write the failing layout test**

```python
def test_page_has_one_unified_log_file_picker_button(self):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    self.assertIn('id="pick-button"', html)
    self.assertIn("选择日志文件", html)
    self.assertNotIn('id="browse-button"', html)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest hplc_web.tests.test_ui_layout.UiLayoutTests.test_page_has_one_unified_log_file_picker_button`

Expected: FAIL because the current page has a separate `#browse-button`.

- [ ] **Step 3: Implement the minimal HTML change**

```html
<button id="pick-button" class="secondary-button" type="button">选择日志文件</button>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\\Scripts\\python.exe -m unittest hplc_web.tests.test_ui_layout.UiLayoutTests.test_page_has_one_unified_log_file_picker_button`

Expected: PASS.

### Task 2: Route the sole button through the stable browser picker

**Files:**
- Modify: `hplc_web/tests/test_ui_layout.py`
- Modify: `hplc_web/static/app.js`
- Modify: `hplc_web/app.py`
- Modify: `hplc_web/tests/test_app.py`

**Interfaces:**
- Consumes: `pickerOpen()` and `pickerConfirm()`.
- Produces: `pick-button` opens the modal; confirming a file updates `#log-path`; indexing still uses `/api/logs/open`.

- [ ] **Step 1: Write the failing behavior assertions**

```python
def test_js_uses_only_browser_picker_for_file_selection(self):
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    self.assertIn('elements.pick.addEventListener("click", pickerOpen)', js)
    self.assertNotIn('/api/fs/pick', js)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest hplc_web.tests.test_ui_layout.UiLayoutTests.test_js_uses_only_browser_picker_for_file_selection`

Expected: FAIL because the current JavaScript calls `/api/fs/pick`.

- [ ] **Step 3: Implement the minimal JavaScript and API cleanup**

```javascript
elements.pick.addEventListener("click", pickerOpen);
```

Remove the unused `browse` element/event, native picker API call, and server-side Tkinter helper/route tests.

- [ ] **Step 4: Run targeted tests**

Run: `.venv\\Scripts\\python.exe -m unittest hplc_web.tests.test_ui_layout hplc_web.tests.test_app`

Expected: PASS.

### Task 3: Verify the regression and hand off

**Files:**
- Modify: `doc/任务交接需求与进度表.md`

- [ ] **Step 1: Record the new single-button behavior**

State that the browser picker is the only selection flow and that its confirmation fills the log path before indexing.

- [ ] **Step 2: Run the full web test suite**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s hplc_web\\tests -p "test_*.py"`

Expected: PASS with no failures.

