import os
import asyncio
import time
import random
import httpx
from typing import Callable, Dict, Any, Optional
from pydantic import BaseModel

from .config import (
    API_KEY_HEADER_NAME,
    DISCOVERY_HOST,
    GOSSIP_BOOTSTRAP_TIMEOUT_SEC,
    GOSSIP_FIRE_FORGET_TIMEOUT_SEC,
    GOSSIP_HTTP_CLIENT_TIMEOUT_SEC,
    GOSSIP_INITIAL_SCAN_CHANCE,
    GOSSIP_INITIAL_SCAN_WINDOW_SEC,
    GOSSIP_LEAVE_TIMEOUT_SEC,
    GOSSIP_LOOP_SLEEP_SEC,
    GOSSIP_REAPER_SLEEP_SEC,
    GOSSIP_STEADY_SCAN_CHANCE,
    GOSSIP_TOMBSTONE_TTL_SEC,
    MESH_PROTOCOL_VERSION,
    PORT_RANGE,
    REAPER_PATIENCE,
    SECRET_TOKEN,
    SEED_PEERS,
)

class InstanceState(BaseModel):
    base_url: str
    contract: Dict[str, Any]
    heartbeat: int
    local_updated_at: float
    status: str = "active"

class GossipPayload(BaseModel):
    sender_id: str
    registry: Dict[str, InstanceState]

class GossipMesh:
    def __init__(
        self,
        instance_id: str,
        base_url: str,
        port_range: tuple = PORT_RANGE,
        on_registry_change: Optional[Callable[[Dict[str, InstanceState]], Any]] = None,
    ):
        self.instance_id = instance_id
        self.base_url = base_url
        self.port_range = port_range
        self.registry: Dict[str, InstanceState] = {}
        self.tombstones: Dict[str, float] = {}
        self.http_client = httpx.AsyncClient(timeout=GOSSIP_HTTP_CLIENT_TIMEOUT_SEC)
        self.started_at = time.time()
        self.on_registry_change = on_registry_change

    def _notify_topology_change(self) -> None:
        if not self.on_registry_change:
            return
        try:
            res = self.on_registry_change(self.registry)
            if asyncio.iscoroutine(res):
                asyncio.create_task(res)
        except Exception:
            pass

    async def leave_network(self):
        """Паттерн Tombstone: оповещаем сеть о своем закрытии"""
        if self.instance_id not in self.registry:
            return

        # Инкрементируем пульс, чтобы соседи точно приняли обновление, и ставим статус мертвеца
        self.registry[self.instance_id].heartbeat += 1
        self.registry[self.instance_id].status = "dead"
        self._notify_topology_change()

        payload_json = GossipPayload(
            sender_id=self.instance_id, registry=self.registry
        ).model_dump_json()

        headers = {
            "X-Mesh-Version": MESH_PROTOCOL_VERSION,
            "Content-Type": "application/json",
            API_KEY_HEADER_NAME: SECRET_TOKEN
        }

        # Рассылаем прощальное письмо всем известным узлам параллельно
        async with httpx.AsyncClient() as client:
            peers = [state.base_url for i_id, state in self.registry.items() if i_id != self.instance_id]
            tasks = [
                client.post(f"{url}/_gossip", content=payload_json, headers=headers, timeout=GOSSIP_LEAVE_TIMEOUT_SEC)
                for url in peers
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def _update_self_state(self, local_contract: Dict[str, Any]):
        is_new = self.instance_id not in self.registry
        if is_new:
            self.registry[self.instance_id] = InstanceState(
                base_url=self.base_url,
                contract=local_contract,
                heartbeat=0,
                local_updated_at=time.time()
            )
            self._notify_topology_change()
        else:
            self.registry[self.instance_id].contract = local_contract
            self.registry[self.instance_id].heartbeat += 1
            self.registry[self.instance_id].local_updated_at = time.time()

    async def bootstrap(self, get_local_contract_func):
        print(f"[{self.instance_id}] Bootstrapping (Mesh v{MESH_PROTOCOL_VERSION})...")
        headers = {
            "X-Mesh-Version": MESH_PROTOCOL_VERSION,
            "Content-Type": "application/json",
            API_KEY_HEADER_NAME: SECRET_TOKEN
        }

        # Обновляем наш локальный стейт перед тем, как стучаться к соседям
        self._update_self_state(get_local_contract_func())
        payload = GossipPayload(sender_id=self.instance_id, registry=self.registry)

        candidate_urls = [f"{peer}/_gossip" for peer in SEED_PEERS if peer != self.base_url]
        candidate_urls.extend(
            f"http://{DISCOVERY_HOST}:{port}/_gossip"
            for port in range(self.port_range[0], self.port_range[1])
            if f":{port}" not in self.base_url
        )

        for target_url in candidate_urls:
            try:
                # Стучимся сразу в системный эндпоинт, кидая о себе слух!
                response = await self.http_client.post(
                    target_url,
                    content=payload.model_dump_json(),
                    headers=headers,
                    timeout=GOSSIP_BOOTSTRAP_TIMEOUT_SEC
                )

                if response.status_code == 200:
                    print(f"[{self.instance_id}] Seed found at {target_url} and successfully joined!")
                    return
            except Exception:
                pass

    def merge_registry(self, incoming_registry: Dict[str, InstanceState]):
        current_time = time.time()
        topology_changed = False
        for i_id, inc_state in incoming_registry.items():
            if i_id in self.tombstones and current_time - self.tombstones[i_id] < GOSSIP_TOMBSTONE_TTL_SEC:
                continue
            if inc_state.status == "dead":
                self.tombstones[i_id] = current_time
                if i_id in self.registry and self.registry[i_id].status != "dead":
                    self.registry[i_id] = inc_state
                    self.registry[i_id].local_updated_at = current_time
                    topology_changed = True
                continue
            if i_id not in self.registry:
                inc_state.local_updated_at = current_time
                self.registry[i_id] = inc_state
                topology_changed = True
            elif inc_state.heartbeat > self.registry[i_id].heartbeat:
                prev_url = self.registry[i_id].base_url
                prev_status = self.registry[i_id].status
                self.registry[i_id] = inc_state
                self.registry[i_id].local_updated_at = current_time
                if inc_state.base_url != prev_url or inc_state.status != prev_status:
                    topology_changed = True
        if topology_changed:
            self._notify_topology_change()

    async def gossip_loop(self, get_local_contract_func):
        headers = {
            "X-Mesh-Version": MESH_PROTOCOL_VERSION,
            "Content-Type": "application/json",
            API_KEY_HEADER_NAME: SECRET_TOKEN
        }

        # Локальная функция для фоновой отправки без блокировки цикла
        def fire_and_forget(url: str, payload_str: str):
            async def _send():
                try:
                    await self.http_client.post(
                        url, content=payload_str, headers=headers, timeout=GOSSIP_FIRE_FORGET_TIMEOUT_SEC
                    )
                except Exception:
                    pass
            asyncio.create_task(_send())

        while True:
            await asyncio.sleep(GOSSIP_LOOP_SLEEP_SEC)
            try:
                self._update_self_state(get_local_contract_func())
            except Exception as e:
                print(f"[{self.instance_id}] Error generating contract: {e}")
                continue

            # Сериализуем payload один раз за цикл
            payload_json = GossipPayload(
                sender_id=self.instance_id, registry=self.registry
            ).model_dump_json()

            # --- ШАГ 1: Асинхронный поиск новых соседей ---
            time_alive = time.time() - self.started_at
            scan_chance = (
                GOSSIP_INITIAL_SCAN_CHANCE
                if time_alive < GOSSIP_INITIAL_SCAN_WINDOW_SEC
                else GOSSIP_STEADY_SCAN_CHANCE
            )

            if random.random() < scan_chance:
                known_urls = {state.base_url for state in self.registry.values()}
                for peer in SEED_PEERS:
                    if peer != self.base_url and peer not in known_urls:
                        fire_and_forget(f"{peer}/_gossip", payload_json)

                for port in range(self.port_range[0], self.port_range[1]):
                    if f":{port}" in self.base_url: continue
                    if any(f":{port}" in state.base_url for state in self.registry.values()):
                        continue

                    # Запускаем в фоне! Цикл больше не ждет таймаутов!
                    fire_and_forget(f"http://{DISCOVERY_HOST}:{port}/_gossip", payload_json)

            # --- ШАГ 2: Отправка сплетен случайному соседу ---
            peers = [i_id for i_id in self.registry.keys() if i_id != self.instance_id]
            if peers:
                target_id = random.choice(peers)
                target_url = f"{self.registry[target_id].base_url}/_gossip"
                fire_and_forget(target_url, payload_json)

    async def reaper_loop(self):
        while True:
            await asyncio.sleep(GOSSIP_REAPER_SLEEP_SEC)
            now = time.time()
            dead_nodes = []
            for i_id, state in self.registry.items():
                if i_id == self.instance_id: continue
                # Увеличиваем терпение до REAPER_PATIENCE секунд
                if now - state.local_updated_at > REAPER_PATIENCE:
                    dead_nodes.append(i_id)

            for d in dead_nodes:
                del self.registry[d]
            if dead_nodes:
                self._notify_topology_change()

