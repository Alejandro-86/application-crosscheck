from crosscheck.corpus import load_documents
from crosscheck.generate import main


def test_cli_writes_loadable_corpus(tmp_path, monkeypatch):
    out = tmp_path / "gen"
    monkeypatch.setattr(
        "sys.argv", ["generate", "--n-docs", "50", "--seed", "5", "--out", str(out)]
    )
    main()
    docs = load_documents(out)
    assert len(docs) == 50
    assert all(d.text.strip() for d in docs)
