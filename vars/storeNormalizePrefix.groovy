def call(String value, List allowedPrefixes = []) {
  def prefix = value?.trim()
  if (!prefix) {
    error('Store prefix is required.')
  }
  prefix = prefix.replace('\\', '/')
  if (!prefix.endsWith('/')) {
    prefix += '/'
  }
  if (prefix.startsWith('/') || prefix.contains('//') || prefix.contains('../') || prefix.contains('/..')) {
    error('Invalid store prefix.')
  }
  if (!(prefix ==~ /[A-Za-z0-9][A-Za-z0-9._\/-]*\//)) {
    error('Store prefix contains unsupported characters.')
  }
  if (allowedPrefixes && !allowedPrefixes.any { allowed -> prefix.startsWith(allowed) }) {
    error('Store prefix must stay under: ' + allowedPrefixes.join(', '))
  }
  return prefix
}
