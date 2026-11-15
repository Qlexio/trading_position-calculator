"""Point d'entrée en ligne de commande : démarre le serveur web local."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Lance l'application sur http://localhost:8000."""
    uvicorn.run(
        "trading_position_calculator.app:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
