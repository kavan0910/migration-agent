function log(level, msg) {
  console.log(`[${level}] ${msg}`);
}

module.exports = { info: (m) => log('info', m), error: (m) => log('error', m) };
