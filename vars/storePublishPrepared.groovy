def call(Map request) {
  def result = runnerctlPost('/api/v1/store/publish', request)
  echo('Published ' + result.file_count + ' files (' + result.bytes + ' bytes) to ' + result.url)
  return result
}
