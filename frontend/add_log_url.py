import os

path = '/opt/opscenter/frontend/assets/js/app.js'
content = open(path, 'r', encoding='utf-8').read()

# Add getContainerLogUrl function before the Return section
old_return = "    /* ========== Return ========== */"
new_code = """    /* v2.5: Generate Grafana log URL for a container */
    function getContainerLogUrl(containerName) {
      const expr = '{container_name="' + containerName + '"}';
      const params = JSON.stringify({datasource:'Loki',expressions:[{refId:'A',expr:expr}]});
      return '/grafana/explore?orgId=1&left=' + encodeURIComponent(params);
    }

    /* ========== Return ========== */"""

if old_return in content:
    content = content.replace(old_return, new_code, 1)
    # Also add getContainerLogUrl to the return statement
    content = content.replace(
        '      getMetricColor, getMetricClass, isMetricCritical,',
        '      getMetricColor, getMetricClass, isMetricCritical,\n      getContainerLogUrl,'
    )
    open(path, 'w', encoding='utf-8').write(content)
    print('Added: getContainerLogUrl function and export')
else:
    print('WARNING: Return section pattern not found')
