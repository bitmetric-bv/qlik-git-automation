"""Tests voor de seed/scan-beslislogica in init_repo.py (zonder netwerk)."""

import os
import sys

import pytest

os.environ.setdefault("GH_TOKEN", "x")
os.environ.setdefault("ORG_NAME", "acme")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

import init_repo as ir


def test_seed_skips_repo_without_marker():
    info = {"description": "gewone repo", "default_branch": "main"}
    assert ir.seed_repo("acme", "plain", info) is False


def test_seed_runs_for_marked_repo(monkeypatch):
    calls = []
    monkeypatch.setattr(ir, "push_file", lambda org, repo, path, *a, **k: calls.append(("push", path)))
    monkeypatch.setattr(ir, "ensure_dev_branch", lambda *a, **k: calls.append(("dev", None)))
    monkeypatch.setattr(ir, "protect_main", lambda *a, **k: calls.append(("protect", None)))
    monkeypatch.setattr(ir, "protect_dev", lambda *a, **k: calls.append(("protect_dev", None)))
    monkeypatch.setattr(ir, "disable_delete_branch_on_merge", lambda *a, **k: calls.append(("no_delete", None)))
    info = {"description": "Sales %gitoqlok_repo%", "default_branch": "main"}
    assert ir.seed_repo("acme", "sales", info) is True
    kinds = [c[0] for c in calls]
    pushed = [c[1] for c in calls if c[0] == "push"]
    assert kinds.count("push") == 5          # 3 workflows + config + PR-template
    assert ".github/pull_request_template.md" in pushed
    assert ".github/workflows/release-pr.yml" in pushed
    assert "dev" in kinds and "protect" in kinds
    assert "protect_dev" in kinds and "no_delete" in kinds


def test_seed_uses_automation_org_in_caller(monkeypatch):
    pushed = {}
    monkeypatch.setattr(ir, "push_file",
                        lambda org, repo, path, content, branch: pushed.setdefault(path, content))
    monkeypatch.setattr(ir, "ensure_dev_branch", lambda *a, **k: None)
    monkeypatch.setattr(ir, "protect_main", lambda *a, **k: None)
    monkeypatch.setattr(ir, "protect_dev", lambda *a, **k: None)
    monkeypatch.setattr(ir, "disable_delete_branch_on_merge", lambda *a, **k: None)
    monkeypatch.setattr(ir, "AUTOMATION_ORG", "bitmetric-bv")     # centraal model
    info = {"description": "%gitoqlok_repo%", "default_branch": "main"}
    ir.seed_repo("klant-org", "sales", info)
    release = pushed[".github/workflows/release.yml"]
    assert "uses: bitmetric-bv/qlik-git-automation" in release
    assert "automation_repo: bitmetric-bv/qlik-git-automation" in release


def test_scan_seeds_only_marked_and_unseen(monkeypatch):
    repos = [
        {"name": "sales", "description": "%gitoqlok_repo%", "default_branch": "main"},
        {"name": "plain", "description": "geen marker", "default_branch": "main"},
        {"name": "already", "description": "%gitoqlok_repo%", "default_branch": "main"},
    ]
    seeded = []
    monkeypatch.setattr(ir, "list_org_repos", lambda org: repos)
    # 'already' geldt als reeds geseed
    monkeypatch.setattr(ir, "file_exists", lambda org, repo, path, branch: repo == "already")
    monkeypatch.setattr(ir, "seed_repo", lambda org, repo, info: seeded.append(repo) or True)
    synced = []
    monkeypatch.setattr(ir, "sync_managed_files", lambda org, repo, branch: synced.append(repo))

    ir.scan_org("acme")
    assert seeded == ["sales"]               # plain: geen marker, already: al geseed
    assert synced == ["sales", "already"]    # beheerde bestanden ook voor al geseede repo's


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._p, self.text = status, payload or {}, ""

    def json(self):
        return self._p


def _fake_api(monkeypatch, existing):
    """existing: None (bestand ontbreekt) of tekst. Geeft de PUT-bodies terug."""
    import base64
    puts = []
    cur = (_Resp(200, {"sha": "abc", "content": base64.b64encode(existing.encode()).decode()})
           if existing is not None else _Resp(404))
    monkeypatch.setattr(ir.requests, "get", lambda *a, **k: cur)
    monkeypatch.setattr(ir.requests, "put", lambda url, json=None, **k: puts.append(json) or _Resp(200))
    return puts


def test_sync_file_updates_changed_content(monkeypatch):
    puts = _fake_api(monkeypatch, "oud")
    assert ir.sync_file("acme", "sales", "x.md", "nieuw", "main") is True
    assert puts[0]["sha"] == "abc" and "[skip release]" in puts[0]["message"]


def test_sync_file_noop_when_identical(monkeypatch):
    puts = _fake_api(monkeypatch, "zelfde")
    assert ir.sync_file("acme", "sales", "x.md", "zelfde", "main") is False
    assert puts == []


def test_sync_file_never_overwrites_repo_owned_file(monkeypatch):
    puts = _fake_api(monkeypatch, "eigen tekst")
    assert ir.sync_file("acme", "sales", "CLAUDE.md", "stub", "main", overwrite=False) is False
    assert puts == []


def test_sync_file_creates_missing(monkeypatch):
    puts = _fake_api(monkeypatch, None)
    assert ir.sync_file("acme", "sales", "CLAUDE.md", "stub", "main", overwrite=False) is True
    assert "sha" not in puts[0]


def test_scan_continues_after_failing_org(monkeypatch):
    seen = []

    def scan(org):
        seen.append(org)
        if org == "bestaat-niet":
            raise ir.requests.HTTPError("404")
        return 1, 0
    monkeypatch.setattr(ir, "scan_org", scan)
    monkeypatch.setattr(ir, "TARGET_ORGS", ["bestaat-niet", "bitmetric-mcf"])
    monkeypatch.setattr(ir, "REPO_NAME", None)
    with pytest.raises(SystemExit) as e:
        ir.main()
    assert "bestaat-niet" in str(e.value)               # run faalt zichtbaar...
    assert seen == ["bestaat-niet", "bitmetric-mcf"]    # ...maar tweede org is wél gescand
