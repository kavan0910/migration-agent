const logger = require('../utils/logger');

module.exports = function errorHandler(err, req, res, next) {
  logger.error(err.message);
  res.status(400).json({ error: err.message });
};
