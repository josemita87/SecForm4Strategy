#!/usr/bin/env bash
# Builds docs/report/<name>.pdf from <name>.md (default name: report): pandoc to HTML, then headless Chrome to PDF.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name="${1:-report}"
pandoc="${PANDOC:-/opt/homebrew/bin/pandoc}"
chrome="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# <name>.css, when present, overrides report.css for that document
css=(--css "file://$here/report.css")
[[ -f "$here/$name.css" ]] && css+=(--css "file://$here/$name.css")

"$pandoc" -f gfm -t html5 -s \
  --metadata pagetitle="SecForm4Strategy" --metadata lang=en \
  "${css[@]}" \
  -o "$work/raw.html" "$here/$name.md"

# Absolute image paths, a title page, and a landscape page for each wide diagram with its caption
python3 -I - "$here" "$work/raw.html" "$work/report.html" <<'PY'
import re
import sys
from pathlib import Path

here, raw, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
wide = ('aws-architecture', 'aws-network', 'aws-ci-cd', 'data-flow', 'site-', 'validation-timeline', 'ports-and-adapters')
html = raw.read_text()

html = re.sub(
    r'src="((?:\.\./)?diagrams/[^"]+)"',
    lambda m: f'src="{(here / m[1]).resolve().as_uri()}"',
    html,
)

figure = re.compile(
    r'((?:<h[2-4][^>]*>(?:(?!</h[2-4]>).)*</h[2-4]>\s*){0,2})'
    r'<p><img src="([^"]+)"([^>]*)/?></p>\s*(<p><em>Figure[^<]*(?:<(?!/p>)[^<]*)*</p>)?',
    re.S,
)


def wrap(m: re.Match[str]) -> str:
    heading, src, attrs, caption = m[1] or '', m[2], m[3], m[4] or ''
    caption = caption.replace('<p>', '<p class="caption">', 1)
    if not any(w in Path(src).name for w in wide):
        return f'{heading}<div class="figure"><img src="{src}"{attrs}/>{caption}</div>'
    # Headings right above a wide diagram moves onto its landscape page instead of ending a portrait one
    return f'<div class="figure wide">{heading}<img src="{src}"{attrs}/>{caption}</div>'


html = figure.sub(wrap, html)

html = re.sub(
    r'(<h1[^>]*>.*?</h1>.*?</table>)',
    lambda m: f'<header class="title-page">{m[1]}</header>',
    html,
    count=1,
    flags=re.S,
)
out.write_text(html)
PY

"$chrome" --headless --disable-gpu --no-pdf-header-footer \
  --allow-file-access-from-files \
  --print-to-pdf="$here/$name.pdf" "file://$work/report.html" 2>/dev/null

echo "Wrote $here/$name.pdf"
