const express = require('express');
const products = require('./products');
const orders = require('./orders');

const router = express.Router();
router.use('/products', products);
router.use('/orders', orders);

module.exports = router;
