"""Small pure HTML, marker and Polish-number helpers shared by the renderers."""

import html
import re

from tools.release import BuildError

e = html.escape


NUMBER_WORDS = (
    "zero", "jeden", "dwa", "trzy", "cztery", "pięć", "sześć", "siedem", "osiem", "dziewięć", "dziesięć",
    "jedenaście", "dwanaście", "trzynaście", "czternaście", "piętnaście", "szesnaście",
)


def plural(count: int, one: str, few: str, many: str) -> str:
    """The Polish form for ``count``: 1 narzędzie, 2–4 narzędzia, 5 narzędzi, 22 narzędzia."""
    if count == 1:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def _count(count: int, fact: str) -> str:
    return f'<span data-fact="{fact}">{count}</span>' if fact else str(count)


def tools_phrase(count: int, fact: bool = False) -> str:
    """'13 narzędzi'; with ``fact``, the number is marked for tools/check_facts.py."""
    return f"{_count(count, 'tool-count' if fact else '')} {plural(count, 'narzędzie', 'narzędzia', 'narzędzi')}"


def prompts_phrase(count: int, fact: bool = False) -> str:
    """'4 gotowe prompty'; with ``fact``, the number is marked for tools/check_facts.py."""
    return f"{_count(count, 'prompt-count' if fact else '')} {plural(count, 'gotowy prompt', 'gotowe prompty', 'gotowych promptów')}"


def prompts_sentence_start(count: int) -> str:
    """'Cztery gotowe prompty prowadzą': the number as a word and the verb agreeing with it."""
    word = NUMBER_WORDS[count].capitalize() if 0 < count < len(NUMBER_WORDS) else str(count)
    return f"{word} {plural(count, 'gotowy prompt prowadzi', 'gotowe prompty prowadzą', 'gotowych promptów prowadzi')}"


COPY_ICON = ('<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"></rect>'
             '<path d="M5 15V6a2 2 0 0 1 2-2h9"></path></svg>')


def term(command: str, label: str = "Terminal", prompt: bool = True, event: str = "") -> str:
    """A terminal block with a copy button (js/site.js wires every button.copy)."""
    sign = '<span class="prompt-sign" aria-hidden="true">$</span> ' if prompt else ""
    tag = "code" if "\n" not in command else "pre"
    return (
        f'<div class="term"><div class="term-bar"><span aria-hidden="true"></span><span aria-hidden="true"></span>'
        f'<span aria-hidden="true"></span><em>{e(label)}</em></div>'
        f'<div class="cmd"><{tag}>{sign}<span class="cmd-text">{e(command)}</span></{tag}>'
        f'<button type="button" class="copy" data-command="{e(command)}" data-umami-event="{e(event or "docs-copy-" + re.sub(r"[^a-z0-9]+", "-", command.splitlines()[0].lower()).strip("-")[:48])}" hidden>{COPY_ICON}'
        f'<span aria-live="polite">Kopiuj</span></button></div></div>'
    )


def _lines(lines: list[str]) -> str:
    """A block of whole lines: the markers stay on lines of their own."""
    return "\n" + "\n".join(lines) + "\n"


def fill_blocks(page: str, blocks: dict[str, str]) -> str:
    """``page`` with the text between every ``<!-- name:start -->`` and ``<!-- name:end -->`` of a
    name in ``blocks`` replaced by ``blocks[name]``. Each name must be on the page."""
    found = set()

    def fill(match: re.Match) -> str:
        name = match.group(1)
        found.add(name)
        return f"<!-- {name}:start -->{blocks[name]}<!-- {name}:end -->"

    names = "|".join(re.escape(name) for name in blocks)
    page = re.sub(rf"<!-- ({names}):start -->.*?<!-- \1:end -->", fill, page, flags=re.DOTALL)
    if missing := sorted(blocks.keys() - found):
        raise BuildError(f"index.html lacks the <!-- name:start --> and <!-- name:end --> markers of: {', '.join(missing)}")
    return page
