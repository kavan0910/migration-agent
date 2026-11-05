const Product = require('../models/product');

async function listProducts() {
  return Product.findAll();
}

async function getProduct(id) {
  return Product.findByPk(id);
}

async function createProduct(data) {
  if (data.price <= 0) {
    throw new Error('Price must be positive');
  }
  return Product.create(data);
}

async function updateProduct(id, data) {
  const product = await Product.findByPk(id);
  if (!product) return null;
  return product.update(data);
}

async function deleteProduct(id) {
  const product = await Product.findByPk(id);
  if (!product) return false;
  if (product.stock > 0) {
    throw new Error('Cannot delete product with remaining stock');
  }
  await product.destroy();
  return true;
}

module.exports = { listProducts, getProduct, createProduct, updateProduct, deleteProduct };
