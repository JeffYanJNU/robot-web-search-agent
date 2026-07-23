from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.config import Settings
from app.database import SessionLocal
from app.models import ProductCompanyRelation, RobotCompany, RobotProduct
from app.schemas import RunResult
from app.services.pipeline import CompanyDiscoveryPipeline, PipelineController
from app.services.product_pipeline import ProductDiscoveryPipeline
from app.services.result_exporter import export_run_results


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ACTION_LABELS = {
    "starting": "正在准备任务",
    "searching": "正在搜索",
    "search_complete": "搜索完成",
    "fetching": "正在抓取网页",
    "extracting": "正在抽取企业信息",
    "searching_product": "正在搜索机器人新产品",
    "extracting_product": "正在抽取产品与企业关系",
    "verifying_product": "正在核验产品真实性与企业关系",
    "saving": "正在核验并入库",
    "skipped": "跳过重复或无关结果",
    "error": "发生错误，继续下一项",
    "completed": "任务已完成",
    "paused": "任务已暂停",
    "resumed": "任务已继续",
    "cancelled": "任务已停止",
}


class RunManager(PipelineController):
    """Own one discovery job and expose a thread-safe live snapshot."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._pause = Event()
        self._cancel = Event()
        self._thread: Thread | None = None
        self._state: dict[str, Any] = self._empty_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "run_id": None,
            "pipeline_mode": "product",
            "status": "idle",
            "current_action": "尚未启动任务",
            "current_query": "",
            "current_url": "",
            "query_index": 0,
            "max_queries": 0,
            "result": RunResult().model_dump(),
            "logs": deque(maxlen=100),
            "analysis": None,
            "consecutive_model_502": 0,
            "auto_pause_reason": "",
            "started_at": None,
            "updated_at": None,
            "finished_at": None,
        }

    def start(
        self,
        settings: Settings,
        lookback_days: int,
        max_queries: int,
        pipeline_mode: str = "product",
    ) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] in {"running", "pausing", "paused"}:
                raise RuntimeError("已有采集任务正在运行")
            self._pause.clear()
            self._cancel.clear()
            self._state = self._empty_state()
            self._state.update(
                run_id=uuid4().hex,
                pipeline_mode=pipeline_mode,
                status="running",
                current_action=ACTION_LABELS["starting"],
                max_queries=max_queries,
                started_at=utc_iso(),
                updated_at=utc_iso(),
            )
            self._append_log_locked("任务已启动")
            self._thread = Thread(
                target=self._worker,
                args=(settings, lookback_days, max_queries, pipeline_mode),
                name=f"{pipeline_mode}-discovery-run",
                daemon=True,
            )
            self._thread.start()
            return self._snapshot_locked()

    def _worker(
        self,
        settings: Settings,
        lookback_days: int,
        max_queries: int,
        pipeline_mode: str,
    ) -> None:
        try:
            with SessionLocal() as db:
                pipeline = (
                    ProductDiscoveryPipeline(settings)
                    if pipeline_mode == "product"
                    else CompanyDiscoveryPipeline(settings)
                )
                result = pipeline.run(
                    db, lookback_days, max_queries, controller=self
                )
                if not self._cancel.is_set():
                    output_path = export_run_results(
                        db,
                        result,
                        pipeline_mode=pipeline_mode,
                        lookback_days=lookback_days,
                        output_dir=settings.output_dir,
                        run_id=str(self._state.get("run_id") or ""),
                        inventory_workbook_path=settings.product_inventory_workbook_path,
                    )
                    result.output_file = str(output_path)
                    result.output_filename = output_path.name
            with self._lock:
                status = "cancelled" if self._cancel.is_set() else "completed"
                self._state["status"] = status
                self._state["current_action"] = ACTION_LABELS[status]
                self._state["result"] = result.model_dump()
                self._state["finished_at"] = utc_iso()
                self._state["updated_at"] = utc_iso()
                self._append_log_locked(self._state["current_action"])
                if result.output_filename:
                    self._append_log_locked(
                        f"结果已导出：{result.output_filename}"
                    )
        except Exception as exc:
            with self._lock:
                self._state["status"] = "failed"
                self._state["current_action"] = "任务异常终止"
                self._state["finished_at"] = utc_iso()
                self._state["updated_at"] = utc_iso()
                self._append_log_locked(f"任务异常终止：{exc}")

    def checkpoint(self) -> bool:
        if self._cancel.is_set():
            return False
        announced = False
        while self._pause.is_set():
            if not announced:
                with self._lock:
                    self._state["status"] = "paused"
                    self._state["current_action"] = ACTION_LABELS["paused"]
                    self._state["updated_at"] = utc_iso()
                    self._append_log_locked("已到达安全检查点，采集暂停")
                with SessionLocal() as db:
                    self.set_analysis(build_current_analysis(db, self.snapshot()))
                announced = True
            if self._cancel.wait(0.25):
                return False
        if announced:
            with self._lock:
                self._state["status"] = "running"
                self._state["current_action"] = ACTION_LABELS["resumed"]
                self._state["updated_at"] = utc_iso()
                self._append_log_locked("继续采集")
        return not self._cancel.is_set()

    def update(self, event: str, **data: Any) -> None:
        with self._lock:
            if event in ACTION_LABELS:
                self._state["current_action"] = ACTION_LABELS[event]
            for key in ("current_query", "current_url", "query_index"):
                if key in data:
                    self._state[key] = data[key]
            if "result" in data:
                result = data["result"]
                self._state["result"] = result.model_dump() if isinstance(result, RunResult) else result
            if data.get("message"):
                self._append_log_locked(str(data["message"]))
            self._state["updated_at"] = utc_iso()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] != "running":
                raise RuntimeError("当前任务不可暂停")
            self._pause.set()
            self._state["status"] = "pausing"
            self._state["current_action"] = "正在等待安全检查点"
            self._state["updated_at"] = utc_iso()
            self._append_log_locked("收到暂停请求")
            return self._snapshot_locked()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] not in {"paused", "pausing"}:
                raise RuntimeError("当前任务未暂停")
            self._pause.clear()
            self._state["consecutive_model_502"] = 0
            self._state["auto_pause_reason"] = ""
            self._state["status"] = "running"
            self._state["current_action"] = ACTION_LABELS["resumed"]
            self._state["updated_at"] = utc_iso()
            self._append_log_locked("收到继续请求")
            return self._snapshot_locked()

    def model_call_succeeded(self) -> None:
        with self._lock:
            self._state["consecutive_model_502"] = 0

    def model_call_failed(self, status_code: int | None) -> bool:
        """Track consecutive model 502 responses and request a safe automatic pause."""
        with self._lock:
            if status_code != 502:
                self._state["consecutive_model_502"] = 0
                return False
            failures = int(self._state.get("consecutive_model_502", 0)) + 1
            self._state["consecutive_model_502"] = failures
            self._state["updated_at"] = utc_iso()
            self._append_log_locked(f"模型 API 连续返回 502（{failures}/3）")
            if failures < 3 or self._state["status"] not in {"running", "pausing"}:
                return False
            reason = "模型 API 连续 3 次返回 502，任务已自动暂停"
            self._pause.set()
            self._state["status"] = "pausing"
            self._state["current_action"] = "模型异常，正在自动暂停"
            self._state["auto_pause_reason"] = reason
            self._append_log_locked(reason)
            return True

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] not in {"running", "pausing", "paused"}:
                raise RuntimeError("当前没有可停止的任务")
            self._cancel.set()
            self._pause.clear()
            self._state["current_action"] = "正在安全停止"
            self._state["updated_at"] = utc_iso()
            self._append_log_locked("收到停止请求")
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] in {"running", "pausing", "paused"}:
                raise RuntimeError("采集任务运行中，不能重置任务状态")
            self._pause.clear()
            self._cancel.clear()
            self._state = self._empty_state()
            return self._snapshot_locked()

    def set_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._state["analysis"] = analysis
            self._state["updated_at"] = utc_iso()
            self._append_log_locked("已根据当前入库信息生成阶段分析")
            return self._snapshot_locked()

    def _append_log_locked(self, message: str) -> None:
        self._state["logs"].append({"time": utc_iso(), "message": message})

    def _snapshot_locked(self) -> dict[str, Any]:
        result = dict(self._state)
        result["logs"] = list(self._state["logs"])
        result["result"] = dict(self._state["result"])
        return result


def build_current_analysis(db, state: dict[str, Any]) -> dict[str, Any]:
    product_by_status = dict(
        db.execute(
            select(RobotProduct.verification_status, func.count()).group_by(
                RobotProduct.verification_status
            )
        ).all()
    )
    product_by_addition_type = dict(
        db.execute(
            select(RobotProduct.addition_type, func.count()).group_by(
                RobotProduct.addition_type
            )
        ).all()
    )
    by_status = dict(
        db.execute(
            select(RobotCompany.verification_status, func.count()).group_by(
                RobotCompany.verification_status
            )
        ).all()
    )
    by_region = dict(
        db.execute(
            select(RobotCompany.region_type, func.count()).group_by(RobotCompany.region_type)
        ).all()
    )
    by_addition_type = dict(
        db.execute(
            select(RobotCompany.addition_type, func.count()).group_by(RobotCompany.addition_type)
        ).all()
    )
    top_companies = list(
        db.scalars(
            select(RobotCompany)
            .order_by(RobotCompany.priority_score.desc(), RobotCompany.created_at.desc())
            .limit(8)
        )
    )
    result = state.get("result", {})
    errors = result.get("errors", [])
    observations: list[str] = []
    if result.get("created", 0):
        unit = "个候选产品" if state.get("pipeline_mode") == "product" else "家候选企业"
        observations.append(f"本轮已新增 {result['created']} {unit}。")
    if result.get("updated", 0):
        observations.append(f"已有 {result['updated']} 家企业获得新证据。")
    if result.get("rejected", 0):
        observations.append(f"相关性或优先级不足，已排除 {result['rejected']} 个候选。")
    if errors:
        observations.append(f"当前有 {len(errors)} 个失败项，建议关注网络、反爬和模型字段质量。")
    if result.get("database_duplicates", 0):
        observations.append(
            f"有 {result['database_duplicates']} 个候选与当前数据库名称相似度达到阈值，已转入重复候选表。"
        )
    if not observations:
        observations.append("当前样本仍较少，继续采集后再判断区域和赛道趋势。")
    return {
        "generated_at": utc_iso(),
        "headline": (
            f"当前库中共 {sum(product_by_status.values())} 个机器人产品"
            if state.get("pipeline_mode") == "product"
            else f"当前库中共 {sum(by_status.values())} 家重点机器人企业"
        ),
        "product_by_status": product_by_status,
        "product_by_addition_type": product_by_addition_type,
        "relations": db.scalar(
            select(func.count()).select_from(ProductCompanyRelation)
        ) or 0,
        "by_status": by_status,
        "by_region": by_region,
        "by_addition_type": by_addition_type,
        "observations": observations,
        "top_companies": [
            {
                "company_id": item.company_id,
                "name": item.canonical_name,
                "country": item.country,
                "priority_score": item.priority_score,
                "status": item.verification_status,
                "addition_type": item.addition_type,
            }
            for item in top_companies
        ],
    }
