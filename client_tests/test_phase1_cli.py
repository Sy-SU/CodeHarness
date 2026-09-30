import pytest

from agent.oj_client.cli import main


def test_submit_cli_requires_confirmation_and_finite_limits_before_network(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OJ_BASE_URL", "https://oj.example.test")
    monkeypatch.setenv("OJ_API_TOKEN", "oj_test")
    source = tmp_path / "solution.cpp"
    source.write_text("int main(){}", encoding="utf-8")
    base = [
        "submit-fixed",
        "sum",
        str(source),
        "--http-timeout",
        "1",
        "--poll-interval",
        "0",
        "--deadline",
        "1",
    ]
    with pytest.raises(SystemExit, match="requires --confirm-submit"):
        main(base)
    with pytest.raises(SystemExit, match="finite and non-negative"):
        main(
            base[:-1]
            + ["nan", "--confirm-submit"]
        )
