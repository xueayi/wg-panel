"""流量缓存与 agent 原子变更计划接口。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .deps import agent_call, current_reader, current_user, get_executor
from .peers import PeerCreate, PeerUpdate
from ..config import settings

router = APIRouter(prefix="/api", tags=["readonly"])


@router.get("/traffic")
def traffic(request: Request, window: int = 3600, scope: str = "all",
            _: str = Depends(current_reader)):
    try:
        return request.app.state.traffic_sampler.series(window, scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/state")
def state(request: Request, _: str = Depends(current_reader)):
    data = request.app.state.state_cache.snapshot()
    traffic_data = request.app.state.traffic_sampler.snapshot()
    rates = {r.get("public_key"): r for r in traffic_data.get("peers", [])}
    rows = data.get("peers", [])
    for row in rows:
        rate = rates.get(row.get("pubkey"), {})
        row.update(rx_rate=rate.get("rx_rate"), tx_rate=rate.get("tx_rate"))
    traffic_data["rx_rate"] = traffic_data.get("up_rate")
    traffic_data["tx_rate"] = traffic_data.get("down_rate")
    problems = settings.validate()
    if _ == "api-token":
        data["driver"] = {"driver": settings.driver, "iface": settings.iface}
        data.pop("error", None)
        problems = ["节点连接配置需要检查"] if problems else []
    totals = traffic_data.get("totals", {})
    return {**data, "interface": settings.iface, "peers": rows,
            "problems": problems,
            "stats": {"total": len(rows), "online": sum(r.get("state") == "在线" for r in rows),
                      "disabled": sum(bool(r.get("disabled")) for r in rows),
                      "rx": totals.get("rx", traffic_data.get("rx", 0)),
                      "tx": totals.get("tx", traffic_data.get("tx", 0))},
            "traffic": traffic_data}


@router.get("/plan")
def get_plan(request: Request, _: str = Depends(current_reader)):
    return agent_call(get_executor(), ["plan"], store=request.app.state.store)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(pattern="^(add_peer|update_peer|remove_peer)$")
    name: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_params(self):
        if self.operation == "remove_peer":
            if self.params:
                raise ValueError("删除操作不接受 params")
        else:
            model = PeerCreate if self.operation == "add_peer" else PeerUpdate
            values = {**self.params, "name": self.name} if self.operation == "add_peer" else self.params
            validated = model.model_validate(values)
            self.params = validated.model_dump(exclude_unset=True, exclude_none=True)
            self.params.pop("name", None)
        return self

    def agent_request(self):
        return json.dumps({"operation": self.operation, "name": self.name, "params": self.params},
                          ensure_ascii=False, sort_keys=True)


class ApplyRequest(PlanRequest):
    revision: str = Field(min_length=1, max_length=128)
    confirm: bool = False


@router.post("/plan")
def post_plan(body: PlanRequest, request: Request, _: str = Depends(current_user)):
    return agent_call(get_executor(), ["plan", "--request", body.agent_request()],
                      store=request.app.state.store)


@router.post("/apply")
def apply_plan(body: ApplyRequest, request: Request, _: str = Depends(current_user)):
    if body.operation == "remove_peer" and not body.confirm:
        raise HTTPException(status_code=428, detail="删除操作需要 confirm=true")
    args = ["apply", "--request", body.agent_request(), "--revision", body.revision]
    if body.confirm:
        args.append("--confirm")
    out = agent_call(get_executor(), args, action=f"plan.{body.operation}", target=body.name,
                     confirmed=body.confirm, store=request.app.state.store)
    request.app.state.state_cache.refresh(get_executor())
    request.app.state.store.audit(f"plan.{body.operation}", body.name, "ok", "通过变更计划应用")
    return {"applied": True, "operation": body.operation, "name": body.name, "result": out}
