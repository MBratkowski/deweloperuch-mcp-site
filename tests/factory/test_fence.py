"""The fence: allowed and forbidden paths, size, weakened tests and the copy guard."""

import subprocess
from collections import Counter
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


TEST_BASE = (
    "import pytest\n\n"
    "@pytest.mark.parametrize('n', [1, 2, 3])\n"
    "def test_x(n, page):\n"
    "    page.wait_for(timeout=1000)\n"
    "    assert n > 0\n"
    "    assert n > 0\n"
    "    expect(page).to_be_ok()\n"
)


def hard(report, needle: str) -> bool:
    return not report.ok and any(needle in r for r in report.reasons)


def only_review(report) -> bool:
    return report.ok and report.needs_human and report.review


def test_deleted_test_file_needs_a_human(tmp_path: Path) -> None:
    report = check(tmp_path, {"tests/e2e/test_x.py": TEST_BASE}, {"tests/e2e/test_x.py": None})
    assert report.ok and report.review == ["existing tests changed: tests/e2e/test_x.py"]
    assert report.needs_human


@pytest.mark.parametrize(
    "marker",
    [
        "@pytest.mark.skip",
        "@pytest.mark.xfail",
        "@pytest.mark.skipif(True, reason='x')",
        "@pytest.mark.network",
        "@pytest.mark.visual",
        "pytestmark = pytest.mark.skip",
        "__test__ = False",
        "pytest.skip('no')",
        "pytest.importorskip('x')",
        "request.node.add_marker(pytest.mark.skip)",
        "request.applymarker(m)",
        "def pytest_collection_modifyitems(items): pass",
    ],
)
def test_added_skip_forms_are_hard(tmp_path: Path, marker: str) -> None:
    head = TEST_BASE + marker + "\n"
    assert hard(check(tmp_path, {"tests/test_x.py": TEST_BASE}, {"tests/test_x.py": head}), "weakened tests")


def test_adding_a_test_to_an_existing_file_is_ok(tmp_path: Path) -> None:
    head = TEST_BASE + "\n\ndef test_y():\n    assert 2\n"
    report = check(tmp_path, {"tests/test_x.py": TEST_BASE}, {"tests/test_x.py": head})
    assert report.ok and not report.needs_human and report.reasons == []


def test_new_test_file_is_ok(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a\n"}, {"tests/e2e/test_new.py": "def test_z():\n    assert 1\n"})
    assert report.ok and not report.needs_human


def test_changed_baseline_png_is_ok(tmp_path: Path) -> None:
    report = check(
        tmp_path, {"tests/visual/baselines/a.png": b"\x89PNG\0old"}, {"tests/visual/baselines/a.png": b"\x89PNG\0new"}
    )
    assert report.ok and not report.needs_human


@pytest.mark.parametrize(
    "edit",
    [
        TEST_BASE.replace("    assert n > 0\n    assert n > 0\n", "    assert n > 0\n"),
        TEST_BASE.replace("assert n > 0", "assert n > -5"),
        TEST_BASE.replace("[1, 2, 3]", "[1]"),
        TEST_BASE.replace("    expect(page).to_be_ok()\n", ""),
        TEST_BASE.replace("timeout=1000", "timeout=10_000"),
        TEST_BASE.replace("timeout=1000", "timeout=5000"),
        TEST_BASE.replace("def test_x", "def _test_x"),
    ],
)
def test_changes_to_existing_lines_need_a_human(tmp_path: Path, edit: str) -> None:
    report = check(tmp_path, {"tests/test_x.py": TEST_BASE}, {"tests/test_x.py": edit})
    assert report.review == ["existing tests changed: tests/test_x.py"] or hard(report, "weakened")
    assert report.needs_human


def test_renamed_test_function_is_hard(tmp_path: Path) -> None:
    head = TEST_BASE.replace("def test_x", "def _test_x")
    assert hard(check(tmp_path, {"tests/test_x.py": TEST_BASE}, {"tests/test_x.py": head}), "test removed")


def test_inserted_return_is_hard(tmp_path: Path) -> None:
    base = "def test_x():\n    y = 1\n    assert y\n"
    head = "def test_x():\n    y = 1\n    return\n    assert y\n"
    assert hard(check(tmp_path, {"tests/test_x.py": base}, {"tests/test_x.py": head}), "early exit")


def test_inserted_if_false_is_hard(tmp_path: Path) -> None:
    base = "def test_x():\n    y = 1\n    assert y\n"
    head = "def test_x():\n    y = 1\n    if False:\n        pass\n    assert y\n"
    assert hard(check(tmp_path, {"tests/test_x.py": base}, {"tests/test_x.py": head}), "early exit")


def test_added_decorator_is_hard(tmp_path: Path) -> None:
    base = "def test_x(n):\n    assert n\n"
    head = "@pytest.mark.parametrize('n', [])\ndef test_x(n):\n    assert n\n"
    assert hard(check(tmp_path, {"tests/test_x.py": base}, {"tests/test_x.py": head}), "decorators")


def test_redefined_test_is_hard(tmp_path: Path) -> None:
    base = "def test_x():\n    assert 1\n"
    head = base + "\ndef test_x():\n    pass\n"
    assert hard(check(tmp_path, {"tests/test_x.py": base}, {"tests/test_x.py": head}), "repeated")


def test_unparsable_head_is_hard(tmp_path: Path) -> None:
    base = "def test_x():\n    assert 1\n"
    head = base + "def broken(:\n"
    assert hard(check(tmp_path, {"tests/test_x.py": base}, {"tests/test_x.py": head}), "does not parse")


def test_rename_test_file_is_hard(tmp_path: Path) -> None:
    report = check(tmp_path, {"tests/test_x.py": TEST_BASE}, {}, rename=("tests/test_x.py", "css/test_x.py"))
    assert hard(report, "test file renamed")
    report = check(tmp_path / "b", {"tests/test_x.py": TEST_BASE}, {}, rename=("tests/test_x.py", "tests/test_y.py"))
    assert hard(report, "test file renamed")


@pytest.mark.parametrize(
    "path",
    [
        "tests/visual/conftest.py",
        "tests/conftest.py",
        "tests/visual/comparison/__init__.py",
        "tests/pytest.ini",
        "tests/tox.ini",
        "tests/setup.cfg",
        ".gitattributes",
        "tests/__pycache__/x.pyc",
        "tests/helper.pyc",
        "tests/data.json",
        "tests/visual/helper.py",
    ],
)
def test_test_infrastructure_and_unlisted_new_files(tmp_path: Path, path: str) -> None:
    report = check(tmp_path, {"css/a.css": "a\n"}, {path: "x\n"})
    assert not report.ok


def test_changing_visual_conftest_is_hard(tmp_path: Path) -> None:
    base = {"tests/visual/conftest.py": "default = False\n"}
    report = check(tmp_path, base, {"tests/visual/conftest.py": "default = True\n"})
    assert hard(report, "test infrastructure")


def test_comparison_py_edit(tmp_path: Path) -> None:
    report = check(
        tmp_path, {"tests/visual/comparison.py": "TOL = 1\n"}, {"tests/visual/comparison.py": "TOL = 2\n"}
    )
    assert "weakened tests" in report.reasons
    assert any(r.startswith("forbidden path") for r in report.reasons)


def test_symlink_is_hard(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, {"css/a.css": "a\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    first = git(repo, "rev-parse", "HEAD")
    (repo / "css" / "link.css").symlink_to("/etc/passwd")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    report = check_fence(repo, first, git(repo, "rev-parse", "HEAD"), CONFIG.fence, CONFIG.limits)
    assert hard(report, "symlink")


def test_gitlink_is_hard(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, {"css/a.css": "a\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{first},css/sub")
    git(repo, "commit", "-qm", "head")
    report = check_fence(repo, first, git(repo, "rev-parse", "HEAD"), CONFIG.fence, CONFIG.limits)
    assert hard(report, "symlink or submodule")


def test_binary_non_png_is_hard_and_nul_css_is_binary(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a\n"}, {"css/a.css": "a\0\n" + lines(400, "n")})
    assert hard(report, "binary file: css/a.css")


def test_control_character_path_is_hard(tmp_path: Path) -> None:
    report = check(tmp_path, {"css/a.css": "a\n"}, {"css/a\tb.css": "x\n"})
    assert hard(report, "invalid path")
    report = check(tmp_path / "c", {"css/a.css": "a\n"}, {"css/<!--.css": "x\n"})
    assert hard(report, "invalid path")


PAGE = '<html><head><title>T</title><meta name="description" content="Opis"></head><body><p>Cześć   świecie</p><img src="a.png" alt="Logo"><script>var x = "zażółć";</script></body></html>'


def test_visible_text() -> None:
    text = visible_text(PAGE)
    assert text == ["T Cześć świecie", "meta=Opis", "alt=Logo"]
    assert "zażółć" not in " ".join(text)


def test_visible_text_attributes() -> None:
    html = '<meta property="og:title" content="OG"><input placeholder="Szukaj" aria-label="Pole" title="Tytuł"><style>p{}</style>'
    assert visible_text(html) == ["", "meta=OG", "placeholder=Szukaj", "aria-label=Pole", "title=Tytuł"]


def test_duplicate_attributes_are_all_recorded() -> None:
    assert visible_text('<img alt="Nowe" alt="Stare">')[1:] == ["alt=Nowe", "alt=Stare"]


def test_wrapping_in_span_is_not_a_change() -> None:
    assert visible_text("<p>Cześć świecie</p>") == visible_text("<p>Cze<span>ść</span> <wbr>świecie</p>")
    assert visible_text("<p>Cześć świecie</p>") != visible_text("<p>Cześć świat</p>")


def test_js_polish_strings() -> None:
    source = "a('plain'); b(\"zażółć\"); c(`gęś ${x}`); d('it\\'s żółw'); e('Tak'); // 'Komentarz ą'\n/* \"Inny tekst\" */"
    assert js_polish_strings(source) == Counter({"zażółć": 1, "gęś ${x}": 1, "it's żółw": 1, "Tak": 1})


def test_js_escapes_are_decoded() -> None:
    assert js_polish_strings('x("za\\u017c\\u00f3\\u0142\\u0107")') == Counter({"zażółć": 1})
    assert js_polish_strings('x("\\u{17c}ar")') == Counter({"żar": 1})
    assert js_polish_strings('x("\\xF3d\\u017a")') == Counter({"ódź": 1})


def test_changed_text_node(tmp_path: Path) -> None:
    report = check(tmp_path, {"index.html": PAGE}, {"index.html": PAGE.replace("Cześć", "Hej")})
    assert report.copy_changed and "copy" in report.reasons


def test_changed_alt_text(tmp_path: Path) -> None:
    report = check(tmp_path, {"docs/index.html": PAGE}, {"docs/index.html": PAGE.replace("Logo", "Znak")})
    assert report.copy_changed


def test_duplicate_alt_attribute_is_copy(tmp_path: Path) -> None:
    head = PAGE.replace('alt="Logo"', 'alt="Nowe" alt="Logo"')
    assert check(tmp_path, {"index.html": PAGE}, {"index.html": head}).copy_changed


def test_added_script_or_style_or_textarea_is_copy(tmp_path: Path) -> None:
    for i, extra in enumerate(["<script>1</script>", "<style>p{}</style>", "<textarea>Hej</textarea>"]):
        head = PAGE.replace("</body>", extra + "</body>")
        assert check(tmp_path / str(i), {"index.html": PAGE}, {"index.html": head}).copy_changed


def test_markup_only_page_change_keeps_copy(tmp_path: Path) -> None:
    report = check(tmp_path, {"index.html": PAGE}, {"index.html": PAGE.replace("<p>", '<p class="x">')})
    assert report.ok and not report.copy_changed


def test_wrapping_text_in_span_keeps_copy(tmp_path: Path) -> None:
    head = PAGE.replace("<p>Cześć", "<p><span>Cześć</span>")
    report = check(tmp_path, {"index.html": PAGE}, {"index.html": head})
    assert report.ok and not report.copy_changed


def test_css_content_string_is_copy(tmp_path: Path) -> None:
    base = {"css/a.css": 'a::after { content: "Dalej"; }\n'}
    report = check(tmp_path, base, {"css/a.css": 'a::after { content: "Wstecz"; }\n'})
    assert report.copy_changed
    same = check(tmp_path / "s", base, {"css/a.css": 'a::after { content: "Dalej"; color: red; }\n'})
    assert same.ok and not same.copy_changed


def test_changed_polish_literal(tmp_path: Path) -> None:
    base = {"js/site.js": 'const a = "Zażółć";\nconst b = "plain";\n'}
    head = {"js/site.js": 'const a = "Zażółć gęślą";\nconst b = "plain";\n'}
    report = check(tmp_path, base, head)
    assert report.copy_changed and "copy" in report.reasons


def test_duplicate_or_commented_copy_is_seen(tmp_path: Path) -> None:
    base = {"js/site.js": 'a("Zażółć");\n'}
    dup = check(tmp_path / "a", base, {"js/site.js": 'a("Zażółć");\na("Zażółć");\n'})
    assert dup.copy_changed
    swapped = check(tmp_path / "b", base, {"js/site.js": '// a("Zażółć");\na("Inne");\n'})
    assert swapped.copy_changed


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


def test_cli_exit_three_for_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, first, last = run(tmp_path, {"tests/test_x.py": TEST_BASE}, {"tests/test_x.py": None})
    assert main(["--base", first, "--head", last, "--repo", str(repo)]) == 3
    assert '"needs_human": true' in capsys.readouterr().out
