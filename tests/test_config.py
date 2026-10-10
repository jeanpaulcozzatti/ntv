from pathlib import Path

from ntvstreams.config import load_config


def test_defaults_when_file_missing(tmp_path: Path):
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.model == "claude-opus-5-5"
    assert cfg.window_hours == 48
    assert cfg.publish.backend == "local"
    assert cfg.effort == "medium"
    assert cfg.monthly_input_token_ceiling == 5_000_000


def test_reads_toml(tmp_path: Path):
    (tmp_path / "c.toml").write_text('model="claude-sonnet-5-5"\n[publish]\nbackend="s3"\nbucket="b"\n')
    cfg = load_config(tmp_path / "c.toml")
    assert cfg.model == "claude-sonnet-5-5"
    assert cfg.publish.backend == "s3"
    assert cfg.publish.bucket == "b"
