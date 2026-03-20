"""
pr_automation.py – GitLab-versie
==================================
Wordt getriggerd door GitLab CI/CD bij elke Merge Request.
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
# Omgevingsvariabelen (GitLab CI/CD)
# ──────────────────────────────────────────────
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
GITLAB_API_TOKEN = os.environ["GITLAB_API_TOKEN"]  # PAT met api-scope
GITLAB_API_URL  = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
PROJECT_ID      = os.environ["CI_PROJECT_ID"]
MR_IID          = os.environ["CI_MERGE_REQUEST_IID"]
MR_TITLE        = os.environ["CI_MERGE_REQUEST_TITLE"]
MR_DESCRIPTION  = os.environ.get("CI_MERGE_REQUEST_DESCRIPTION", "")
MR_AUTHOR       = os.environ.get("GITLAB_USER_LOGIN", "onbekend")
SOURCE_BRANCH   = os.environ["CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"]
TARGET_BRANCH   = os.environ["CI_MERGE_REQUEST_TARGET_BRANCH_NAME"]
BASE_SHA        = os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA", "")
HEAD_SHA        = os.environ.get("CI_COMMIT_SHA", "")

TODAY          = datetime.date.today().isoformat()
INITIAL_VERSION = "v0.1.0"

GL_HEADS = {
    "PRIVATE-TOKEN": GITLAB_API_TOKEN,
    "Content-Type": "application/json",
}

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL  = "gemini-2.5-flash"


# ──────────────────────────────────────────────
# Versie-logica (zelfde als create_release.py)
# ──────────────────────────────────────────────

def get_latest_tag() -> str | None:
    """Haal het laatste versie-tag op via git."""
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
    """v0.1.1 → v0.1.2"""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", version)
    if not match:
        raise ValueError(f"Ongeldig versieformaat: '{version}'. Verwacht: vX.Y.Z")
    major, minor, patch = match.groups()
    return f"v{major}.{minor}.{int(patch) + 1}"


def determine_next_version() -> tuple[str, str]:
    """Geeft (huidige_versie, nieuwe_versie) terug."""
    latest = get_latest_tag()
    if latest:
        return latest, bump_patch(latest)
    return "v0.0.0", INITIAL_VERSION


# ──────────────────────────────────────────────
# Hulpfuncties – GitLab API
# ──────────────────────────────────────────────

def get_mr_commits() -> list[dict]:
    url  = f"{GITLAB_API_URL}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/commits"
    resp = requests.get(url, headers=GL_HEADS, timeout=30)
    resp.raise_for_status()
    return [{"sha": c["short_id"], "message": c["title"]} for c in resp.json()]


def get_changed_files() -> list[str]:
    url  = f"{GITLAB_API_URL}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/diffs"
    resp = requests.get(url, headers=GL_HEADS, timeout=30)
    resp.raise_for_status()
    return [d["new_path"] for d in resp.json()]


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
    mr_title: str,
    mr_description: str,
    commits: list[dict],
    changed_files: list[str],
    current_readme: str,
    current_changelog: str,
    new_version: str,
) -> dict:
    commits_text = "\n".join(f"  - [{c['sha']}] {c['message']}" for c in commits)
    files_text   = "\n".join(f"  - {f}" for f in changed_files)

    # Versienummer zonder 'v'-prefix voor het Qlik-blok (bijv. 1.2 i.p.v. v1.2.0)
    qlik_version = new_version.lstrip("v")

    prompt = f"""Je bent een technische schrijver gespecialiseerd in Qlik Sense-applicaties.
Je analyseert merge request-informatie en genereert:
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

MR !{MR_IID}: {mr_title}
Auteur: {MR_AUTHOR}
Datum: {TODAY}
Van branch: {SOURCE_BRANCH} → {TARGET_BRANCH}
Versienummer van deze release: {new_version}

MR-beschrijving:
{mr_description or '(geen beschrijving)'}

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
- Gebruik EXACT versienummer {new_version} als heading (bijv. ## [{new_version}] - {TODAY})
- Voeg ook het MR-nummer !{MR_IID} toe in de heading
- Groepeer wijzigingen onder: Added, Changed, Fixed, Removed (alleen relevante koppen)
- Beoordeel of de README-documentatie bijgewerkt moet worden op basis van de wijzigingen
- Genereer een Qlik-laadscript changelog-blok in dit formaat (gebruik /* */ commentaar, GEEN // per regel):
  /*---------------------------------------------------------------------------------------------------------------
  Log & Version

  Versienummer    Datum         Naam            Mutatie
  ---------------------------------------------------------------------------------------------------------------
  {qlik_version}         {TODAY}       {MR_AUTHOR}     <samenvatting van de wijzigingen in deze MR>
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
    Als het versienummer al bestaat (bij herhaalde pipeline-run op dezelfde MR),
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
    print("── PR Automation Script (GitLab + Gemini) ──")
    print(f"  MR !{MR_IID}: {MR_TITLE}")
    print(f"  Branch: {SOURCE_BRANCH} → {TARGET_BRANCH}")

    # 1. Versienummer bepalen
    print("\n▶ Versienummer bepalen …")
    current_version, new_version = determine_next_version()
    print(f"  {current_version} → {new_version}")

    # 2. GitHub/GitLab-data ophalen
    print("\n▶ GitLab-data ophalen …")
    commits       = get_mr_commits()
    changed_files = get_changed_files()
    print(f"  {len(commits)} commits, {len(changed_files)} gewijzigde bestanden")

    current_changelog = read_file("CHANGELOG.md")
    current_readme    = read_file("README.md")

    # 3. Gemini aanroepen — versienummer meegeven
    print(f"\n▶ Gemini aanroepen (versie {new_version}) …")
    result = generate_with_gemini(
        mr_title          = MR_TITLE,
        mr_description    = MR_DESCRIPTION,
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
