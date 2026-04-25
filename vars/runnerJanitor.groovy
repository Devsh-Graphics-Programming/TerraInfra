def call(Map args = [:]) {
  def result = runnerctlPost('/api/v1/janitor/run', [dry_run: args.get('dryRun', false)])
  def timingText = runnerDescribeTimings(result.timings ?: [:], [mode: 'janitor'])
  echo('Runner janitor: cleaned=' + result.cleaned_count + ', skipped=' + result.skipped_count + ', dry_run=' + result.dry_run + (timingText ? ', timings: ' + timingText : '') + '.')
  (result.cleaned ?: []).each { item ->
    def itemTimingText = runnerDescribeTimings(item.timings ?: [:], [mode: 'janitor'])
    echo('Runner janitor cleaned: reason=' + item.reason + ', state=' + item.state + ', host=' + item.host_id + ', node=' + item.node + ', vmid=' + item.vmid + ', destroyed_vm=' + item.destroyed_vm + ', deleted_jenkins_node=' + item.deleted_jenkins_node + (itemTimingText ? ', timings: ' + itemTimingText : '') + '.')
  }
  (result.skipped ?: []).each { item ->
    def itemTimingText = runnerDescribeTimings(item.timings ?: [:], [mode: 'janitor'])
    echo('Runner janitor skipped: reason=' + item.reason + ', lease_id=' + (item.lease_id ?: 'n/a') + (itemTimingText ? ', timings: ' + itemTimingText : '') + '.')
  }
  return result
}
