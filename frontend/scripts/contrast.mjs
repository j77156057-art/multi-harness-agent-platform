// 对比度门禁：解析 src/style.css 的 :root token，对声明的文字/底色对逐对断言
// WCAG 2.1 AA（正文 ≥4.5:1）。零依赖、离线可重复跑；`npm test` 里有一个用例
// 以子进程方式调用本脚本（见 src/__tests__/contrast.test.ts）。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(join(here, '..', 'src', 'style.css'), 'utf8')

function token(name) {
  const bare = String(name).replace(/^--/, '')
  const match = css.match(new RegExp(`--${bare}:\\s*(#[0-9a-fA-F]{6})\\s*;`))
  if (match === null || match[1] === undefined) {
    throw new Error(`token --${bare} 未找到（或不是 6 位 hex 颜色）`)
  }
  return match[1]
}

function channel(hexPair) {
  const value = parseInt(hexPair, 16) / 255
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

function luminance(hex) {
  return (
    0.2126 * channel(hex.slice(1, 3)) +
    0.7152 * channel(hex.slice(3, 5)) +
    0.0722 * channel(hex.slice(5, 7))
  )
}

function contrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  const [lighter, darker] = values
  return (lighter + 0.05) / (darker + 0.05)
}

// 每一对 = 一处真实使用场景（fg token 的文本出现在 bg token 的底上）。
const PAIRS = [
  ['--ink', '--bone', '正文 / 页面底'],
  ['--ink', '--panel', '正文 / 栏底'],
  ['--muted', '--bone', '次要文字 / 页面底'],
  ['--muted', '--panel', '次要文字 / 栏底'],
  ['--accent', '--bone', '链接与激活 / 页面底'],
  ['--accent', '--panel', '链接与激活 / 栏底'],
  ['--ok', '--bone', '状态-通过 / 页面底'],
  ['--warn', '--bone', '状态-警示 / 页面底'],
  ['--bad', '--bone', '状态-失败 / 页面底'],
]

const MIN_RATIO = 4.5

let failures = 0
for (const [foreground, background, label] of PAIRS) {
  const ratio = contrast(token(foreground), token(background))
  const ok = ratio >= MIN_RATIO
  if (!ok) {
    failures += 1
  }
  console.log(
    `${ok ? 'ok  ' : 'FAIL'} ${label}: ${token(foreground)} on ${token(background)} = ${ratio.toFixed(2)}:1`,
  )
}

if (failures > 0) {
  console.error(`\n${failures} 对低于 ${MIN_RATIO}:1 —— 改 token 或改对比。`)
  process.exit(1)
}
console.log(`\n全部 ${PAIRS.length} 对 ≥ ${MIN_RATIO}:1（WCAG AA 正文）。`)
