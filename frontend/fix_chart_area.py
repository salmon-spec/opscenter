#!/usr/bin/env python3
"""Fix fa-chart-area: remove broken remnant from favicon, add complete symbol to sprite"""
import re

INDEX = '/opt/opscenter/frontend/index.html'

with open(INDEX, 'r', encoding='utf-8') as f:
    html = f.read()

# Step 1: Fix the broken favicon line
# Current broken line has fa-chart-area fragment spanning the </svg>">
# Need to remove everything between </text> and </svg>">
# Also remove the orphaned <path>...</symbol> after </svg>">

# Find and fix the favicon
favicon_pattern = r'<link rel="icon" href="data:image/svg\+xml,<svg[^>]*><text[^>]*>🛡️</text>((?!<symbol id="fa-chart-area").)*<symbol id="fa-chart-area"[^>]*></svg>"><path[^/]*/></symbol>'
# This is too complex. Let me just rebuild the favicon line manually.

# The favicon should be: <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🛡️</text></svg>">
# Clean up: find the <link rel="icon" line and replace it entirely
correct_favicon = "<link rel=\"icon\" href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🛡️</text></svg>\">"

# Find the broken favicon line (starts with <link rel="icon")
favicon_start = html.find('<link rel="icon"')
favicon_end = html.find('">', favicon_start) + 2

# Also remove the orphaned path after it
# Check what comes after the favicon line
after_favicon = html[favicon_end:favicon_end+600]
print("After favicon (first 600 chars):")
print(repr(after_favicon[:600]))

# Find where the orphaned content ends - it should be ...</symbol>\n
orphan_end = html.find('</symbol>', favicon_end)
if orphan_end != -1:
    orphan_end += len('</symbol>')
    # Skip any trailing whitespace
    while orphan_end < len(html) and html[orphan_end] in ' \t\n':
        orphan_end += 1

    print(f"\nOrphan content length: {orphan_end - favicon_end} chars")
    
    # Replace the broken favicon + orphan with the correct favicon
    html = html[:favicon_start] + correct_favicon + '\n' + html[orphan_end:]
    print("Step 1: Fixed favicon and removed orphan content")
else:
    print("WARNING: Could not find orphan end, just fixing favicon")
    html = html[:favicon_start] + correct_favicon + html[favicon_end:]

# Step 2: Add fa-chart-area to the correct sprite
# Read the icon data from the downloaded file
import json
with open('/tmp/fa_icons.json') as f:
    icons = json.load(f)

svg_content = icons.get('fa-chart-area')
if svg_content:
    vb_match = re.search(r'viewBox="([^"]+)"', svg_content)
    path_match = re.search(r'<path d="([^"]*)"', svg_content)
    if vb_match and path_match:
        symbol_def = f'    <symbol id="fa-chart-area" viewBox="{vb_match.group(1)}"><path d="{path_match.group(1)}"/></symbol>'
        
        # Find the sprite closing </svg> before <div id="app">
        app_pos = html.find('<div id="app">')
        sprite_close = html.rfind('</svg>', max(0, app_pos - 10000), app_pos)
        if sprite_close != -1:
            html = html[:sprite_close] + symbol_def + '\n' + html[sprite_close:]
            print("Step 2: Added fa-chart-area to sprite")

# Write back and verify
with open(INDEX, 'w', encoding='utf-8') as f:
    f.write(html)

# Verify
with open(INDEX, 'r', encoding='utf-8') as f:
    final = f.read()
    all_ids = set(re.findall(r'<symbol id="([^"]+)"', final))
    new_ids = ['fa-chart-area', 'fa-chart-bar', 'fa-cogs', 'fa-database', 'fa-door-open',
               'fa-envelope', 'fa-file-pdf', 'fa-fire', 'fa-gauge-high', 'fa-git-alt',
               'fa-heartbeat', 'fa-infinity', 'fa-mobile-screen', 'fa-robot',
               'fa-sitemap', 'fa-store', 'fa-wrench']
    still_missing = [id for id in new_ids if id not in all_ids]
    print(f'\nVerification: {len(all_ids)} total symbols')
    print(f'Still missing: {still_missing if still_missing else "None - all fixed!"}')
    
    # Also check favicon is clean
    favicon_line = final[final.find('<link rel="icon"'):final.find('<link rel="icon"')+200]
    if 'symbol' in favicon_line:
        print('WARNING: favicon still contains symbol elements!')
    else:
        print('Favicon is clean')
