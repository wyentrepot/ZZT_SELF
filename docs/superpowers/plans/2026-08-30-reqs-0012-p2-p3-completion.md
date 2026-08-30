# REQS-0012 P2/P3 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every production workbench page respond to the two `data-theme` themes through `tokens-v2.css`, remove all legacy theme dialects and files, and record the approved RX/TX color convention.

**Architecture:** `apps/workbench/static/tokens-v2.css` remains the sole primitive → semantic → component token source. Each production document imports it directly and consumes its standard semantic/component variables; the temporary A/B/C aliases in `compat-dialects.css` and the legacy four-theme `tokens.css` disappear only after every page is converted. Theme selection remains CSS-defined and uses `data-theme`, with a pre-paint boot script and parent-to-iframe propagation.

**Tech Stack:** FastAPI static serving, zero-build HTML/CSS/vanilla JavaScript, Node.js static verifiers, Python unittest.

## Global Constraints

- Work directly on `master`; the user explicitly declined an isolated branch/worktree.
- Production scope is the outer `apps/workbench/static/index.html` plus the nine documents in `apps/workbench/static/app.js` `PAGES`; `apps/workbench/static/preview/index.html` stays local, ignored, and out of all delivery checks.
- Do not change application behavior, API contracts, page routing, or iframe keep-alive behavior. JavaScript may change only when replacing a presentation color literal with a computed v2 token value.
- Exactly two themes remain: `midnight` (default) and `daylight`; CSS owns color definitions and JS only switches/broadcasts the `data-theme` name.
- RX is teal/green (`--color-dir-rx`); TX is amber (`--color-dir-tx`) for every page. Direction must also be represented by existing direction text/icon/class, never color alone.
- Components/pages may consume semantic/component tokens only; do not add raw hex/rgb/hsl color values outside `tokens-v2.css`. `transparent`, `currentColor`, and alpha derived from an existing token are allowed.
- Normal text contrast is at least 4.5:1; retain `contrast-v2.py` as the color-pair gate.
- Preserve existing unrelated working-tree files. Agents must not commit, stage, reset, checkout, clean, or delete files; the lead integrates reviewed batches sequentially.

---

## File Ownership and Interfaces

| Task | Exclusive writable files | Produces |
|---|---|---|
| 1 | `reqs/0012-workbench-theme-refactor/{TODO.md,REQS.md,verify-theme-coverage.js,test_verify_theme_coverage.py}` | P2/P3 acceptance contract and a failing-then-passing static coverage gate |
| 2 | `apps/workbench/static/{index.html,styles.css,workbench.html}`, `apps/workbench/static/pages/{serial-profile/serial-profile.html,maintenance/maintenance.html}` | A-family pages with direct v2 tokens |
| 3 | `apps/workbench/static/pages/{trace/trace.html,dict/dict.html,scenario/scenario.html,simcon/simcon.html}` | B-family pages with standard token names |
| 4 | `apps/workbench/static/pages/module-serial/{module-serial.html,styles.css}` plus a module JS file only if a color literal is used for rendering | C-family module page with RX/TX convention |
| 5 | `apps/workbench/static/pages/listener/{index.html,styles.css,app.js}` | C-family listener page with RX/TX convention |
| 6 | `apps/workbench/static/{compat-dialects.css,tokens.css,app.js}`, `reqs/0010-workbench-ui-landing/REQS.md` | Removed legacy assets and one theme registry contract |
| 7 | `reqs/0012-workbench-theme-refactor/{TODO.md,REQS.md,REFACTOR-PLAN.md}` | Final status/evidence record only after all gates pass |

## Standard Token Mapping

Every conversion below replaces usages, then removes the legacy definition. No compatibility variable is retained.

| Legacy A | Standard v2 | Legacy B | Standard v2 |
|---|---|---|---|
| `--bg-page` | `--color-bg-canvas` | `--bg-0` | `--color-bg-canvas` |
| `--bg-surface` | `--color-bg-surface` | `--bg-1` | `--color-bg-surface` |
| `--bg-elevated` | `--color-bg-raised` | `--bg-2` | `--color-bg-raised` |
| `--bg-input` | `--color-bg-input` | `--bg-3` | `--color-bg-elevated` |
| `--bg-hover` | `--color-bg-hover` | `--bg-4` | `--color-bg-hover` |
| `--bg-active` | `--color-bg-active` | `--tx-1` | `--color-fg-default` |
| `--fg-default` | `--color-fg-default` | `--tx-2` | `--color-fg-muted` |
| `--fg-muted` | `--color-fg-muted` | `--tx-3` | `--color-fg-subtle` |
| `--fg-subtle` | `--color-fg-subtle` | `--tx-4` | `--color-fg-dim` |
| `--fg-dim` | `--color-fg-dim` | `--ac` | `--color-accent` |
| `--border` | `--color-border` | `--am` | `--color-status-warn` |
| `--border-strong` | `--color-border-strong` | `--rx-c` | `--color-dir-rx` |
| `--accent` | `--color-accent` | `--tx-c` | `--color-dir-tx` |
| `--accent-strong` | `--color-accent-strong` | | |

| Legacy C | Standard v2 |
|---|---|
| `--canvas` | `--color-bg-canvas` |
| `--canvas-2`, `--panel` | `--color-bg-surface` |
| `--panel-raised` | `--color-bg-raised` |
| `--ink` | `--color-fg-default` |
| `--muted` | `--color-fg-muted` |
| `--faint` | `--color-fg-subtle` |
| `--cyan` | `--color-accent` (interaction only, never direction) |
| receiver/receive/RX classes | `--color-dir-rx` |
| sender/send/TX classes | `--color-dir-tx` |

## Task 1: Contract, decision record, and coverage gate

**Files:**
- Create: `reqs/0012-workbench-theme-refactor/verify-theme-coverage.js`
- Create: `reqs/0012-workbench-theme-refactor/test_verify_theme_coverage.py`
- Modify: `reqs/0012-workbench-theme-refactor/TODO.md`
- Modify: `reqs/0012-workbench-theme-refactor/REQS.md`

**Interfaces:** `verify-theme-coverage.js` exits zero only when it prints `主题覆盖率：10 个生产页面 / 0 issues`. It takes no arguments and evaluates the ten production documents plus their owned CSS. `test_verify_theme_coverage.py` launches it using `node` and asserts that exact success line.

- [ ] Write `test_verify_theme_coverage.py` first with one `unittest` case that invokes `node verify-theme-coverage.js`, expects return code 0 and the exact success line. Run it before adding the verifier; expected result: failure because the file is absent.
- [ ] Implement `verify-theme-coverage.js` using only Node built-ins (`fs`, `path`). Its page manifest contains the outer document plus `workbench`, `serial-profile`, `maintenance`, `trace`, `dict`, `scenario`, `simcon`, `module-serial`, and `listener`. For each document assert a direct `tokens-v2.css` link, a head pre-paint script that validates `midnight|daylight`, no `tokens.css` or `compat-dialects.css` reference, and no legacy theme selector. For its owned CSS/HTML assert no `var(--bg-`, `var(--fg-`, `var(--tx-`, `var(--ac)`, `var(--am)`, `var(--canvas`, `var(--panel`, `var(--ink)`, `var(--muted)`, `var(--faint)`, or `var(--cyan)` use and no raw CSS color literal. Print each issue then set non-zero; otherwise print the interface success line.
- [ ] Update `TODO.md` and `REQS.md` to replace the former manual color-direction blocker with the approved convention `RX=青绿，TX=琥珀`, identify it as P5's final decision, and set P2/P3 to active until the final task marks them complete. Do not claim completion here.
- [ ] Run `python reqs/0012-workbench-theme-refactor/test_verify_theme_coverage.py -v`; expected result: initially fails before conversion and stays failing until Tasks 2–6 complete.

## Task 2: Outer shell and A-family conversion

**Files:**
- Modify: `apps/workbench/static/index.html`
- Modify: `apps/workbench/static/styles.css`
- Modify: `apps/workbench/static/workbench.html`
- Modify: `apps/workbench/static/pages/serial-profile/serial-profile.html`
- Modify: `apps/workbench/static/pages/maintenance/maintenance.html`

**Interfaces:** Each document links `tokens-v2.css` directly before its local stylesheet, keeps its validated data-theme boot script, and no longer needs any A-family variable alias.

- [ ] Locate all A-family variables and raw colors in exactly these files; record the original counts with `rg -n -- '--(bg|fg|accent|border)|#[0-9a-fA-F]{3,8}|rgb\\(' <files>`.
- [ ] Replace all A-family variables with the Standard Token Mapping table and replace presentation raw colors with the semantic/component token whose role matches the selector. Remove each private `:root` theme/color definition rather than shadowing v2.
- [ ] Add a direct relative/absolute `tokens-v2.css` link to `serial-profile.html` and `maintenance.html`; preserve all existing scripts, form names, routes, iframe IDs, and JS behavior.
- [ ] Run `node reqs/0012-workbench-theme-refactor/verify-p0.js`; expected result: `10 production pages / 0 issues`. Run the Task 1 verifier and report its current remaining issue count (other families may still fail).

## Task 3: B-family conversion

**Files:**
- Modify: `apps/workbench/static/pages/trace/trace.html`
- Modify: `apps/workbench/static/pages/dict/dict.html`
- Modify: `apps/workbench/static/pages/scenario/scenario.html`
- Modify: `apps/workbench/static/pages/simcon/simcon.html`

**Interfaces:** The four design pages use direct `tokens-v2.css`, keep their existing document-specific JS/API behavior, and use `--color-dir-rx`/`--color-dir-tx` only where their existing markup already identifies a direction.

- [ ] Add the direct v2 stylesheet link to all four documents before inline style blocks.
- [ ] Replace all B variable uses by the exact B mapping table; delete their local `:root` variable definitions and raw presentation colors. Preserve `transparent` and `currentColor` only where used as non-color visual behavior.
- [ ] Do not make interaction accent (`--color-accent`) stand for RX or TX. Any existing receiver/sender class uses `--color-dir-rx`/`--color-dir-tx` respectively while retaining its text/icon distinction.
- [ ] Run `node reqs/0012-workbench-theme-refactor/verify-p0.js`; expected result: `10 production pages / 0 issues`. Run the Task 1 verifier and report current remaining issue count.

## Task 4: Module-log C-family conversion

**Files:**
- Modify: `apps/workbench/static/pages/module-serial/module-serial.html`
- Modify: `apps/workbench/static/pages/module-serial/styles.css`
- Modify only if needed: the module page's existing JavaScript file that contains a presentation color literal

**Interfaces:** Direction is explicit: receiver/RX uses `--color-dir-rx`, sender/TX uses `--color-dir-tx`; `--color-accent` remains interaction/focus only. All existing serial/log controls and message payload processing remain byte-for-byte unchanged outside presentation color extraction.

- [ ] Add a direct `tokens-v2.css` link before the local stylesheet, then replace all C aliases and `var(--x, #fallback)` with the C mapping table. Remove the local C color `:root` block and every fallback raw color that v2 now supplies.
- [ ] Inspect every selector/class whose label or data field denotes receiving/sending. Bind RX/receive to `--color-dir-rx` and TX/send to `--color-dir-tx`, without renaming business data values or reversing labels.
- [ ] If canvas/SVG/JS assigns a literal visual color, replace only that literal with `getComputedStyle(document.documentElement).getPropertyValue('--color-…').trim()` read at render time; do not alter parsing, event, request, or storage logic.
- [ ] Run `node reqs/0012-workbench-theme-refactor/verify-p0.js`; expected result: `10 production pages / 0 issues`. Run the Task 1 verifier and report current remaining issue count.

## Task 5: Listener C-family conversion

**Files:**
- Modify: `apps/workbench/static/pages/listener/index.html`
- Modify: `apps/workbench/static/pages/listener/styles.css`
- Modify: `apps/workbench/static/pages/listener/app.js` only for presentation colors

**Interfaces:** The listener still starts/stops, filters, renders, and routes exactly as before. A received frame is teal/green through `--color-dir-rx`; a transmitted frame is amber through `--color-dir-tx`; canvas rendering reads the correct computed v2 token after each theme change.

- [ ] Add direct v2 CSS before the local stylesheet. Convert every legacy C variable/raw presentation color using the C mapping table and remove local C color definitions.
- [ ] For the existing canvas trace color literal, introduce a small presentation-only helper that reads a named v2 token from `document.documentElement`; call it during drawing so changing `data-theme` changes the next render. Do not modify listener APIs, timers, protocol parsing, message schemas, or user-data filters.
- [ ] Map each existing receive/send visual selector according to the approved convention, retaining any existing `RX`/`TX` text or icon cue.
- [ ] Run `node reqs/0012-workbench-theme-refactor/verify-p0.js`; expected result: `10 production pages / 0 issues`. Run the Task 1 verifier and report current remaining issue count.

## Task 6: Converge to the one source of truth

**Files:**
- Delete: `apps/workbench/static/compat-dialects.css`
- Delete: `apps/workbench/static/tokens.css`
- Modify: `apps/workbench/static/app.js`
- Modify: `reqs/0010-workbench-ui-landing/REQS.md`

**Interfaces:** `tokens-v2.css` is the only imported token stylesheet. `app.js` uses the registry read from `--theme-registry` (or a single `THEMES` array containing only `midnight` and `daylight`) and broadcasts only those names.

- [ ] First run the Task 1 verifier and require its zero-issue result before deleting any token file.
- [ ] Inspect `app.js` theme initialization, persistence, click handlers, and `postMessage` payload. Remove legacy theme names and generate dots/options from the one canonical two-theme registry; preserve the existing local-storage key and message `{ type: 'wb-theme-change', theme }` contract.
- [ ] Remove all imports/references to `compat-dialects.css` and `tokens.css`, then delete exactly those two files. Do not delete `tokens-v2.css` or `preview/`.
- [ ] Update REQS-0010 P5 from pending to completed-by-REQS-0012, linking the two themes and the RX/TX convention without changing its unrelated P1–P4 scope.
- [ ] Run `node reqs/0012-workbench-theme-refactor/verify-theme-coverage.js`; expected `主题覆盖率：10 个生产页面 / 0 issues`.

## Task 7: Final audit and authoritative status record

**Files:**
- Modify: `reqs/0012-workbench-theme-refactor/TODO.md`
- Modify: `reqs/0012-workbench-theme-refactor/REQS.md`
- Modify: `reqs/0012-workbench-theme-refactor/REFACTOR-PLAN.md`

**Interfaces:** All P2/P3 items are marked complete only with fresh command evidence and the final RX/TX decision; historical diagnosis remains readable and is annotated rather than rewritten as if it never existed.

- [ ] Run these fresh commands from repository root:

```powershell
python reqs/0012-workbench-theme-refactor/test_contrast_v2.py -v
python reqs/0012-workbench-theme-refactor/test_verify_theme_coverage.py -v
node reqs/0012-workbench-theme-refactor/verify-p0.js
node reqs/0012-workbench-theme-refactor/verify-theme-coverage.js
git diff --check
```

Expected: contrast `50 组 / FAIL 0`; both Python suites pass; both Node scripts report 10 pages / 0 issues; `git diff --check` prints nothing and exits zero.

- [ ] Start the existing workbench application from the working tree and use a real Chromium/Chrome engine (not jsdom) to inspect all ten production URLs under both `midnight` and `daylight`. Record only observed facts: each body background computed value differs between themes; no console-load failure; preview is excluded. If a browser engine is unavailable, leave the visual gate unclaimed and report that blocker rather than fabricating a pass.
- [ ] Append a dated entry in `TODO.md` that marks every P2/P3 checklist item complete, records `RX=青绿 / TX=琥珀`, names all four automated gates, and says `preview/` remains ignored/uncommitted. Align REQS status and add a P5 completion addendum to the historical plan without erasing its former risk discussion.
- [ ] Lead runs task-scoped reviews plus a final whole-change review; only then stages an explicit allowlist and creates integration commits.

## Plan Self-Review

- Coverage: P2 page onboarding, approved direction colors, private fallback cleanup, single registry, coverage assertion, two-theme convergence, auto-follow, alias/legacy-file removal, REQS-0010 closure, TODO synchronization, and visual/static/contrast regression gates each have a task.
- No placeholders: every task declares exclusive files, exact mapping, verification command, expected output, and the no-business-logic boundary.
- Parallel safety: Tasks 1–5 have disjoint writable files. Task 6 waits for all of them; Task 7 waits for Task 6.
