/* OpsCenter Tools (P3-02: Pure utility functions, no Vue dependency) */
window.OpsTools = {

  /* Timestamp */
  convertTimestamp(dir, data) {
    if (dir === 'unix' && data.unix) {
      const ts = parseInt(data.unix);
      if (!isNaN(ts)) {
        const d = new Date(ts < 1e12 ? ts * 1000 : ts);
        return {
          date: d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).replace(/\//g, '-'),
          iso: d.toISOString(),
        };
      }
    } else if (dir === 'date' && data.date) {
      const d = new Date(data.date);
      if (!isNaN(d.getTime())) {
        return { unix: Math.floor(d.getTime() / 1000), iso: d.toISOString() };
      }
    }
    return {};
  },

  getNowTimestamp() {
    const now = new Date();
    return { unix: Math.floor(now.getTime() / 1000), iso: now.toISOString() };
  },

  /* Base64 */
  encodeBase64(input) {
    try { return { encoded: btoa(unescape(encodeURIComponent(input))), error: '' }; }
    catch (e) { return { error: '编码失败' }; }
  },

  decodeBase64(encoded) {
    try { return { input: decodeURIComponent(escape(atob(encoded))), error: '' }; }
    catch (e) { return { error: '无效的 Base64 字符串' }; }
  },

  /* JSON */
  formatJson(input) {
    try { return { output: JSON.stringify(JSON.parse(input), null, 2), error: '' }; }
    catch (e) { return { error: 'JSON 解析失败: ' + e.message }; }
  },

  compressJson(input) {
    try { return { output: JSON.stringify(JSON.parse(input)), error: '' }; }
    catch (e) { return { error: 'JSON 解析失败: ' + e.message }; }
  },

  /* Password */
  generatePassword(options) {
    let chars = '';
    if (options.upper) chars += 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    if (options.lower) chars += 'abcdefghijklmnopqrstuvwxyz';
    if (options.digits) chars += '0123456789';
    if (options.symbols) chars += '!@#$%^&*()_+-=[]{}|;:,.<>?';
    if (!chars) chars = 'abcdefghijklmnopqrstuvwxyz';
    let result = '';
    const arr = new Uint32Array(options.length);
    crypto.getRandomValues(arr);
    for (let i = 0; i < options.length; i++) {
      result += chars[arr[i] % chars.length];
    }
    return result;
  },

  getPasswordStrength(options) {
    let score = 0;
    if (options.upper) score++;
    if (options.lower) score++;
    if (options.digits) score++;
    if (options.symbols) score++;
    if (options.length >= 12) score++;
    if (options.length >= 20) score++;
    if (score <= 2) return { label: '弱', color: '#ef4444' };
    if (score <= 4) return { label: '中等', color: '#f59e0b' };
    return { label: '强', color: '#22c55e' };
  },

  /* Network formatting */
  formatNetwork(bytesPerSec) {
    if (bytesPerSec >= 1048576) return { value: (bytesPerSec / 1048576).toFixed(1), unit: ' MB/s' };
    if (bytesPerSec >= 1024) return { value: (bytesPerSec / 1024).toFixed(1), unit: ' KB/s' };
    return { value: bytesPerSec.toFixed ? bytesPerSec.toFixed(0) : bytesPerSec, unit: ' B/s' };
  },

  /* Status helpers */
  statusClass(status) {
    return status === 'up' || status === 'online' ? 'bg-emerald-500' :
           status === 'down' || status === 'offline' ? 'bg-red-500' : 'bg-amber-500';
  },
};
