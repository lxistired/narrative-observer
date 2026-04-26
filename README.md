# Narrative Observer · 散户叙事观测

三市场（美 / 台 / 韩）散户题材热度自动观测，多源融合，生成静态 HTML 站点。

## 数据源

| 市场 | 数据源 |
|---|---|
| US | xAI x_search (X 全网) + Reddit `.json` (WSB / stocks / investing / CryptoCurrency) |
| TW | xAI x_search + PTT Stock 板 |
| KR | xAI x_search + KRX 散户净买入 (pykrx) + Naver 题材榜 |

每市场拉完原始信号 → Grok 合成统一题材热度榜 → 渲染为 editorial 风格 HTML。

## 跑

```bash
# 安装
python3 -m venv .venv
.venv/bin/pip install -e .

# 设置 xAI key
echo 'XAI_API_KEY=xai-...' > .env

# 跑一发
.venv/bin/observer run

# 单市场冒烟测试（最便宜）
.venv/bin/observer probe us --window 7

# 重建索引
.venv/bin/observer reindex

# 本地预览
cd site && python3 -m http.server 8765
# → http://localhost:8765/
```

## 部署到 GitHub Pages

仓库 Settings：

1. **Pages** → Source = `GitHub Actions`
2. **Secrets and variables → Actions**：
   - Secret `XAI_API_KEY` = 您的 xAI key
   - Variable `SITE_URL` = `https://<您id>.github.io/<repo>` （RSS 用）
   - Variable `XAI_MODEL` = `grok-4-1-fast-reasoning` (默认)

Push 到 `main` → Actions 会按 cron（每 6h）自动跑 → 部署到 Pages。也可手动从 Actions 页 Run workflow。

## 成本

每次完整跑 ~$0.10-0.20（xAI tool fees + tokens）。
- 4 次/天 × 30 天 ≈ **$15-25/月**
- 频率改 2 次/天 ≈ $7-12/月

## 结构

```
src/observer/
  ├── config.py         # env + paths
  ├── sources/
  │   ├── xai.py        # x_search 三市场封装
  │   ├── reddit.py     # 4 sub hot+top
  │   ├── ptt.py        # PTT Stock 板
  │   ├── krx.py        # 散户净买入 (pykrx)
  │   └── naver.py      # finance.naver.com 题材榜
  ├── synth/merge.py    # Grok 合成市场叙事
  ├── render/
  │   ├── html.py       # markdown → 编辑部风格 HTML
  │   └── feed.py       # RSS feed
  └── cli.py            # 入口
templates/
  ├── _base.html.j2     # masthead / theme toggle / 字体
  ├── report.html.j2    # 单期报告
  └── index.html.j2     # 期刊存档
.github/workflows/run.yml  # cron + Pages 部署
```

## 设计

Editorial Quarterly · Stripe Press × FT Weekend × Linear docs

- Newsreader (display) + Inter (UI) + JetBrains Mono (tickers) + Noto Serif SC (中文)
- 单一 oxblood (#7c2d3a) accent
- 暖白纸 (#f6f1e7) 浅色 / 深墨 (#161311) 深色
- SVG 噪点纸纹叠加
- ticker $NVDA / 2330.TW / （000660）自动识别成 mono chip
- 题材热度 emoji → 椭圆形 typography pill (low / mid / high)
