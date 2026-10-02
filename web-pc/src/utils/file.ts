/** 触发浏览器下载一个 Blob。
 *
 * 受保护资源（/process/{id}/download 等）不能直接把 URL 交给 <a href>，
 * 那样发不出 Authorization 头，浏览器会当成匿名请求而失败。
 * 所以先用带鉴权的 XHR 取回 Blob，再用 object URL 触发保存。
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  // 立即 revoke 会让部分浏览器来不及读取，延后释放
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
