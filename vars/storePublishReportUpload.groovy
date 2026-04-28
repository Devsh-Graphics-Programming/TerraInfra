import com.cloudbees.groovy.cps.NonCPS
import groovy.json.JsonSlurperClassic

@NonCPS
private Map uploadZipToRunnerctl(hudson.FilePath remoteFile, String urlText, long fileLength, int connectTimeoutMs, int readTimeoutMs) {
  HttpURLConnection connection = null
  InputStream input = null
  OutputStream output = null
  InputStream responseStream = null
  long streamMs = 0L
  try {
    connection = (HttpURLConnection) new URL(urlText).openConnection()
    connection.setRequestMethod('PUT')
    connection.setDoOutput(true)
    connection.setConnectTimeout(connectTimeoutMs)
    connection.setReadTimeout(readTimeoutMs)
    connection.setRequestProperty('Content-Type', 'application/octet-stream')
    connection.setFixedLengthStreamingMode(fileLength)

    long streamStarted = System.currentTimeMillis()
    input = remoteFile.read()
    output = connection.outputStream
    byte[] buffer = new byte[1024 * 1024]
    int count = 0
    while ((count = input.read(buffer)) >= 0) {
      output.write(buffer, 0, count)
    }
    output.close()
    output = null
    input.close()
    input = null
    streamMs = System.currentTimeMillis() - streamStarted

    int status = connection.responseCode
    responseStream = status >= 400 ? connection.errorStream : connection.inputStream
    String responseText = responseStream == null ? '' : responseStream.getText('UTF-8')
    return [status: status, responseText: responseText, streamMs: streamMs]
  } finally {
    if (responseStream != null) {
      responseStream.close()
    }
    if (output != null) {
      output.close()
    }
    if (input != null) {
      input.close()
    }
    if (connection != null) {
      connection.disconnect()
    }
  }
}

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
  def fileLength = remoteFile.length()
  echo('Publishing report zip: artifact=' + artifactName + ', bytes=' + fileLength + ', prefix=' + prefix)
  def upload = uploadZipToRunnerctl(
    remoteFile,
    'http://127.0.0.1:18080/api/v1/store/publish-report-upload?' + query.join('&'),
    fileLength as long,
    ((args.get('connectTimeoutSeconds') ?: 30) as int) * 1000,
    ((args.get('readTimeoutSeconds') ?: 7200) as int) * 1000
  )
  def streamMs = upload.streamMs as long
  echo('Report zip stream completed in ' + runnerFormatDuration(streamMs) + '. Waiting for store publish result.')
  def status = upload.status as int
  def responseText = upload.responseText ?: ''
  def result = responseText ? new JsonSlurperClassic().parseText(responseText) : [:]
  result.runnerctl_http_ms = System.currentTimeMillis() - started
  if (status != 200 || result.status != 'ok') {
    def message = result.message ?: 'unexpected error'
    if (result.details) {
      message += ', details=' + writeJSON(returnText: true, json: result.details)
    }
    error('runnerctl report upload failed: ' + message)
  }
  def timings = result.timings ?: [:]
  echo(
    'Published report with ' + result.publisher +
    ': uploaded=' + result.uploaded_count +
    ', skipped=' + result.skipped_count +
    ', files=' + result.file_count +
    ', bytes=' + result.bytes +
    ', pruned=' + (result.pruned_count ?: 0) +
    ', url=' + result.url +
    ', stream=' + runnerFormatDuration(streamMs) +
    ', runnerctl=' + runnerFormatDuration(result.runnerctl_http_ms as long) +
    ', publish_s3=' + runnerFormatDuration((timings.publish_s3_ms ?: 0) as long) +
    ', prune=' + runnerFormatDuration((timings.prune_ms ?: 0) as long)
  )
  return result
}
