"""Generate SVG contact sheets from the current cards. Run from project root."""
from pathlib import Path
import re

for theme in ('dark', 'light'):
    background, foreground = ('#111827', '#cbd5e1') if theme == 'dark' else ('#e7edf5', '#334155')
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="894" height="1166"><rect width="894" height="1166" fill="{background}"/>']
    for name, x, y, width, height in [('full',24,48,846,225),('compact',24,321,846,195),('half',24,564,423,195),('grass',447,564,423,195),('half-grass',24,807,423,335)]:
        parts.append(f'<text x="{x}" y="{y-12}" font-family="Arial" font-size="13" fill="{foreground}">{name} · {width} × {height}</text>')
        svg = Path(f'cards/ai-usage-{name}-{theme}.svg').read_text().replace('<svg ', f'<svg x="{x}" y="{y}" ', 1)
        for ident in re.findall(r'\bid="([^"]+)"', svg):
            svg = svg.replace(f'id="{ident}"', f'id="{name}-{ident}"').replace(f'#{ident})', f'#{name}-{ident})')
        svg = svg.replace('aria-labelledby="title desc"', f'aria-labelledby="{name}-title {name}-desc"')
        parts.append(svg)
    parts.append('</svg>')
    Path('previews').mkdir(exist_ok=True)
    Path(f'previews/sizes-{theme}.svg').write_text(''.join(parts))
