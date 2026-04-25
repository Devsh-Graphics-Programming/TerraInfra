def call(String path) {
  def lower = path.toLowerCase()
  if (lower.endsWith('.html')) { return 'text/html; charset=utf-8' }
  if (lower.endsWith('.css')) { return 'text/css; charset=utf-8' }
  if (lower.endsWith('.js')) { return 'application/javascript; charset=utf-8' }
  if (lower.endsWith('.json')) { return 'application/json; charset=utf-8' }
  if (lower.endsWith('.txt') || lower.endsWith('.log')) { return 'text/plain; charset=utf-8' }
  if (lower.endsWith('.exr')) { return 'image/aces' }
  return 'application/octet-stream'
}
