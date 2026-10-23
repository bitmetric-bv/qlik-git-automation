"""
init_repo.py – Seedt Gitoqlok-beheerde Qlik-repo's (GitHub)
============================================================
Plaatst automatisch de dunne caller-workflows, het configbestand, de
dev-branch en branch protection in een repo waarvan de beschrijving de marker
'%gitoqlok_repo%' bevat.

Cross-org: de plek van de automation-repo (AUTOMATION_ORG) staat los van de
org(s) die worden gescand (TARGET_ORGS). Zo werken beide topologieën:
  • Centraal: AUTOMATION_ORG=bitmetric-bv, TARGET_ORGS=orgA,orgB (repo public
    of gedeeld binnen een Enterprise).
  • Per org: AUTOMATION_ORG=orgA, TARGET_ORGS=orgA (eigen kopie in de org).

Twee modi (idempotent — bestaande bestanden/branches worden overgeslagen):
  • single : seed één repo (REPO_NAME + ORG_NAME gezet) — bijv. via dispatch.
  • scan   : loop TARGET_ORGS af en seed elke repo met de marker.

Env:
  GH_TOKEN        PAT met toegang tot de doel-org(s) (contents/workflows/admin)
  TARGET_ORGS     komma-gescheiden org(s) om te scannen (default: ORG_NAME)
  ORG_NAME        doel-org in single-modus / default target
  REPO_NAME       repo in single-modus (leeg = scan)
  AUTOMATION_ORG  org waar qlik-git-automation staat (default: doel-org)
"""

import os
import base64
import requests

GH_TOKEN       = os.environ["GH_TOKEN"]
ORG_NAME       = os.environ.get("ORG_NAME")
REPO_NAME      = os.environ.get("REPO_NAME")           # leeg = scan-modus
TARGET_ORGS    = [o.strip() for o in os.environ.get("TARGET_ORGS", ORG_NAME or "").split(",") if o.strip()]
AUTOMATION_ORG = os.environ.get("AUTOMATION_ORG")      # default per org bepaald

HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}

API = "https://api.github.com"

QLIK_MARKER = "%gitoqlok_repo%"

# Caller-workflows. {auto} = AUTOMATION_ORG (waar de reusable workflows staan).
WORKFLOWS = {
    ".github/workflows/pr-changelog.yml": """\
name: Qlik Changelog Preview

# Draait op PR's naar de integratiebranch (feature/* -> dev).
on:
  pull_request:
    types: [opened, synchronize, reopened]
    branches:
      - dev

jobs:
  preview:
    uses: {auto}/qlik-git-automation/.github/workflows/pr-changelog.yml@main
    with:
      automation_repo: {auto}/qlik-git-automation
    secrets: inherit
""",
    ".github/workflows/release.yml": """\
name: Qlik Release

# Draait op merge dev -> main. Loop-beveiliging zit in het release-script,
# maar we slaan een expliciete release-commit hier al over.
on:
  push:
    branches:
      - main

jobs:
  release:
    if: ${{{{ !contains(github.event.head_commit.message, '[skip release]') }}}}
    uses: {auto}/qlik-git-automation/.github/workflows/release.yml@main
    with:
      automation_repo: {auto}/qlik-git-automation
    secrets: inherit
""",
    ".github/workflows/release-pr.yml": """\
name: Qlik Release PR

# Draait op elke push naar dev en houdt de dev -> main PR bij: titel
# ("Release vX.Y.Z") en changelog-omschrijving worden automatisch ingevuld.
on:
  push:
    branches:
      - dev

jobs:
  release-pr:
    if: ${{{{ !contains(github.event.head_commit.message, '[skip release]') }}}}
    uses: {auto}/qlik-git-automation/.github/workflows/release-pr.yml@main
    with:
      automation_repo: {auto}/qlik-git-automation
    secrets: inherit
""",
}

# PR-template: GitHub past dit alleen toe in de repo waar de PR wordt aangemaakt,
# dus het moet in élke consumer-repo staan (feature/* -> dev krijgt zo de checklist).
PR_TEMPLATE_PATH = ".github/pull_request_template.md"
PR_TEMPLATE_CONTENT = """\
<!-- PR-template voor Gitoqlok-beheerde Qlik-repo's -->

## Wat verandert er?

<!-- Korte omschrijving van de wijziging in de Qlik-app (sheets, measures, script, ...) -->

## Type wijziging

- [ ] `feat:` nieuwe functionaliteit
- [ ] `fix:` bugfix
- [ ] `docs:` documentatie
- [ ] `chore:` onderhoud / overig

## Checklist

- [ ] Deze PR is gericht op **`dev`** (niet rechtstreeks op `main`)
- [ ] Commit-berichten beschrijven de wijziging (bij voorkeur `type: omschrijving`)
- [ ] Wijziging is via **Gitoqlok** vanuit de Qlik-app gecommit
- [ ] Ik snap dat de changelog automatisch wordt gegenereerd bij merge `dev -> main`

> Versienummer en release worden automatisch bepaald bij de merge naar `main`.
> Je hoeft zelf geen changelog of versienummer bij te werken.
"""

CONFIG_FILE_PATH = "qlik-release.yml"
CONFIG_FILE_CONTENT = """\
# qlik-release.yml — zie qlik-git-automation voor alle opties.
main_branch: main
dev_branch: dev
load_script:
  path: ""
  glob:
    - "**/Changelog.qvs"
    - "**/*changelog*.qvs"
  tab_marker: "Changelog"
initial_version: "v0.0.1"
ai:
  polish:
    - release_notes
    - qlik_block
"""

# Beheerde bestanden voor Claude Code. De conventies worden bij élke scan naar elke
# gemarkeerde repo gesynct (overschrijven); CLAUDE.md is van de repo zelf en wordt
# alleen aangemaakt als hij ontbreekt.
CONVENTIONS_PATH = ".claude/qlik-conventions.md"
CONVENTIONS_SRC  = os.path.join(os.path.dirname(__file__), "..", "..", "templates", "qlik-conventions.md")
CLAUDE_MD_PATH   = "CLAUDE.md"
CLAUDE_MD_STUB   = """\
# {repo}

@.claude/qlik-conventions.md

## Deze app

<!-- App-specifiek: doel, upstream/downstream repo's, dev- en prd-app (space + app-ID). -->
"""

# Bestand dat als "volledig geseed"-markering geldt (idempotente scan). Wijs naar
# het nieuwst toegevoegde bestand, zodat eerder geseede repos die dit nog missen
# opnieuw langs seed_repo gaan (push_file slaat bestaande bestanden over).
SEEDED_SENTINEL = ".github/workflows/release-pr.yml"


def automation_org_for(target_org: str) -> str:
    """Waar staat de automation-repo? Expliciet gezet, anders = doel-org."""
    return AUTOMATION_ORG or target_org


# ──────────────────────────────────────────────
# API-helpers (org + repo als parameter → cross-org bruikbaar)
# ──────────────────────────────────────────────

def get_repo_info(org: str, repo: str) -> dict:
    resp = requests.get(f"{API}/repos/{org}/{repo}", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def file_exists(org: str, repo: str, path: str, branch: str) -> bool:
    url = f"{API}/repos/{org}/{repo}/contents/{path}?ref={branch}"
    return requests.get(url, headers=HEADERS, timeout=30).status_code == 200


def push_file(org: str, repo: str, path: str, content: str, branch: str) -> None:
    if file_exists(org, repo, path, branch):
        print(f"    ⚠ {path} bestaat al — overgeslagen.")
        return
    url     = f"{API}/repos/{org}/{repo}/contents/{path}"
    encoded = base64.b64encode(content.encode()).decode()
    body    = {
        "message": "chore: workflow-bestanden toegevoegd via qlik-git-automation [skip release]",
        "content": encoded,
        "branch":  branch,
    }
    resp = requests.put(url, headers=HEADERS, json=body, timeout=30)
    if resp.status_code == 201:
        print(f"    ✔ {path} aangemaakt")
    else:
        print(f"    ✘ Fout bij {path}: {resp.status_code} – {resp.text}")
        resp.raise_for_status()


def sync_file(org: str, repo: str, path: str, content: str, branch: str, overwrite: bool = True) -> bool:
    """Zet ``path`` op ``content``. Geeft True als er een commit is gemaakt.

    overwrite=False: alleen aanmaken (bestand is van de repo zelf). Faalt zacht
    (bv. beschermde main), zodat één repo de hele scan niet breekt.
    """
    url = f"{API}/repos/{org}/{repo}/contents/{path}"
    cur = requests.get(f"{url}?ref={branch}", headers=HEADERS, timeout=30)
    body = {"content": base64.b64encode(content.encode()).decode(), "branch": branch}
    if cur.status_code == 200:
        data = cur.json()
        if not overwrite or base64.b64decode(data["content"]).decode() == content:
            return False
        body["sha"] = data["sha"]
    verb = "bijgewerkt" if "sha" in body else "toegevoegd"
    body["message"] = f"chore: {path} {verb} via qlik-git-automation [skip release]"
    resp = requests.put(url, headers=HEADERS, json=body, timeout=30)
    if resp.status_code in (200, 201):
        print(f"    ✔ {path} {verb} op {branch}")
        return True
    print(f"    ⚠ {path} niet gesynct op {branch}: {resp.status_code} – {resp.text}")
    return False


def merge_main_into_dev(org: str, repo: str, main: str) -> None:
    """Houd dev een voorouder-superset van main (merge, nooit force), zodat de
    fast-forward van dev na een release blijft werken."""
    resp = requests.post(
        f"{API}/repos/{org}/{repo}/merges", headers=HEADERS, timeout=30,
        json={"base": "dev", "head": main, "commit_message": f"chore: sync {main} -> dev [skip release]"},
    )
    if resp.status_code == 201:
        print(f"    ✔ {main} gemerged in dev")
    elif resp.status_code != 204:   # 204 = dev bevat main al
        print(f"    ⚠ {main} -> dev niet gemerged: {resp.status_code} – {resp.text}")


def sync_managed_files(org: str, repo: str, branch: str) -> None:
    """Conventies + CLAUDE.md-stub naar main; daarna main -> dev."""
    with open(CONVENTIONS_SRC, encoding="utf-8") as f:
        conventions = f.read()
    changed = sync_file(org, repo, CONVENTIONS_PATH, conventions, branch)
    changed |= sync_file(org, repo, CLAUDE_MD_PATH, CLAUDE_MD_STUB.format(repo=repo), branch, overwrite=False)
    if changed and get_branch_sha(org, repo, "dev"):
        merge_main_into_dev(org, repo, branch)


def get_branch_sha(org: str, repo: str, branch: str) -> str | None:
    url  = f"{API}/repos/{org}/{repo}/git/ref/heads/{branch}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    return resp.json()["object"]["sha"] if resp.status_code == 200 else None


def ensure_dev_branch(org: str, repo: str, default_branch: str) -> None:
    url = f"{API}/repos/{org}/{repo}/git/ref/heads/dev"
    if requests.get(url, headers=HEADERS, timeout=30).status_code == 200:
        print("    ⚠ dev-branch bestaat al — overgeslagen.")
        return
    sha = get_branch_sha(org, repo, default_branch)
    if not sha:
        print("    ⚠ Kon SHA van default branch niet ophalen — dev-branch niet aangemaakt.")
        return
    resp = requests.post(
        f"{API}/repos/{org}/{repo}/git/refs",
        headers=HEADERS, json={"ref": "refs/heads/dev", "sha": sha}, timeout=30,
    )
    if resp.status_code == 201:
        print("    ✔ dev-branch aangemaakt")
    else:
        print(f"    ⚠ dev-branch niet aangemaakt: {resp.status_code} – {resp.text}")


def protect_main(org: str, repo: str, branch: str) -> None:
    """Beperk main tot PR-merges (releases). Best-effort; vereist admin-rechten."""
    url = f"{API}/repos/{org}/{repo}/branches/{branch}/protection"
    body = {
        "required_status_checks": None,
        "enforce_admins": False,
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
    resp = requests.put(url, headers=HEADERS, json=body, timeout=30)
    if resp.status_code == 200:
        print(f"    ✔ Branch protection op '{branch}' ingesteld")
    else:
        print(f"    ⚠ Branch protection niet ingesteld: {resp.status_code} – {resp.text}")


def protect_dev(org: str, repo: str, branch: str = "dev") -> None:
    """Bescherm dev tegen verwijdering en force-push, maar laat gewone pushes toe.

    GÉÉN PR-review-eis: de release-bot moet dev na een release fast-forward naar
    main kunnen pushen. Dit is puur een vangnet tegen (per ongeluk) verwijderen —
    het uitzetten van 'delete branch on merge' voorkomt de automatische verwijdering.
    """
    url = f"{API}/repos/{org}/{repo}/branches/{branch}/protection"
    body = {
        "required_status_checks": None,
        "enforce_admins": False,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }
    resp = requests.put(url, headers=HEADERS, json=body, timeout=30)
    if resp.status_code == 200:
        print(f"    ✔ Branch protection op '{branch}' ingesteld (niet verwijderbaar)")
    else:
        print(f"    ⚠ Dev-protection niet ingesteld: {resp.status_code} – {resp.text}")


def disable_delete_branch_on_merge(org: str, repo: str) -> None:
    """Zet 'automatically delete head branches' uit, zodat dev na een
    dev -> main merge NIET wordt verwijderd."""
    url = f"{API}/repos/{org}/{repo}"
    resp = requests.patch(url, headers=HEADERS, json={"delete_branch_on_merge": False}, timeout=30)
    if resp.status_code == 200:
        print("    ✔ 'delete branch on merge' uitgezet (dev blijft behouden)")
    else:
        print(f"    ⚠ Repo-instelling niet aangepast: {resp.status_code} – {resp.text}")


# ──────────────────────────────────────────────
# Seed één repo
# ──────────────────────────────────────────────

def seed_repo(org: str, repo: str, info: dict | None = None) -> bool:
    """Seed één repo. Geeft True als er iets is gedaan, False bij overslaan."""
    info        = info or get_repo_info(org, repo)
    description = info.get("description") or ""
    branch      = info.get("default_branch", "main")

    if QLIK_MARKER not in description:
        return False

    auto = automation_org_for(org)
    print(f"  ▶ {org}/{repo}  (default: {branch}, automation: {auto})")

    for path, content in WORKFLOWS.items():
        push_file(org, repo, path, content.format(auto=auto), branch)
    push_file(org, repo, CONFIG_FILE_PATH, CONFIG_FILE_CONTENT, branch)
    push_file(org, repo, PR_TEMPLATE_PATH, PR_TEMPLATE_CONTENT, branch)

    ensure_dev_branch(org, repo, branch)
    disable_delete_branch_on_merge(org, repo)
    protect_main(org, repo, branch)
    protect_dev(org, repo, "dev")
    return True


# ──────────────────────────────────────────────
# Scan org(s)
# ──────────────────────────────────────────────

def list_org_repos(org: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        url = f"{API}/orgs/{org}/repos?per_page=100&page={page}&type=all"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos


def scan_org(org: str) -> tuple[int, int]:
    print(f"── Scan org '{org}' op Qlik-repo's ──")
    repos = list_org_repos(org)
    print(f"  {len(repos)} repo(s) gevonden")

    seeded = skipped = 0
    for info in repos:
        repo = info["name"]
        if QLIK_MARKER not in (info.get("description") or ""):
            continue
        branch = info.get("default_branch", "main")
        if file_exists(org, repo, SEEDED_SENTINEL, branch):
            print(f"  ⏭  {repo} al geseed — overgeslagen")
            skipped += 1
        elif seed_repo(org, repo, info):
            seeded += 1
        # Beheerde bestanden gelden voor nieuwe én bestaande repo's.
        sync_managed_files(org, repo, branch)
    return seeded, skipped


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    if REPO_NAME:
        org = ORG_NAME
        print(f"── Repo initialiseren: {org}/{REPO_NAME} ──")
        if not seed_repo(org, REPO_NAME):
            print(f"  ⚠ Geen Qlik Sense repo (marker '{QLIK_MARKER}' niet gevonden) — overgeslagen.")
        else:
            print("\n✅ Klaar!")
        return

    if not TARGET_ORGS:
        raise SystemExit("Zet TARGET_ORGS (of ORG_NAME) voor de scan-modus.")

    total_seeded = total_skipped = 0
    failed = []
    for org in TARGET_ORGS:
        try:
            s, k = scan_org(org)
        except requests.HTTPError as e:      # bv. onbekende org: de rest gewoon doorscannen
            print(f"  ✘ Org '{org}' overgeslagen: {e}")
            failed.append(org)
            continue
        total_seeded += s
        total_skipped += k
    print(f"\n✅ Scan klaar over {len(TARGET_ORGS)} org(s) — {total_seeded} geseed, {total_skipped} al gereed.")
    if failed:
        raise SystemExit(f"Mislukt voor org(s): {', '.join(failed)}")


if __name__ == "__main__":
    main()
