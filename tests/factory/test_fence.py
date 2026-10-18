"""The fence: allowed and forbidden paths, size, weakened tests and the copy guard."""

import subprocess
from pathlib import Path

import pytest

from tools.factory.config import load_config
from tools.factory.fence import (
    check_fence,
    js_polish_strings,
    main,
    matches,
    visible_text,
)

CONFIG = load_config()
ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


def write(repo: Path, files: dict[str, str | bytes | None]) -> None:
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def run(tmp_path: Path, base: dict, head: dict, rename: tuple[str, str] | None = None):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    write(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    first = git(repo, "rev-parse", "HEAD")
    if rename:
        (repo / rename[1]).parent.mkdir(parents=True, exist_ok=True)
        git(repo, "mv", rename[0], rename[1])
    write(repo, head)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    return repo, first, git(repo, "rev-parse", "HEAD")


def check(tmp_path: Path, base: dict, head: dict, rename=None):
    repo, first, last = run(tmp_path, base, head, rename)
    return check_fence(repo, first, last, CONFIG.fence, CONFIG.limits)


def lines(n: int, tag: str) -> str:
    return "".join(f".{tag}{i} {{}}\n" for i in range(n))


def test_matches() -> None:
    assert matches("css/a.css", "css/**")
    assert not matches("css", "css/**")
    assert not matches("cssx/a.css", "css/**")
    assert matches("CLAUDE.md", "CLAUDE.md")
    assert not matches("docs/CLAUDE.md", "CLAUDE.md")


def test_allowed_css_change(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a{}\n"}, {"css/a.css": "a{color:red}\n"})
    assert report.ok and report.reasons == [] and not report.copy_changed
    assert report.changed_lines == 2


def test_forbidden_path(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a\n"}, {".github/workflows/x.yml": "on: push\n"})
    assert not report.ok
    assert any(r.startswith("forbidden path") for r in report.reasons)


def test_outside_both_lists(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a\n"}, {"README.md": "hello\n"})
    assert not report.ok
    assert "outside the allowed paths: README.md" in report.reasons


def test_rename_into_forbidden(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a{}\n" * 5}, {}, rename=("css/a.css", ".github/a.css"))
    assert not report.ok
    assert "forbidden path: .github/a.css" in report.reasons


def test_rename_out_of_forbidden(tmp_path: Path) -> None:
    report = check(tmp_path, {".github/a.css": "a{}\n" * 5}, {}, rename=(".github/a.css", "css/a.css"))
    assert not report.ok
    assert "forbidden path: .github/a.css" in report.reasons


def test_deleting_a_disallowed_file(tmp_path: Path) -> None:
    report = check(tmp_path, {"CLAUDE.md": "rules\n"}, {"CLAUDE.md": None})
    assert not report.ok


def test_deleted_test_file(tmp_path: Path) -> None:
    report = check(
        tmp_path, {"tests/e2e/test_x.py": "def test_x():\n    assert 1\n"}, {"tests/e2e/test_x.py": None}
    )
    assert "weakened tests" in report.reasons


def test_added_skip(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x():\n    assert 1\n"}
    head = {"tests/test_x.py": "import pytest\n@pytest.mark.skip\ndef test_x():\n    assert 1\n"}
    assert "weakened tests" in check(tmp_path, base, head).reasons


@pytest.mark.parametrize("marker", ["@pytest.mark.xfail", "    pytest.skip('no')", "@pytest.mark.skipif(True)"])
def test_other_skip_forms(tmp_path: Path, marker: str) -> None:
    base = {"tests/test_x.py": "def test_x():\n    assert 1\n"}
    head = {"tests/test_x.py": f"def test_x():\n{marker}\n    assert 1\n"}
    assert "weakened tests" in check(tmp_path, base, head).reasons


def test_removed_assert(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x():\n    assert a == 1\n    assert b == 2\n"}
    head = {"tests/test_x.py": "def test_x():\n    assert a == 1\n"}
    assert "weakened tests" in check(tmp_path, base, head).reasons


def test_changed_assertion_value(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x():\n    assert width == 44\n"}
    head = {"tests/test_x.py": "def test_x():\n    assert width == 10\n"}
    assert "weakened tests" in check(tmp_path, base, head).reasons


def test_moved_assert_is_fine(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x():\n    assert a == 1\n    y = 2\n"}
    head = {"tests/test_x.py": "def test_x():\n    y = 2\n    assert a == 1\n"}
    assert check(tmp_path, base, head).ok


def test_raised_timeout(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x(page):\n    page.wait_for(timeout=5000)\n"}
    head = {"tests/test_x.py": "def test_x(page):\n    page.wait_for(timeout=10000)\n"}
    assert "weakened tests" in check(tmp_path, base, head).reasons


def test_lowered_timeout_is_fine(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x(page):\n    page.wait_for(timeout=10000)\n"}
    head = {"tests/test_x.py": "def test_x(page):\n    page.wait_for(timeout=5000)\n"}
    assert check(tmp_path, base, head).ok


def test_new_and_extended_tests_are_fine(tmp_path: Path) -> None:
    base = {"tests/test_x.py": "def test_x():\n    assert 1\n"}
    head = {
        "tests/test_x.py": "def test_x():\n    assert 1\n\ndef test_y():\n    assert 2\n",
        "tests/test_new.py": "import pytest\n@pytest.mark.skip\ndef test_z():\n    pass\n",
    }
    assert check(tmp_path, base, head).ok


def test_comparison_py_edit(tmp_path: Path) -> None:
    report = check(
        tmp_path, {"tests/visual/comparison.py": "TOL = 1\n"}, {"tests/visual/comparison.py": "TOL = 2\n"}
    )
    assert "weakened tests" in report.reasons
    assert any(r.startswith("forbidden path") for r in report.reasons)


PAGE = '<html><head><title>T</title><meta name="description" content="Opis"></head><body><p>Cześć   świecie</p><img src="a.png" alt="Logo"><script>var x = "zażółć";</script></body></html>'


def test_visible_text() -> None:
    text = visible_text(PAGE)
    assert text == ["T", "Opis", "Cześć świecie", "Logo"]
    assert "zażółć" not in " ".join(text)


def test_visible_text_attributes() -> None:
    html = '<meta property="og:title" content="OG"><input placeholder="Szukaj" aria-label="Pole" title="Tytuł"><style>p{}</style>'
    assert visible_text(html) == ["OG", "Pole", "Tytuł", "Szukaj"]


def test_js_polish_strings() -> None:
    source = "a('plain'); b(\"zażółć\"); c(`gęś ${x}`); d('it\\'s żółw');"
    assert js_polish_strings(source) == {'"zażółć"', "`gęś ${x}`", "'it\\'s żółw'"}


def test_changed_text_node(tmp_path: Path) -> None:
    report = check(tmp_path, {"index.html": PAGE}, {"index.html": PAGE.replace("Cześć", "Hej")})
    assert report.copy_changed and "copy" in report.reasons


def test_changed_alt_text(tmp_path: Path) -> None:
    report = check(tmp_path, {"docs/index.html": PAGE}, {"docs/index.html": PAGE.replace("Logo", "Znak")})
    assert report.copy_changed


def test_markup_only_page_change_keeps_copy(tmp_path: Path) -> None:
    report = check(tmp_path, {"index.html": PAGE}, {"index.html": PAGE.replace("<p>", '<p class="x">')})
    assert report.ok and not report.copy_changed


def test_changed_polish_literal(tmp_path: Path) -> None:
    base = {"js/site.js": 'const a = "Zażółć";\nconst b = "plain";\n'}
    head = {"js/site.js": 'const a = "Zażółć gęślą";\nconst b = "plain";\n'}
    report = check(tmp_path, base, head)
    assert report.copy_changed and "copy" in report.reasons


def test_non_polish_js_change_is_fine(tmp_path: Path) -> None:
    base = {"js/site.js": 'const a = "Zażółć";\nconst b = "plain";\n'}
    head = {"js/site.js": 'const a = "Zażółć";\nconst b = "other";\n'}
    assert check(tmp_path, base, head).ok


def test_size_limits(tmp_path: Path) -> None:
    at_limit = check(tmp_path / "b", {"css/a.css": lines(150, "o")}, {"css/a.css": lines(149, "n")})
    assert at_limit.changed_lines == 299 and at_limit.ok
    over = check(tmp_path / "c", {"css/a.css": lines(150, "o")}, {"css/a.css": lines(151, "n")})
    assert over.changed_lines == 301 and "size" in over.reasons


def test_generated_files_do_not_count(tmp_path: Path) -> None:
    body = "<html><body><p>x</p>\n" + "<!-- c -->\n" * 400 + "</body></html>\n"
    report = check(
        tmp_path,
        {"index.html": "<html><body><p>x</p></body></html>\n", "css/a.css": "a{}\n"},
        {"index.html": body, "css/a.css": "a{}\n" + lines(10, "n")},
    )
    assert report.ok and report.changed_lines == 10


def test_png_does_not_count(tmp_path: Path) -> None:
    png = bytes(range(256)) * 4000
    report = check(tmp_path, {"tests/visual/baselines/a.png": b"\x89PNG\0old"}, {"tests/visual/baselines/a.png": png})
    assert report.ok and report.changed_lines == 0


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, first, last = run(tmp_path, {"css/a.css": "a\n"}, {"css/a.css": "b\n"})
    assert main(["--base", first, "--head", last, "--repo", str(repo)]) == 0
    assert '"ok": true' in capsys.readouterr().out
    repo2, first2, last2 = run(tmp_path / "x", {"css/a.css": "a\n"}, {"CLAUDE.md": "x\n"})
    assert main(["--base", first2, "--head", last2, "--repo", str(repo2)]) == 1
    assert main(["--base", "nope", "--head", last, "--repo", str(repo)]) == 2
