import logging
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pathlib import Path
from app.models.database import Database
from app.routers import videos

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Iniciando aplicação...")
    db = Database()
    await db.init_db()
    logger.info("Banco de dados inicializado.")
    yield
    # Shutdown
    logger.info("Encerrando aplicação...")


app = FastAPI(
    title="Resumo YouTube API",
    description="API para buscar vídeos do YouTube e gerar resumos via IA",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(videos.router, prefix="/api", tags=["videos"])

# Mount templates
templates_path = Path(__file__).parent / "templates"
if templates_path.exists():
    app.mount("/static", StaticFiles(directory=str(templates_path)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = templates_path / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return {"status": "ok", "message": "Resumo YouTube API está rodando!"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
