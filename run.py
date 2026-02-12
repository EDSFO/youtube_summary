import uvicorn
import logging
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

if __name__ == "__main__":
    print("=" * 50)
    print("Resumo YouTube API")
    print("=" * 50)
    print(f"Servidor rodando em: http://{settings.host}:{settings.port}")
    print(f"Endpoints disponiveis:")
    print(f"  - GET  /                    : Status")
    print(f"  - GET  /health              : Health check")
    print(f"  - GET  /api/videos          : Videos de ontem")
    print(f"  - POST /api/resumo          : Gerar resumos")
    print(f"  - GET  /api/videos/all      : Videos processados")
    print(f"  - POST /api/schedule        : Executar schedule")
    print("=" * 50)

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False
    )
