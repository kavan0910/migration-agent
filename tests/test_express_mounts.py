from migration_agent.extract.express_mounts import apply_prefix, mount_prefixes


def test_nested_mounts():
    texts = {
        "src/app.js": "const routes = require('./routes');\napp.use('/api', routes);\napp.use(errorHandler);\n",
        "src/routes/index.js": "const products = require('./products');\nconst orders = require('./orders');\n"
                               "router.use('/products', products);\nrouter.use('/orders', auth, orders);\n",
        "src/routes/products.js": "router.get('/', h)\n",
        "src/routes/orders.js": "router.get('/', h)\n",
    }
    p = mount_prefixes(texts)
    assert p == {"src/routes/index.js": "/api", "src/routes/products.js": "/api/products",
                 "src/routes/orders.js": "/api/orders"}


def test_ambiguous_mount_left_relative():
    texts = {"a.js": "const r = require('./r');\napp.use('/v1', r);\n",
             "b.js": "const r = require('./r');\napp.use('/v2', r);\n", "r.js": ""}
    assert mount_prefixes(texts) == {}


def test_apply_prefix():
    assert apply_prefix("GET /", "/api/products") == "GET /api/products"
    assert apply_prefix("PUT /:id", "/api/products") == "PUT /api/products/:id"
    assert apply_prefix("GET /api/x", "/api") == "GET /api/x"       # already full
