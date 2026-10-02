/**
 * Token 安全存储工具
 * 使用 AES-GCM 加密 localStorage 中的敏感数据，防止明文泄露
 */
const ENCRYPTION_KEY = 'photo-admin-2026-secure-key-v1'
const SALT = 'photo-admin-salt'

let cachedKey: CryptoKey | null = null

/** 获取/缓存 AES-GCM 加密密钥（PBKDF2 派生） */
async function getKey(): Promise<CryptoKey> {
  if (cachedKey) return cachedKey

  const encoder = new TextEncoder()
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    encoder.encode(ENCRYPTION_KEY),
    'PBKDF2',
    false,
    ['deriveKey'],
  )
  cachedKey = await crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt: encoder.encode(SALT),
      iterations: 100000,
      hash: 'SHA-256',
    },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
  return cachedKey
}

/** 加密字符串并返回 Base64 */
export async function encrypt(plaintext: string): Promise<string> {
  const key = await getKey()
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encoder = new TextEncoder()
  const encrypted = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    encoder.encode(plaintext),
  )
  // 将 IV + 密文拼接后 Base64 编码
  const combined = new Uint8Array(iv.length + encrypted.byteLength)
  combined.set(iv, 0)
  combined.set(new Uint8Array(encrypted), iv.length)
  return btoa(String.fromCharCode(...combined))
}

/** 解密 Base64 密文并返回原始字符串 */
export async function decrypt(ciphertext: string): Promise<string> {
  const key = await getKey()
  const combined = Uint8Array.from(atob(ciphertext), (c) => c.charCodeAt(0))
  const iv = combined.slice(0, 12)
  const data = combined.slice(12)
  const decrypted = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv },
    key,
    data,
  )
  return new TextDecoder().decode(decrypted)
}

/** 安全写入 localStorage（加密存储） */
export async function secureSet(key: string, value: string): Promise<void> {
  try {
    const encrypted = await encrypt(value)
    localStorage.setItem(key, encrypted)
  } catch {
    // 加密失败时降级为明文（兼容旧浏览器）
    localStorage.setItem(key, value)
  }
}

/** 安全读取 localStorage（自动解密） */
export async function secureGet(key: string): Promise<string | null> {
  const raw = localStorage.getItem(key)
  if (!raw) return null
  try {
    return await decrypt(raw)
  } catch {
    // 解密失败时返回原始值（兼容旧数据）
    return raw
  }
}

/** 安全删除 localStorage */
export function secureRemove(key: string): void {
  localStorage.removeItem(key)
}