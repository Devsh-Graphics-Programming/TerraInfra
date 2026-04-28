def call(String prefix, String filePath = 'publish.zip', Map args = [:]) {
  def artifactName = (args.get('artifactName') ?: filePath.tokenize('/\\').last())?.toString()
  if (!(artifactName ==~ /[A-Za-z0-9][A-Za-z0-9._-]*\.zip/)) {
    error('Report upload artifact name must be a simple zip filename.')
  }

  def buildNumber = (args.get('build') ?: env.BUILD_NUMBER)?.toString()
  def jobName = (args.get('job') ?: env.JOB_NAME)?.toString()
  def encode = { Object value -> java.net.URLEncoder.encode(value?.toString() ?: '', 'UTF-8') }
  def query = [
    'prefix=' + encode(prefix),
    'artifact=' + encode(artifactName),
    'jobs=' + encode((args.get('jobs') ?: 8).toString()),
    'prune=' + encode((args.get('pruneAfterPublish') == true).toString())
  ]
  if (jobName?.trim()) {
    query << ('job=' + encode(jobName))
  }
  if (buildNumber ==~ /[0-9]+/) {
    query << ('build=' + encode(buildNumber))
  }

  def started = System.currentTimeMillis()
  def response = httpRequest(
    consoleLogResponseBody: false,
    contentType: 'APPLICATION_OCTETSTREAM',
    httpMode: 'PUT',
    uploadFile: filePath,
    url: 'http://127.0.0.1:18080/api/v1/store/publish-report-upload?' + query.join('&'),
    validResponseCodes: '100:599'
  )
  def result = readJSON(text: response.content, returnPojo: true)
  result.runnerctl_http_ms = System.currentTimeMillis() - started
  if (response.status != 200 || result.status != 'ok') {
    def message = result.message ?: 'unexpected error'
    if (result.details) {
      message += ', details=' + writeJSON(returnText: true, json: result.details)
    }
    error('runnerctl report upload failed: ' + message)
  }
  echo(
    'Published report with ' + result.publisher +
    ': uploaded=' + result.uploaded_count +
    ', skipped=' + result.skipped_count +
    ', files=' + result.file_count +
    ', bytes=' + result.bytes +
    ', pruned=' + (result.pruned_count ?: 0) +
    ', url=' + result.url
  )
  return result
}
