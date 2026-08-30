// blob 下载工具：CSV 导出等文件流触发浏览器保存
// 说明：request.ts 拦截器对无 code 字段的响应体（Blob）原样透传，无需绕过信封

import request from '@/api/request'

/** 从 Content-Disposition 解析文件名（attachment; filename="xxx.csv"） */
function filenameFromDisposition(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback
  const m = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(disposition)
  return m?.[1] ? decodeURIComponent(m[1].replace(/"/g, '')) : fallback
}

/** GET 下载：blob → ObjectURL → <a download> 触发保存，结束后回收 */
export async function downloadFile(
  url: string,
  params: Record<string, unknown>,
  fallbackName: string,
): Promise<void> {
  const blob = await request.get<Blob, Blob>(url, {
    params,
    responseType: 'blob',
    timeout: 60000, // 导出可能量大，覆盖实例默认 10s
  })
  // 错误信封也可能以 blob 返回（JSON 文本）：识别后按文本报错
  if (blob.type.includes('application/json')) {
    const text = await blob.text()
    let message = '导出失败'
    try {
      message = JSON.parse(text).message ?? message
    } catch {
      /* 保留默认文案 */
    }
    throw new Error(message)
  }
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filenameFromDisposition(
    // axios 把 Content-Disposition 收敛在 headers 里，blob 场景从响应头拿不到；
    // 用兜底名（含日期）即可，后端文件名格式一致
    undefined,
    fallbackName,
  )
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(link.href)
}
