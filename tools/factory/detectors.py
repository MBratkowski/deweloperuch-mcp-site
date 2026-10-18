"""In-page JavaScript checks shared by the browser tests and the factory probe.

Each script is a single function expression for `page.evaluate`. Findings are objects
`{selector, measurement, box}`; `box` is `[x, y, width, height]` in CSS px relative to the
document.
"""

# Defines `describe(el)` (a CSS path of up to three ancestors, stopping at an id; segments are
# `tag#id` or `tag.class1.class2`, never `nth-child`) and `boxOf(el)`.
DESCRIBE = """
  const segment = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.id) return `${tag}#${el.id}`;
    return tag + Array.from(el.classList).map(name => `.${name}`).join('');
  };
  const describe = (el) => {
    const path = [];
    for (let node = el, depth = 0; node && node !== document.documentElement && depth < 4; node = node.parentElement, depth += 1) {
      path.unshift(segment(node));
      if (node.id) break;
    }
    return path.join(' > ');
  };
  const boxOf = (el) => {
    const rect = el.getBoundingClientRect();
    return [rect.left + window.scrollX, rect.top + window.scrollY, rect.width, rect.height].map(Math.round);
  };
"""

# `.page` clips horizontal overflow (overflow-x: clip), so the document never scrolls
# sideways and a too-wide element is cut off instead. Hence the check looks for elements
# past the right edge of the viewport that no scrolling box holds: a long command or a
# table must scroll inside its own row (overflow-x: auto), not get cut off by a box with
# overflow: hidden. Decorative art is aria-hidden and may bleed out.
WIDE_ELEMENTS = """width => {""" + DESCRIBE + """
  const contained = (el) => {
    for (let box = el.parentElement; box && !box.matches('.page, body'); box = box.parentElement) {
      const scrolls = ['auto', 'scroll'].includes(getComputedStyle(box).overflowX);
      if (scrolls && box.getBoundingClientRect().right <= width + 1) return true;
    }
    return false;
  };
  return Array.from(document.querySelectorAll('body *'))
    .filter(e => e.checkVisibility() && !e.closest('[aria-hidden="true"]'))
    .filter(e => e.getBoundingClientRect().right > width + 1 && !contained(e))
    .slice(0, 5)
    .map(e => ({
      selector: describe(e),
      measurement: `right=${Math.round(e.getBoundingClientRect().right)} > ${width}`,
      box: boxOf(e),
    }));
}"""

FOCUS_PROBLEMS = """() => {""" + DESCRIBE + """
  const problems = [];
  const elements = document.querySelectorAll('a[href], button, summary, [tabindex]:not([tabindex="-1"])');
  for (const el of elements) {
    if (el.hidden || !el.checkVisibility()) continue;
    el.focus();
    if (document.activeElement !== el) {
      problems.push({ selector: describe(el), measurement: 'not focusable', box: boxOf(el) });
      continue;
    }
    const style = getComputedStyle(el);
    const visible = el.matches(':focus-visible') && style.outlineStyle !== 'none'
      && parseFloat(style.outlineWidth) >= 2 && style.outlineColor !== 'rgba(0, 0, 0, 0)';
    if (!visible) {
      problems.push({
        selector: describe(el),
        measurement: `outline ${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
        box: boxOf(el),
      });
    }
  }
  return { checked: elements.length, problems };
}"""
