from pathlib import Path

from term5.config import load_config


def test_defaults_match_alpha_profile(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.model.model == "deepseek-flash"
    assert cfg.model.context_limit == 1_000_000
    assert cfg.model.max_output_tokens == 384_000
    assert cfg.fim.hard_max_output_tokens == 4096
    assert cfg.reasoning.max_parallel_total == 32
    assert cfg.ui.web_enabled is False
