def call(Map args = [:]) {
  def started = System.currentTimeMillis()
  def labels = (args.labels ?: []).collect { it.toString().trim() }.findAll { it }
  if (labels.isEmpty()) {
    error('runnerLease requires at least one label.')
  }
  labels.each { label ->
    if (!(label ==~ /[a-z0-9][a-z0-9_.-]*/)) {
      error('Invalid runner label: ' + label)
    }
  }
  if (labels.unique(false).size() != labels.size()) {
    error('Runner labels must be unique.')
  }

  def result = runnerctlPost('/api/v1/agent/lease', [
    labels: labels,
    lease_ttl_minutes: (args.leaseTtlMinutes ?: 30).toString()
  ])
  result.client_lease_ms = System.currentTimeMillis() - started
  echo('Runner online after ' + runnerFormatDuration(result.client_lease_ms) + ': label=' + result.label + ', allocation_mode=' + result.allocation_mode + ', host=' + result.host_id + ', node=' + result.node + ', vmid=' + result.vmid + '.')
  def timingText = runnerDescribeTimings(result.timings ?: [:])
  if (timingText) {
    echo('Runner lease timings: ' + timingText + '.')
  }
  def poolTimingText = runnerDescribeTimings(result.timings ?: [:], [mode: 'pool'])
  if (poolTimingText) {
    echo('Runner hot-pool provenance timings: ' + poolTimingText + '.')
  }
  return result
}
