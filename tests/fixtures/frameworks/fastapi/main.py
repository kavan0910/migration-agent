from fastapi import FastAPI, APIRouter

app = FastAPI()
router = APIRouter(prefix="/items", tags=["items"])
inner = APIRouter(prefix="/sub")


@app.get("/health")
async def health():
    return {}


@router.get("/")
async def list_items():
    return []


@router.post("/{item_id}")
async def create_item(item_id: int):
    return {}


@inner.delete("/{x}")
async def rm(x: int):
    return {}


router.include_router(inner)
app.include_router(router, prefix="/v1")
