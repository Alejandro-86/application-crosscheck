from crosscheck.corpus import (
    load_applications,
    load_documents,
    load_patients,
    load_pharmacy,
)
from crosscheck.generate import main


def test_cli_writes_loadable_data(tmp_path, monkeypatch):
    out = tmp_path / "gen"
    monkeypatch.setattr(
        "sys.argv", ["generate", "--n-docs", "50", "--seed", "5", "--out", str(out)]
    )
    main()
    docs = load_documents(out)
    patients = load_patients(out)
    assert len(docs) == 50
    assert all(d.text.strip() for d in docs)
    assert len(load_applications(out)) == len(patients)
    assert load_pharmacy(out)
