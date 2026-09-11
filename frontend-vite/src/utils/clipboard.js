export async function copyToClipboard(text) {
  const value = String(text ?? '')
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // HTTP（非 localhost）等非安全上下文下 Clipboard API 不可用，继续走降级路径
    }
  }
  const area = document.createElement('textarea')
  area.value = value
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '-9999px'
  document.body.appendChild(area)
  area.select()
  area.setSelectionRange(0, area.value.length)
  try {
    if (!document.execCommand('copy')) throw new Error('execCommand copy failed')
  } finally {
    document.body.removeChild(area)
  }
}
