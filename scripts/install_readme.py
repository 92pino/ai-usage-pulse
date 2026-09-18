#!/usr/bin/env python3
"""Install or update an AI usage card block in a GitHub profile README."""
import argparse
from pathlib import Path
import sys

START = '<!-- AI_USAGE_CARD:START -->'
END = '<!-- AI_USAGE_CARD:END -->'
VARIANTS = {
    'dashboard': ('ai-usage', 900),
    'combo': ('ai-usage-combo', 900),
    'full': ('ai-usage-full', 846),
    'compact': ('ai-usage-compact', 846),
    'half': ('ai-usage-half', 423),
    'grass': ('ai-usage-grass', 423),
    'half-grass': ('ai-usage-half-grass', 423),
}


def picture(stem, width, base='./cards'):
    base = base.rstrip('/')
    return f'''<picture>
  <source media="(prefers-color-scheme: dark)" srcset="{base}/{stem}-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="{base}/{stem}-light.svg">
  <img alt="AI coding token usage" src="{base}/{stem}-light.svg" width="{width}">
</picture>'''


def card_block(variant, base='./cards'):
    if variant == 'split':
        body = (picture('ai-usage-half', '49%', base) + '\n' +
                picture('ai-usage-grass', '49%', base))
    else:
        stem, width = VARIANTS[variant]
        body = picture(stem, width, base)
    return f'{START}\n{body}\n{END}'


def install(text, block, heading):
    has_start, has_end = START in text, END in text
    if has_start != has_end:
        raise ValueError('README has only one AI usage marker; repair or remove it first')
    if has_start:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        return before + block + after
    prefix = text.rstrip()
    section = f'{heading}\n\n{block}' if heading else block
    return (prefix + '\n\n' if prefix else '') + section + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True,
                        help='Local GitHub profile repository')
    parser.add_argument('--variant', choices=[*VARIANTS, 'split'], default='combo')
    parser.add_argument('--heading', default='## AI Coding Activity',
                        help='Heading used only on first install; pass an empty value to omit')
    parser.add_argument('--base', default='./cards',
                        help='Card URL/path as referenced from README')
    args = parser.parse_args(argv)
    readme = args.repo/'README.md'
    if not args.repo.is_dir():
        raise ValueError(f'Profile repository directory does not exist: {args.repo}')
    old = readme.read_text(encoding='utf-8') if readme.exists() else ''
    new = install(old,card_block(args.variant,args.base),args.heading)
    readme.write_text(new,encoding='utf-8')
    action = 'updated' if START in old else 'installed'
    print(f'{action}: {readme} ({args.variant})')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        sys.exit(1)
