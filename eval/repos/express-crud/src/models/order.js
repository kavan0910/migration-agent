const { DataTypes } = require('sequelize');
const sequelize = require('./db');
const Product = require('./product');

const Order = sequelize.define('Order', {
  customerEmail: { type: DataTypes.STRING, allowNull: false },
  status: { type: DataTypes.STRING, defaultValue: 'pending' },
  total: { type: DataTypes.DECIMAL(10, 2), defaultValue: 0 },
});

Order.belongsToMany(Product, { through: 'OrderItems' });

module.exports = Order;
