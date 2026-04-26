def call(String prefix, String artifactPath = 'publish.zip', Map args = [:]) {
  def jobName = (args.get('job') ?: env.JOB_NAME)?.toString()
  def buildNumber = (args.get('build') ?: env.BUILD_NUMBER)?.toString()
  if (!jobName?.trim()) {
    error('Jenkins job name is required for report store publish.')
  }
  if (!(buildNumber ==~ /[0-9]+/)) {
    error('Jenkins build number is required for report store publish.')
  }
  def result = runnerctlPost('/api/v1/store/publish-report-artifact', [
    prefix: prefix,
    job: jobName,
    build: buildNumber,
    artifact: artifactPath,
    jobs: (args.get('jobs') ?: 8).toString()
  ])
  echo(
    'Published report with ' + result.publisher +
    ': uploaded=' + result.uploaded_count +
    ', skipped=' + result.skipped_count +
    ', files=' + result.file_count +
    ', bytes=' + result.bytes +
    ', url=' + result.url
  )
  return result
}
