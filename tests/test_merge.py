"""Golden tests for merge._extract_json + merge.validate_themes / _normalize_theme."""
import json
from pathlib import Path

import pytest

from observer.synth.merge import _extract_json, validate_themes, _validate_theme

FIXTURES = Path(__file__).parent / "fixtures" / "grok_output"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ============================================================
# _extract_json — handles raw / fenced / prose / nested braces
# ============================================================

def test_extract_clean_json():
    parsed = _extract_json(_load("clean.json"))
    assert "themes" in parsed
    assert parsed["themes"][0]["name"] == "AI 半导体"


def test_extract_fenced_json_label():
    parsed = _extract_json(_load("fenced_json.txt"))
    assert parsed["themes"][0]["name"] == "新冒头题材"


def test_extract_fenced_bare():
    parsed = _extract_json(_load("fenced_bare.txt"))
    assert parsed["themes"][0]["name"] == "题材A"


def test_extract_prose_suffix():
    parsed = _extract_json(_load("prose_suffix.txt"))
    assert parsed["themes"][0]["name"] == "尾随题材"


def test_extract_braces_in_string():
    parsed = _extract_json(_load("braces_in_string.json"))
    assert "{curly braces}" in parsed["themes"][0]["narrative"]


def test_extract_no_brace_raises():
    with pytest.raises(ValueError):
        _extract_json("there is no JSON here")


# ============================================================
# validate_themes — coerces string→list, drops invalid, enums
# ============================================================

def test_clean_themes_pass():
    raw = json.loads(_load("clean.json"))["themes"]
    valid, warnings = validate_themes(raw)
    assert len(valid) == 1
    assert valid[0]["heat"] == "high"
    assert valid[0]["tempo"] == "accelerating"
    assert warnings == []


def test_string_evidence_coerced_to_list():
    raw = json.loads(_load("string_evidence.json"))["themes"]
    valid, _ = validate_themes(raw)
    assert len(valid) == 1
    t = valid[0]
    assert isinstance(t["evidence"], list) and len(t["evidence"]) == 1
    assert t["evidence"][0].startswith("这是一整段")
    assert isinstance(t["sources"], list) and t["sources"] == ["xai_x_24h"]


def test_invalid_enums_corrected_or_dropped():
    raw = json.loads(_load("invalid_enums.json"))["themes"]
    valid, warnings = validate_themes(raw)
    # 第二个题材两窗口都没热 → 必须被丢弃
    assert len(valid) == 1
    t = valid[0]
    # 无效 heat → low
    assert t["heat"] == "low"
    # 无效 tempo → 由 hot_24h/hot_7d 推导，hot_24h=true hot_7d=false → "new"
    assert t["tempo"] == "new"
    assert any("invalid heat" in w for w in warnings)
    assert any("invalid tempo" in w for w in warnings)
    assert any("dropping" in w for w in warnings)


def test_missing_name_dropped():
    raw = [{"name": "", "heat": "high", "hot_24h": True}]
    valid, warnings = validate_themes(raw)
    assert len(valid) == 0
    assert any("missing required 'name'" in w for w in warnings)


def test_non_dict_theme_dropped():
    raw = ["not a dict", 123]
    valid, warnings = validate_themes(raw)
    assert len(valid) == 0
    assert len(warnings) == 2


def test_string_ticker_wrapped():
    raw = [{
        "name": "test",
        "heat": "high",
        "hot_24h": True,
        "hot_7d": False,
        "tempo": "new",
        "tickers": ["$NVDA", {"code": "$AMD", "name_cn": "AMD"}],
    }]
    valid, _ = validate_themes(raw)
    t = valid[0]
    assert len(t["tickers"]) == 2
    assert t["tickers"][0] == {"code": "$NVDA", "name_cn": "", "name_native": ""}
    assert t["tickers"][1]["code"] == "$AMD"


def test_themes_not_list():
    valid, warnings = validate_themes("not a list")
    assert valid == []
    assert any("not a list" in w for w in warnings)
