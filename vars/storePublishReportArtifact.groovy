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
    jobs: (args.get('jobs') ?: 8).toString(),
    prune: args.get('pruneAfterPublish') == true
  ])
  echo(
    'Published report with ' + result.publisher +
    ': uploaded=' + result.uploaded_count +
    ', skipped=' + result.skipped_count +
    ', files=' + result.file_count +
    ', bytes=' + result.bytes +
    ', pruned=' + (result.pruned_count ?: 0) +
    ', url=' + result.url
  )
  if (args.get('deleteAfterPublish') == true) {
    def cleanup = runnerctlPost('/api/v1/jenkins/delete-artifact', [
      job: jobName,
      build: buildNumber,
      artifact: artifactPath
    ])
    echo(
      'Deleted transient publish artifact: result=' + cleanup.result +
      ', artifact=' + cleanup.artifact +
      ', bytes=' + cleanup.bytes + '.'
    )
  }
  return result
}
