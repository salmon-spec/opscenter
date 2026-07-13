#!/usr/bin/env python3
"""Fix: Move 17 new SVG symbols from favicon to the correct sprite block"""
import re

INDEX = '/opt/opscenter/frontend/index.html'

with open(INDEX, 'r', encoding='utf-8') as f:
    html = f.read()

# Step 1: Find and extract the new symbols that were wrongly inserted in favicon
# The favicon line looks like: <link rel="icon" href="data:image/svg+xml,<svg ...new symbols...</svg>">
# We need to remove the new symbols from there and fix the favicon

new_icon_ids = ['fa-chart-area', 'fa-chart-bar', 'fa-cogs', 'fa-database', 'fa-door-open',
                'fa-envelope', 'fa-file-pdf', 'fa-fire', 'fa-gauge-high', 'fa-git-alt',
                'fa-heartbeat', 'fa-infinity', 'fa-mobile-screen', 'fa-robot',
                'fa-sitemap', 'fa-store', 'fa-wrench']

# Find the favicon line
favicon_start = html.find('<link rel="icon" href="data:image/svg+xml,<svg')
if favicon_start == -1:
    print('ERROR: Could not find favicon link')
    exit(1)

favicon_end = html.find('">', favicon_start)
favicon_line = html[favicon_start:favicon_end+2]

# Extract new symbols from favicon
new_symbols_in_favicon = re.findall(r'(<symbol id="(' + '|'.join(new_icon_ids) + r')"[^/]+/>)', favicon_line)

# Remove new symbols from favicon - restore original favicon
# The original favicon should just be: <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🛡️</text></svg>">
original_favicon = '<link rel="icon" href="data:image/svg+xml,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'><text y=\'.9em\' font-size=\'90\'>🛡️</text></svg>">'

# Actually, let's just rebuild the favicon by removing all <symbol>...</symbol> from it
clean_favicon = re.sub(r'\s*<symbol[^/]+/>', '', favicon_line)
# Also clean closing </svg> that might appear before ">
# The favicon should end with </svg>">
clean_favicon = re.sub(r'</svg>\s*$', '', clean_favicon)
if not clean_favicon.endswith('</svg>">'):
    clean_favicon = clean_favicon.rstrip() + '</svg>">'

html = html.replace(favicon_line, clean_favicon)
print('Step 1: Cleaned favicon')

# Step 2: Collect all new symbol definitions from the file
new_symbol_defs = []
for icon_id in new_icon_ids:
    pattern = r'(<symbol id="' + re.escape(icon_id) + r'"[^>]*><path[^/]*/></symbol>)'
    match = re.search(pattern, html)
    if match:
        new_symbol_defs.append(match.group(1))
        # Remove from current (wrong) location
        html = html.replace(match.group(1), '', 1)
        print(f'  Extracted: {icon_id}')

# Step 3: Find the correct sprite </svg> and insert symbols before it
# The correct sprite is the standalone <svg>...</svg> block that contains the existing symbols
# Find it by looking for </svg>\n<div id="app">
sprite_marker = '</svg>\n<div id="app">'
if sprite_marker not in html:
    # Try other patterns
    sprite_marker = '</svg>\n<div id="app"'
    if sprite_marker not in html:
        print('ERROR: Could not find sprite closing tag near <div id="app">')
        # Let's find it differently
        marker_pos = html.find('<div id="app">')
        if marker_pos == -1:
            print('ERROR: Could not find <div id="app">')
            exit(1)
        # Find the </svg> just before it
        search_start = max(0, marker_pos - 5000)
        last_svg_close = html.rfind('</svg>', search_start, marker_pos)
        if last_svg_close == -1:
            print('ERROR: Could not find </svg> before <div id="app">')
            exit(1)
sprite_close_pos = html.find('</svg>', html.find('<div id="app">') - 5000, html.find('<div id="app">'))

if sprite_close_pos == -1:
    print('ERROR: Still could not find sprite closing tag')
    exit(1)

# Insert new symbols before the closing </svg>
insert_text = '\n'.join(new_symbol_defs) + '\n'
html = html[:sprite_close_pos] + insert_text + html[sprite_close_pos:]

print(f'\nStep 3: Inserted {len(new_symbol_defs)} symbols into correct sprite')

# Write back
with open(INDEX, 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open(INDEX, 'r', encoding='utf-8') as f:
    final = f.read()
    all_ids = set(re.findall(r'<symbol id="([^"]+)"', final))
    still_missing = [id for id in new_icon_ids if id not in all_ids]
    print(f'\nVerification: {len(all_ids)} total symbols')
    print(f'Still missing: {still_missing if still_missing else "None - all fixed!"}')
