def call(String path) {
  def started = System.currentTimeMillis()
  def response = httpRequest(
    consoleLogResponseBody: false,
    httpMode: 'GET',
    url: 'http://127.0.0.1:18080' + path,
    validResponseCodes: '100:599'
  )
  def result = readJSON(text: response.content, returnPojo: true)
  result.runnerctl_http_ms = System.currentTimeMillis() - started
  if (response.status != 200 || result.status != 'ok') {
    def message = result.message ?: 'unexpected error'
    if (result.details) {
      message += ', details=' + writeJSON(returnText: true, json: result.details)
    }
    error('runnerctl ' + path + ' failed: ' + message)
  }
  return result
}
