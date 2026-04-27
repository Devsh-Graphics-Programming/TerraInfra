def call(Map args = [:]) {
  def started = System.currentTimeMillis()
  def waitTimeoutMinutes = (args.get('leaseWaitTimeoutMinutes', 120) as int)
  def retrySeconds = (args.get('leaseRetrySeconds', 30) as int)
  if (waitTimeoutMinutes < 1 || waitTimeoutMinutes > 720) {
    error('leaseWaitTimeoutMinutes must be between 1 and 720.')
  }
  if (retrySeconds < 5 || retrySeconds > 300) {
    error('leaseRetrySeconds must be between 5 and 300.')
  }
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

  def result = null
  def deadline = started + waitTimeoutMinutes * 60000L
  for (int attempt = 1; result == null; attempt++) {
    try {
      result = runnerctlPost('/api/v1/agent/lease', [
        labels: labels,
        lease_ttl_minutes: (args.leaseTtlMinutes ?: 30).toString()
      ])
    } catch (Throwable err) {
      def message = err.getMessage() ?: err.toString()
      def retryable = message.contains('capacity-exhausted') || message.contains('no-capacity') || message.contains('No Proxmox host could create the requested runner lease')
      if (!retryable || System.currentTimeMillis() >= deadline) {
        throw err
      }
      def remainingSeconds = Math.max(1L, ((deadline - System.currentTimeMillis()) / 1000L) as long)
      def sleepSeconds = Math.min(retrySeconds as long, remainingSeconds)
      echo('Runner capacity unavailable on attempt ' + attempt + '; retrying in ' + sleepSeconds + 's. ' + message)
      sleep time: sleepSeconds, unit: 'SECONDS'
    }
  }
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
