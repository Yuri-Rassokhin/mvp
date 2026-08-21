import asyncio
import time
import random
import httpx
from typing import Dict, Any, Optional
from pydantic import BaseModel

### SETTINGS ###
REAPER_PATIENCE = 30.0 # How long the Reaper will wait before killing inactive nodes


MESH_PROTOCOL_VERSION = "1.0"

class InstanceState(BaseModel):
    base_url: str
    contract: Dict[str, Any]
    heartbeat: int
    local_updated_at: float

class GossipPayload(BaseModel):
    sender_id: str
    registry: Dict[str, InstanceState]

class GossipMesh:
    def __init__(self, instance_id: str, base_url: str, port_range: tuple = (8500, 8550)):
        self.instance_id = instance_id
        self.base_url = base_url
        self.port_range = port_range
        self.registry: Dict[str, InstanceState] = {}
        self.tombstones: Dict[str, float] = {}
        self.http_client = httpx.AsyncClient(timeout=2.0)
        self.started_at = time.time()

    def _update_self_state(self, local_contract: Dict[str, Any]):
        if self.instance_id not in self.registry:
            self.registry[self.instance_id] = InstanceState(
                base_url=self.base_url,
                contract=local_contract,
                heartbeat=0,
                local_updated_at=time.time()
            )
        else:
            self.registry[self.instance_id].contract = local_contract
            self.registry[self.instance_id].heartbeat += 1
            self.registry[self.instance_id].local_updated_at = time.time()

    async def bootstrap(self, get_local_contract_func):
        print(f"[{self.instance_id}] Bootstrapping (Mesh v{MESH_PROTOCOL_VERSION})...")
        headers = {
                "X-Mesh-Version": MESH_PROTOCOL_VERSION,
                "Content-Type": "application/json"
                }
        
        # Обновляем наш локальный стейт перед тем, как стучаться к соседям
        self._update_self_state(get_local_contract_func())
        payload = GossipPayload(sender_id=self.instance_id, registry=self.registry)
        
        for port in range(self.port_range[0], self.port_range[1]):
            if str(port) in self.base_url: continue
            try:
                # Стучимся сразу в системный эндпоинт, кидая о себе слух!
                response = await self.http_client.post(
                    f"http://127.0.0.1:{port}/_gossip",
                    content=payload.model_dump_json(),
                    headers=headers,
                    timeout=2.0
                )
                
                if response.status_code == 200:
                    print(f"[{self.instance_id}] Seed found at port {port} and successfully joined!")
                    return
            except Exception as e:
                pass



    def merge_registry(self, incoming_registry: Dict[str, InstanceState]):
        current_time = time.time()
        for i_id, inc_state in incoming_registry.items():
            if i_id in self.tombstones and current_time - self.tombstones[i_id] < 60:
                continue
            if i_id not in self.registry:
                inc_state.local_updated_at = current_time
                self.registry[i_id] = inc_state
            elif inc_state.heartbeat > self.registry[i_id].heartbeat:
                self.registry[i_id] = inc_state
                self.registry[i_id].local_updated_at = current_time



    async def gossip_loop(self, get_local_contract_func):
        headers = {
            "X-Mesh-Version": MESH_PROTOCOL_VERSION,
            "Content-Type": "application/json"
        }

        # Локальная функция для фоновой отправки без блокировки цикла
        def fire_and_forget(url: str, payload_str: str):
            async def _send():
                try:
                    await self.http_client.post(
                        url, content=payload_str, headers=headers, timeout=1.0
                    )
                except Exception:
                    pass
            asyncio.create_task(_send())

        while True:
            await asyncio.sleep(1.5)
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
            scan_chance = 1.0 if time_alive < 30.0 else 0.1
            
            if random.random() < scan_chance:
                for port in range(self.port_range[0], self.port_range[1]):
                    if str(port) in self.base_url: continue
                    if any(str(port) in state.base_url for state in self.registry.values()):
                        continue

                    # Запускаем в фоне! Цикл больше не ждет таймаутов!
                    fire_and_forget(f"http://127.0.0.1:{port}/_gossip", payload_json)

            # --- ШАГ 2: Отправка сплетен случайному соседу ---
            peers = [i_id for i_id in self.registry.keys() if i_id != self.instance_id]
            if peers:
                target_id = random.choice(peers)
                target_url = f"{self.registry[target_id].base_url}/_gossip"
                fire_and_forget(target_url, payload_json)



    async def reaper_loop(self):
        while True:
            await asyncio.sleep(5.0)
            now = time.time()
            dead_nodes = []
            for i_id, state in self.registry.items():
                if i_id == self.instance_id: continue
                # Увеличиваем терпение до 30 секунд
                if now - state.local_updated_at > REAPER_PATIENCE:
                    dead_nodes.append(i_id)
            
            for d in dead_nodes:
                del self.registry[d]

