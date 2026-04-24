def call(Object value) {
  long ms = 0
  try {
    ms = value as long
  } catch (ignored) {
    ms = 0
  }
  if (ms < 1000) {
    return ms + ' ms'
  }
  long tenths = Math.round(ms / 100.0D)
  if (ms < 60000) {
    return (Math.floor(tenths / 10) as long) + '.' + (tenths % 10) + ' s'
  }
  long minutes = Math.floor(ms / 60000) as long
  long remainderSeconds = Math.floor((ms % 60000) / 1000) as long
  return minutes + ' min ' + remainderSeconds + ' s'
}
