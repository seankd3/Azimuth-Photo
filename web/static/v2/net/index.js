const paths = Object.freeze({
  counts: '/api/library/counts',
  drives: '/api/drives',
  photos: '/api/photos',
  photo: (id) => `/api/photos/${id}`,
  tile: (id, size = 400) => `/api/photos/${id}/tile?size=${size}`,
  refresh: (uuid) => `/api/drives/${encodeURIComponent(uuid)}/refresh`,
});

export { paths };

export async function call(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).filter(Boolean).join(' ')
      : body.detail;
    throw new Error(detail || `Azimuth could not complete that (${response.status}).`);
  }
  return response.json();
}

export function json(body) {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}
