/* OpsCenter API Layer (P3-02: Extracted from inline script) */
window.OpsAPI = {
  base: '/ops/api/v2',

  async fetch(path, options = {}) {
    try {
      const res = await fetch(`${this.base}${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
      }
      return await res.json();
    } catch (e) {
      console.error(`API ${path}:`, e);
      throw e;
    }
  },

  /* Servers */
  getServers() { return this.fetch('/servers'); },
  addServer(payload) { return this.fetch('/servers', { method: 'POST', body: JSON.stringify(payload) }); },
  deleteServer(id) { return this.fetch(`/servers/${id}`, { method: 'DELETE' }); },
  scanServer(id) { return this.fetch(`/servers/${id}/scan`, { method: 'POST' }); },
  testSSH(id, payload) { return this.fetch(`/servers/${id}/ssh-test`, { method: 'POST', body: JSON.stringify(payload) }); },

  /* Services */
  getServices(serverId) { return this.fetch(`/services?server_id=${serverId}`); },
  getCategories(serverId) { return this.fetch(`/categories?server_id=${serverId}`); },
  addService(serverId, payload) { return this.fetch(`/services?server_id=${serverId}`, { method: 'POST', body: JSON.stringify(payload) }); },
  togglePin(id) { return this.fetch(`/services/${id}/pin`, { method: 'PATCH' }); },
  triggerScan() { return this.fetch('/scan', { method: 'POST' }); },

  /* Monitor */
  getMonitor(serverId) { return this.fetch(`/monitor/${serverId}`); },
  getHistory(serverId, metric, hours = 24) { return this.fetch(`/monitor/${serverId}/history?metric=${metric}&hours=${hours}`); },

  /* Stats */
  getStats() { return this.fetch('/stats'); },
};
