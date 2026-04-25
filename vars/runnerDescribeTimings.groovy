def call(Map timings = [:], Map args = [:]) {
  def mode = (args.mode ?: 'current').toString()
  def hotPoolRecord = timings.containsKey('pool_member_ready_ms') && timings.pool_member_ready_ms != null
  def hotCurrentKeys = [
    'agent_lease_total_ms',
    'lease_total_ms',
    'allocator_api_ms',
    'allocation_ms',
    'hot_pool_acquire_ms',
    'guest_agent_guard_ms',
    'health_guard_ms',
    'prepare_ms',
    'jenkins_agent_connect_ms'
  ]
  def coldCurrentKeys = hotCurrentKeys + [
    'clone_ms',
    'configure_ms',
    'start_vm_ms',
    'guest_agent_wait_ms',
    'health_check_ms'
  ]
  def poolKeys = [
    'clone_ms',
    'configure_ms',
    'start_vm_ms',
    'guest_agent_wait_ms',
    'health_check_ms',
    'pool_jenkins_agent_connect_ms',
    'pool_member_ready_ms'
  ]
  def releaseKeys = [
    'delete_jenkins_node_ms',
    'destroy_vm_ms',
    'release_ms'
  ]
  def janitorKeys = [
    'lease_scan_ms',
    'vm_lookup_ms',
    'vm_status_ms',
    'vm_config_ms',
    'delete_jenkins_node_ms',
    'destroy_vm_ms',
    'janitor_ms'
  ]
  def refillKeys = [
    'refill_ms'
  ]
  def keys
  if (args.keys) {
    keys = args.keys
  } else if (mode == 'pool') {
    keys = poolKeys
  } else if (mode == 'release') {
    keys = releaseKeys
  } else if (mode == 'janitor') {
    keys = janitorKeys
  } else if (mode == 'refill') {
    keys = refillKeys
  } else if (mode == 'all') {
    keys = (coldCurrentKeys + poolKeys + releaseKeys + janitorKeys + refillKeys).unique()
  } else {
    keys = hotPoolRecord ? hotCurrentKeys : coldCurrentKeys
  }
  def parts = []
  keys.each { key ->
    if (timings.containsKey(key) && timings[key] != null) {
      parts << key.replace('_ms', '') + '=' + runnerFormatDuration(timings[key])
    }
  }
  if (mode != 'pool' && timings.health_source) {
    parts << 'health_source=' + timings.health_source
  }
  if (mode != 'pool' && timings.health_cache_age_seconds != null) {
    parts << 'health_cache_age=' + timings.health_cache_age_seconds + 's'
  }
  return parts.join(', ')
}
