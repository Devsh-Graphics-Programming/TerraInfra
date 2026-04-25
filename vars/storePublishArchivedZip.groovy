def call(String prefix, String artifactPath = 'publish.zip', Map args = [:]) {
  def jobName = (args.get('job') ?: env.JOB_NAME)?.toString()
  def buildNumber = (args.get('build') ?: env.BUILD_NUMBER)?.toString()
  if (!jobName?.trim()) {
    error('Jenkins job name is required for archived store publish.')
  }
  if (!(buildNumber ==~ /[0-9]+/)) {
    error('Jenkins build number is required for archived store publish.')
  }
  def result = runnerctlPost('/api/v1/store/publish-artifact', [
    prefix: prefix,
    cache_control: args.get('cacheControl', 'no-store'),
    job: jobName,
    build: buildNumber,
    artifact: artifactPath
  ])
  echo('Published ' + result.file_count + ' files (' + result.bytes + ' bytes) from ' + artifactPath + ' to ' + result.url)
  return result
}
