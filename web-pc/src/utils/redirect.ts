/**
 * 处理登录后的回跳地址。
 *
 * 路由守卫会把用户原本要访问的页面写进 `?redirect=`（值来自 `to.fullPath`，
 * 已去掉 Vite base，形如 `/editor?template=3`）。这里做两件事：
 *   1. 只接受站内相对路径，挡掉 `//evil.com`、`https://evil.com` 之类的开放重定向；
 *   2. 兼容误传带 base 前缀的地址（`/web-pc/editor`），避免跳成 `/web-pc/web-pc/editor`。
 */
export function safeRedirect(raw: unknown, fallback = '/'): string {
  if (typeof raw !== 'string') return fallback

  let target = raw.trim()
  if (!target) return fallback

  // 必须是站内绝对路径：以单个 `/` 开头
  if (!target.startsWith('/') || target.startsWith('//')) return fallback
  // 反斜杠在部分浏览器里等价于斜杠，可能被用来绕过上面的检查
  if (target.includes('\\')) return fallback

  const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
  if (base && base !== '' && (target === base || target.startsWith(`${base}/`))) {
    target = target.slice(base.length)
  }

  return target.startsWith('/') ? target : `/${target}`
}

const PENDING_OAUTH_KEY = 'web_oauth_redirect'

/** 扫码登录会整页/顶层跳走到平台页面，先把回跳目标存进 sessionStorage */
export function rememberOAuthRedirect(target: string): void {
  try {
    sessionStorage.setItem(PENDING_OAUTH_KEY, safeRedirect(target))
  } catch {
    // 隐私模式下 sessionStorage 可能不可用，忽略即可
  }
}

/** 扫码回调页取回并清除回跳目标 */
export function takeOAuthRedirect(): string {
  try {
    const saved = sessionStorage.getItem(PENDING_OAUTH_KEY)
    sessionStorage.removeItem(PENDING_OAUTH_KEY)
    return safeRedirect(saved)
  } catch {
    return '/'
  }
}
