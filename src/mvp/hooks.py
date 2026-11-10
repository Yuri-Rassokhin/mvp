import asyncio
from collections import deque
from dataclasses import dataclass, field
import importlib
import itertools
import json
import os
from pathlib import Path
import random
import re
import subprocess
import threading
import time
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from .config import (
    DRAIN_POLL_INTERVAL_SEC,
    DRAIN_TIMEOUT_SEC,
    METRICS_WINDOW_SEC,
    ORCHESTRATOR_HOOK_SPEC,
    PUBLIC_GATEWAY_URL,
    PUBLIC_MODULE_URL_TEMPLATE,
    REPLICA_LB_STRATEGY,
    UPSTREAM_CONF_PATH,
    UPSTREAM_JSON_PATH,
    UPSTREAM_RELOAD_CMD,
)

# Базовая транслитерация кириллицы, чтобы имена модулей на русском языке
# давали читаемые DNS/OpenAPI-совместимые слоги (например, "Аналитика" -> "analitika")
_CYRILLIC_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_module_name(name: str) -> str:
    """
    Преобразует человекочитаемое имя модуля (например, 'Excel Reader' или 'Ontologist')
    в канонический DNS- и URL-безопасный идентификатор ('excel-reader', 'ontologist').
    """
    raw = (name or "unknown-module").strip().lower()
    transliterated = "".join(_CYRILLIC_MAP.get(ch, ch) for ch in raw)
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return slug or "module"


def pascalize_slug(slug: str) -> str:
    """Преобразует slug модуля ('excel-reader') в префикс схемы OpenAPI ('ExcelReader')."""
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", slug) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Module"


@dataclass
class InstanceLifecycleMeta:
    """Контекст конкретного инстанса (реплики) модуля MVP, передаваемый в хуки оркестратора."""
    instance_id: str
    module_title: str
    module_slug: str
    base_url: str
    bind_host: str
    port: int
    active_tier: str
    manifest_path: str
    pid: int
    started_at: float = field(default_factory=time.time)


class _RequestTrackContext:
    """Контекстный менеджер замера одного входящего запроса (in-flight + латентность + статус)."""

    def __init__(self, tracker: "LoadTracker", endpoint: str):
        self._tracker = tracker
        self._endpoint = endpoint
        self._t0 = 0.0
        self.is_error = False

    def mark_error(self) -> None:
        self.is_error = True

    def __enter__(self) -> "_RequestTrackContext":
        self._t0 = time.perf_counter()
        self._tracker._on_start(self._endpoint)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        failed = self.is_error or (exc_type is not None)
        self._tracker._on_finish(self._endpoint, elapsed_ms, failed)
        return False


class LoadTracker:
    """
    Потокобезопасный счетчик нагрузки реплики для внешних автоскейлеров
    (Azure Container Apps / KEDA Metrics API, Prometheus, AWS Application Auto Scaling, OCI).
    """

    def __init__(self, window_sec: float = METRICS_WINDOW_SEC):
        self.window_sec = max(1.0, float(window_sec))
        self._lock = threading.Lock()
        self._in_flight_total: int = 0
        self._in_flight_by_ep: Dict[str, int] = {}
        self._requests_total: int = 0
        self._errors_total: int = 0
        self._requests_by_ep: Dict[str, int] = {}
        self._errors_by_ep: Dict[str, int] = {}
        # Скользящее окно завершенных запросов: (timestamp_sec, latency_ms, endpoint)
        self._recent_samples: Deque[Tuple[float, float, str]] = deque()

    def track(self, endpoint: str) -> _RequestTrackContext:
        return _RequestTrackContext(self, endpoint=endpoint or "unknown")

    @property
    def in_flight_total(self) -> int:
        with self._lock:
            return self._in_flight_total

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._recent_samples and self._recent_samples[0][0] < cutoff:
            self._recent_samples.popleft()

    def _on_start(self, endpoint: str) -> None:
        with self._lock:
            self._in_flight_total += 1
            self._in_flight_by_ep[endpoint] = self._in_flight_by_ep.get(endpoint, 0) + 1

    def _on_finish(self, endpoint: str, latency_ms: float, is_error: bool) -> None:
        now = time.time()
        with self._lock:
            self._in_flight_total = max(0, self._in_flight_total - 1)
            if endpoint in self._in_flight_by_ep:
                self._in_flight_by_ep[endpoint] = max(0, self._in_flight_by_ep[endpoint] - 1)
            self._requests_total += 1
            self._requests_by_ep[endpoint] = self._requests_by_ep.get(endpoint, 0) + 1
            if is_error:
                self._errors_total += 1
                self._errors_by_ep[endpoint] = self._errors_by_ep.get(endpoint, 0) + 1
            self._recent_samples.append((now, latency_ms, endpoint))
            self._prune_locked(now)

    def snapshot(
        self,
        meta: InstanceLifecycleMeta,
        is_ready: bool,
        is_draining: bool,
        module_active_replicas: int = 1,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            samples = list(self._recent_samples)
            in_flight = self._in_flight_total
            in_flight_ep = dict(self._in_flight_by_ep)
            req_total = self._requests_total
            err_total = self._errors_total
            req_ep = dict(self._requests_by_ep)
            err_ep = dict(self._errors_by_ep)

        sample_count = len(samples)
        rps = round(sample_count / self.window_sec, 4)
        avg_lat = round(sum(s[1] for s in samples) / sample_count, 3) if sample_count else 0.0
        max_lat = round(max((s[1] for s in samples), default=0.0), 3)

        endpoints_breakdown: Dict[str, Dict[str, Any]] = {}
        all_eps = set(in_flight_ep) | set(req_ep) | set(err_ep)
        for ep in sorted(all_eps):
            ep_lats = [s[1] for s in samples if s[2] == ep]
            endpoints_breakdown[ep] = {
                "in_flight": in_flight_ep.get(ep, 0),
                "requests_total": req_ep.get(ep, 0),
                "errors_total": err_ep.get(ep, 0),
                "rps": round(len(ep_lats) / self.window_sec, 4),
                "avg_latency_ms": round(sum(ep_lats) / len(ep_lats), 3) if ep_lats else 0.0,
            }

        return {
            "instance_id": meta.instance_id,
            "module": meta.module_title,
            "module_slug": meta.module_slug,
            "tier": meta.active_tier,
            "ready": bool(is_ready and not is_draining),
            "draining": bool(is_draining),
            "uptime_sec": round(max(0.0, now - meta.started_at), 2),
            "window_sec": self.window_sec,
            "module_active_replicas": int(module_active_replicas),
            # Верхнеуровневые скалярные метрики для прямого чтения через KEDA Metrics API Scaler
            "in_flight_total": in_flight,
            "requests_total": req_total,
            "errors_total": err_total,
            "rps": rps,
            "avg_latency_ms": avg_lat,
            "max_latency_ms": max_lat,
            "endpoints": endpoints_breakdown,
        }

    def to_prometheus(
        self,
        meta: InstanceLifecycleMeta,
        is_ready: bool,
        is_draining: bool,
        module_active_replicas: int = 1,
    ) -> str:
        snap = self.snapshot(
            meta=meta,
            is_ready=is_ready,
            is_draining=is_draining,
            module_active_replicas=module_active_replicas,
        )

        def _esc(val: str) -> str:
            return str(val).replace("\\", "\\\\").replace('"', '\"').replace("\n", "\\n")

        base_labels = (
            f'module="{_esc(meta.module_title)}",'
            f'module_slug="{_esc(meta.module_slug)}",'
            f'instance_id="{_esc(meta.instance_id)}",'
            f'tier="{_esc(meta.active_tier)}"'
        )

        lines = [
            "# HELP mvp_instance_ready Whether this MVP replica is ready to serve traffic (1=ready, 0=not ready/draining).",
            "# TYPE mvp_instance_ready gauge",
            f"mvp_instance_ready{{{base_labels}}} {1 if snap['ready'] else 0}",
            "# HELP mvp_instance_draining Whether this MVP replica is currently draining in-flight requests (1=draining, 0=normal).",
            "# TYPE mvp_instance_draining gauge",
            f"mvp_instance_draining{{{base_labels}}} {1 if snap['draining'] else 0}",
            "# HELP mvp_module_active_replicas Number of active replicas for this module currently visible in Gossip mesh.",
            "# TYPE mvp_module_active_replicas gauge",
            f"mvp_module_active_replicas{{{base_labels}}} {snap['module_active_replicas']}",
            "# HELP mvp_in_flight_requests Current number of concurrent in-flight HTTP requests on this replica.",
            "# TYPE mvp_in_flight_requests gauge",
            f"mvp_in_flight_requests{{{base_labels}}} {snap['in_flight_total']}",
            "# HELP mvp_requests_total Total number of HTTP requests processed by this replica.",
            "# TYPE mvp_requests_total counter",
            f"mvp_requests_total{{{base_labels}}} {snap['requests_total']}",
            "# HELP mvp_request_errors_total Total number of 5xx/failed HTTP requests on this replica.",
            "# TYPE mvp_request_errors_total counter",
            f"mvp_request_errors_total{{{base_labels}}} {snap['errors_total']}",
            "# HELP mvp_requests_per_second Sliding-window request rate (requests/sec) on this replica.",
            "# TYPE mvp_requests_per_second gauge",
            f"mvp_requests_per_second{{{base_labels}}} {snap['rps']}",
            "# HELP mvp_request_latency_ms_avg Sliding-window mean request latency in milliseconds.",
            "# TYPE mvp_request_latency_ms_avg gauge",
            f"mvp_request_latency_ms_avg{{{base_labels}}} {snap['avg_latency_ms']}",
        ]

        for ep, ep_stats in snap["endpoints"].items():
            ep_labels = f'{base_labels},endpoint="{_esc(ep)}"'
            lines.append(f"mvp_endpoint_in_flight_requests{{{ep_labels}}} {ep_stats['in_flight']}")
            lines.append(f"mvp_endpoint_requests_total{{{ep_labels}}} {ep_stats['requests_total']}")

        return "\n".join(lines) + "\n"


class ReplicaOrchestratorHook:
    """
    Базовый универсальный контракт интеграции MVP с внешними оркестраторами реплик
    и балансировщиками (Azure Container Apps / KEDA, AWS ECS/ALB, OCI, Kubernetes, локальный Nginx/Caddy).

    Пользовательские адаптеры могут наследоваться от этого класса и подключаться через
    переменную окружения MVP_ORCHESTRATOR_HOOK="my_pkg.my_module:MyCustomHook".
    """

    def __init__(self) -> None:
        self._rr_counters: Dict[str, itertools.count] = {}
        self._rr_lock = threading.Lock()

    async def on_gateway_started(self, meta: InstanceLifecycleMeta) -> None:
        """Вызывается при запуске процесса шлюза до прогрева воркеров."""
        return None

    async def on_instance_ready(
        self, meta: InstanceLifecycleMeta, registry: Dict[str, Any]
    ) -> None:
        """Вызывается, когда prod-воркер отдал схему OpenAPI и реплика готова принимать трафик."""
        return None

    async def on_mesh_topology_changed(
        self, meta: InstanceLifecycleMeta, registry: Dict[str, Any]
    ) -> None:
        """Вызывается при изменении состава живых реплик в Gossip-реестре."""
        return None

    async def on_instance_draining(
        self, meta: InstanceLifecycleMeta, load_tracker: Optional[LoadTracker] = None
    ) -> None:
        """
        Вызывается перед остановкой реплики (после перевода /_sys/ready в 503).
        Ожидает завершения текущих in-flight запросов в пределах DRAIN_TIMEOUT_SEC.
        """
        if load_tracker is None:
            return None
        deadline = time.time() + DRAIN_TIMEOUT_SEC
        while time.time() < deadline:
            if load_tracker.in_flight_total <= 0:
                break
            await asyncio.sleep(DRAIN_POLL_INTERVAL_SEC)

    async def on_instance_stopped(
        self, meta: InstanceLifecycleMeta, registry: Optional[Dict[str, Any]] = None
    ) -> None:
        """Вызывается на финальной стадии завершения процесса шлюза."""
        return None

    def resolve_public_gateway_url(
        self, request_base_url: str, meta: InstanceLifecycleMeta
    ) -> str:
        """
        Определяет публичный DNS-адрес шлюза для корневого блока "servers" в /integration.
        Если задан MVP_PUBLIC_GATEWAY_URL, скрывает внутренний IP/порт реплики за DNS-именем.
        """
        if PUBLIC_GATEWAY_URL:
            return PUBLIC_GATEWAY_URL
        return request_base_url.rstrip("/")

    def resolve_module_server_url(
        self, module_title: str, module_slug: str, fallback_gateway_url: str
    ) -> Optional[str]:
        """
        Если задан MVP_PUBLIC_MODULE_URL_TEMPLATE (например, 'https://{module_slug}.core.internal'),
        возвращает индивидуальный DNS URL модуля для поля 'servers' внутри операций OpenAPI 3.0.3.
        Иначе возвращает None (используется корневой 'servers' документа /integration).
        """
        if not PUBLIC_MODULE_URL_TEMPLATE:
            return None
        return PUBLIC_MODULE_URL_TEMPLATE.format(
            module_slug=module_slug,
            module_title=module_title,
        ).rstrip("/")

    def select_replica(
        self,
        candidates: List[Tuple[str, Any]],
        key: str = "global",
        strategy: str = REPLICA_LB_STRATEGY,
    ) -> Tuple[str, Any]:
        """
        Выбирает живую реплику из списка кандидатов [(instance_id, state), ...].
        По умолчанию использует детерминированный потокобезопасный Round-Robin.
        """
        if not candidates:
            raise ValueError("Cannot select replica from empty candidate list")
        if len(candidates) == 1:
            return candidates[0]

        ordered = sorted(candidates, key=lambda item: item[0])
        if strategy == "random":
            return random.choice(ordered)

        with self._rr_lock:
            counter = self._rr_counters.get(key)
            if counter is None:
                counter = itertools.count(0)
                self._rr_counters[key] = counter
            idx = next(counter) % len(ordered)
        return ordered[idx]


class CloudNoOpHook(ReplicaOrchestratorHook):
    """
    Стандартный адаптер для облачных сред (Azure Container Apps + KEDA, AWS ECS/EKS, OCI),
    где внешний оркестратор сам опрашивает /_sys/health, /_sys/ready, /_sys/load и /_sys/metrics.
    """
    pass


class LocalUpstreamFileHook(ReplicaOrchestratorHook):
    """
    Встроенный адаптер для локальной установки или single-VM стенда, где все реплики модулей
    стартуют на динамических портах (8500..8999) за локальным DNS-слоем / L7 reverse-proxy (Nginx/Caddy).

    Автоматически поддерживает актуальные файлы:
      - UPSTREAM_JSON_PATH (JSON-карта живых модулей и их base_url)
      - UPSTREAM_CONF_PATH (готовые директивы `upstream mvp_mesh_gateway` и `upstream mvp_mod_<slug>` для Nginx)
    И при наличии MVP_UPSTREAM_RELOAD_CMD выполняет команду мягкой перезагрузки прокси.
    """

    def __init__(
        self,
        conf_path: Path = UPSTREAM_CONF_PATH,
        json_path: Path = UPSTREAM_JSON_PATH,
        reload_cmd: Optional[str] = UPSTREAM_RELOAD_CMD,
    ) -> None:
        super().__init__()
        self.conf_path = Path(conf_path)
        self.json_path = Path(json_path)
        self.reload_cmd = reload_cmd
        self._write_lock = threading.Lock()
        self._last_rendered_conf: Optional[str] = None

    def _extract_host_port(self, base_url: str) -> Optional[str]:
        cleaned = re.sub(r"^https?://", "", (base_url or "").strip()).rstrip("/")
        if not cleaned:
            return None
        return cleaned.split("/")[0]

    def _sync_files(
        self,
        registry: Dict[str, Any],
        exclude_instance_id: Optional[str] = None,
    ) -> None:
        all_gateways: List[str] = []
        by_module: Dict[str, List[str]] = {}

        for inst_id, state in sorted((registry or {}).items(), key=lambda kv: kv[0]):
            if exclude_instance_id and inst_id == exclude_instance_id:
                continue
            status = getattr(state, "status", None) or (
                state.get("status") if isinstance(state, dict) else "active"
            )
            if status == "dead":
                continue

            base_url = getattr(state, "base_url", None) or (
                state.get("base_url") if isinstance(state, dict) else ""
            )
            contract = getattr(state, "contract", None) or (
                state.get("contract") if isinstance(state, dict) else {}
            ) or {}

            host_port = self._extract_host_port(base_url)
            if not host_port:
                continue

            if host_port not in all_gateways:
                all_gateways.append(host_port)

            title = contract.get("info", {}).get("title") or "unknown-module"
            slug = slugify_module_name(title)
            mod_list = by_module.setdefault(slug, [])
            if host_port not in mod_list:
                mod_list.append(host_port)

        conf_lines = [
            "# Auto-generated by MVP LocalUpstreamFileHook — do not edit manually",
            "upstream mvp_mesh_gateway {",
        ]
        if all_gateways:
            for hp in all_gateways:
                conf_lines.append(f"    server {hp} max_fails=2 fail_timeout=3s;")
        else:
            conf_lines.append("    server 127.0.0.1:65535 down;")
        conf_lines.append("}")

        for slug, servers in sorted(by_module.items()):
            safe_ident = slug.replace("-", "_")
            conf_lines.append(f"\nupstream mvp_mod_{safe_ident} {{")
            for hp in servers:
                conf_lines.append(f"    server {hp} max_fails=2 fail_timeout=3s;")
            conf_lines.append("}")

        rendered_conf = "\n".join(conf_lines) + "\n"
        json_payload = {
            "updated_at": time.time(),
            "gateway_upstreams": all_gateways,
            "modules": by_module,
        }

        with self._write_lock:
            if rendered_conf == self._last_rendered_conf:
                return
            self.conf_path.parent.mkdir(parents=True, exist_ok=True)
            self.json_path.parent.mkdir(parents=True, exist_ok=True)

            tmp_conf = self.conf_path.with_suffix(f".tmp.{os.getpid()}")
            tmp_json = self.json_path.with_suffix(f".tmp.{os.getpid()}")

            tmp_conf.write_text(rendered_conf, encoding="utf-8")
            os.replace(tmp_conf, self.conf_path)

            tmp_json.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
            os.replace(tmp_json, self.json_path)

            self._last_rendered_conf = rendered_conf

        if self.reload_cmd:
            try:
                subprocess.run(self.reload_cmd, shell=True, check=False, timeout=5.0)
            except Exception:
                pass

    async def on_instance_ready(
        self, meta: InstanceLifecycleMeta, registry: Dict[str, Any]
    ) -> None:
        self._sync_files(registry)

    async def on_mesh_topology_changed(
        self, meta: InstanceLifecycleMeta, registry: Dict[str, Any]
    ) -> None:
        self._sync_files(registry)

    async def on_instance_draining(
        self, meta: InstanceLifecycleMeta, load_tracker: Optional[LoadTracker] = None
    ) -> None:
        await super().on_instance_draining(meta, load_tracker=load_tracker)

    async def on_instance_stopped(
        self, meta: InstanceLifecycleMeta, registry: Optional[Dict[str, Any]] = None
    ) -> None:
        if registry is not None:
            self._sync_files(registry, exclude_instance_id=meta.instance_id)


class CompositeOrchestratorHook(ReplicaOrchestratorHook):
    """Композитный хук для одновременного запуска нескольких адаптеров."""

    def __init__(self, hooks: Iterable[ReplicaOrchestratorHook]) -> None:
        super().__init__()
        self.hooks = list(hooks)

    async def on_gateway_started(self, meta: InstanceLifecycleMeta) -> None:
        for h in self.hooks:
            await h.on_gateway_started(meta)

    async def on_instance_ready(
        self, meta: InstanceLifecycleMeta, registry: Dict[str, Any]
    ) -> None:
        for h in self.hooks:
            await h.on_instance_ready(meta, registry)

    async def on_mesh_topology_changed(
        self, meta: InstanceLifecycleMeta, registry: Dict[str, Any]
    ) -> None:
        for h in self.hooks:
            await h.on_mesh_topology_changed(meta, registry)

    async def on_instance_draining(
        self, meta: InstanceLifecycleMeta, load_tracker: Optional[LoadTracker] = None
    ) -> None:
        for idx, h in enumerate(self.hooks):
            await h.on_instance_draining(
                meta, load_tracker=load_tracker if idx == 0 else None
            )

    async def on_instance_stopped(
        self, meta: InstanceLifecycleMeta, registry: Optional[Dict[str, Any]] = None
    ) -> None:
        for h in self.hooks:
            await h.on_instance_stopped(meta, registry=registry)

    def resolve_public_gateway_url(
        self, request_base_url: str, meta: InstanceLifecycleMeta
    ) -> str:
        for h in self.hooks:
            resolved = h.resolve_public_gateway_url(request_base_url, meta)
            if resolved and resolved != request_base_url.rstrip("/"):
                return resolved
        return super().resolve_public_gateway_url(request_base_url, meta)

    def resolve_module_server_url(
        self, module_title: str, module_slug: str, fallback_gateway_url: str
    ) -> Optional[str]:
        for h in self.hooks:
            resolved = h.resolve_module_server_url(
                module_title, module_slug, fallback_gateway_url
            )
            if resolved:
                return resolved
        return None


_BUILTIN_HOOKS = {
    "noop": CloudNoOpHook,
    "cloud": CloudNoOpHook,
    "local_upstream": LocalUpstreamFileHook,
    "nginx": LocalUpstreamFileHook,
}


def _instantiate_single_hook(token: str) -> ReplicaOrchestratorHook:
    clean = token.strip()
    low = clean.lower()
    if low in _BUILTIN_HOOKS:
        return _BUILTIN_HOOKS[low]()
    if ":" not in clean:
        raise ValueError(
            f"Invalid MVP_ORCHESTRATOR_HOOK '{clean}'. Expected built-in alias "
            f"({', '.join(sorted(_BUILTIN_HOOKS))}) or 'package.module:ClassName'."
        )
    mod_name, cls_name = clean.split(":", 1)
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    instance = cls()
    if not isinstance(instance, ReplicaOrchestratorHook):
        raise TypeError(
            f"Hook '{clean}' must inherit from mvp.hooks.ReplicaOrchestratorHook"
        )
    return instance


def load_orchestrator_hook(
    spec: str = ORCHESTRATOR_HOOK_SPEC,
) -> ReplicaOrchestratorHook:
    """Загружает хук (или цепочку хуков через запятую) по спецификации из конфига."""
    tokens = [t.strip() for t in (spec or "cloud").split(",") if t.strip()]
    if not tokens:
        return CloudNoOpHook()
    instances = [_instantiate_single_hook(t) for t in tokens]
    if len(instances) == 1:
        return instances[0]
    return CompositeOrchestratorHook(instances)
