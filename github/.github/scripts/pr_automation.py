"""
PR Automation Script (Google Gemini) – GitHub-versie
======================================================
Wordt getriggerd door een GitHub Actions workflow bij elke pull request.
- Berekent het volgende versienummer op basis van de laatste git-tag
- Genereert een changelog-entry met het juiste versienummer (via Gemini)
- Werkt CHANGELOG.md bij
- Past README.md aan als de wijzigingen dat vereisen
- Werkt de Changelog-sectie in het Qlik laadscript bij
"""

import os
import re
import json
import glob
import datetime
import subprocess
import requests
from google import genai

# ──────────────────────────────────────────────
# Omgevingsvariabelen
# ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
PR_NUMBER      = os.environ["PR_NUMBER"]
PR_TITLE       = os.environ["PR_TITLE"]
PR_BODY        = os.environ.get("PR_BODY", "")
PR_AUTHOR      = os.environ["PR_AUTHOR"]
REPO_FULL_NAME = os.environ["REPO_FULL_NAME"]
BASE_SHA       = os.environ["BASE_SHA"]
HEAD_SHA       = os.environ["HEAD_SHA"]

TODAY          = datetime.date.today().isoformat()
INITIAL_VERSION = "v0.1.0"

GH_API   = "https://api.github.com"
GH_HEADS = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL  = "gemini-2.5-flash"


# ──────────────────────────────────────────────
# Versie-logica
# ──────────────────────────────────────────────

def get_latest_tag() -> str | None:
    try:
        result = subprocess.run(
            ["git", "tag", "--list", "v*", "--sort=-version:refname"],
            capture_output=True, text=True, check=True
        )
        tags = [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]
        return tags[0] if tags else None
    except subprocess.CalledProcessError:
        return None


def bump_patch(version: str) -> str:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        raise ValueError(f"Ongeldig versieformaat: '{version}'. Verwacht: vX.Y.Z")
    major, minor, patch = match.groups()
    return f"v{major}.{minor}.{int(patch) + 1}"


def determine_next_version() -> tuple[str, str]:
    latest = get_latest_tag()
    if latest:
        return latest, bump_patch(latest)
    return "v0.0.0", INITIAL_VERSION


# ──────────────────────────────────────────────
# Hulpfuncties – GitHub API
# ──────────────────────────────────────────────

def get_pr_commits() -> list[dict]:
    url  = f"{GH_API}/repos/{REPO_FULL_NAME}/pulls/{PR_NUMBER}/commits"
    resp = requests.get(url, headers=GH_HEADS, timeout=30)
    resp.raise_for_status()
    return [
        {"sha": c["sha"][:7], "message": c["commit"]["message"].splitlines()[0]}
        for c in resp.json()
    ]


def get_changed_files() -> list[str]:
    url  = f"{GH_API}/repos/{REPO_FULL_NAME}/pulls/{PR_NUMBER}/files"
    resp = requests.get(url, headers=GH_HEADS, timeout=30)
    resp.raise_for_status()
    return [f["filename"] for f in resp.json()]


# ──────────────────────────────────────────────
# Hulpfuncties – bestanden
# ──────────────────────────────────────────────

def read_file(path: str) -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✔ Geschreven: {path}")


def find_qlik_changelog_script() -> str | None:
    # Zoek op bestandsnaam (met of zonder emoji of prefix)
    for pattern in ["**/Changelog.qvs", "**/changelog.qvs",
                    "**/*Changelog*.qvs", "**/*changelog*.qvs"]:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
    # Zoek op $tab header — inclusief emoji en andere tekens vóór "Changelog"
    for path in glob.glob("**/*.qvs", recursive=True):
        if re.search(r"///\s*\$tab\s+.*[Cc]hangelog", read_file(path)):
            return path
    return None


# ──────────────────────────────────────────────
# Gemini
# ──────────────────────────────────────────────

def generate_with_gemini(
    pr_title: str,
    pr_body: str,
    commits: list[dict],
    changed_files: list[str],
    current_readme: str,
    current_changelog: str,
    new_version: str,
) -> dict:
    commits_text = "\n".join(f"  - [{c['sha']}] {c['message']}" for c in commits)
    files_text   = "\n".join(f"  - {f}" for f in changed_files)
    qlik_version = new_version.lstrip("v")

    prompt = f"""Je bent een technische schrijver gespecialiseerd in Qlik Sense-applicaties.
Je analyseert pull request-informatie en genereert:
1. Een beknopte changelog-entry in Markdown
2. Een aangepaste README.md (alleen de secties die moeten veranderen)
3. Een changelog-blok voor in een Qlik laadscript

Reageer UITSLUITEND met geldige JSON zonder markdown-code-omhulsels (geen ```json tags).

Formaat:
{{
  "changelog_entry": "<markdown tekst voor CHANGELOG.md>",
  "readme_needs_update": true/false,
  "readme_updated": "<volledige bijgewerkte README.md inhoud of lege string>",
  "qlik_changelog_block": "<changelog commentaarblok voor in het Qlik laadscript>"
}}

---

PR #{PR_NUMBER}: {pr_title}
Auteur: {PR_AUTHOR}
Datum: {TODAY}
Versienummer van deze release: {new_version}

PR-beschrijving:
{pr_body or '(geen beschrijving)'}

Commit-berichten:
{commits_text}

Gewijzigde bestanden:
{files_text}

Huidige CHANGELOG.md (eerste 3000 tekens):
{current_changelog[:3000] or '(nog geen changelog)'}

Huidige README.md (eerste 5000 tekens):
{current_readme[:5000] or '(nog geen README)'}

Instructies:
- Maak een changelog-entry in Keep a Changelog-formaat
- Gebruik EXACT versienummer {new_version} als heading (bijv. ## [{new_version}] - {TODAY} PR#{PR_NUMBER})
- Groepeer wijzigingen onder: Added, Changed, Fixed, Removed (alleen relevante koppen)
- Beoordeel of de README-documentatie bijgewerkt moet worden op basis van de wijzigingen
- Genereer een Qlik-laadscript changelog-blok in dit formaat (gebruik /* */ commentaar, GEEN // per regel):
  /*---------------------------------------------------------------------------------------------------------------
  Log & Version

  Versienummer    Datum         Naam            Mutatie
  ---------------------------------------------------------------------------------------------------------------
  {qlik_version}         {TODAY}       {PR_AUTHOR}     <samenvatting van de wijzigingen in deze PR>
                                                <extra mutatieregel indien nodig>
  <voeg bestaande versieregels uit het huidige blok hieronder toe>
  ---------------------------------------------------------------------------------------------------------------*/
- Houd kolomuitlijning netjes met spaties (geen tabs)
"""

    response = client.models.generate_content(model=MODEL, contents=prompt)
    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ──────────────────────────────────────────────
# Update-functies
# ──────────────────────────────────────────────

def update_changelog(current: str, new_entry: str) -> str:
    """
    Voeg nieuwe entry toe aan CHANGELOG.md.
    Als het versienummer al bestaat (bij herhaalde pipeline-run op dezelfde PR),
    vervang dan de bestaande entry in plaats van hem er nogmaals aan toe te voegen.
    """
    header = "# Changelog\n\nAlle wijzigingen aan dit project worden hier bijgehouden.\n\n"

    # Haal versienummer op uit de nieuwe entry (bijv. ## [v0.1.2])
    version_match = re.search(r"^## \[?(v[\d.]+)\]?", new_entry, re.MULTILINE)

    if version_match and current:
        version = re.escape(version_match.group(1))
        # Controleer of dit versienummer al in de changelog staat
        existing = re.search(
            rf"^## \[?{version}\]?.*?(?=^## |\Z)",
            current, re.MULTILINE | re.DOTALL
        )
        if existing:
            # Vervang de bestaande entry met de nieuwe (bijgewerkte) versie
            print(f"  ↻ Versie {version_match.group(1)} al aanwezig — bestaande entry vervangen.")
            updated = current[:existing.start()] + new_entry + "\n\n" + current[existing.end():]
            return updated.rstrip() + "\n"

    # Versienummer bestaat nog niet → toevoegen bovenaan
    if not current:
        return header + new_entry + "\n"
    if current.startswith("# "):
        lines  = current.split("\n")
        insert = 1
        while insert < len(lines) and lines[insert].strip() == "":
            insert += 1
        lines.insert(insert, new_entry + "\n")
        return "\n".join(lines)
    return new_entry + "\n\n" + current


def update_qlik_changelog(script_content: str, qlik_block: str) -> str:
    pattern = r"/\*-{5,}.*?Log\s*&\s*Version.*?-{5,}\*/"
    match   = re.search(pattern, script_content, re.DOTALL | re.IGNORECASE)
    if match:
        return script_content[: match.start()] + qlik_block + script_content[match.end() :]
    return qlik_block + "\n\n" + script_content


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    print("── PR Automation Script (GitHub + Gemini) ──")
    print(f"  PR #{PR_NUMBER}: {PR_TITLE}")
    print(f"  Repo: {REPO_FULL_NAME}")

    # 1. Versienummer bepalen
    print("\n▶ Versienummer bepalen …")
    current_version, new_version = determine_next_version()
    print(f"  {current_version} → {new_version}")

    # 2. GitHub-data ophalen
    print("\n▶ GitHub-data ophalen …")
    commits       = get_pr_commits()
    changed_files = get_changed_files()
    print(f"  {len(commits)} commits, {len(changed_files)} gewijzigde bestanden")

    current_changelog = read_file("CHANGELOG.md")
    current_readme    = read_file("README.md")

    # 3. Gemini aanroepen — versienummer meegeven
    print(f"\n▶ Gemini aanroepen (versie {new_version}) …")
    result = generate_with_gemini(
        pr_title          = PR_TITLE,
        pr_body           = PR_BODY,
        commits           = commits,
        changed_files     = changed_files,
        current_readme    = current_readme,
        current_changelog = current_changelog,
        new_version       = new_version,
    )
    print("  ✔ Gemini-respons ontvangen")

    # 4. CHANGELOG.md bijwerken
    print("\n▶ CHANGELOG.md bijwerken …")
    write_file("CHANGELOG.md", update_changelog(current_changelog, result["changelog_entry"]))

    # 5. README.md bijwerken (alleen als nodig)
    if result.get("readme_needs_update") and result.get("readme_updated"):
        print("\n▶ README.md bijwerken …")
        write_file("README.md", result["readme_updated"])
    else:
        print("\n▶ README.md hoeft niet bijgewerkt te worden.")

    # 6. Qlik laadscript bijwerken
    print("\n▶ Qlik laadscript (Changelog-sectie) bijwerken …")
    qlik_path = find_qlik_changelog_script()
    if qlik_path:
        print(f"  Gevonden: {qlik_path}")
        write_file(qlik_path, update_qlik_changelog(read_file(qlik_path), result["qlik_changelog_block"]))
    else:
        print("  ⚠ Geen Qlik Changelog-scriptbestand gevonden — opgeslagen als qlik_changelog_block.txt")
        write_file("qlik_changelog_block.txt", result["qlik_changelog_block"])

    print(f"\n✅ Automation voltooid — versie {new_version} gedocumenteerd.")


if __name__ == "__main__":
    main()
