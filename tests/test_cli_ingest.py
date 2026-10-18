import httpx
from click.testing import CliRunner

from scout import cli


class _Ok:
    def run(self, db):
        return 4, 1


class _Broken:
    def run(self, db):
        raise httpx.HTTPStatusError("401 Unauthorized", request=None, response=None)


def _invoke(monkeypatch, tmp_path, adapters):
    monkeypatch.setattr(cli, "ADAPTERS", adapters)
    monkeypatch.setenv("SCOUT_DB_PATH", str(tmp_path / "scout.db"))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    return CliRunner().invoke(cli.main, ["ingest"])


def test_failing_source_warns_and_others_still_ingest(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, {"bad": _Broken, "good": _Ok})
    assert result.exit_code == 0
    expected = "::warning title=Ingest failed for bad::HTTPStatusError: 401 Unauthorized"
    assert expected in result.output
    assert "[good] total=4 new_or_amended=1" in result.output


def test_all_sources_failing_exits_nonzero(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, {"bad": _Broken})
    assert result.exit_code != 0
    assert "all sources failed: bad" in result.output
