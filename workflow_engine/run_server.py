# -*- coding: utf-8 -*-
"""
L4 服务层：WorkflowHTTPServer
- GET  /               可视化页面
- GET  /api/state      工作流全状态（JSON）
- GET  /api/evidence   循证溯源日志（JSON）
- GET  /api/events     SSE 实时事件流
- GET  /api/metrics    全局指标
- GET  /api/artifacts  产物文件列表（JSON）
- GET  /download?path= 下载产物文件
- GET  /api/top        Top N 评分结果
- POST /api/run        启动工作流
- POST /api/reset      重置（新 run_id）
启动：python run_server.py [port]
"""
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import yaml

from engine import WorkflowEngine
import steps_uc_screening as steps
import tools as agent_tools
from agent import AgentSession, PROVIDERS

# 冻结感知路径：PyInstaller 打包后，资源与数据放在 exe 同级目录
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.yaml")
DASHBOARD = os.path.join(BASE, "dashboard.html")
ARTIFACTS_ROOT = os.path.join(BASE, "artifacts")
PROVIDERS_PATH = os.path.join(BASE, "llm_providers.json")


def load_dashboard_html() -> bytes:
    """读取可视化页面；打包模式下优先 exe 旁，其次 sys._MEIPASS 内置副本"""
    candidates = [DASHBOARD]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "dashboard.html"))
    for p in candidates:
        if os.path.exists(p):
            with open(p, "rb") as f:
                return f.read()
    return ("<h1>dashboard.html 未找到</h1><p>请确保 dashboard.html 与程序在同一目录。</p>"
            ).encode("utf-8")


HISTORY_PATH = os.path.join(BASE, "chat_history.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def build_engine(cfg):
    engine = WorkflowEngine(
        name=cfg.get("workflow", {}).get("name", "UC临床锚定靶点法小分子筛选"),
        params=cfg, artifacts_root=ARTIFACTS_ROOT)
    steps.build_workflow(engine)
    return engine


class DashboardServer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine = build_engine(cfg)
        self.sse_queues: list = []          # 每个 SSE 客户端一个队列
        self.sse_lock = threading.Lock()
        self.port = cfg.get("server", {}).get("port", 8765)
        self.agent_session: AgentSession | None = None
        self.llm_cfg = self._load_llm_cfg()
        agent_params = {**cfg.get("agent", {})}
        agent_params.pop("llm_timeout", None)  # 兼容旧配置键名，避免歧义可另行映射
        self.agent_params = agent_params

        # 引擎事件 → SSE 分发
        self.engine.bus.subscribe(self._on_event)
        # 注入工具回调（AI Agent 可控工作流数据）
        self._inject_tool_callbacks()

    # ---------- LLM 配置 ----------

    def _load_llm_cfg(self):
        if os.path.exists(PROVIDERS_PATH):
            try:
                with open(PROVIDERS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"provider": "", "base_url": "", "api_key": "", "model": ""}

    def save_llm_cfg(self, d: dict):
        self.llm_cfg = {**self.llm_cfg, **d}
        with open(PROVIDERS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.llm_cfg, f, ensure_ascii=False, indent=2)
        self.agent_session = None    # 配置变更，重建会话
        return self.llm_cfg

    def _emit(self, event: dict):
        """Agent 事件 → SSE"""
        self._on_event(event)

    def _inject_tool_callbacks(self):
        """把引擎数据能力注入工具层"""
        app = self

        def cb_state():
            snap = app.engine.snapshot()
            return {"status": snap["status"], "run_id": snap["run_id"],
                    "steps": {n: {"status": v["status"], "duration_ms": v["duration_ms"]}
                              for n, v in snap["steps"].items()},
                    "evidence_count": snap["evidence_count"],
                    "artifacts": snap.get("artifacts", [])}

        def cb_results(n):
            scored = app.engine.data.get("scored")
            if scored is None:
                return {"ok": False, "msg": "评分未完成，请先运行工作流"}
            cols = ["排名", "中文名", "英文名", "总分", "靶点覆盖分", "ADME分",
                    "命中靶点数", "锚定靶点命中数", "文献靶点命中数", "OB", "DL"]
            return {"ok": True, "total": len(scored),
                    "rows": json.loads(scored.head(n)[cols].to_json(orient="records"))}

        def cb_targets(gene, layer):
            uni = app.engine.data.get("unified_targets")
            if uni is None:
                return {"ok": False, "msg": "靶点集未构建，请先运行工作流"}
            d = uni.copy()
            if gene:
                d = d[d["Gene"].str.upper() == gene.upper()]
            if layer:
                d = d[d["Layer"] == layer]
            return {"ok": True, "total": len(d),
                    "rows": json.loads(d.head(60).to_json(orient="records"))}

        def cb_detail(compound):
            scored = app.engine.data.get("scored")
            if scored is None:
                return {"ok": False, "msg": "评分未完成"}
            row = scored[scored["英文名"].astype(str).str.lower() == compound.lower()]
            if not len(row):
                return {"ok": False, "msg": f"未找到成分 {compound}"}
            r = row.iloc[0]
            uni = app.engine.data.get("unified_targets")
            detail = []
            if uni is not None:
                u = uni.set_index("Gene")
                for g in str(r.get("命中锚定靶点", "")).split("；"):
                    if g and g in u.index:
                        detail.append({"gene": g, "layer": "临床锚定",
                                       "n_drugs": int(u.loc[g, "Anchor_n_drugs"]),
                                       "actions": str(u.loc[g, "Anchor_actions"])})
            for g in str(r.get("命中文献靶点", "")).split("；"):
                if g:
                    detail.append({"gene": g, "layer": "文献机制", "n_drugs": 0, "actions": ""})
            return {"ok": True, "compound": str(r["英文名"]), "rank": int(r["排名"]),
                    "score": float(r["总分"]), "targets": detail}

        def cb_compound(keyword, field):
            lib = app.engine.data.get("molecule_lib")
            if lib is None:
                # 未运行时直接读文件
                p = app.cfg.get("paths", {}).get("molecule_library", "")
                try:
                    lib = pd.read_excel(p)
                except Exception:
                    return {"ok": False, "msg": "分子库文件不存在"}
            kw = str(keyword).lower()
            if field and field != "任意" and field in lib.columns:
                d = lib[lib[field].astype(str).str.lower().str.contains(kw, na=False)]
            else:
                mask = pd.Series(False, index=lib.index)
                for col in ("英文名", "中文名", "CAS号", "来源药材", "化学结构大类"):
                    if col in lib.columns:
                        mask |= lib[col].astype(str).str.lower().str.contains(kw, na=False)
                d = lib[mask]
            cols = [c for c in ["中文名", "英文名", "化学结构大类", "来源药材",
                                "PubChem CID", "CAS号", "分子式", "OB(%)", "DL"] if c in d.columns]
            return {"ok": True, "total": len(d),
                    "rows": json.loads(d.head(30)[cols].to_json(orient="records"))}

        def cb_dataframes():
            return {k: v for k, v in app.engine.data.items()
                    if isinstance(v, pd.DataFrame)}

        def cb_screen(dataset, filters, sort_by, ascending, limit):
            d = app.engine.data.get("scored") if dataset == "scored" \
                else app.engine.data.get("molecule_lib")
            if d is None:
                return {"ok": False, "msg": f"数据集 {dataset} 未就绪，请先运行工作流"}
            d = d.copy()
            ops = {">": lambda s, v: s > v, ">=": lambda s, v: s >= v,
                   "<": lambda s, v: s < v, "<=": lambda s, v: s <= v,
                   "==": lambda s, v: s.astype(str).str.lower() == str(v).lower(),
                   "contains": lambda s, v: s.astype(str).str.lower().str.contains(
                       str(v).lower(), na=False)}
            for f in (filters or []):
                fld, op, val = f.get("field"), f.get("op"), f.get("value")
                if fld not in d.columns or op not in ops:
                    return {"ok": False, "msg": f"无效条件：{fld} {op}（列不存在或 op 非法）"}
                col = d[fld]
                if op in (">", ">=", "<", "<="):
                    col = pd.to_numeric(col, errors="coerce")
                try:
                    d = d[ops[op](col, val)]
                except Exception as e:
                    return {"ok": False, "msg": f"条件 {fld} {op} {val} 执行失败：{e}"}
            if sort_by and sort_by in d.columns:
                d = d.sort_values(sort_by, ascending=bool(ascending),
                                  na_position="last")
            rows_json = d.head(limit).to_json(orient="records", force_ascii=False)
            return {"ok": True, "total": len(d),
                    "rows": json.loads(rows_json) if rows_json else []}

        agent_tools.WORKFLOW_CALLBACKS.update({
            "state": cb_state, "results": cb_results, "targets": cb_targets,
            "detail": cb_detail, "compound": cb_compound, "dataframes": cb_dataframes,
            "screen": cb_screen,
        })

    # ---------- Agent 会话 ----------

    def chat(self, message: str) -> dict:
        """启动/复用 Agent 会话（后台线程执行）"""
        c = self.llm_cfg
        if not (c.get("base_url") and c.get("api_key") and c.get("model")):
            return {"ok": False, "need_config": True,
                    "msg": "未配置 LLM，请先在侧边栏设置中填写 API 配置"}
        if self.agent_session is None:
            self.agent_session = AgentSession(
                {"base_url": c["base_url"], "api_key": c["api_key"], "model": c["model"]},
                emitter=self._emit, params=self.agent_params,
                history_path=HISTORY_PATH)
        # 新会话恢复历史后，确保本次消息不会与历史中最后一条重复
        if self.agent_session.messages and \
                self.agent_session.messages[-1].get("content") == message:
            self.agent_session.messages.pop()

        def worker():
            self._emit({"type": "chat_start", "message": message})
            self.agent_session.chat(message)
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "msg": "已提交"}

    def reset_chat(self):
        if self.agent_session:
            self.agent_session.reset()
        return {"ok": True}

    def _on_event(self, event: dict):
        """引擎事件广播给所有 SSE 客户端"""
        with self.sse_lock:
            dead = []
            for q in self.sse_queues:
                try:
                    q.put_nowait(event)
                except Exception:
                    dead.append(q)
            for q in dead:
                self.sse_queues.remove(q)

    def sse_subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.sse_lock:
            self.sse_queues.append(q)
        return q

    def sse_unsubscribe(self, q):
        with self.sse_lock:
            if q in self.sse_queues:
                self.sse_queues.remove(q)

    def start_run(self):
        if self.engine.state["status"] == "running":
            return False, "工作流正在运行中"
        # 重建引擎（干净状态）
        self.engine = build_engine(self.cfg)
        self.engine.bus.subscribe(self._on_event)
        self._inject_tool_callbacks()
        self.engine.run(background=True)
        return True, f"已启动 run_id={self.engine.state['run_id']}"


# ---------------- HTTP Handler ----------------

def make_handler(app: DashboardServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 静默访问日志

        # ---------- helpers ----------
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

        def _read_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                return self.rfile.read(length)
            return b""

        # ---------- GET ----------
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)

            if route in ("/", "/index.html"):
                self._send(200, load_dashboard_html(), "text/html; charset=utf-8")

            elif route == "/api/state":
                self._json(app.engine.snapshot())

            elif route == "/api/evidence":
                limit = int(qs.get("limit", [500])[0])
                step = qs.get("step", [None])[0]
                evs = app.engine.evidence_snapshot()
                if step:
                    evs = [e for e in evs if e.get("step") == step]
                self._json({"total": app.engine.evidence.__len__(),
                            "items": evs[:limit]})

            elif route == "/api/metrics":
                snap = app.engine.snapshot()
                self._json({"metrics": snap.get("metrics", {}),
                            "steps_summary": {
                                n: {"status": v["status"],
                                    "duration_ms": v["duration_ms"],
                                    "outputs": v["outputs"]}
                                for n, v in snap["steps"].items()}})

            elif route == "/api/top":
                try:
                    n = int(qs.get("n", [20])[0])
                    scored = app.engine.data.get("scored")
                    if scored is None or not len(scored):
                        self._json({"ok": False, "msg": "评分未完成"})
                    else:
                        rows = scored.head(n).replace({"": None}).where(
                            lambda d: d.notna(), None).to_dict("records")
                        # NaN → null 序列化修复
                        import math
                        clean = []
                        for r in rows:
                            clean.append({k: (None if (isinstance(v, float) and math.isnan(v))
                                              else ("_NaN_" if v == "_NaN_" else v))
                                          for k, v in r.items()})
                        self._json({"ok": True,
                                    "columns": list(scored.columns),
                                    "rows": clean})
                except Exception as e:
                    self._json({"ok": False, "msg": f"{type(e).__name__}: {e}"})

            elif route == "/api/artifacts":
                snap = app.engine.snapshot()
                items = []
                for a in snap.get("artifacts", []):
                    items.append({"name": os.path.basename(a),
                                  "path": a,
                                  "size": os.path.getsize(a) if os.path.exists(a) else 0})
                self._json({"artifacts": items})

            elif route == "/download":
                path = qs.get("path", [""])[0]
                root = os.path.normpath(ARTIFACTS_ROOT)
                norm = os.path.normpath(path)
                if not norm.startswith(root) or not os.path.isfile(norm):
                    self._send(404, '{"error": "not found"}')
                    return
                with open(norm, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/octet-stream")
                self.send_header("Content-Disposition",
                                 'attachment; filename="'
                                 + urllib.parse.quote(os.path.basename(norm)) + '"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            elif route == "/api/events":
                self._sse()

            elif route == "/api/step_logs":
                step = qs.get("step", [None])[0]
                limit = int(qs.get("limit", [200])[0])
                logs = app.engine.step_logs_snapshot(step)
                self._json({"ok": True, "logs": logs[-limit:]})

            elif route == "/api/providers":
                # LLM provider 模板与当前配置（脱敏）
                c = app.llm_cfg
                return_cfg = {**c, "api_key": ("*" * (len(c.get("api_key") or "") - 4)
                                               + c["api_key"][-4:]) if c.get("api_key") else ""}
                self._json({"providers": PROVIDERS, "current": return_cfg,
                            "tools": [{"name": n, "domain": t["domain"]}
                                      for n, t in agent_tools.TOOL_REGISTRY.items()]})

            elif route == "/api/tools":
                self._json({"tools": agent_tools.get_tool_schemas()})

            elif route == "/api/chat_history":
                if app.agent_session:
                    msgs = [m for m in app.agent_session.messages
                            if m.get("role") in ("user", "assistant") and m.get("content")]
                    self._json({"ok": True, "messages": msgs[-40:]})
                else:
                    self._json({"ok": True, "messages": []})

            elif route == "/api/scored_detail":
                # 单个成分的循证详情
                try:
                    name = qs.get("compound", [""])[0]
                    scored = app.engine.data.get("scored")
                    if scored is None:
                        self._json({"ok": False})
                        return
                    row = scored[scored["英文名"].astype(str) == name]
                    if not len(row):
                        self._json({"ok": False, "msg": "未找到该成分"})
                        return
                    r = row.iloc[0]
                    anchors = [g for g in str(r.get("命中锚定靶点", "")).split("；") if g]
                    mechs = [g for g in str(r.get("命中文献靶点", "")).split("；") if g]
                    unified = app.engine.data.get("unified_targets")
                    detail = []
                    if unified is not None:
                        uni = unified.set_index("Gene")
                        for g in anchors:
                            if g in uni.index:
                                detail.append({
                                    "gene": g, "layer": "临床锚定",
                                    "n_drugs": int(uni.loc[g, "Anchor_n_drugs"]),
                                    "actions": str(uni.loc[g, "Anchor_actions"])})
                    for g in mechs:
                        detail.append({"gene": g, "layer": "文献机制",
                                       "n_drugs": 0, "actions": ""})
                    self._json({"ok": True, "compound": name,
                                "score": float(r["总分"]), "rank": int(r["排名"]),
                                "targets": detail})
                except Exception as e:
                    self._json({"ok": False, "msg": f"{type(e).__name__}: {e}"})

            else:
                self._send(404, '{"error": "not found"}')

        # ---------- SSE ----------
        def _sse(self):
            q = app.sse_subscribe()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                # 初始：全量状态
                self._sse_write({"type": "full_state",
                                 "state": app.engine.snapshot()})
                while True:
                    try:
                        event = q.get(timeout=15)
                        self._sse_write(event)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                app.sse_unsubscribe(q)

        def _sse_write(self, event: dict):
            data = json.dumps(event, ensure_ascii=False, default=str)
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

        # ---------- POST ----------
        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_body()
            if parsed.path == "/api/run":
                ok, msg = app.start_run()
                self._json({"ok": ok, "msg": msg})
            elif parsed.path == "/api/reset":
                app.engine = build_engine(app.cfg)
                app.engine.bus.subscribe(app._on_event)
                app._inject_tool_callbacks()
                self._json({"ok": True, "msg": "已重置"})
            elif parsed.path == "/api/chat":
                try:
                    d = json.loads(body.decode("utf-8") or "{}")
                except Exception:
                    d = {}
                msg = str(d.get("message", "")).strip()
                if not msg:
                    self._json({"ok": False, "msg": "消息为空"})
                    return
                self._json(app.chat(msg))
            elif parsed.path == "/api/chat_reset":
                app.reset_chat()
                self._json({"ok": True, "msg": "对话已重置"})
            elif parsed.path == "/api/llm_config":
                try:
                    d = json.loads(body.decode("utf-8") or "{}")
                except Exception:
                    d = {}
                # 空值不覆盖已有 key
                for k in ("base_url", "api_key", "model"):
                    if d.get(k):
                        d[k] = str(d[k]).strip()
                d = {k: v for k, v in d.items() if v}
                saved = app.save_llm_cfg(d)
                self._json({"ok": True, "current": {
                    **saved, "api_key": ("*" * (len(saved.get("api_key") or "") - 4)
                                          + saved["api_key"][-4:]) if saved.get("api_key") else ""}})
            else:
                self._send(404, '{"error": "not found"}')

    return Handler


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def main():
    cfg = load_config()
    app = DashboardServer(cfg)
    port = app.port
    # 端口占用自动避让（最多 +10）
    tried = [port]
    while not port_available(port) and len(tried) <= 10:
        port += 1
        tried.append(port)
    app.port = port
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://localhost:{port}"
    print(f"[Workflow Dashboard] {url}")
    print(f"[Workflow] {app.engine.name} · {len(app.engine.steps)} 步骤已注册")
    print("[API] /api/state /api/evidence /api/events(SSE) /api/top POST /api/run")
    if len(tried) > 1:
        print(f"[Port] 默认端口被占用，已改用 {port}（尝试过 {tried}）")
    # 一键启动体验：自动打开浏览器（frozen 模式，延迟等服务器就绪）
    if getattr(sys, "frozen", False):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        print("[Browser] 将自动打开浏览器…（关闭本窗口即停止服务）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()
