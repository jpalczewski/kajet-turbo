export const customFetch = async <T>(url: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(url, { credentials: 'include', ...options })
  const body = [204, 205, 304].includes(response.status) ? null : await response.text()
  const data = body ? JSON.parse(body) : {}
  if (!response.ok) {
    throw Object.assign(new Error(data?.error ?? `HTTP ${response.status}`), {
      status: response.status,
      data,
    })
  }
  return { data, status: response.status, headers: response.headers } as T
}
