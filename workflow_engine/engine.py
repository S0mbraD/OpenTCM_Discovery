# -*- coding: utf-8 -*-
"""
L3 工作流引擎：DAG 调度 + 状态持久化 + 循证溯源 + 事件总线
可复用核心：与具体工作流无关，任何 Step 均可注册执行
"""
import json
import os
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any


# ---------------- 循证溯源模型 ----------------

@dataclass
class EvidenceSource:
    kind: str            # file | db | api | computed
    ref: str             # 精确定位（文件#Sheet!Record 或 API 端点）
    detail: str = ""     # 人类可读说明（DOI/文献/行数等）
    url: str = ""        # 可点开核对的链接
    ts: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self):
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail,
                "url": self.url, "ts": self.ts}


@dataclass
class Evidence:
    claim: str                                  # 断言内容
    sources: List[EvidenceSource] = field(default_factory=list)
    transform: str = ""                         # 处理逻辑说明
    step: str = ""                              # 产生该证据的步骤

    def to_dict(self):
        return {"claim": self.claim, "sources": [s.to_dict() for s in self.sources],
                "transform": self.transform, "step": self.step, "ts":
                datetime.now().isoformat(timespec="seconds")}


class EvidenceLog:
    """全局循证日志（线程安全）"""

    def __init__(self):
        self._items: List[dict] = []
        self._lock = threading.Lock()

    def add(self, claim: str, sources: List[EvidenceSource], transform: str = "", step: str = ""):
        ev = Evidence(claim, sources, transform, step)
        with self._lock:
            self._items.append(ev.to_dict())

    def add_dict(self, d: dict):
        with self._lock:
            self._items.append(d)

    def to_list(self):
        with self._lock:
            return list(self._items)

    def __len__(self):
        with self._lock:
            return len(self._items)


# ---------------- Step 定义 ----------------

@dataclass
class StepDef:
    name: str
    func: Callable
    deps: List[str]
    domain: str           # 领域分组（数据加载/标准化/筛选评分/外部API/输出）
    desc: str             # 中文描述（可视化展示）


@dataclass
class StepResult:
    ok: bool
    outputs: dict = field(default_factory=dict)    # 计数/摘要（可视化展示）
    artifacts: List[str] = field(default_factory=list)  # 产物文件路径
    metrics: dict = field(default_factory=dict)    # 指标
    error: str = ""


# ---------------- 上下文 ----------------

class WorkflowContext:
    """步骤执行上下文：数据注册表 + 参数 + 循证 + 进度上报 + 日志

    线程安全说明：
    - data 注册表：单键赋值原子（CPython GIL），不同步骤写不同键，无锁安全
    - _step_name：并行步骤共用 ctx 时用 threading.local 隔离，避免相互覆盖
    """

    def __init__(self, run_id: str, params: dict, evidence: EvidenceLog,
                 artifacts_dir: str, engine):
        self.run_id = run_id
        self.params = params                  # config 传入
        self.evidence = evidence              # 循证日志
        self.artifacts_dir = artifacts_dir    # 产物目录（本 run）
        self.engine = engine
        self.data: Dict[str, Any] = {}        # 内存数据注册表（DataFrame/集合等）
        self._tl = threading.local()          # 线程本地：当前步骤名（progress 上报用）

    def set_current_step(self, name):
        self._tl.step_name = name

    @property
    def current_step(self):
        return getattr(self._tl, "step_name", "")

    def report_progress(self, progress: float, message: str = "",
                        detail: Optional[dict] = None):
        """步骤内进度上报（0-1）+ 实时详情结构（驱动可视化实时刷新）

        detail 示例：{"matched": 100, "total": 1638, "speed": "12.3/s"}
        前端在详情面板实时渲染这些键值对。
        """
        if self.engine:
            self.engine.update_step_progress(self.current_step, progress, message,
                                             detail)

    def log(self, message: str, level: str = "info"):
        """步骤内日志上报：实时推送 step_log 事件（前端日志控制台渲染）

        level: info | success | warn | error
        """
        if self.engine:
            self.engine.push_step_log(self.current_step, message, level)

    def artifact_path(self, filename: str) -> str:
        os.makedirs(self.artifacts_dir, exist_ok=True)
        return os.path.join(self.artifacts_dir, filename)


# ---------------- 事件总线 ----------------

class EventBus:
    """发布/订阅：SSE 与状态查询共用"""

    def __init__(self):
        self._subscribers: List[Callable[[dict], None]] = []
        self._lock = threading.Lock()
        self.history: List[dict] = []

    def subscribe(self, cb):
        with self._lock:
            self._subscribers.append(cb)

    def unsubscribe(self, cb):
        with self._lock:
            if cb in self._subscribers:
                self._subscribers.remove(cb)

    def publish(self, event: dict):
        event["ts"] = datetime.now().isoformat(timespec="milliseconds")
        with self._lock:
            self.history.append(event)
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass


# ---------------- 工作流引擎 ----------------

class WorkflowEngine:
    """
    DAG 调度器：
    - register(step) 注册步骤
    - run() 阻塞执行（内部线程）
    - 状态机：pending -> running -> success | failed；依赖失败 -> skipped
    - 状态持久化（state.json）+ 事件实时发布
    """

    def __init__(self, name: str, params: dict, artifacts_root: str):
        self.name = name
        self.params = params
        self.artifacts_root = artifacts_root
        self.steps: Dict[str, StepDef] = {}
        self.step_order: List[str] = []            # 注册顺序（保持稳定展示）
        self.bus = EventBus()
        self.evidence = EvidenceLog()
        self.state: dict = self._init_state()
        self.step_logs: Dict[str, deque] = {}      # 每步滚动日志缓冲（maxlen 300）
        self._state_lock = threading.Lock()
        self._run_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._shared_ctx: Optional[WorkflowContext] = None   # 共享上下文（run 后可用）

    @property
    def data(self) -> Dict[str, Any]:
        """数据注册表（服务层只读访问；未运行时为空 dict）"""
        return self._shared_ctx.data if self._shared_ctx else {}

    # ----- 状态结构 -----

    def _init_state(self) -> dict:
        return {
            "workflow": self.name,
            "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "status": "idle",       # idle|running|success|failed|partial
            "started_at": None,
            "ended_at": None,
            "steps": {},            # name -> step_state
            "metrics": {},
            "evidence_count": 0,
            "artifacts": [],
        }

    def _step_state(self, sd: StepDef) -> dict:
        return {
            "name": sd.name, "domain": sd.domain, "desc": sd.desc,
            "deps": list(sd.deps), "status": "pending",
            "started_at": None, "ended_at": None, "duration_ms": None,
            "progress": 0, "progress_msg": "", "progress_detail": {},
            "outputs": {}, "metrics": {}, "artifacts": [], "error": "",
        }

    # ----- 注册 -----

    def register(self, name: str, func: Callable, deps: Optional[List[str]] = None,
                 domain: str = "通用", desc: str = ""):
        if name in self.steps:
            raise ValueError(f"步骤重名: {name}")
        for d in (deps or []):
            if d not in self.steps:
                raise ValueError(f"步骤 {name} 的依赖 {d} 未注册（需先注册依赖）")
        sd = StepDef(name, func, list(deps or []), domain, desc)
        self.steps[name] = sd
        self.step_order.append(name)
        self.state["steps"][name] = self._step_state(sd)
        return sd

    # ----- 拓扑排序（Kahn）-----

    def topo_order(self) -> List[str]:
        indeg = {n: len(self.steps[n].deps) for n in self.steps}
        children: Dict[str, List[str]] = {n: [] for n in self.steps}
        for n, sd in self.steps.items():
            for d in sd.deps:
                children[d].append(n)
        q = deque([n for n in self.step_order if indeg[n] == 0])
        order = []
        while q:
            n = q.popleft()
            order.append(n)
            for c in children[n]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    q.append(c)
        if len(order) != len(self.steps):
            raise ValueError("DAG 存在环或缺失依赖")
        return order

    # ----- 状态更新 -----

    def _update(self, fn: Callable[[dict], None]):
        with self._state_lock:
            fn(self.state)

    def update_step_progress(self, name: str, progress: float, message: str = "",
                             detail: Optional[dict] = None):
        def _f(s):
            st = s["steps"].get(name)
            if st:
                st["progress"] = max(0.0, min(1.0, progress))
                if message:
                    st["progress_msg"] = message
                if detail is not None:
                    st["progress_detail"] = detail
        self._update(_f)
        self.bus.publish({"type": "progress", "step": name,
                          "progress": progress, "message": message,
                          "detail": detail or {}})

    def push_step_log(self, name: str, message: str, level: str = "info"):
        """步骤日志：缓存（滚动窗口）+ 事件推送"""
        entry = {"step": name, "level": level, "message": str(message)[:500],
                 "ts": datetime.now().isoformat(timespec="milliseconds")}
        with self._state_lock:
            dq = self.step_logs.setdefault(name, deque(maxlen=300))
            dq.append(entry)
        self.bus.publish({"type": "step_log", **entry})

    def step_logs_snapshot(self, name: str = None) -> List[dict]:
        with self._state_lock:
            if name:
                return list(self.step_logs.get(name, []))
            out = []
            for dq in self.step_logs.values():
                out.extend(dq)
            out.sort(key=lambda x: x["ts"])
            return out

    def _finish_step(self, name: str, status: str, result: Optional[StepResult] = None,
                     error: str = ""):
        def _f(s):
            st = s["steps"][name]
            st["status"] = status
            st["ended_at"] = datetime.now().isoformat(timespec="seconds")
            t0 = st.get("_t0")
            st["duration_ms"] = int((time.time() - t0) * 1000) if t0 else None
            st.pop("_t0", None)
            if result:
                st["outputs"] = result.outputs
                st["metrics"] = result.metrics
                st["artifacts"] = result.artifacts
                for a in result.artifacts:
                    if a not in s["artifacts"]:
                        s["artifacts"].append(a)
            if error:
                st["error"] = error
            s["evidence_count"] = len(self.evidence)
        self._update(_f)
        self.bus.publish({"type": "step_end", "step": name, "status": status,
                          "outputs": (result.outputs if result else {}),
                          "error": error})

    def _start_step(self, name: str):
        def _f(s):
            st = s["steps"][name]
            st["status"] = "running"
            st["started_at"] = datetime.now().isoformat(timespec="seconds")
            st["_t0"] = time.time()
        self._update(_f)
        self.bus.publish({"type": "step_start", "step": name})

    # ----- 执行 -----

    def run(self, background: bool = False) -> Optional[threading.Thread]:
        """启动工作流；background=True 时后台线程执行"""
        if self.state["status"] == "running":
            raise RuntimeError("工作流已在运行")
        # 重置运行态
        self._update(lambda s: s.update(
            status="running",
            started_at=datetime.now().isoformat(timespec="seconds"),
            ended_at=None, metrics={}, evidence_count=0, artifacts=[],
        ))
        with self._state_lock:
            self.step_logs = {}   # 清空上轮日志
        for name in self.steps:
            self._update(lambda s, n=name: s["steps"].update(
                {n: {**self._step_state(self.steps[n]), "status": "pending"}}))
        self.bus.publish({"type": "run_start", "run_id": self.state["run_id"],
                          "steps": [{"name": n, "domain": self.steps[n].domain,
                                     "desc": self.steps[n].desc,
                                     "deps": list(self.steps[n].deps)}
                                    for n in self.step_order]})

        t = threading.Thread(target=self._execute, daemon=True)
        self._run_thread = t
        t.start()
        if not background:
            t.join()
        return t

    def _execute(self):
        order = self.topo_order()
        status_map = {n: "pending" for n in order}
        children: Dict[str, List[str]] = {n: [] for n in order}
        for n in order:
            for d in self.steps[n].deps:
                children[d].append(n)
        pending = set(order)
        running: Dict[str, threading.Thread] = {}
        threads_available = self.params.get("engine", {}).get("max_parallel", 3)
        # 关键：所有步骤共用同一个 context，data 注册表跨步骤共享
        shared_ctx = WorkflowContext(self.state["run_id"], self.params, self.evidence,
                                     self._run_artifacts_dir(), self)
        self._shared_ctx = shared_ctx

        def ready(n):
            return all(status_map[d] == "success" for d in self.steps[n].deps)

        def launch(n):
            status_map[n] = "running"
            pending.discard(n)
            ctx = shared_ctx
            sd = self.steps[n]

            def worker():
                # 关键：在线程内部设置 thread-local 步骤名，
                # 否则调度线程设置的值对 worker 线程不可见（current_step 恒为空）
                ctx.set_current_step(n)
                self._start_step(n)
                try:
                    result = sd.func(ctx)
                    if result is None:
                        result = StepResult(ok=True)
                    if result.ok:
                        status_map[n] = "success"
                        self._finish_step(n, "success", result)
                    else:
                        status_map[n] = "failed"
                        self._finish_step(n, "failed", result, result.error or "步骤返回失败")
                except Exception as e:
                    status_map[n] = "failed"
                    self._finish_step(n, "failed", None,
                                      f"{e}\n{traceback.format_exc(limit=3)}")

            th = threading.Thread(target=worker, daemon=True)
            running[n] = th
            th.start()

        try:
            while pending or running:
                if self._stop_flag.is_set():
                    break
                # 启动就绪步骤
                for n in list(pending):
                    if ready(n):
                        if len(running) < threads_available:
                            launch(n)
                        else:
                            break
                # 标记被跳过（依赖失败）
                for n in list(pending):
                    if any(status_map[d] in ("failed", "skipped") for d in self.steps[n].deps):
                        status_map[n] = "skipped"
                        pending.discard(n)
                        self._finish_step(n, "skipped")
                # 等待任一线程结束
                if running:
                    done = [n for n, th in running.items() if not th.is_alive()]
                    for n in done:
                        running.pop(n)
                    if not running and not any(ready(n) for n in pending) and pending:
                        # 无可推进：全部失败路径
                        for n in list(pending):
                            status_map[n] = "skipped"
                            pending.discard(n)
                            self._finish_step(n, "skipped")
                        break
                    time.sleep(0.08)
                else:
                    if not any(ready(n) for n in pending):
                        for n in list(pending):
                            status_map[n] = "skipped"
                            pending.discard(n)
                            self._finish_step(n, "skipped")
                        break
                    time.sleep(0.05)

            # 汇总
            statuses = [s["status"] for s in self.state["steps"].values()]
            overall = "success" if all(x == "success" for x in statuses) else (
                "failed" if all(x in ("failed", "skipped") for x in statuses) else "partial")
            self._update(lambda s: s.update(
                status=overall, ended_at=datetime.now().isoformat(timespec="seconds"),
                evidence_count=len(self.evidence)))
            self._persist()
            self.bus.publish({"type": "run_end", "status": overall})
        except Exception as e:
            self._update(lambda s: s.update(status="failed",
                         ended_at=datetime.now().isoformat(timespec="seconds")))
            self.bus.publish({"type": "run_end", "status": "failed", "error": str(e)})

    def _run_artifacts_dir(self) -> str:
        d = os.path.join(self.artifacts_root, self.state["run_id"])
        os.makedirs(d, exist_ok=True)
        return d

    # ----- 持久化 -----

    def _persist(self):
        try:
            d = self._run_artifacts_dir()
            with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=1)
            with open(os.path.join(d, "evidence.json"), "w", encoding="utf-8") as f:
                json.dump(self.evidence.to_list(), f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def snapshot(self) -> dict:
        with self._state_lock:
            import copy
            st = copy.deepcopy(self.state)
            # 清理内部字段
            for v in st["steps"].values():
                v.pop("_t0", None)
            return st

    def evidence_snapshot(self) -> List[dict]:
        return self.evidence.to_list()

    def stop(self):
        self._stop_flag.set()
