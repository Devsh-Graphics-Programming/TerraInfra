def call(Map args = [:]) {
  def runner = args.runner
  if (!(runner instanceof Map)) {
    error('runnerScratch requires runner metadata.')
  }
  def scratch = runner.scratch
  if (!(scratch instanceof Map)) {
    error('Runner lease did not include scratch metadata.')
  }
  def apiUrl = scratch.api_url?.toString()?.trim()
  def uncRoot = scratch.unc_root?.toString()?.trim()
  if (!apiUrl || !uncRoot) {
    error('Runner scratch metadata is incomplete.')
  }
  def id = args.id?.toString()?.trim()
  if (!(id ==~ /[A-Za-z0-9][A-Za-z0-9._-]{0,95}/)) {
    error('Scratch id is invalid.')
  }
  def action = args.get('action', 'create').toString().trim()
  if (!(action in ['create', 'delete'])) {
    error('runnerScratch action must be create or delete.')
  }
  def response = httpRequest(
    consoleLogResponseBody: false,
    contentType: 'APPLICATION_JSON',
    httpMode: 'POST',
    requestBody: writeJSON(returnText: true, json: [id: id]),
    url: apiUrl.replaceAll('/+$', '') + '/api/v1/scratch/' + action,
    validResponseCodes: '100:599'
  )
  def result = readJSON(text: response.content, returnPojo: true)
  if (response.status != 200 || result.status != 'ok') {
    error('runner scratch ' + action + ' failed: ' + (result.message ?: 'unexpected error'))
  }
  result.unc_root = uncRoot
  if (!result.unc_path) {
    result.unc_path = uncRoot.replaceAll(/[\\\\\\/]+$/, '') + '\\' + id
  }
  if (result.smb_auth instanceof Map) {
    result.smb_username = result.smb_auth.username?.toString()
    result.smb_credential = result.smb_auth.credential?.toString()
    result.remove('smb_auth')
  }
  echo('Runner scratch ' + action + ': id=' + result.id + ', unc_path=' + result.unc_path)
  return result
}
