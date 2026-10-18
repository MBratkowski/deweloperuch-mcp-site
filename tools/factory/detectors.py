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

# Elements whose own content is cut off by a box that hides its overflow.
CLIPPED_TEXT = """() => {""" + DESCRIBE + """
  const hides = (value) => value === 'hidden' || value === 'clip';
  return Array.from(document.querySelectorAll('body *'))
    .filter(e => e.checkVisibility() && !e.closest('[aria-hidden="true"]'))
    .filter(e => Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()))
    .filter(e => {
      const style = getComputedStyle(e);
      // Visually hidden text (a 1 px box, clip or clip-path) is clipped on purpose.
      if (e.clientWidth <= 2 || e.clientHeight <= 2) return false;
      if (style.clip !== 'auto' || style.clipPath !== 'none') return false;
      const x = hides(style.overflowX) && e.scrollWidth > e.clientWidth + 1;
      const y = hides(style.overflowY) && e.scrollHeight > e.clientHeight + 1;
      return x || y;
    })
    .slice(0, 5)
    .map(e => ({
      selector: describe(e),
      measurement: `scroll ${e.scrollWidth}x${e.scrollHeight} > client ${e.clientWidth}x${e.clientHeight}`,
      box: boxOf(e),
    }));
}"""

# Pairs of visible interactive elements whose boxes intersect by more than 1 px, and
# interactive elements that sit on top of the centre of another element's text. The element
# reported is the later one of a pair, or the covering one.
OVERLAPS = """() => {""" + DESCRIBE + """
  const INTERACTIVE = 'a[href], button, summary, input, [tabindex]:not([tabindex="-1"])';
  const visible = (e) => e.checkVisibility() && !e.closest('[aria-hidden="true"]');
  const related = (a, b) => a.contains(b) || b.contains(a);
  const out = [];
  const seen = new Set();
  const add = (el, measurement) => {
    if (seen.has(el)) return;
    seen.add(el);
    out.push({ selector: describe(el), measurement, box: boxOf(el) });
  };

  const interactive = Array.from(document.querySelectorAll(INTERACTIVE)).filter(visible);
  for (let i = 0; i < interactive.length; i += 1) {
    for (let j = i + 1; j < interactive.length; j += 1) {
      const a = interactive[i], b = interactive[j];
      if (related(a, b)) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (w > 1 && h > 1) add(b, `overlaps ${describe(a)} by ${Math.round(w)}x${Math.round(h)}`);
    }
  }

  const scrollX = window.scrollX, scrollY = window.scrollY;
  const texts = Array.from(document.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, td, code, span'))
    .filter(e => visible(e) && Array.from(e.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()));
  for (const el of texts) {
    if (out.length >= 5) break;
    el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    const rect = el.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    const cover = hit && hit.closest(INTERACTIVE);
    // A hit from a ::after overlay lies outside the element's own box: a stretched link, on purpose.
    const box = cover && cover.getBoundingClientRect();
    const px = rect.left + rect.width / 2, py = rect.top + rect.height / 2;
    const inside = box && px >= box.left && px <= box.right && py >= box.top && py <= box.bottom;
    if (cover && inside && !related(cover, el)) add(cover, `covers the text of ${describe(el)}`);
  }
  window.scrollTo({ left: scrollX, top: scrollY, behavior: 'instant' });
  return out.slice(0, 5);
}"""
