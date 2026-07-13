import os

path = '/opt/opscenter/frontend/index.html'
content = open(path, 'r', encoding='utf-8').read()

# Fix Grafana link - the double quotes in JSON conflict with HTML attribute quotes
# Replace the broken :href with a method call
old_link = """                      <a :href="'/grafana/explore?orgId=1&left={"datasource":"Loki","expressions":[{"refId":"A","expr":"{container_name=\"'+c.name+'\"}"}]}'" 
                         target="_blank" class="text-emerald-500 hover:underline" title="\u5728Grafana\u4e2d\u67e5\u770b\u65e5\u5fd7">\u65e5\u5fd7</a>"""

new_link = """                      <a :href="getContainerLogUrl(c.name)" 
                         target="_blank" class="text-emerald-500 hover:underline" title="\u5728Grafana\u4e2d\u67e5\u770b\u65e5\u5fd7">\u65e5\u5fd7</a>"""

if old_link in content:
    content = content.replace(old_link, new_link)
    print('Fixed: Grafana container log link')
else:
    print('WARNING: Grafana link pattern not found, trying alternative...')
    # Try line-by-line approach
    lines = content.split('\n')
    fixed = False
    for i, line in enumerate(lines):
        if "grafana/explore" in line and "container_name" in line and ":href" in line:
            lines[i] = """                      <a :href="getContainerLogUrl(c.name)" """
            # Also fix the next line if it has target="_blank"
            if i+1 < len(lines) and 'target="_blank"' in lines[i+1]:
                lines[i+1] = """                         target="_blank" class="text-emerald-500 hover:underline" title="\u5728Grafana\u4e2d\u67e5\u770b\u65e5\u5fd7">\u65e5\u5fd7</a>"""
            fixed = True
            print(f'  Fixed line {i+1} via line-by-line approach')
            break
    if fixed:
        content = '\n'.join(lines)
    else:
        print('  Could not find Grafana link to fix')

open(path, 'w', encoding='utf-8').write(content)
print('index.html updated')
