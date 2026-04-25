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
      echo('Runner workspace entered after ' + runnerFormatDuration(runner.ready_wall_ms) + ' from request start; Jenkins node wait=' + runnerFormatDuration(runner.node_enter_ms) + '.')
      if (maxReadySeconds != null) {
        def maxReadyMs = maxReadySeconds * 1000L
        echo('Runner readiness budget: observed=' + runner.ready_wall_ms + ' ms, budget=' + maxReadyMs + ' ms.')
        if (runner.ready_wall_ms > maxReadyMs) {
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
