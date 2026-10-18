"""Small pure HTML, template and Polish-number helpers shared by the renderers."""

import html
import re
from collections import Counter
from string import Template

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


def copy_button(command: str, event: str = "", classes: str = "copy") -> str:
    """Use the same command value for the visible text and clipboard payload."""
    event = event or "docs-copy-" + re.sub(r"[^a-z0-9]+", "-", command.splitlines()[0].lower()).strip("-")[:48]
    return (
        f'<button type="button" class="{e(classes)}" data-command="{e(command)}" data-umami-event="{e(event)}" hidden>'
        f'{COPY_ICON}<span aria-live="polite">Kopiuj</span></button>'
    )


# A command-line option such as "--", "--from" or "-c": a token that starts with a hyphen.
OPTION = re.compile(r"(?<!\S)-\S*")


def command_text(command: str) -> str:
    """The visible command as phones should wrap it: options stay whole (browsers break "--"
    between its hyphens) and paths and URLs may break after a slash. The added <span> and
    <wbr> carry no text, so the copied and selected command is unchanged. A multi-line block
    (an agent prompt, JSON) wraps like prose instead, moving a whole URL to the next line."""
    if "\n" in command:
        return f'<span class="cmd-text">{e(command)}</span>'
    text = OPTION.sub(lambda option: f'<span class="cmd-option">{option.group()}</span>', e(command).replace("/", "/<wbr>"))
    return f'<span class="cmd-text">{text}</span>'


def landing_command(command: str, event: str, *, hero: bool = False) -> str:
    """A landing command row or hero pill, retaining their existing CSS variants."""
    if hero:
        return (f'<code class="text-primary">{command_text(command)}</code>\n'
                + copy_button(command, event, "copy copy--pill"))
    return (
        '<div class="command-row">\n'
        '<code class="command-code"><span aria-hidden="true" class="text-accent">$</span> '
        f'{command_text(command)}</code>\n'
        f'{copy_button(command, event)}\n</div>'
    )


def term(command: str, label: str = "Terminal", prompt: bool = True, event: str = "") -> str:
    """A terminal block with a copy button (js/site.js wires every button.copy)."""
    sign = '<span class="prompt-sign" aria-hidden="true">$</span> ' if prompt else ""
    tag = "code" if "\n" not in command else "pre"
    return (
        f'<div class="term"><div class="term-bar"><span aria-hidden="true"></span><span aria-hidden="true"></span>'
        f'<span aria-hidden="true"></span><em>{e(label)}</em></div>'
        f'<div class="cmd"><{tag}>{sign}{command_text(command)}</{tag}>'
        f'{copy_button(command, event)}</div></div>'
    )


def _lines(lines: list[str]) -> str:
    """A fragment of whole lines, with its surrounding newlines preserved."""
    return "\n" + "\n".join(lines) + "\n"


def render_template(source: str, values: dict[str, str], *, name: str, unique: bool = False) -> str:
    """Substitute named slots, rejecting invalid, unknown or missing slots.

    Landing slots are unique so accidentally duplicating a section fails the build.
    Use ``$$`` for a literal dollar sign, as in ``string.Template``.
    """
    template = Template(source)
    if not template.is_valid():
        raise BuildError(f"{name}: invalid placeholder; use ${{slot_name}} or $$ for a literal dollar")
    slots = Counter(
        match.group("named") or match.group("braced")
        for match in template.pattern.finditer(source)
        if match.group("named") or match.group("braced")
    )
    problems = []
    if missing := sorted(values.keys() - slots.keys()):
        problems.append(f"missing slots: {', '.join(missing)}")
    if unknown := sorted(slots.keys() - values.keys()):
        problems.append(f"unknown slots: {', '.join(unknown)}")
    if unique and (duplicates := sorted(slot for slot, count in slots.items() if count > 1)):
        problems.append(f"duplicate slots: {', '.join(duplicates)}")
    if problems:
        raise BuildError(f"{name}: {'; '.join(problems)}")
    return template.substitute(values)
