/* OpsCenter Main App (P3-02: Vue app wiring, uses config/api/terminal-sim/tools) */
const { createApp, ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } = Vue;

createApp({
  setup() {
    /* ========== State ========== */
    const isDark = ref(localStorage.getItem('oc-theme') !== 'light');
    const currentPage = ref('nav');
    const currentServerId = ref('');
    const sidebarCollapsed = ref(localStorage.getItem('oc-sidebar-collapsed') === 'true');
    const searchQuery = ref('');
    const searchSelectedIndex = ref(-1);
    const mobileView = ref(window.innerWidth < 769);
    const isMac = ref(navigator.platform.toUpperCase().indexOf('MAC') >= 0);

    const navItems = OpsConfig.navItems;
    const loading = reactive({ servers: false, services: false, monitor: false });
    const servers = ref([]);
    const allServices = ref([]);
    const categories = ref([]);
    const categoryExpanded = reactive({});
    const pinnedIds = ref(JSON.parse(localStorage.getItem('oc-pinned') || '[]'));
    const recentIds = ref(JSON.parse(localStorage.getItem('oc-recent') || '[]'));
    const toasts = ref([]);

    const monitor = reactive({
      connected: false,
      cpu: 0, memory: 0, disk: 0, network: 0,
      cpu_total: 0, memory_total: 0, memory_used: 0, disk_total: 0, disk_used: 0,
      network_in: 0, network_out: 0,
      containers: [], history: [],
    });
    const historyMetric = ref('cpu');
    const refreshCountdown = ref(OpsConfig.refreshInterval);
    let refreshTimer = null;
    const historyChart = ref(null);
    let chartInstance = null;

    const terminalLines = ref([]);
    const terminalInput = ref('');
    const terminalHistoryIdx = ref(-1);
    let terminalHistoryList = [];

    const tools = reactive({
      timestamp: { unix: '', date: '', now: '', iso: '' },
      base64: { input: '', encoded: '', error: '' },
      json: { input: '', output: '', error: '' },
      password: { length: 16, upper: true, lower: true, digits: true, symbols: true, result: '' },
    });

    const showAddServer = ref(false);
    const newServer = reactive({ name: '', host: '', ssh_port: 22, username: 'root', password: '' });
    const sshTesting = ref(false);
    const showServerPwd = ref(false);
    const sshTestResult = reactive({ testing: false, success: null, message: '' });
    const newService = reactive({ name: '', url: '', category: '', icon: '', description: '' });
    const scanning = ref(false);
    const stats = reactive({ servers: 0, services: 0, categories: 0, containers: 0 });
    const confirmModal = reactive({ show: false, title: '', message: '', action: () => {} });
    const serverMetrics = reactive({});

    /* ========== Theme & Toast ========== */
    function applyTheme() {
      document.documentElement.classList.toggle('light', !isDark.value);
      localStorage.setItem('oc-theme', isDark.value ? 'dark' : 'light');
    }

    function toggleTheme() {
      isDark.value = !isDark.value;
      applyTheme();
      updateChart();
    }

    function toast(message, type = 'info') {
      const id = Date.now() + Math.random();
      toasts.value.push({ id, message, type });
      setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, 3000);
    }

    /* ========== Server & Service ========== */
    async function loadServers() {
      loading.servers = true;
      try {
        const data = await OpsAPI.getServers();
        servers.value = data.servers || data || [];
        if (servers.value.length) {
          const savedServer = localStorage.getItem('oc-server');
          currentServerId.value = (savedServer && servers.value.find(s => s.id === savedServer)) ? savedServer : servers.value[0].id;
          await loadServices();
        }
      } catch (e) {
        toast('加载服务器列表失败: ' + e.message, 'error');
      } finally {
        loading.servers = false;
        loadServerMetrics();
      }
    }

    async function loadServices() {
      if (!currentServerId.value) return;
      loading.services = true;
      try {
        const [svcData, catData] = await Promise.all([
          OpsAPI.getServices(currentServerId.value),
          OpsAPI.getCategories(currentServerId.value),
        ]);
        allServices.value = (svcData.services || svcData || []).map(s => {
          // P4-02: Normalize status to online/offline/unknown
          let status = s.status;
          if (status === 'up' || status === 'running') status = 'online';
          else if (status === 'down') status = 'offline';
          else if (!status) status = 'unknown';
          return { ...s, status, pinned: pinnedIds.value.includes(s.id) };
        });
        categories.value = catData.categories || catData || [];
        categories.value.forEach((c, i) => {
          if (categoryExpanded[c.name] === undefined) categoryExpanded[c.name] = (i === 0);
        });
      } catch (e) {
        toast('加载服务列表失败: ' + e.message, 'error');
      } finally {
        loading.services = false;
      }
    }

    async function onServerChange() {
      localStorage.setItem('oc-server', currentServerId.value);
      searchQuery.value = '';
      await loadServices();
      if (currentPage.value === 'monitor') loadMonitor();
      if (currentPage.value === 'terminal') initTerminal();
    }

    async function addServer() {
      try {
        const payload = { name: newServer.name, host: newServer.host, ssh_port: newServer.ssh_port, ssh_user: newServer.username };
        if (newServer.password) payload.ssh_password = newServer.password;
        const res = await OpsAPI.addServer(payload);
        toast('服务器添加成功', 'success');
        showAddServer.value = false;
        Object.assign(newServer, { name: '', host: '', ssh_port: 22, username: 'root', password: '' });
        sshTestResult.success = null; sshTestResult.message = '';
        await loadServers();
        if (res.id && payload.ssh_password) {
          toast('正在自动扫描远程服务...', 'info');
          try {
            await OpsAPI.scanServer(res.id);
            toast('远程服务扫描完成', 'success');
            await loadServices();
            await loadServers();
          } catch (e) { toast('自动扫描失败: ' + e.message, 'error'); }
        }
      } catch (e) {
        toast('添加失败: ' + e.message, 'error');
      }
    }

    async function scanServer(serverId) {
      try {
        toast('正在扫描远程服务...', 'info');
        const res = await OpsAPI.scanServer(serverId);
        const data = res.discovered !== undefined ? res : await res.json();
        toast(`扫描完成，发现 ${data.discovered || 0} 个服务`, 'success');
        await loadServices();
        await loadServers();
      } catch (e) {
        toast('扫描失败: ' + e.message, 'error');
      }
    }

    async function testConnection() {
      if (!newServer.host) { toast('请先填写主机地址', 'error'); return; }
      sshTestResult.testing = true; sshTestResult.success = null; sshTestResult.message = '';
      try {
        const payload = { name: newServer.name || 'test', host: newServer.host, ssh_port: newServer.ssh_port, ssh_user: newServer.username };
        if (newServer.password) payload.ssh_password = newServer.password;
        const res = await OpsAPI.addServer(payload);
        const testRes = await OpsAPI.testSSH(res.id, { password: newServer.password });
        const data = await testRes.json || testRes;
        sshTestResult.testing = false;
        sshTestResult.success = data.success;
        sshTestResult.message = data.message || (data.success ? '连接成功' : '连接失败');
        if (data.success) {
          toast('SSH 连接测试成功', 'success');
        } else {
          toast('SSH 连接失败: ' + sshTestResult.message, 'error');
        }
      } catch (e) {
        sshTestResult.testing = false;
        sshTestResult.success = false;
        sshTestResult.message = e.message;
        toast('测试失败: ' + e.message, 'error');
      }
    }

    function deleteServer(id) {
      const s = servers.value.find(x => x.id === id);
      confirmModal.title = '删除服务器';
      confirmModal.message = `确定删除 "${s?.name}" 及其所有服务？此操作不可撤销。`;
      confirmModal.action = async () => {
        try {
          await OpsAPI.deleteServer(id);
          toast('服务器已删除', 'success');
          if (currentServerId.value === id) currentServerId.value = '';
          await loadServers();
        } catch (e) {
          toast('删除失败: ' + e.message, 'error');
        }
      };
      confirmModal.show = true;
    }

    async function addService() {
      if (!currentServerId.value) return;
      try {
        await OpsAPI.addService(currentServerId.value, newService);
        toast('服务注册成功', 'success');
        Object.assign(newService, { name: '', url: '', category: '', icon: '', description: '' });
        await loadServices();
      } catch (e) {
        toast('注册失败: ' + e.message, 'error');
      }
    }

    async function togglePin(svc) {
      try {
        await OpsAPI.togglePin(svc.id);
        svc.pinned = !svc.pinned;
        if (svc.pinned) {
          pinnedIds.value.push(svc.id);
        } else {
          pinnedIds.value = pinnedIds.value.filter(id => id !== svc.id);
        }
        localStorage.setItem('oc-pinned', JSON.stringify(pinnedIds.value));
      } catch (e) {
        toast('操作失败: ' + e.message, 'error');
      }
    }

    async function triggerScan() {
      scanning.value = true;
      try {
        await OpsAPI.triggerScan();
        toast('扫描已触发', 'success');
        setTimeout(() => loadServices(), 3000);
      } catch (e) {
        toast('扫描失败: ' + e.message, 'error');
      } finally {
        scanning.value = false;
      }
    }

    async function loadStats() {
      try {
        const data = await OpsAPI.getStats();
        Object.assign(stats, data.stats || data);
      } catch (e) { /* silent */ }
    }

    function getCategoryColor(category) {
      const cat = categories.value.find(c => (c.name || c) === category);
      return (cat && cat.color) ? cat.color : null;
    }

    function openService(svc) {
      if (svc.url) {
        const rid = svc.id;
        recentIds.value = [rid, ...recentIds.value.filter(id => id !== rid)].slice(0, OpsConfig.maxRecentItems);
        localStorage.setItem('oc-recent', JSON.stringify(recentIds.value));
        window.open(svc.url, '_blank');
      }
    }

    function toggleCategory(name) {
      categoryExpanded[name] = !categoryExpanded[name];
    }

    async function loadServerMetrics() {
      for (const s of servers.value) {
        try {
          const data = await OpsAPI.getMonitor(s.id);
          const m = data.metrics || data.monitor || data;
          serverMetrics[s.id] = {
            cpu: m.cpu_percent ?? m.cpu ?? null,
            memory: m.memory_percent ?? m.memory ?? null,
            container_count: (data.containers || m.containers || []).length,
          };
        } catch (e) {
          serverMetrics[s.id] = { cpu: null, memory: null, container_count: null };
        }
      }
    }

    /* ========== ECharts Lazy Loading ========== */
    let echartsLoaded = false;
    let echartsLib = null;
    async function loadEcharts() {
      if (echartsLib) return echartsLib;
      if (echartsLoaded) return null;
      return new Promise((resolve) => {
        echartsLoaded = true;
        const s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js';
        s.onload = () => { echartsLib = window.echarts; resolve(echartsLib); };
        s.onerror = () => { console.error('ECharts load failed'); resolve(null); };
        document.head.appendChild(s);
      });
    }

    /* ========== Monitor ========== */
    async function loadMonitor() {
      if (!currentServerId.value) return;
      loading.monitor = true;
      try {
        const data = await OpsAPI.getMonitor(currentServerId.value);
        const m = data.metrics || data.monitor || data;
        monitor.connected = true;
        monitor.cpu = m.cpu_percent ?? m.cpu ?? 0;
        monitor.memory = m.memory_percent ?? m.memory ?? 0;
        monitor.disk = m.disk_percent ?? m.disk ?? 0;
        monitor.network = m.network_mbps != null ? m.network_mbps : ((m.net_rx || 0) + (m.net_tx || 0) || 0);
        monitor.cpu_total = m.cpu_count ?? m.cpu_total ?? 0;
        monitor.memory_total = m.memory_total ?? 0;
        monitor.memory_used = m.memory_used ?? (m.memory_total != null && m.memory_avail != null ? m.memory_total - m.memory_avail : 0);
        monitor.disk_total = m.disk_total ?? 0;
        monitor.disk_used = m.disk_used ?? (m.disk_total != null && m.disk_avail != null ? m.disk_total - m.disk_avail : 0);
        monitor.network_in = m.network_in ?? m.net_rx ?? 0;
        monitor.network_out = m.network_out ?? m.net_tx ?? 0;
        monitor.containers = data.containers ?? m.containers ?? [];
      } catch (e) {
        monitor.connected = false;
        toast('监控数据加载失败', 'error');
      } finally {
        loading.monitor = false;
      }
    }

    async function loadHistory() {
      if (!currentServerId.value) return;
      try {
        const data = await OpsAPI.getHistory(currentServerId.value, historyMetric.value, 24);
        monitor.history = data.values || data.history || data.points || [];
        updateChart();
      } catch (e) { /* fail silently */ }
    }

    async function initChart() {
      if (!echartsLib) await loadEcharts();
      if (!echartsLib || !historyChart.value || chartInstance) return;
      chartInstance = echartsLib.init(historyChart.value, isDark.value ? 'dark' : null);
      window.addEventListener('resize', () => chartInstance?.resize());
    }

    function updateChart() {
      if (!chartInstance) return;
      const metricLabels = { cpu: 'CPU 使用率', memory: '内存使用率', disk: '磁盘使用率', network: '网络流量' };
      const metricUnits = { cpu: '%', memory: '%', disk: '%', network: 'MB/s' };
      const metricColors = { cpu: '#22c55e', memory: '#3b82f6', disk: '#f59e0b', network: '#8b5cf6' };
      const h = monitor.history;
      const normalized = h.map ? h.map(p => {
        if (Array.isArray(p)) return { timestamp: p[0] * 1000, value: parseFloat(p[1]) };
        return p;
      }) : [];
      const xData = normalized.map(p => {
        const d = new Date(p.timestamp || p.time || p.x);
        return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
      });
      const yData = normalized.map(p => p.value ?? p.y ?? 0);

      chartInstance.setOption({
        backgroundColor: 'transparent',
        textStyle: { color: isDark.value ? '#94a3b8' : '#64748b' },
        grid: { left: 45, right: 15, top: 15, bottom: 30 },
        tooltip: { trigger: 'axis', backgroundColor: isDark.value ? '#1e293b' : '#fff', borderColor: isDark.value ? '#334155' : '#e2e8f0', textStyle: { color: isDark.value ? '#e2e8f0' : '#1e293b', fontSize: 12 } },
        xAxis: { type: 'category', data: xData, axisLine: { lineStyle: { color: isDark.value ? '#334155' : '#e2e8f0' } }, axisLabel: { fontSize: 10 }, boundaryGap: false },
        yAxis: { type: 'value', axisLine: { show: false }, splitLine: { lineStyle: { color: isDark.value ? '#1e293b' : '#f1f5f9' } }, axisLabel: { fontSize: 10, formatter: `{value}${metricUnits[historyMetric.value]}` } },
        series: [{
          type: 'line', data: yData, smooth: true, symbol: 'none', lineStyle: { width: 2, color: metricColors[historyMetric.value] },
          areaStyle: { color: new echartsLib.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: metricColors[historyMetric.value] + '40' },
            { offset: 1, color: metricColors[historyMetric.value] + '05' },
          ]) },
        }],
      });
    }

    /* ========== Terminal (uses OpsTerminalSim) ========== */
    function initTerminal() {
      const s = currentServer.value;
      terminalLines.value = OpsTerminalSim.init(s);
      terminalHistoryList = [];
      terminalHistoryIdx.value = -1;
    }

    function executeTerminal() {
      const cmd = terminalInput.value.trim();
      if (!cmd) return;
      terminalLines.value.push({ type: 'cmd', text: cmd });
      terminalHistoryList.unshift(cmd);
      terminalHistoryIdx.value = -1;
      terminalInput.value = '';

      const parts = cmd.split(/\s+/);
      const command = parts[0].toLowerCase();
      const args = parts.slice(1);

      const ctx = {
        name: currentServer.value?.name,
        host: currentServer.value?.host,
        username: currentServer.value?.username,
        ssh_port: currentServer.value?.ssh_port,
        containers: monitor.containers,
        cpu: monitor.cpu,
        memory: monitor.memory,
      };
      const result = OpsTerminalSim.execute(command, args, ctx);

      if (result.clear) {
        terminalLines.value = [];
        return;
      }
      if (result.error) {
        terminalLines.value.push({ type: 'error', text: result.error });
        scrollTerminal();
        return;
      }
      if (result.output) {
        result.output.split('\n').forEach(line => {
          terminalLines.value.push({ type: 'output', text: line.replace(/</g, '&lt;').replace(/>/g, '&gt;') });
        });
      }
      scrollTerminal();
    }

    function terminalHistoryUp() {
      if (terminalHistoryIdx.value < terminalHistoryList.length - 1) {
        terminalHistoryIdx.value++;
        terminalInput.value = terminalHistoryList[terminalHistoryIdx.value];
      }
    }

    function terminalHistoryDown() {
      if (terminalHistoryIdx.value > 0) {
        terminalHistoryIdx.value--;
        terminalInput.value = terminalHistoryList[terminalHistoryIdx.value];
      } else {
        terminalHistoryIdx.value = -1;
        terminalInput.value = '';
      }
    }

    function scrollTerminal() {
      nextTick(() => {
        const el = document.querySelector('.terminal-window');
        if (el) el.scrollTop = el.scrollHeight;
      });
    }

    /* ========== Tools (uses OpsTools) ========== */
    function convertTimestamp(dir) {
      const result = OpsTools.convertTimestamp(dir, tools.timestamp);
      Object.assign(tools.timestamp, result);
    }

    function updateTimestampNow() {
      const result = OpsTools.getNowTimestamp();
      tools.timestamp.now = result.unix;
      tools.timestamp.iso = result.iso;
    }

    function encodeBase64() {
      const result = OpsTools.encodeBase64(tools.base64.input);
      tools.base64.encoded = result.encoded || tools.base64.encoded;
      tools.base64.error = result.error;
    }

    function decodeBase64() {
      const result = OpsTools.decodeBase64(tools.base64.encoded);
      tools.base64.input = result.input || tools.base64.input;
      tools.base64.error = result.error;
    }

    function formatJson() {
      const result = OpsTools.formatJson(tools.json.input);
      tools.json.output = result.output || tools.json.output;
      tools.json.error = result.error;
    }

    function compressJson() {
      const result = OpsTools.compressJson(tools.json.input);
      tools.json.output = result.output || tools.json.output;
      tools.json.error = result.error;
    }

    function copyJson() {
      navigator.clipboard?.writeText(tools.json.output);
      toast('已复制', 'success');
    }

    function generatePassword() {
      tools.password.result = OpsTools.generatePassword(tools.password);
    }

    function copyPassword() {
      navigator.clipboard?.writeText(tools.password.result);
      toast('密码已复制', 'success');
    }

    /* ========== P4: Icon, Search, Highlight ========== */
    // P4-pre: Map FA class names to SVG sprite hrefs
    function iconHref(iconName) {
      if (!iconName) return '#fa-cube';
      let name = iconName.replace(/^fa-/, '');
      // Map FA5 names to FA6 names
      const fa5tofa6 = {
        'check-circle': 'circle-check',
        'times-circle': 'circle-xmark',
        'exclamation-circle': 'circle-exclamation',
        'info-circle': 'circle-info',
        'radar': 'satellite-dish',
      };
      name = fa5tofa6[name] || name;
      return '#fa-' + name;
    }

    // P4-03: Keyword highlighting
    function highlightText(text) {
      if (!text) return '';
      if (!searchQuery.value) return text;
      const q = searchQuery.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp('(' + q + ')', 'gi');
      return String(text).replace(re, '<mark class="search-hl">$1</mark>');
    }

    // P4-03: Search keyboard navigation
    function searchUp() {
      if (!filteredServices.value.length) return;
      searchSelectedIndex.value = searchSelectedIndex.value <= 0
        ? filteredServices.value.length - 1
        : searchSelectedIndex.value - 1;
    }
    function searchDown() {
      if (!filteredServices.value.length) return;
      searchSelectedIndex.value = searchSelectedIndex.value >= filteredServices.value.length - 1
        ? 0
        : searchSelectedIndex.value + 1;
    }
    function searchEnter() {
      if (searchSelectedIndex.value >= 0 && searchSelectedIndex.value < filteredServices.value.length) {
        openService(filteredServices.value[searchSelectedIndex.value]);
      } else if (filteredServices.value.length === 1) {
        openService(filteredServices.value[0]);
      }
    }

    /* ========== Computed ========== */
    const currentServer = computed(() => servers.value.find(s => s.id === currentServerId.value));

    const filteredServices = computed(() => {
      if (!searchQuery.value) return allServices.value;
      const q = searchQuery.value.toLowerCase();
      return allServices.value.filter(s =>
        (s.name || '').toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q) ||
        (s.category || '').toLowerCase().includes(q) ||
        (s.url || '').toLowerCase().includes(q)
      );
    });

    const pinnedServices = computed(() => allServices.value.filter(s => s.pinned));

    const recentServices = computed(() => {
      if (!recentIds.value.length) return [];
      const recent = recentIds.value.map(id => allServices.value.find(s => s.id === id)).filter(Boolean);
      return recent.slice(0, OpsConfig.maxRecentDisplay);
    });

    const categoriesWithServices = computed(() => {
      return categories.value.map(cat => {
        const catObj = typeof cat === 'string' ? { name: cat, icon: 'fa-folder', color: '#94a3b8', order: 99 } : cat;
        const catName = catObj.name;
        // P4-04: Sort pinned services to top
        const svcs = allServices.value.filter(s => s.category === catName)
          .sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
        return { ...catObj, services: svcs };
      }).filter(c => c.services.length > 0);
    });

    const metricCards = computed(() => {
      const net = OpsTools.formatNetwork(monitor.network);
      return [
        { key: 'cpu', label: 'CPU 使用率', value: monitor.cpu.toFixed ? monitor.cpu.toFixed(1) : monitor.cpu, unit: '%', icon: 'fa-microchip', color: '#22c55e' },
        { key: 'memory', label: '内存使用率', value: monitor.memory.toFixed ? monitor.memory.toFixed(1) : monitor.memory, unit: '%', icon: 'fa-memory', color: '#3b82f6' },
        { key: 'disk', label: '磁盘使用率', value: monitor.disk.toFixed ? monitor.disk.toFixed(1) : monitor.disk, unit: '%', icon: 'fa-hard-drive', color: '#f59e0b' },
        { key: 'network', label: '网络流量', value: net.value, unit: net.unit, icon: 'fa-network-wired', color: '#8b5cf6' },
      ];
    });

    const statsCards = computed(() => [
      { label: '服务器', value: stats.servers || servers.value.length },
      { label: '服务', value: stats.services || allServices.value.length },
      { label: '分类', value: stats.categories || categories.value.length },
      { label: '容器', value: stats.containers || monitor.containers.length },
    ]);

    const passwordStrengthLabel = computed(() => OpsTools.getPasswordStrength(tools.password).label);
    const passwordStrengthColor = computed(() => OpsTools.getPasswordStrength(tools.password).color);

    /* ========== Watchers ========== */
    watch(currentPage, (page) => {
      if (page === 'monitor' && currentServerId.value) {
        nextTick(async () => { await loadEcharts(); await initChart(); loadMonitor(); loadHistory(); });
      }
      if (page === 'terminal' && currentServerId.value && !terminalLines.value.length) {
        initTerminal();
      }
      if (page === 'settings') loadStats();
    });

    watch(historyMetric, () => { loadHistory(); });
    watch(searchQuery, () => { searchSelectedIndex.value = -1; });
    watch(sidebarCollapsed, (v) => { localStorage.setItem('oc-sidebar-collapsed', v); });
    watch(pinnedIds, (v) => {
      allServices.value.forEach(s => { s.pinned = v.includes(s.id); });
    }, { deep: true });

    /* ========== Keyboard ========== */
    function onKeyDown(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        currentPage.value = 'nav';
        nextTick(() => {
          const input = document.querySelector('header input');
          if (input) input.focus();
        });
      }
      if (e.key === 'Escape') {
        searchQuery.value = '';
      }
    }

    /* ========== Lifecycle ========== */
    onMounted(() => {
      applyTheme();
      window.addEventListener('keydown', onKeyDown);
      window.addEventListener('resize', () => { mobileView.value = window.innerWidth < 769; });
      loadServers();
      updateTimestampNow();
      setInterval(updateTimestampNow, 1000);
      generatePassword();

      refreshTimer = setInterval(() => {
        refreshCountdown.value--;
        if (refreshCountdown.value <= 0) {
          refreshCountdown.value = OpsConfig.refreshInterval;
          if (currentPage.value === 'monitor' && currentServerId.value) {
            loadMonitor();
            loadHistory();
          }
        }
      }, 1000);
    });

    onUnmounted(() => {
      window.removeEventListener('keydown', onKeyDown);
      if (refreshTimer) clearInterval(refreshTimer);
      if (chartInstance) chartInstance.dispose();
    });

    /* ========== Return ========== */
    return {
      isDark, currentPage, currentServerId, sidebarCollapsed, searchQuery, searchSelectedIndex, mobileView, isMac,
      navItems, loading, servers, allServices, categories, categoryExpanded, pinnedIds, toasts,
      monitor, historyMetric, refreshCountdown, historyChart,
      terminalLines, terminalInput, terminalHistoryIdx,
      tools, showAddServer, newServer, newService, scanning, stats, confirmModal, showServerPwd, sshTestResult, serverMetrics,
      currentServer, filteredServices, pinnedServices, recentServices, categoriesWithServices,
      metricCards, statsCards, passwordStrengthLabel, passwordStrengthColor,
      toggleTheme, onServerChange, addServer, deleteServer, addService,
      recentIds, recentServices,
      togglePin, triggerScan, openService, toggleCategory, statusClass: OpsTools.statusClass, getCategoryColor,
      loadServers, loadServices, loadMonitor,
      executeTerminal, terminalHistoryUp, terminalHistoryDown,
      convertTimestamp, encodeBase64, decodeBase64,
      formatJson, compressJson, copyJson,
      generatePassword, copyPassword,
      iconHref, highlightText, searchUp, searchDown, searchEnter,
    };
  },
}).mount('#app');
