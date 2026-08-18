import asyncio
import time
import random
import httpx
from typing import Dict, Any, Optional
from pydantic import BaseModel



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

    def _update_self_state(self, local_contract: Dict[str, Any]):
        if self.instance_id not in self.registry:
            self.registry[self.instance_id] = InstanceState(
                base_url=self.base_url,
                contract=local_contract,
                heartbeat=0,
                local_updated_at=time.time()
            )
        else:
            self.registry[self.instance_id].contract = local_contract # Обновляем на случай изменения public_funcs
            self.registry[self.instance_id].heartbeat += 1
            self.registry[self.instance_id].local_updated_at = time.time()

    async def bootstrap(self):
        print(f"[{self.instance_id}] Bootstrapping...")
        for port in range(self.port_range[0], self.port_range[1]):
            if str(port) in self.base_url: continue
            try:
                response = await self.http_client.post(f"http://127.0.0.1:{port}/contract", timeout=0.5)
                if response.status_code == 200:
                    print(f"[{self.instance_id}] Seed found at port {port}")
                    return
            except:
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
        while True:
            await asyncio.sleep(1.5)
            self._update_self_state(get_local_contract_func())
            
            peers = [i_id for i_id in self.registry.keys() if i_id != self.instance_id]
            if not peers: continue
            
            target_id = random.choice(peers)
            target_url = f"{self.registry[target_id].base_url}/_gossip"
            payload = GossipPayload(sender_id=self.instance_id, registry=self.registry)
            try:
                await self.http_client.post(target_url, content=payload.model_dump_json())
            except:
                pass

    async def reaper_loop(self):
        while True:
            await asyncio.sleep(1.0)
            current_time = time.time()
            dead_nodes = [i_id for i_id, state in self.registry.items() 
                         if i_id != self.instance_id and (current_time - state.local_updated_at > 5.0)]
            for d_id in dead_nodes:
                del self.registry[d_id]
                self.tombstones[d_id] = current_time
