"""Snapshot tests for render module — ensures template + normalize stay aligned."""
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def site_tmp(tmp_path, monkeypatch):
    """Point SITE_DIR at a tmp dir before importing render."""
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    # observer.config caches paths from ROOT — patch SITE_DIR after import
    from observer import config
    monkeypatch.setattr(config, "SITE_DIR", site)
    from observer.render import html as render_html
    monkeypatch.setattr(render_html, "SITE_DIR", site)
    return site


def _sample_merged():
    return {
        "us": {
            "themes": [
                {
                    "name": "AI 半导体",
                    "name_native": "AI semiconductors",
                    "heat": "high",
                    "cross_source": True,
                    "hot_24h": True, "hot_7d": True,
                    "tempo": "accelerating",
                    "tickers": [
                        {"code": "$NVDA", "name_cn": "英伟达", "name_native": "NVIDIA"},
                    ],
                    "narrative": "散户押注 AI",
                    "evidence": ["WSB 1200 赞"],
                    "sources": ["xai_x_24h", "reddit_wsb"],
                }
            ],
        },
        "tw": {
            "all_sources_failed": True,
            "errors": ["xAI: ConnectError", "PTT: timeout"],
            "themes": [],
        },
        "kr": {
            "merge_error": "schema parse failed",
            "themes": [],
        },
    }


def test_render_report_writes_file(site_tmp):
    from observer.render.html import render_report
    out = render_report(_sample_merged(), {}, total_cost=0.12)
    assert out.exists()
    body = out.read_text(encoding="utf-8")

    # core content
    assert "AI 半导体" in body
    assert "$NVDA" in body
    assert "英伟达" in body
    # tempo + window badges
    assert "加速中" in body
    # status banners for failed/degraded markets
    assert "数据源全部失败" in body
    assert "合成失败" in body
    # smoke: output is HTML (jinja may emit leading newline)
    assert "<!DOCTYPE html>" in body[:100]


def test_render_report_xss_safe(site_tmp):
    """If a malicious theme name slipped through, it must NOT execute."""
    from observer.render.html import render_report
    merged = {
        "us": {
            "themes": [{
                "name": "<script>alert('xss')</script>",
                "heat": "high",
                "cross_source": False,
                "hot_24h": True, "hot_7d": True,
                "tempo": "sustained",
                "tickers": [],
                "narrative": "",
                "evidence": [],
                "sources": [],
            }],
        },
    }
    out = render_report(merged, {}, total_cost=0)
    body = out.read_text(encoding="utf-8")
    # raw script tag must not appear; escaped form must
    assert "<script>alert('xss')</script>" not in body
    assert "&lt;script&gt;" in body


def test_render_index_handles_hhmm_and_hhmmss(site_tmp):
    # write some fake report files with both timestamp formats
    (site_tmp / "2026-04-26_1500.html").write_text("<html></html>")
    (site_tmp / "2026-04-26_153045.html").write_text("<html></html>")
    from observer.render.html import render_index
    out = render_index()
    body = out.read_text(encoding="utf-8")
    assert "2026-04-26 · 15:00 UTC" in body
    assert "2026-04-26 · 15:30 UTC" in body
    assert out.exists()
