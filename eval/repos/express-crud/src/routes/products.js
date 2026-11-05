const express = require('express');
const service = require('../services/productService');
const requireAuth = require('../middleware/auth');

const router = express.Router();

router.get('/', async (req, res) => res.json(await service.listProducts()));

router.get('/:id', async (req, res) => {
  const product = await service.getProduct(req.params.id);
  if (!product) return res.status(404).end();
  res.json(product);
});

router.post('/', requireAuth, async (req, res, next) => {
  try { res.status(201).json(await service.createProduct(req.body)); } catch (e) { next(e); }
});

router.put('/:id', requireAuth, async (req, res) => {
  res.json(await service.updateProduct(req.params.id, req.body));
});

router.delete('/:id', requireAuth, async (req, res, next) => {
  try { await service.deleteProduct(req.params.id); res.status(204).end(); } catch (e) { next(e); }
});

module.exports = router;
