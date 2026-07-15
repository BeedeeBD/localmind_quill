from pathlib import Path

import config
import selfcode


def test_self_editing_is_disabled(tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    sample = project / "sample.txt"
    sample.write_text("hello\n", encoding="utf-8")

    monkeypatch.setattr(config, "PROJECT_ROOT", project)
    monkeypatch.setattr(config, "SELFCODE_DB", tmp_path / "selfcode.db")

    selfcode.init()

    proposal = selfcode.propose_write("sample.txt", "hello\nworld\n", "test note")
    assert proposal == {"error": "self-editing is disabled in this build", "refused": True}

    pending = selfcode.list_proposals("pending")
    assert pending == {"proposals": []}

    approved = selfcode.approve_proposal("fake-id")
    assert approved == {"error": "self-editing is disabled in this build", "refused": True}
    assert sample.read_text(encoding="utf-8") == "hello\n"
