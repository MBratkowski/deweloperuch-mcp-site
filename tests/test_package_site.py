"""The publish boundary is exercised against real temporary files, entirely offline."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_CONTENT = {
    "index.html": b"landing\x00page",
    "docs/index.html": b"documentation",
    "css/site.css": b"body {}",
    "js/site.js": b"console.log('site')",
    "assets/fonts/font.woff2": b"font\x00data",
    "assets/fonts/OFL.txt": b"font licence",
    "assets/icon/logo.svg": b"<svg></svg>",
    "favicon.png": b"favicon\x00data",
    "apple-touch-icon.png": b"apple icon",
}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for name, content in {
        **PUBLIC_CONTENT,
        "README.md": b"readme",
        "tools/private.py": b"private tool",
        "tests/test_private.py": b"private test",
        "docs/design/reference.html": b"design reference",
        "docs/superpowers/plans/plan.md": b"plan",
        "data/server/version.json": b"metadata",
        ".git/config": b"git config",
        ".github/workflows/pages.yml": b"workflow",
        "draft.html": b"unpublished page",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def contents(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_packages_only_public_files_with_their_original_content(source: Path) -> None:
    from tools.package_site import package_site

    output = package_site(source)

    assert output == source / "_site"
    assert contents(output) == PUBLIC_CONTENT


def test_repackaging_removes_stale_files_and_uses_current_sources(source: Path) -> None:
    from tools.package_site import package_site

    output = package_site(source)
    (output / "stale.html").write_text("old page")
    (source / "assets/icon/logo.svg").unlink()
    (source / "index.html").write_bytes(b"updated landing")

    package_site(source)

    expected = {**PUBLIC_CONTENT, "index.html": b"updated landing"}
    del expected["assets/icon/logo.svg"]
    assert contents(output) == expected


@pytest.mark.parametrize("missing", ["index.html", "docs/index.html", "css", "js", "assets", "favicon.png", "apple-touch-icon.png"])
def test_missing_public_input_fails_before_removing_previous_output(source: Path, missing: str) -> None:
    from tools.package_site import package_site

    output = package_site(source)
    path = source / missing
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()

    with pytest.raises(ValueError, match="public"):
        package_site(source)

    assert contents(output) == PUBLIC_CONTENT


@pytest.mark.parametrize("linked", ["index.html", "docs", "assets", "assets/fonts", "assets/fonts/font.woff2"])
def test_public_symlinks_cannot_leak_files_outside_the_publish_boundary(source: Path, linked: str) -> None:
    from tools.package_site import package_site

    private = source.parent / "private"
    private.write_bytes(b"secret")
    path = source / linked
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    path.symlink_to(private)

    with pytest.raises(ValueError, match="[Ss]ymlink"):
        package_site(source)

    assert private.read_bytes() == b"secret"
    assert not (source / "_site").exists()


def test_output_symlink_is_rejected_without_touching_its_target(source: Path) -> None:
    from tools.package_site import package_site

    outside = source.parent / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    (source / "_site").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="_site"):
        package_site(source)

    assert contents(outside) == {"keep.txt": b"keep"}
    assert (source / "_site").is_symlink()


def test_output_file_is_rejected_without_deleting_it(source: Path) -> None:
    from tools.package_site import package_site

    output = source / "_site"
    output.write_bytes(b"keep")

    with pytest.raises(ValueError, match="_site"):
        package_site(source)

    assert output.read_bytes() == b"keep"


def test_cli_packages_its_repository_independently_of_the_working_directory(source: Path, tmp_path: Path) -> None:
    script = source / "tools/package_site.py"
    shutil.copyfile(ROOT / "tools/package_site.py", script)

    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert contents(source / "_site") == PUBLIC_CONTENT
    assert not (tmp_path / "_site").exists()


@pytest.mark.parametrize("manifest", ["PUBLIC_FILES", "PUBLIC_DIRECTORIES"])
def test_allowlist_parent_traversal_is_rejected_before_copying_or_cleanup(
    source: Path, monkeypatch: pytest.MonkeyPatch, manifest: str,
) -> None:
    from tools import package_site

    output = package_site.package_site(source)
    private = source.parent / "private"
    if manifest == "PUBLIC_FILES":
        private.write_bytes(b"secret")
    else:
        private.mkdir()
        (private / "secret.txt").write_bytes(b"secret")
    monkeypatch.setattr(package_site, manifest, (*getattr(package_site, manifest), "../private"))

    with pytest.raises(ValueError, match="relative public path"):
        package_site.package_site(source)

    assert contents(output) == PUBLIC_CONTENT
    assert not (source / "private").exists()
