"""Same as `skyvern run server`, but bound to 127.0.0.1 instead of 0.0.0.0 (the CLI has no host option).

Run from this folder (start_server.sh does that): it loads ./.env exactly like the CLI's server intent.
"""

import uvicorn

from skyvern._cli_bootstrap import prepare_cli_runtime
from skyvern.utils.env_paths import EnvIntent

prepare_cli_runtime(intent=EnvIntent.SERVER)
from skyvern.config import settings  # noqa: E402

uvicorn.run(
    "skyvern.forge.api_app:create_api_app",
    host="127.0.0.1",
    port=settings.PORT,
    access_log=False,
    log_config={"version": 1, "disable_existing_loggers": False},
    factory=True,
    loop="asyncio",
    timeout_keep_alive=settings.UVICORN_TIMEOUT_KEEP_ALIVE,
    ws="websockets-sansio",
    ws_ping_interval=20.0,
    ws_ping_timeout=120.0,
)
