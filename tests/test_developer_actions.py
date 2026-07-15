import config
import developer_actions
import selfcode


def test_self_editing_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SELFCODE_DB", tmp_path / "selfcode.db")
    selfcode.init()

    assert developer_actions.is_developer_request("please edit your code") is False
    assert selfcode.propose_write("sample.py", "print('hi')\n", "test") == {
        "error": "self-editing is disabled in this build",
        "refused": True,
    }
