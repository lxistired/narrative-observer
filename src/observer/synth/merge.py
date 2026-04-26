"""LLM merging — structured JSON output per market for clean rendering."""
import json
import re
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from observer.config import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL


VALID_HEAT = {"high", "mid", "low"}
VALID_TEMPO = {"new", "accelerating", "sustained", "fading"}
VALID_SOURCES = {
    "xai_x_24h", "xai_x_7d",
    "reddit_wsb", "reddit_stocks", "reddit_investing", "reddit_cryptocurrency", "reddit",
    "ptt", "krx", "naver_themes", "naver",
}


SCHEMA_DOC = """{
  "themes": [
    {
      "name": "题材中文名（必须中文，简短，5-12字）",
      "name_native": "原始语言名（如英文/韩文/繁中），可空",
      "heat": "high | mid | low",
      "cross_source": true,        // 多个数据源都提到 = true
      "hot_24h": true,             // 在 24h 窗口出现/被讨论 = true
      "hot_7d":  true,             // 在 7d  窗口出现/被讨论 = true
      "tempo":  "new | accelerating | sustained | fading",
      "tickers": [
        {
          "code": "$NVDA 或 005930.KS 或 2330.TW",
          "name_cn": "中文公司名（韩股必填中译）",
          "name_native": "原文名（韩文/英文/繁中），可空"
        }
      ],
      "narrative": "1-2句中文核心叙事，要有信息密度",
      "evidence": [
        "1句具体证据（含数字/帖文摘要/链接），中文"
      ],
      "sources": ["xai_x_24h", "xai_x_7d", "reddit_wsb", "ptt", "krx", "naver_themes"]
    }
  ]
}"""


PROMPT_TEMPLATE = """你是金融叙事整合分析师。下面是同一个市场（{market_label}）当日多个数据源的散户讨论/资金流摘要。

任务：合并去重，输出 JSON 结构化的统一题材热度榜（5-7条），按真实热度排序。

### 严格翻译规则（重要）

- 韩股：所有韩文公司名/股票名必须给出中文译名（如 SK하이닉스 → SK海力士、두산에너빌리티 → 斗山能源、삼성전자 → 三星电子、한미반도체 → 韩美半导体）。原文写在 name_native 字段。
- 美股：ticker 保留 $XXX 格式，公司名给中文（如 Tesla → 特斯拉、Palantir → Palantir/帕兰提尔保留英文亦可）。
- 台股：保留繁体或转简体均可，但要一致。代码格式 2330.TW。
- 题材名一律用简洁中文。

### 多源融合规则

- 同一题材在 ≥2 个数据源出现 → cross_source = true，排序靠前
- 仅 1 个源 → 也保留但 cross_source = false
- 真无信号就少条，不凑数

### 时间窗口判定（重要新维度）

数据源会包含两个 xAI 时间窗口的搜索结果：**24h 窗口** 和 **7d 窗口**。

每个题材必须判定：
- `hot_24h`: 该题材在 24h 窗口的内容里出现 / 被显著讨论 → true，否则 false
- `hot_7d`:  该题材在 7d  窗口的内容里出现 / 被显著讨论 → true，否则 false
- `tempo`：综合时序节奏，4选1：
  - `"new"`        → hot_24h=true, hot_7d=false        // 新冒头
  - `"accelerating"` → hot_24h=true, hot_7d=true 且 24h 提及强度大 // 加速中
  - `"sustained"`  → hot_24h=true, hot_7d=true 且 平稳 // 本周持续
  - `"fading"`     → hot_24h=false, hot_7d=true        // 退潮

**至少 hot_24h / hot_7d 之一为 true**，否则不要输出该题材。
sources 数组里要把对应窗口（"xai_x_24h" 或 "xai_x_7d"）写进去，证据来自哪个窗口就标哪个。

### 数据源（{market_label}）

{sources_block}

### 输出格式

只输出一个 JSON 对象，无其他文字。Schema:

```
{schema_doc}
```

记住：JSON 键名严格按 schema，值全部中文（除 ticker code 和 name_native）。"""


def _format_sources(sources: dict) -> str:
    blocks = []
    for src_name, content in sources.items():
        if not content:
            continue
        blocks.append(f"#### 来源: {src_name}\n{content}\n")
    return "\n".join(blocks) if blocks else "(无数据)"


def _extract_json(text: str) -> dict:
    """Extract first JSON object from possibly-wrapped LLM output.

    Handles three common Grok shapes:
      1. raw JSON
      2. ```json\n{...}\n``` fenced block (possibly with prose before/after)
      3. JSON with trailing prose
    """
    text = text.strip()
    # 1. fenced block — match first ```json ... ``` even if prose surrounds it
    m = re.search(r"```(?:json)?\s*\n(.+?)\n```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 2. find balanced first-{ to matching-}
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no '{{' found in:\n{text[:500]}")
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        raise ValueError(f"unbalanced JSON braces in:\n{text[:500]}")
    return json.loads(text[start:end + 1])


def _validate_theme(t: dict) -> tuple[dict | None, list[str]]:
    """Validate one theme. Returns (cleaned_theme | None, list_of_warnings).

    Returns None if theme is unsalvageable. Otherwise returns a cleaned dict
    with enums coerced to defaults if invalid, and a list of warnings logged.
    """
    warnings = []

    if not isinstance(t, dict):
        return None, [f"theme is not a dict: {type(t).__name__}"]

    name = t.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, ["theme missing required 'name'"]

    out = dict(t)

    # heat must be in enum, else default to "low" with warning
    heat = out.get("heat")
    if heat not in VALID_HEAT:
        warnings.append(f"theme {name!r}: invalid heat={heat!r} → defaulting to 'low'")
        out["heat"] = "low"

    # tempo must be in enum, else default by hot_24h/hot_7d combination
    hot_24h = bool(out.get("hot_24h"))
    hot_7d = bool(out.get("hot_7d"))
    out["hot_24h"] = hot_24h
    out["hot_7d"] = hot_7d

    if not hot_24h and not hot_7d:
        # neither window — skip per prompt rule
        return None, [f"theme {name!r}: not hot in either window, dropping"]

    tempo = out.get("tempo")
    if tempo not in VALID_TEMPO:
        # derive from window flags
        if hot_24h and not hot_7d:
            out["tempo"] = "new"
        elif hot_24h and hot_7d:
            out["tempo"] = "sustained"
        elif not hot_24h and hot_7d:
            out["tempo"] = "fading"
        else:
            out["tempo"] = "new"  # unreachable given guard above
        warnings.append(f"theme {name!r}: invalid tempo={tempo!r} → derived {out['tempo']!r}")

    # cross_source coerce to bool
    out["cross_source"] = bool(out.get("cross_source"))

    # sources: keep only known + ensure list
    raw_sources = out.get("sources") or []
    if isinstance(raw_sources, str):
        raw_sources = [raw_sources]
    cleaned_sources = []
    for s in raw_sources:
        if isinstance(s, str) and s in VALID_SOURCES:
            cleaned_sources.append(s)
        elif isinstance(s, str):
            warnings.append(f"theme {name!r}: unknown source {s!r} (kept anyway)")
            cleaned_sources.append(s)
    out["sources"] = cleaned_sources

    # tickers: must be list of dicts with at least 'code'
    raw_tickers = out.get("tickers") or []
    if isinstance(raw_tickers, str):
        raw_tickers = [raw_tickers]
    cleaned_tickers = []
    for tk in raw_tickers:
        if isinstance(tk, str):
            cleaned_tickers.append({"code": tk, "name_cn": "", "name_native": ""})
        elif isinstance(tk, dict) and tk.get("code"):
            cleaned_tickers.append({
                "code": str(tk.get("code", "")),
                "name_cn": str(tk.get("name_cn") or ""),
                "name_native": str(tk.get("name_native") or ""),
            })
        else:
            warnings.append(f"theme {name!r}: dropping malformed ticker {tk!r}")
    out["tickers"] = cleaned_tickers

    # evidence: list of strings
    raw_evidence = out.get("evidence") or []
    if isinstance(raw_evidence, str):
        raw_evidence = [raw_evidence]
    out["evidence"] = [str(e) for e in raw_evidence if e]

    # narrative: string
    nar = out.get("narrative")
    if not isinstance(nar, str):
        out["narrative"] = "" if nar is None else str(nar)

    return out, warnings


def validate_themes(raw_themes: list) -> tuple[list[dict], list[str]]:
    """Validate a list of themes. Returns (valid_themes, all_warnings)."""
    if not isinstance(raw_themes, list):
        return [], [f"themes is not a list: {type(raw_themes).__name__}"]
    valid = []
    all_warnings = []
    for t in raw_themes:
        cleaned, warns = _validate_theme(t)
        all_warnings.extend(warns)
        if cleaned is not None:
            valid.append(cleaned)
    return valid, all_warnings


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def merge_market(market: str, market_label: str, today: str, sources: dict) -> dict:
    sources_block = _format_sources(sources)
    prompt = PROMPT_TEMPLATE.format(
        market_label=market_label,
        sources_block=sources_block,
        schema_doc=SCHEMA_DOC,
    )
    body = {
        "model": XAI_MODEL,
        "input": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=180.0) as client:
        r = client.post(f"{XAI_BASE_URL}/responses", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()

    text = ""
    for o in data.get("output", []):
        if o.get("type") == "message":
            for c in o.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")

    usage = data.get("usage", {})
    cost = usage.get("cost_in_usd_ticks", 0) / 1e10

    try:
        parsed = _extract_json(text)
    except Exception as e:
        return {
            "market": market,
            "themes": [],
            "raw_text": text,
            "parse_error": str(e),
            "cost_usd": cost,
            "warnings": [],
        }

    raw_themes = parsed.get("themes", [])
    valid_themes, warnings = validate_themes(raw_themes)
    for w in warnings:
        print(f"  ⚠ schema: {w}")

    return {
        "market": market,
        "themes": valid_themes,
        "cost_usd": cost,
        "warnings": warnings,
        "themes_dropped": len(raw_themes) - len(valid_themes),
    }
