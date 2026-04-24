def call(Map args = [:], Closure body) {
  def runner = null
  def requestedAt = System.currentTimeMillis()
  try {
    stage(args.leaseStage ?: 'Lease runner') {
      runner = runnerLease(args)
    }
    def nodeWaitStarted = System.currentTimeMillis()
    node(runner.label) {
      runner.node_enter_ms = System.currentTimeMillis() - nodeWaitStarted
      runner.ready_wall_ms = System.currentTimeMillis() - requestedAt
      echo('Runner workspace entered after ' + runnerFormatDuration(runner.ready_wall_ms) + ' from request start; Jenkins node wait=' + runnerFormatDuration(runner.node_enter_ms) + '.')
      body(runner)
    }
  } finally {
    stage(args.releaseStage ?: 'Release runner') {
      runnerRelease(runner ?: [:])
    }
  }
}
