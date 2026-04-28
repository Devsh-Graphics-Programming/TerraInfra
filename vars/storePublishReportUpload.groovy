import groovy.json.JsonSlurperClassic

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
  def workspace = getContext(hudson.FilePath)
  if (workspace == null) {
    error('Report upload requires a workspace context.')
  }
  def remoteFile = workspace.child(filePath)
  if (!remoteFile.exists()) {
    error('Report upload file does not exist: ' + filePath)
  }
  def url = new URL('http://127.0.0.1:18080/api/v1/store/publish-report-upload?' + query.join('&'))
  def connection = (HttpURLConnection) url.openConnection()
  connection.setRequestMethod('PUT')
  connection.setDoOutput(true)
  connection.setConnectTimeout(((args.get('connectTimeoutSeconds') ?: 30) as int) * 1000)
  connection.setReadTimeout(((args.get('readTimeoutSeconds') ?: 7200) as int) * 1000)
  connection.setRequestProperty('Content-Type', 'application/octet-stream')
  connection.setFixedLengthStreamingMode(remoteFile.length())
  def input = remoteFile.read()
  try {
    def output = connection.outputStream
    try {
      byte[] buffer = new byte[1024 * 1024]
      int count = 0
      while ((count = input.read(buffer)) >= 0) {
        output.write(buffer, 0, count)
      }
    } finally {
      output.close()
    }
  } finally {
    input.close()
  }
  def status = connection.responseCode
  def responseStream = status >= 400 ? connection.errorStream : connection.inputStream
  def responseText = responseStream == null ? '' : responseStream.getText('UTF-8')
  def result = responseText ? new JsonSlurperClassic().parseText(responseText) : [:]
  result.runnerctl_http_ms = System.currentTimeMillis() - started
  if (status != 200 || result.status != 'ok') {
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
