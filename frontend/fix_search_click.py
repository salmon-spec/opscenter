import os
path = '/opt/opscenter/frontend/index.html'
content = open(path, 'r', encoding='utf-8').read()
old_text = '@click="openService(svc)"\n                   @mouseenter="searchSelectedIndex=idx"'
new_text = '@click="openDrawer(svc)"\n                   @mouseenter="searchSelectedIndex=idx"'
if old_text in content:
    content = content.replace(old_text, new_text)
    open(path, 'w', encoding='utf-8').write(content)
    print('Fixed: search mode card click -> openDrawer')
else:
    print('Already fixed or pattern not found')
