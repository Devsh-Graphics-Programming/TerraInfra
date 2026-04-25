def call(Map runner = [:]) {
  if (!runner?.lease_id) {
    return null
  }
  def result = null
  for (int attempt = 1; attempt <= 6; attempt++) {
    try {
      result = runnerctlPost('/api/v1/release', [lease_id: runner.lease_id])
      break
    } catch (Throwable error) {
      if (attempt == 6) {
        throw error
      }
      echo('Runner release attempt ' + attempt + ' failed; retrying: ' + error.getMessage())
      sleep time: 10, unit: 'SECONDS'
    }
  }
  echo('Runner release: result=' + result.result + ', vmid=' + (result.vmid ?: 'n/a') + ', destroyed_vm=' + (result.destroyed_vm ?: false) + ', deleted_jenkins_node=' + (result.deleted_jenkins_node ?: false) + '.')
  def timingText = runnerDescribeTimings(result.timings ?: [:], [mode: 'release'])
  if (timingText) {
    echo('Runner release timings: ' + timingText + '.')
  }
  return result
}
