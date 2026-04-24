def call(Map args = [:]) {
  def body = [:]
  if (args.runnerClass) {
    body.runner_class = args.runnerClass
  }
  if (args.labels) {
    body.required_labels = args.labels
  }
  def result = runnerctlPost('/api/v1/pool/refill', body)
  result.pools.each { pool ->
    def created = pool.created ?: []
    echo('Runner pool refill: class=' + pool.runner_class + ', created=' + created.size() + ', janitor_cleaned=' + (result.janitor?.cleaned_count ?: 0) + '.')
    created.each { member ->
      def timingText = runnerDescribeTimings(member.timings ?: [:])
      echo('Runner pool ready: host=' + member.host_id + ', node=' + member.node + ', vmid=' + member.vmid + ', clone=' + member.clone_name + (timingText ? ', timings: ' + timingText : '') + '.')
    }
  }
  return result
}
