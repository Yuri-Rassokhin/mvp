import os
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: List[str]) -> List[str]:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return list(default)
    return [item.strip() for item in val.split(",") if item.strip()]


# ===========================================================================
# 1. АУТЕНТИФИКАЦИЯ И БЕЗОПАСНОСТЬ
# ===========================================================================
API_KEY_HEADER_NAME: str = os.environ.get("MVP_API_KEY_HEADER", "X-API-Key")
DEFAULT_SECRET_TOKEN: str = "default_secret"
SECRET_TOKEN: str = (
    os.environ.get("MVP_API_KEY")
    or os.environ.get("COGNITIVE_CORE_API_KEY")
    or DEFAULT_SECRET_TOKEN
)
REQUIRE_CUSTOM_API_KEY: bool = _env_bool(
    "MVP_REQUIRE_CUSTOM_API_KEY",
    os.environ.get("MVP_ENV", "").strip().lower() == "production",
)


def validate_secret_token() -> None:
    """Выбрасывает ошибку в production, если используется дефолтный ключ."""
    if REQUIRE_CUSTOM_API_KEY and SECRET_TOKEN == DEFAULT_SECRET_TOKEN:
        raise RuntimeError(
            "Security error: COGNITIVE_CORE_API_KEY (or MVP_API_KEY) must be explicitly "
            "configured in production; refusing to start with 'default_secret'."
        )


# ===========================================================================
# 2. СЕТЕВЫЕ НАСТРОЙКИ, ХОСТЫ, ПОРТЫ И CORS
# ===========================================================================
CORS_ORIGINS: List[str] = _env_list(
    "MVP_CORS_ORIGINS",
    ["https://localhost:3000", "https://api.cognitive-core.io"],
)

GATEWAY_BIND_HOST: str = os.environ.get("MVP_GATEWAY_BIND_HOST", "0.0.0.0")
WORKER_BIND_HOST: str = os.environ.get("MVP_WORKER_BIND_HOST", "127.0.0.1")
ADVERTISE_HOST: str = os.environ.get("MVP_ADVERTISE_HOST", "127.0.0.1")
DISCOVERY_HOST: str = os.environ.get("MVP_DISCOVERY_HOST", ADVERTISE_HOST)
SEED_PEERS: List[str] = [
    peer.rstrip("/") for peer in _env_list("MVP_SEED_PEERS", [])
]

PORT_RANGE_START: int = int(os.environ.get("MVP_PORT_RANGE_START", "8500"))
PORT_RANGE_END: int = int(os.environ.get("MVP_PORT_RANGE_END", "8999"))

GOSSIP_PORT_RANGE_START: int = int(
    os.environ.get("MVP_GOSSIP_PORT_RANGE_START", str(PORT_RANGE_START))
)
GOSSIP_PORT_RANGE_END: int = int(
    os.environ.get("MVP_GOSSIP_PORT_RANGE_END", "8600")
)
PORT_RANGE: Tuple[int, int] = (GOSSIP_PORT_RANGE_START, GOSSIP_PORT_RANGE_END)

WORKER_PORT_MAX: int = int(os.environ.get("MVP_WORKER_PORT_MAX", "8990"))
SOCKET_LISTEN_BACKLOG: int = int(os.environ.get("MVP_SOCKET_LISTEN_BACKLOG", "256"))

MESH_PROTOCOL_VERSION: str = os.environ.get("MVP_MESH_PROTOCOL_VERSION", "1.0")


# ===========================================================================
# 3. GIT, ТИРЫ И МЕТАДАННЫЕ OPENAPI
# ===========================================================================
GIT_REMOTE: str = os.environ.get("MVP_GIT_REMOTE", "origin")
DEFAULT_PROD_BRANCH: str = os.environ.get("MVP_DEFAULT_PROD_BRANCH", "main")
DEFAULT_DEV_BRANCH: str = os.environ.get("MVP_DEFAULT_DEV_BRANCH", "main")
DEFAULT_TIER: str = os.environ.get("MVP_DEFAULT_TIER", "prod")
DEFAULT_COMPONENT_NAME: str = os.environ.get(
    "MVP_DEFAULT_COMPONENT_NAME", "unknown-component"
)

OPENAPI_SPEC_VERSION: str = os.environ.get("MVP_OPENAPI_SPEC_VERSION", "3.0.3")
OPENAPI_TITLE: str = os.environ.get("MVP_OPENAPI_TITLE", "Cognitive Core Platform")
OPENAPI_VERSION: str = os.environ.get("MVP_OPENAPI_VERSION", "1.0.0")
OPENAPI_DESCRIPTION: str = os.environ.get(
    "MVP_OPENAPI_DESCRIPTION", "Unified Enterprise API (Dynamic Mesh Gateway)"
)
MANAGER_OPENAPI_TITLE: str = os.environ.get(
    "MVP_MANAGER_OPENAPI_TITLE", "MVP Framework Manager"
)


# ===========================================================================
# 4. ФАЙЛОВАЯ СИСТЕМА, ЛОГИ, PID-ФАЙЛЫ И AST
# ===========================================================================
STATE_DIR: Path = Path(
    os.environ.get("MVP_STATE_DIR", str(Path.home() / ".mvp"))
).expanduser()
LOG_DIR: Path = Path(
    os.environ.get("MVP_LOG_DIR", str(STATE_DIR / "logs"))
).expanduser()
RUN_DIR: Path = Path(
    os.environ.get("MVP_RUN_DIR", str(STATE_DIR / "run"))
).expanduser()
EXCLUDE_FILE: Path = Path(
    os.environ.get("MVP_EXCLUDE_FILE", str(STATE_DIR / "exclude"))
).expanduser()

DEFAULT_EXCLUDED_DIRS: Set[str] = set(
    _env_list(
        "MVP_EXCLUDED_DIRS",
        [".git", "venv", ".venv", "env", "__pycache__", "node_modules"],
    )
)

TMP_DIR_BASE: Optional[str] = os.environ.get("MVP_TMP_DIR") or None
TMP_SRC_PREFIX: str = os.environ.get("MVP_TMP_SRC_PREFIX", "mvp-src-")
TEMP_DIR_MARKER: str = ".mvp_temp_dir"

ENSURE_DEFAULT_RETURN: bool = _env_bool("MVP_ENSURE_DEFAULT_RETURN", True)
_raw_default_return = os.environ.get("MVP_DEFAULT_RETURN_VALUE", "200")
DEFAULT_RETURN_VALUE: Union[int, str] = (
    int(_raw_default_return)
    if _raw_default_return.lstrip("-").isdigit()
    else _raw_default_return
)

CONSOLE_WIDTH: int = int(os.environ.get("MVP_CONSOLE_WIDTH", "10000"))


# ===========================================================================
# 5. ВСЕ ТАЙМАУТЫ, ИНТЕРВАЛЫ И ЗАДЕРЖКИ (1-в-1 без объединения)
# ===========================================================================
# --- autorouter.py ---
MOCK_DEFAULT_TIMEOUT: float = float(
    os.environ.get("MVP_MOCK_DEFAULT_TIMEOUT", "0.0")
)
AUTOROUTER_SCHEMA_PUSH_ATTEMPTS: int = int(
    os.environ.get("MVP_AUTOROUTER_SCHEMA_PUSH_ATTEMPTS", "10")
)
AUTOROUTER_SCHEMA_PUSH_SLEEP_SEC: float = float(
    os.environ.get("MVP_AUTOROUTER_SCHEMA_PUSH_SLEEP_SEC", "1.0")
)
AUTOROUTER_SCHEMA_PUSH_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_AUTOROUTER_SCHEMA_PUSH_TIMEOUT_SEC", "5.0")
)
AUTOROUTER_LIFESPAN_DEV_SLEEP_SEC: float = float(
    os.environ.get("MVP_AUTOROUTER_LIFESPAN_DEV_SLEEP_SEC", "3.0")
)
AUTOROUTER_UPDATE_DEV_SLEEP_SEC: float = float(
    os.environ.get("MVP_AUTOROUTER_UPDATE_DEV_SLEEP_SEC", "3.0")
)

# --- gossip.py ---
REAPER_PATIENCE: float = float(
    os.environ.get("MVP_REAPER_PATIENCE", "30.0")
)
GOSSIP_HTTP_CLIENT_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_GOSSIP_HTTP_CLIENT_TIMEOUT_SEC", "2.0")
)
GOSSIP_LEAVE_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_GOSSIP_LEAVE_TIMEOUT_SEC", "0.5")
)
GOSSIP_BOOTSTRAP_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_GOSSIP_BOOTSTRAP_TIMEOUT_SEC", "2.0")
)
GOSSIP_TOMBSTONE_TTL_SEC: float = float(
    os.environ.get("MVP_GOSSIP_TOMBSTONE_TTL_SEC", "60.0")
)
GOSSIP_FIRE_FORGET_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_GOSSIP_FIRE_FORGET_TIMEOUT_SEC", "1.0")
)
GOSSIP_LOOP_SLEEP_SEC: float = float(
    os.environ.get("MVP_GOSSIP_LOOP_SLEEP_SEC", "1.5")
)
GOSSIP_INITIAL_SCAN_WINDOW_SEC: float = float(
    os.environ.get("MVP_GOSSIP_INITIAL_SCAN_WINDOW_SEC", "30.0")
)
GOSSIP_INITIAL_SCAN_CHANCE: float = float(
    os.environ.get("MVP_GOSSIP_INITIAL_SCAN_CHANCE", "1.0")
)
GOSSIP_STEADY_SCAN_CHANCE: float = float(
    os.environ.get("MVP_GOSSIP_STEADY_SCAN_CHANCE", "0.1")
)
GOSSIP_REAPER_SLEEP_SEC: float = float(
    os.environ.get("MVP_GOSSIP_REAPER_SLEEP_SEC", "5.0")
)

# --- file_tree.py ---
FILE_TREE_STARTUP_TIMEOUT_SEC: int = int(
    os.environ.get("MVP_FILE_TREE_STARTUP_TIMEOUT_SEC", "180")
)
FILE_TREE_STARTUP_POLL_INTERVAL_SEC: float = float(
    os.environ.get("MVP_FILE_TREE_STARTUP_POLL_INTERVAL_SEC", "0.5")
)

# --- framework.py ---
FRAMEWORK_ADD_SLEEP_SEC: float = float(
    os.environ.get("MVP_FRAMEWORK_ADD_SLEEP_SEC", "1.5")
)
FRAMEWORK_PROBE_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_FRAMEWORK_PROBE_TIMEOUT_SEC", "0.5")
)
FRAMEWORK_OPENAPI_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_FRAMEWORK_OPENAPI_TIMEOUT_SEC", "2.0")
)
FRAMEWORK_LOG_FOLLOW_SLEEP_SEC: float = float(
    os.environ.get("MVP_FRAMEWORK_LOG_FOLLOW_SLEEP_SEC", "0.2")
)
FRAMEWORK_PURGE_WAIT_TIMEOUT_SEC: float = float(
    os.environ.get("MVP_FRAMEWORK_PURGE_WAIT_TIMEOUT_SEC", "5.0")
)
FRAMEWORK_PURGE_POLL_INTERVAL_SEC: float = float(
    os.environ.get("MVP_FRAMEWORK_PURGE_POLL_INTERVAL_SEC", "0.1")
)


# ===========================================================================
# 6. ВНЕШНИЙ КОНТУР (/integration), DNS И ХУКИ ОРКЕСТРАТОРОВ РЕПЛИК
# ===========================================================================
# Режим неймспейса путей в /integration:
#   "always"      -> /{module_slug}/{endpoint} для всех модулей (нулевые коллизии)
#   "on_conflict" -> /{endpoint} для уникальных имен, /{module_slug}/{endpoint} при дубликатах
#   "none"        -> /{endpoint} без префикса пути (только префиксация operationId и схем)
INTEGRATION_NAMESPACE_MODE: str = (
    os.environ.get("MVP_INTEGRATION_NAMESPACE_MODE", "always").strip().lower()
)

# Заголовок явного указания целевого модуля при вызове короткого пути /{endpoint}
MODULE_HEADER_NAME: str = os.environ.get("MVP_MODULE_HEADER", "X-MVP-Module")

# Публичный DNS-адрес единого шлюза для блока "servers" в /integration
# Например: "https://api.cognitive-core.internal" или "http://mvp.local"
PUBLIC_GATEWAY_URL: Optional[str] = (
    os.environ.get("MVP_PUBLIC_GATEWAY_URL", "").strip().rstrip("/") or None
)

# Опциональный шаблон индивидуального DNS-адреса каждого модуля в /integration
# Например: "https://{module_slug}.cognitive-core.internal"
PUBLIC_MODULE_URL_TEMPLATE: Optional[str] = (
    os.environ.get("MVP_PUBLIC_MODULE_URL_TEMPLATE", "").strip() or None
)

# Подключаемый хук жизненного цикла и балансировки реплик (через запятую можно несколько):
#   "cloud" / "noop" -> CloudNoOpHook (Azure Container Apps / KEDA, AWS, OCI)
#   "local_upstream" -> LocalUpstreamFileHook (генерация upstream-конфига Nginx/прокси на одной VM)
#   "pkg.mod:Class"  -> произвольный пользовательский класс, наследующий ReplicaOrchestratorHook
ORCHESTRATOR_HOOK_SPEC: str = os.environ.get("MVP_ORCHESTRATOR_HOOK", "cloud")

# Стратегия выбора реплики внутри mesh ("round_robin" | "random")
REPLICA_LB_STRATEGY: str = (
    os.environ.get("MVP_REPLICA_LB_STRATEGY", "round_robin").strip().lower()
)

# Настройки LocalUpstreamFileHook (для локальной VM с динамическими портами 8500..8999)
UPSTREAM_CONF_PATH: Path = Path(
    os.environ.get("MVP_UPSTREAM_CONF_PATH", str(RUN_DIR / "mvp_nginx_upstreams.conf"))
).expanduser()
UPSTREAM_JSON_PATH: Path = Path(
    os.environ.get("MVP_UPSTREAM_JSON_PATH", str(RUN_DIR / "mvp_upstreams.json"))
).expanduser()
UPSTREAM_RELOAD_CMD: Optional[str] = (
    os.environ.get("MVP_UPSTREAM_RELOAD_CMD", "").strip() or None
)

# Настройки телеметрии нагрузки, проб готовности и Graceful Drain (KEDA / Prometheus / ALB)
PROBE_AUTH_REQUIRED: bool = _env_bool("MVP_PROBE_AUTH_REQUIRED", False)
METRICS_AUTH_REQUIRED: bool = _env_bool("MVP_METRICS_AUTH_REQUIRED", True)
METRICS_WINDOW_SEC: float = float(os.environ.get("MVP_METRICS_WINDOW_SEC", "60.0"))
DRAIN_TIMEOUT_SEC: float = float(os.environ.get("MVP_DRAIN_TIMEOUT_SEC", "10.0"))
DRAIN_POLL_INTERVAL_SEC: float = float(
    os.environ.get("MVP_DRAIN_POLL_INTERVAL_SEC", "0.1")
)
