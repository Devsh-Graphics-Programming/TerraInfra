def call(String prefix, String root, Map args = [:]) {
  def normalizedRoot = root.replace('\\', '/')
  def files = findFiles(glob: normalizedRoot + '/**').findAll { !it.directory }
  if (files.isEmpty()) {
    error('Publish directory is empty.')
  }
  def prefixWithSlash = normalizedRoot.endsWith('/') ? normalizedRoot : normalizedRoot + '/'
  def requestFiles = files.collect { item ->
    def relative = item.path.replace('\\', '/').substring(prefixWithSlash.length())
    [
      path: relative,
      content_type: storeContentTypeFor(relative),
      content_base64: readFile(file: item.path, encoding: 'Base64')
    ]
  }
  echo('Prepared ' + requestFiles.size() + ' files for store publish.')
  return [
    prefix: prefix,
    cache_control: args.get('cacheControl', 'no-store'),
    files: requestFiles
  ]
}
