def call(Map args = [:]) {
  def runnerClass = args.runnerClass?.toString()
  if (!runnerClass) {
    error('runnerWaitForPoolReady requires runnerClass.')
  }
  int minReady = (args.minReady ?: 1) as int
  int timeoutSeconds = (args.timeoutSeconds ?: 300) as int
  int pollSeconds = (args.pollSeconds ?: 5) as int
  long deadline = System.currentTimeMillis() + (timeoutSeconds * 1000L)
  while (System.currentTimeMillis() < deadline) {
    def status = runnerPoolStatus()
    def pool = status.pools.find { it.runner_class == runnerClass }
    if (!pool) {
      error('Runner pool status did not include class ' + runnerClass + '.')
    }
    int ready = ((pool.counts ?: [:]).ready ?: 0) as int
    echo('Runner pool status: class=' + runnerClass + ', ready=' + ready + ', target=' + minReady + ', counts=' + writeJSON(returnText: true, json: pool.counts ?: [:]) + '.')
    pool.members.each { member ->
      echo('Runner pool member: state=' + member.state + ', host=' + member.host_id + ', node=' + member.node + ', vmid=' + member.vmid + ', has_agent=' + member.has_jenkins_agent + ', age=' + member.age_seconds + 's, expires_in=' + member.expires_in_seconds + 's.')
    }
    if (ready >= minReady) {
      return pool
    }
    sleep time: pollSeconds, unit: 'SECONDS'
  }
  error('Timed out waiting for runner pool ' + runnerClass + ' to reach ready=' + minReady + '.')
}
