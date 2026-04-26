"""LLM merging — structured JSON output per market for clean rendering."""
import json
import re
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from observer.config import XAI_API_KEY, XAI_BASE_URL, XAI_MODEL


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
    """Extract first JSON object from possibly-wrapped LLM output."""
    text = text.strip()
    # strip ```json fences
    fence = re.match(r"^```(?:json)?\s*\n?(.+?)\n?```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # find first '{' to last '}'
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in:\n{text[:500]}")
    return json.loads(text[start:end + 1])


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
        # fallback: keep as raw text so caller can show something
        return {
            "market": market,
            "themes": [],
            "raw_text": text,
            "parse_error": str(e),
            "cost_usd": cost,
        }

    themes = parsed.get("themes", [])
    return {
        "market": market,
        "themes": themes,
        "cost_usd": cost,
    }
