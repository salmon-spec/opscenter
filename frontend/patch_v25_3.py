#!/usr/bin/env python3
"""OpsCenter v2.5 Patch 3: Add 17 missing SVG icons to sprite"""
import json
import re

INDEX = '/opt/opscenter/frontend/index.html'

# Load downloaded icon SVGs
with open('/tmp/fa_icons.json') as f:
    icons = json.load(f)

# Read current index.html
with open(INDEX, 'r', encoding='utf-8') as f:
    html = f.read()

# Find existing symbols to check what's already there
existing_ids = set(re.findall(r'<symbol id="([^"]+)"', html))
print(f'Existing symbol IDs: {len(existing_ids)}')

# Parse each downloaded SVG and convert to <symbol>
new_symbols = []
added = 0
for icon_name, svg_content in icons.items():
    if icon_name in existing_ids:
        print(f'  SKIP (already exists): {icon_name}')
        continue

    # Extract viewBox and path from the SVG
    vb_match = re.search(r'viewBox="([^"]+)"', svg_content)
    path_match = re.search(r'<path d="([^"]*)"[^/]*/>', svg_content)

    if not vb_match or not path_match:
        print(f'  SKIP (parse failed): {icon_name}')
        continue

    viewBox = vb_match.group(1)
    path_d = path_match.group(1)

    symbol = f'    <symbol id="{icon_name}" viewBox="{viewBox}"><path d="{path_d}"/></symbol>'
    new_symbols.append(symbol)
    added += 1
    print(f'  ADD: {icon_name} (viewBox={viewBox}, path={len(path_d)} chars)')

if not new_symbols:
    print('No new icons to add!')
    exit(0)

# Find the closing </svg> of the sprite and insert before it
# The sprite is the first <svg> block containing <symbol> elements
# Find the position right before the closing </svg> of the defs/sprite block
sprite_close = html.find('</svg>')
if sprite_close == -1:
    print('ERROR: Could not find </svg> in index.html')
    exit(1)

# Insert new symbols before the closing </svg> of the sprite
insert_text = '\n'.join(new_symbols) + '\n'
html = html[:sprite_close] + insert_text + html[sprite_close:]

# Write back
with open(INDEX, 'w', encoding='utf-8') as f:
    f.write(html)

print(f'\nAdded {added} new SVG symbols to sprite')
print('Total symbols now:', len(existing_ids) + added)
