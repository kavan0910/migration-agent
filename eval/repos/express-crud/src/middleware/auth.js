const config = require('../config');

module.exports = function requireAuth(req, res, next) {
  const token = req.headers['x-api-token'];
  if (!token || token !== config.apiToken) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  next();
};
