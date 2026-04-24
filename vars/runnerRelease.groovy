def call(Map runner = [:]) {
  if (!runner?.lease_id) {
    return null
  }
  def result = runnerctlPost('/api/v1/release', [lease_id: runner.lease_id])
  echo('Runner release: result=' + result.result + ', vmid=' + (result.vmid ?: 'n/a') + ', destroyed_vm=' + (result.destroyed_vm ?: false) + ', deleted_jenkins_node=' + (result.deleted_jenkins_node ?: false) + '.')
  return result
}
