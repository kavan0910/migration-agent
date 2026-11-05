const app = require('./app');
const config = require('./config');
const sequelize = require('./models/db');
const logger = require('./utils/logger');

sequelize.sync().then(() => {
  app.listen(config.port, () => logger.info('listening on ' + config.port));
});
