"""Stage the public site for both Lighthouse and GitHub Pages, without dependencies."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Add new standalone pages here; only these files and directory trees are public.
PUBLIC_FILES = ("index.html", "docs/index.html", "favicon.png", "apple-touch-icon.png")
PUBLIC_DIRECTORIES = ("css", "js", "assets")


def _reject_symlinks(path: Path, root: Path) -> None:
    # Check parents too: docs/index.html must not traverse a symlinked docs/.
    while path != root:
        if path.is_symlink():
            raise ValueError(f"Symlink in public path: {path}")
        path = path.parent


def package_site(source: Path = ROOT) -> Path:
    """Replace source/_site with the allowlisted files; never follow symlinks."""
    source = source.resolve()
    output = source / "_site"
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ValueError(f"Output must be a regular directory: {output}")

    # Keep allowlist edits inside the source and away from the output tree.
    for name in (*PUBLIC_FILES, *PUBLIC_DIRECTORIES):
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts or relative.parts[0] == "_site":
            raise ValueError(f"Expected a relative public path outside _site: {name}")

    # Validate every input before removing a previous package.
    for relative in PUBLIC_FILES:
        path = source / relative
        _reject_symlinks(path, source)
        if not path.is_file():
            raise ValueError(f"Missing or non-file public input: {path}")
    for relative in PUBLIC_DIRECTORIES:
        directory = source / relative
        _reject_symlinks(directory, source)
        if not directory.is_dir():
            raise ValueError(f"Missing or non-directory public input: {directory}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Symlink in public path: {path}")
            if not path.is_file() and not path.is_dir():
                raise ValueError(f"Non-regular public input: {path}")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    for relative in PUBLIC_FILES:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    for relative in PUBLIC_DIRECTORIES:
        shutil.copytree(source / relative, output / relative)
    return output


if __name__ == "__main__":
    print(f"Packaged public site: {package_site()}")
