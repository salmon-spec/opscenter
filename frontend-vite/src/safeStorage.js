// Sandboxed portal documents use memory when browser storage is unavailable.
const memory = new Map()
export const safeStorage = {
 getItem(key) { try { return localStorage.getItem(key) } catch { return memory.get(key) ?? null } },
 setItem(key, value) { try { localStorage.setItem(key, value) } catch { memory.set(key, String(value)) } },
}
