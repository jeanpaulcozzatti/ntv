from pathlib import Path

from ntvstreams.config import Config, PublishConfig
from ntvstreams.publish import LocalPublisher, S3Publisher, get_publisher


def _site(root: Path) -> Path:
    out = root / "out"
    (out / "static").mkdir(parents=True)
    (out / "index.html").write_text("<p>v1</p>")
    (out / "static" / "site.css").write_text("body{}")
    return out


def test_local_mirror_copies_and_deletes(tmp_path):
    out = _site(tmp_path)
    target = tmp_path / "site"
    target.mkdir()
    (target / "index.html").write_text("<p>v0</p>")
    (target / "stale.html").write_text("old")
    actions = LocalPublisher(target).publish(out)
    assert (target / "index.html").read_text() == "<p>v1</p>"
    assert (target / "static" / "site.css").exists()
    assert not (target / "stale.html").exists()
    assert any("index.html" in a for a in actions) and any("stale.html" in a for a in actions)


def test_local_mirror_dry_run_changes_nothing(tmp_path):
    out = _site(tmp_path)
    target = tmp_path / "site"
    actions = LocalPublisher(target).publish(out, dry_run=True)
    assert not target.exists() and actions


def test_local_same_dir_is_noop(tmp_path):
    out = _site(tmp_path)
    assert LocalPublisher(out).publish(out) == []
    assert LocalPublisher(tmp_path / "out" / "." ).publish(out) == []


def test_s3_dry_run_lists_commands(tmp_path):
    out = _site(tmp_path)
    calls = []
    pub = S3Publisher("my-bucket", "site", "E123", runner=lambda argv: calls.append(argv) or 0)
    actions = pub.publish(out, dry_run=True)
    assert calls == []
    assert actions[0].startswith("aws s3 sync") and "s3://my-bucket/site" in actions[0] and "--delete" in actions[0]
    assert "create-invalidation" in actions[1] and "E123" in actions[1]


def test_s3_runs_sync_then_invalidation(tmp_path):
    out = _site(tmp_path)
    calls = []
    pub = S3Publisher("my-bucket", "", None, runner=lambda argv: calls.append(argv) or 0)
    pub.publish(out)
    assert len(calls) == 1 and calls[0][:3] == ["aws", "s3", "sync"] and calls[0][3] == str(out) and calls[0][4] == "s3://my-bucket/"
    calls.clear()
    S3Publisher("b", "p", "E9", runner=lambda argv: calls.append(argv) or 0).publish(out)
    assert [c[1] for c in calls] == ["s3", "cloudfront"]


def test_s3_failure_raises(tmp_path):
    out = _site(tmp_path)
    import pytest
    with pytest.raises(RuntimeError):
        S3Publisher("b", "", None, runner=lambda argv: 1).publish(out)


def test_get_publisher_by_backend(tmp_path):
    local = get_publisher(Config(publish=PublishConfig(backend="local", target=str(tmp_path / "x"))))
    assert isinstance(local, LocalPublisher) and local.target == tmp_path / "x"
    s3 = get_publisher(Config(publish=PublishConfig(backend="s3", bucket="b", prefix="p")))
    assert isinstance(s3, S3Publisher) and s3.bucket == "b"


def test_local_refuses_to_mirror_into_a_foreign_directory(tmp_path):
    import pytest
    out = _site(tmp_path)
    foreign = tmp_path / "home"
    foreign.mkdir()
    (foreign / "thesis.docx").write_text("precious")
    with pytest.raises(RuntimeError):
        LocalPublisher(foreign).publish(out)
    assert (foreign / "thesis.docx").exists()
    # an empty or missing target, or one that already holds a site, is fine
    LocalPublisher(tmp_path / "fresh").publish(out)
    LocalPublisher(tmp_path / "fresh").publish(out)
