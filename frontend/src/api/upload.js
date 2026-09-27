import { apiUrl } from './client.js'

/** XHR is used here because upload byte progress is required by the existing UI. */
export function postForm(path, formData, { onProgress, signal } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl(path))
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.({ phase: 'upload', ratio: event.loaded / event.total })
    }
    xhr.upload.onload = () => onProgress?.({ phase: 'processing', ratio: 1 })
    xhr.onerror = () => reject(new Error('网络错误'))
    xhr.onabort = () => reject(new DOMException('已取消', 'AbortError'))
    xhr.onload = () => {
      let data
      try { data = JSON.parse(xhr.responseText || '{}') } catch { reject(new Error('响应格式错误')); return }
      if (xhr.status >= 400 || (data.code != null && data.code >= 400)) {
        reject(new Error(data.message || '入库失败'))
      } else {
        resolve(data.data !== undefined ? data.data : data)
      }
    }
    if (signal) {
      if (signal.aborted) { reject(new DOMException('已取消', 'AbortError')); return }
      signal.addEventListener('abort', () => xhr.abort(), { once: true })
    }
    xhr.send(formData)
  })
}
