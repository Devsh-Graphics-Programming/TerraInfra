def call(Map args = [:], Closure body) {
  def runner = null
  def requestedAt = System.currentTimeMillis()
  def maxReadySeconds = args.maxReadySeconds == null ? null : (args.maxReadySeconds as int)
  try {
    stage(args.leaseStage ?: 'Lease runner') {
      runner = runnerLease(args)
    }
    def nodeWaitStarted = System.currentTimeMillis()
    node(runner.label) {
      runner.node_enter_ms = System.currentTimeMillis() - nodeWaitStarted
      runner.ready_wall_ms = System.currentTimeMillis() - requestedAt
      def leaseReadyMs = runner.timings?.agent_lease_total_ms ?: runner.client_lease_ms
      runner.ready_budget_ms = (leaseReadyMs == null ? runner.ready_wall_ms : (leaseReadyMs as long) + (runner.node_enter_ms as long))
      echo('Runner workspace entered after ' + runnerFormatDuration(runner.ready_wall_ms) + ' from request start; Jenkins node wait=' + runnerFormatDuration(runner.node_enter_ms) + '.')
      if (maxReadySeconds != null) {
        def maxReadyMs = maxReadySeconds * 1000L
        echo('Runner readiness budget: observed=' + runner.ready_budget_ms + ' ms, budget=' + maxReadyMs + ' ms.')
        if (runner.ready_budget_ms > maxReadyMs) {
          error('Runner readiness budget exceeded.')
        }
      }
      body(runner)
    }
  } finally {
    stage(args.releaseStage ?: 'Release runner') {
      runnerRelease(runner ?: [:])
    }
  }
}
