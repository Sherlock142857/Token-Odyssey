import re
from pathlib import Path
from urllib.parse import unquote

from conftest import ROOT

MARKDOWN_LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    return sorted([*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), *(ROOT / ".github").rglob("*.md")])


def test_local_markdown_links_resolve_to_repository_files():
    failures = []
    for document in markdown_files():
        for raw_target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / unquote(target)).resolve()
            if ROOT not in resolved.parents and resolved != ROOT:
                failures.append(f"{document.relative_to(ROOT)}: link escapes repository: {raw_target}")
            elif not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)}: missing {raw_target}")
    assert not failures, "\n".join(failures)


def test_release_docs_name_current_formats_and_offline_boundary():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert all(name in readme for name in ("Scenario v3", "RunConfig v3", "Run Log v4", "Campaign Checkpoint v1"))
    assert "selftest" in readme and "never" in readme
    assert "scenarios/sealed_chalice.yaml" not in readme
