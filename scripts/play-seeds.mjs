#!/usr/bin/env node
/**
 * play 渠道种子生成：国家 × 类目分榜 → 开发者官网（developerWebsite）+ 开发者主体名。
 *
 * 输出格式：每行 `URL<TAB>developerName`（developerName 可空）——seeds.py 解析时按
 * \t 切，前段做 URL 归一，后段作为 P0 第 1 强信号入打分（spec 3.5）。
 *
 * 为什么是 JS：Python 版 google-play-scraper 已无 top_chart/list 分榜 API，
 * JS 版 list(collection, category, fullDetail:true) 一次拿全详情。
 *
 * 用法：
 *   node scripts/play-seeds.mjs --countries id,br,mx --categories BUSINESS \
 *        --num 150 --out data/seeds-play.txt
 * CN 网络需代理：NODE_USE_ENV_PROXY=1 HTTPS_PROXY=http://127.0.0.1:7890 node …
 * 依赖：cd scripts && npm i（package.json 已声明）
 */
import { writeFileSync } from 'node:fs'
import gplay from 'google-play-scraper'

const args = process.argv.slice(2)
const opt = (name, def) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 && args[i + 1] ? args[i + 1] : def
}

const countries = (opt('countries', 'id')).split(',').map((s) => s.trim()).filter(Boolean)
const categories = (opt('categories', 'BUSINESS')).split(',').map((s) => s.trim()).filter(Boolean)
const num = parseInt(opt('num', '150'), 10)
const out = opt('out', 'data/seeds-play.txt')

const all = new Map()  // web -> developer
let failed = 0

for (const country of countries) {
  for (const category of categories) {
    const cat = gplay.category[category]
    if (!cat) {
      console.error(`[skip] 未知类目: ${category}`)
      failed++
      continue
    }
    try {
      const apps = await gplay.list({
        collection: gplay.collection.TOP_FREE,
        category: cat,
        country,
        num,
        fullDetail: true,
      })
      let got = 0
      for (const a of apps) {
        const web = (a.developerWebsite || '').trim()
        if (web.startsWith('http')) {
          // 同一官网多家 app 共用：保留首个非空 developer 名（首次见到时存）
          if (!all.has(web)) {
            all.set(web, (a.developer || '').trim())
            got++
          }
        }
      }
      console.log(`${country} ${category}: apps=${apps.length} with-site=${got}`)
    } catch (e) {
      // 单图失败不中断整体（限流/网络抖动），最终退出码非 0 提醒
      console.error(`[fail] ${country} ${category}: ${e.message}`)
      failed++
    }
  }
}

const lines = [...all.entries()].map(([web, dev]) => dev ? `${web}\t${dev}` : web)
writeFileSync(out, lines.join('\n') + '\n')
console.log(`TOTAL unique=${lines.length} -> ${out}${failed ? ` (failed-charts=${failed})` : ''}`)
process.exitCode = lines.length === 0 ? 1 : 0
