def call(Map args = [:], Closure body) {
  def runner = null
  try {
    stage(args.leaseStage ?: 'Lease runner') {
      runner = runnerLease(args)
    }
    node(runner.label) {
      body(runner)
    }
  } finally {
    stage(args.releaseStage ?: 'Release runner') {
      runnerRelease(runner ?: [:])
    }
  }
}
