#!/usr/bin/env python3
"""OpsCenter v2.5 Patch 2: Multi-dim search + container actions + tools context + fixes"""
import os, shutil

BASE = '/opt/opscenter/frontend'
INDEX = os.path.join(BASE, 'index.html')
APP_JS = os.path.join(BASE, 'assets/js/app.js')

def read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def write(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def patch_index():
    print('\n=== Patching index.html ===')
    html = read(INDEX)

    # 1. Fix search mode card click → openDrawer
    # Line 451 area: @click="openService(svc)" in search mode
    old = '''                   @click="openService(svc)"
               @mouseenter="searchSelectedIndex=idx"
               :title="svc.url"'''
    new = '''                   @click="openDrawer(svc)"
               @mouseenter="searchSelectedIndex=idx"
               :title="svc.url"'''
    if old in html:
        html = html.replace(old, new)
        print('  Fixed: search mode card click → openDrawer')
    else:
        print('  WARNING: search mode card click already fixed or pattern changed')

    # 2. Container table: add action column header + action cells
    old_th = '''                    <th class="px-4 py-2.5 text-left text-xs font-medium" style="color:var(--text2)">端口</th>
                  </tr>'''
    new_th = '''                    <th class="px-4 py-2.5 text-left text-xs font-medium" style="color:var(--text2)">端口</th>
                    <th class="px-4 py-2.5 text-left text-xs font-medium" style="color:var(--text2)">操作</th>
                  </tr>'''
    if old_th in html:
        html = html.replace(old_th, new_th)
        print('  Added: container table action column header')

    old_td = '''                    <td class="px-4 py-2.5 text-xs" style="color:var(--text2)">{{ c.ports }}</td>'''
    new_td = '''                    <td class="px-4 py-2.5 text-xs" style="color:var(--text2)">{{ c.ports }}</td>
                    <td class="px-4 py-2.5 text-xs">
                      <a :href="'/grafana/explore?orgId=1&left={\"datasource\":\"Loki\",\"expressions\":[{\"refId\":\"A\",\"expr\":\"{container_name=\\\"'+c.name+'\\\"}\"}]}'" 
                         target="_blank" class="text-emerald-500 hover:underline" title="在Grafana中查看日志">日志</a>
                    </td>'''
    if old_td in html:
        html = html.replace(old_td, new_td)
        print('  Added: container table action links (logs)')
    else:
        print('  WARNING: container ports cell not found')

    # Fix colspan for empty row
    html = html.replace('colspan="4"', 'colspan="5"')
    print('  Fixed: container table colspan')

    # 3. Tools page: add server context header
    old_tools_title = '''        <h2 class="text-lg font-semibold flex items-center gap-2 mb-5" style="color:var(--text1)">
          <svg class="icon" style="color:var(--accent)"><use href="#fa-toolbox"></use></svg> 运维工具
        </h2>'''
    new_tools_title = '''        <h2 class="text-lg font-semibold flex items-center gap-2 mb-2" style="color:var(--text1)">
          <svg class="icon" style="color:var(--accent)"><use href="#fa-toolbox"></use></svg> 工具箱
        </h2>
        <!-- v2.5: Server context -->
        <div v-if="currentServer" class="mb-5 flex items-center gap-2 text-xs" style="color:var(--text2)">
          <svg class="icon"><use href="#fa-server"></use></svg>
          <span>当前: <strong style="color:var(--text1)">{{ currentServer.name }}</strong> ({{ currentServer.host }})</span>
        </div>'''
    if old_tools_title in html:
        html = html.replace(old_tools_title, new_tools_title)
        print('  Added: tools page server context header')

    # 4. Recent access section: click should also open drawer
    # (keep openService for quick access items since they're meant to open directly)
    # Actually, recent services should also open drawer for consistency
    html = html.replace(
        '@click="openService(rs)"',
        '@click="openDrawer(rs)"'
    )
    print('  Fixed: recent services click → openDrawer')

    write(INDEX, html)
    print('  index.html patched')

def patch_app_js():
    print('\n=== Patching app.js ===')
    js = read(APP_JS)

    # 1. Replace filteredServices with multi-dim search
    old_filtered = """    const filteredServices = computed(() => {
      if (!searchQuery.value) return allServices.value;
      const q = searchQuery.value.toLowerCase();
      return allServices.value.filter(s =>
        (s.name || '').toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q) ||
        (s.category || '').toLowerCase().includes(q) ||
        (s.url || '').toLowerCase().includes(q)
      );
    });"""
    new_filtered = """    // v2.5: Multi-dim search (status:/cat:/server: + text)
    const filteredServices = computed(() => {
      const filters = OpsTools.parseSearchQuery(searchQuery.value);
      return allServices.value.filter(s => {
        // Status filter
        if (filters.status && s.status !== filters.status) return false;
        // Category filter
        if (filters.category && !(s.category || '').toLowerCase().includes(filters.category)) return false;
        // Text filter
        if (filters.text) {
          const q = filters.text;
          if (!(s.name || '').toLowerCase().includes(q) &&
              !(s.description || '').toLowerCase().includes(q) &&
              !(s.category || '').toLowerCase().includes(q) &&
              !(s.url || '').toLowerCase().includes(q)) return false;
        }
        return true;
      });
    });"""
    if old_filtered in js:
        js = js.replace(old_filtered, new_filtered)
        print('  Upgraded: filteredServices with multi-dim search')
    else:
        print('  WARNING: filteredServices pattern not found')

    # 2. Fix searchEnter: when no selection, open drawer for single result
    old_enter = """    function searchEnter() {
      if (searchSelectedIndex.value >= 0 && searchSelectedIndex.value < filteredServices.value.length) {
        openService(filteredServices.value[searchSelectedIndex.value]);
      } else if (filteredServices.value.length === 1) {
        openService(filteredServices.value[0]);
      }
    }"""
    new_enter = """    function searchEnter() {
      if (searchSelectedIndex.value >= 0 && searchSelectedIndex.value < filteredServices.value.length) {
        openDrawer(filteredServices.value[searchSelectedIndex.value]);
      } else if (filteredServices.value.length === 1) {
        openDrawer(filteredServices.value[0]);
      }
    }"""
    if old_enter in js:
        js = js.replace(old_enter, new_enter)
        print('  Fixed: searchEnter → openDrawer')

    write(APP_JS, js)
    print('  app.js patched')

def main():
    print('🔧 OpsCenter v2.5 Patch 2')
    patch_index()
    patch_app_js()
    print('\n✅ Patches applied! Deploy with:')
    print('  sudo docker cp /opt/opscenter/frontend/index.html nginx:/usr/share/nginx/html/ops/index.html')
    print('  sudo docker cp /opt/opscenter/frontend/assets/js/. nginx:/usr/share/nginx/html/ops/assets/js/')

if __name__ == '__main__':
    main()
