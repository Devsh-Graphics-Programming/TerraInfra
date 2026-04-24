def call(Map args = [:]) {
  def result = runnerctlPost('/api/v1/janitor/run', [dry_run: args.get('dryRun', false)])
  echo('Runner janitor: cleaned=' + result.cleaned_count + ', skipped=' + result.skipped_count + ', dry_run=' + result.dry_run + '.')
  result.cleaned.each { item ->
    echo('Runner janitor cleaned: reason=' + item.reason + ', state=' + item.state + ', host=' + item.host_id + ', node=' + item.node + ', vmid=' + item.vmid + ', destroyed_vm=' + item.destroyed_vm + ', deleted_jenkins_node=' + item.deleted_jenkins_node + '.')
  }
  return result
}
