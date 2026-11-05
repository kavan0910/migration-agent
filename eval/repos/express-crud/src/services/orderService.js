const Order = require('../models/order');
const Product = require('../models/product');
const config = require('../config');
const logger = require('../utils/logger');

async function createOrder(email, items) {
  if (items.length > config.maxOrderItems) {
    throw new Error('Order exceeds maximum item count');
  }
  let total = 0;
  const products = [];
  for (const item of items) {
    const product = await Product.findByPk(item.productId);
    if (!product || product.stock < item.qty) {
      throw new Error('Insufficient stock for ' + item.productId);
    }
    product.stock -= item.qty;
    await product.save();
    total += product.price * item.qty;
    products.push(product);
  }
  if (total > 500) {
    total = total * 0.9; // bulk discount of 10% over 500
  }
  const order = await Order.create({ customerEmail: email, total });
  await order.addProducts(products);
  logger.info('order created ' + order.id);
  return order;
}

async function listOrders() {
  return Order.findAll();
}

async function cancelOrder(id) {
  const order = await Order.findByPk(id);
  if (!order) return null;
  if (order.status === 'shipped') {
    throw new Error('Shipped orders cannot be cancelled');
  }
  return order.update({ status: 'cancelled' });
}

module.exports = { createOrder, listOrders, cancelOrder };
