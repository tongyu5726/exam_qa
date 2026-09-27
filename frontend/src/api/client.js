const BASE = '/api/v1'

export function apiUrl(path, params) {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, String(value))
    }
  }
  return url
}

export async function unwrapResponse(response) {
  const body = await response.json().catch(() => ({}))
  if (!response.ok || (body.code != null && body.code >= 400)) {
    const error = new Error(body.message || response.statusText || '请求失败')
    error.status = response.status
    throw error
  }
  return body.data !== undefined ? body.data : body
}

export async function apiRequest(method, path, { params, body, signal } = {}) {
  const options = { method, credentials: 'same-origin', signal }
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' }
    options.body = JSON.stringify(body)
  }
  return unwrapResponse(await fetch(apiUrl(path, params), options))
}

export const apiGet = (path, params, signal) => apiRequest('GET', path, { params, signal })
export const apiPost = (path, body, signal) => apiRequest('POST', path, { body, signal })
export const apiPatch = (path, body, params, signal) => apiRequest('PATCH', path, { body, params, signal })
export const apiDelete = (path, params, signal) => apiRequest('DELETE', path, { params, signal })
