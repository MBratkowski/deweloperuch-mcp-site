## Problem

The installation prompt overflows horizontally.

## Where

- Pages: index
- Engines: webkit
- Widths: 375 px
- Mode: default
- Live SHA: `1111111111111111111111111111111111111111`
- `main` SHA: `2222222222222222222222222222222222222222`
- URL: https://example.test/

## Reproduce

```sh
uv run python -m tools.factory.probe --reproduce finding.json --target https://example.test/
```

## Actual and expected

**Actual:** scrollWidth 640 px in a 375 px viewport.

**Expected:** No horizontal overflow.

## Why it matters

Visitors must scroll sideways; WCAG 1.4.10 Reflow.

## Evidence

![viewport.png](https://raw.example/viewport.png)

![crop.png](https://raw.example/crop.png)

## Acceptance criteria

- The reproduction passes in webkit at 375 px
- No new finding on index

## Likely sources

- `css/site.css`
- `tools/html_helpers.py`

<!-- factory:fp=aaaaaaaaaaaa -->
<!-- factory:groups=W3siYnJlYWtwb2ludHMiOiBbNzIwLCA3NjBdLCAiY2hlY2siOiAib3ZlcmZsb3ciLCAiZW5naW5lcyI6IFsid2Via2l0Il0sICJmaW5kaW5ncyI6IFt7ImJveCI6IFswLCAwLCA2NDAsIDIwXSwgImNlbGwiOiB7ImVuZ2luZSI6ICJ3ZWJraXQiLCAiaGVpZ2h0IjogODAwLCAibW9kZSI6ICJkZWZhdWx0IiwgInBhZ2UiOiAiaW5kZXgiLCAid2lkdGgiOiAzNzV9LCAiY2hlY2siOiAib3ZlcmZsb3ciLCAibWVhc3VyZW1lbnQiOiAic2Nyb2xsV2lkdGggNjQwID4gY2xpZW50V2lkdGggMzc1IiwgInNlbGVjdG9yIjogImRpdi50ZXJtIn1dLCAiZmluZ2VycHJpbnQiOiAiZDE4Yzg2NmNlNjQ0IiwgIm1vZGUiOiAiZGVmYXVsdCIsICJub3RlIjogIiIsICJwYWdlIjogImluZGV4IiwgInNlbGVjdG9yIjogImRpdi50ZXJtIiwgInNldmVyaXR5IjogIlMzIiwgIndpZHRocyI6IFszNzVdfV0= -->
