def call(String path, Map body) {
  def response = httpRequest(
    consoleLogResponseBody: false,
    contentType: 'APPLICATION_JSON',
    httpMode: 'POST',
    requestBody: writeJSON(returnText: true, json: body),
    url: 'http://127.0.0.1:18080' + path,
    validResponseCodes: '100:599'
  )
  def result = readJSON(text: response.content, returnPojo: true)
  if (response.status != 200 || result.status != 'ok') {
    error('runnerctl ' + path + ' failed: ' + (result.message ?: 'unexpected error'))
  }
  return result
}
