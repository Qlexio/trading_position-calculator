"""Point d'entrée en ligne de commande : démarre le serveur web local."""

from __future__ import annotations

import os

import uvicorn

DEFAULT_PORT = 8000


def main() -> None:
    """Lance l'application sur http://127.0.0.1:8000 (port réglable par PORT)."""
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    uvicorn.run(
        "trading_position_calculator.app:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
