def call(Map timings = [:]) {
  def keys = [
    'agent_lease_total_ms',
    'lease_total_ms',
    'allocator_api_ms',
    'allocation_ms',
    'hot_pool_acquire_ms',
    'clone_ms',
    'configure_ms',
    'start_vm_ms',
    'guest_agent_wait_ms',
    'guest_agent_guard_ms',
    'health_check_ms',
    'prepare_ms',
    'jenkins_agent_connect_ms',
    'pool_member_ready_ms',
    'destroy_vm_ms',
    'release_ms'
  ]
  def parts = []
  keys.each { key ->
    if (timings.containsKey(key) && timings[key] != null) {
      parts << key.replace('_ms', '') + '=' + runnerFormatDuration(timings[key])
    }
  }
  if (timings.health_source) {
    parts << 'health_source=' + timings.health_source
  }
  if (timings.health_cache_age_seconds != null) {
    parts << 'health_cache_age=' + timings.health_cache_age_seconds + 's'
  }
  return parts.join(', ')
}
