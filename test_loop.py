import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REQUIRED = ["normal_event.json", "quiet_standby.json", "large_event.json", "history_sample.csv"]


def test_required_files_exist():
    missing = [name for name in REQUIRED if not (FIXTURES / name).exists()]
    if missing:
        raise AssertionError(f"Missing fixture files: {missing}")


def test_json_fixtures_parse():
    for path in FIXTURES.glob("*.json"):
        if path.name == "malformed.json":
            continue
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        assert isinstance(payload, dict), path.name
        assert "latest_rep_header" in payload or "containers" in payload, path.name


def test_malformed_fixture_is_detectable():
    path = FIXTURES / "malformed.json"
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    head = payload.get("latest_rep_header", {}).get("head", [])
    assert head, "malformed fixture should still contain a partial header"


def main():
    test_required_files_exist()
    test_json_fixtures_parse()
    test_malformed_fixture_is_detectable()
    print("OK: replay fixtures validated")


if __name__ == "__main__":
    main()
