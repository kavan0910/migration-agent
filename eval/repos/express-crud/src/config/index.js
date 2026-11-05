require('dotenv').config();

module.exports = {
  port: process.env.PORT || 3000,
  dbUrl: process.env.DB_URL || 'sqlite::memory:',
  apiToken: process.env.API_TOKEN,
  maxOrderItems: parseInt(process.env.MAX_ORDER_ITEMS || '20', 10),
};
