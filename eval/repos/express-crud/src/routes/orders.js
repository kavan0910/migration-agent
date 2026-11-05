const express = require('express');
const service = require('../services/orderService');
const requireAuth = require('../middleware/auth');

const router = express.Router();

router.get('/', requireAuth, async (req, res) => res.json(await service.listOrders()));

router.post('/', async (req, res, next) => {
  try {
    const order = await service.createOrder(req.body.email, req.body.items);
    res.status(201).json(order);
  } catch (e) { next(e); }
});

router.post('/:id/cancel', requireAuth, async (req, res, next) => {
  try { res.json(await service.cancelOrder(req.params.id)); } catch (e) { next(e); }
});

module.exports = router;
