# -*- coding: utf-8 -*-
"""
AI Agent 层：OpenAI 兼容 function calling 循环（流式版）
支持任意 OpenAI 兼容端点：Kimi(moonshot) / Qwen(dashscope/siliconflow) / DeepSeek / Ollama 等
循环：用户消息 → LLM(流式) → 工具调用 → 结果回填 → LLM → ... → 最终回复
- 流式输出：内容 delta 实时推送 chat_delta 事件（前端打字机效果）
- 失败重试：429/5xx/超时/断连 自动指数退避重试
- 历史持久化：对话记录写入 JSON，服务器重启后自动恢复
"""
import json
import os
import threading
import time

import requests

from tools import get_tool_schemas, execute_tool

# 预置 Provider 模板（baseUrl 均为 OpenAI 兼容端点）
PROVIDERS = {
    "kimi": {
        "label": "Kimi (Moonshot AI)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-latest"],
        "key_hint": "platform.moonshot.cn 获取",
    },
    "qwen": {
        "label": "Qwen (阿里云百炼)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-32b"],
        "key_hint": "bailian.console.aliyun.com 获取",
    },
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3",
                   "Qwen/Qwen2.5-7B-Instruct"],
        "key_hint": "cloud.siliconflow.cn 获取（有免费额度）",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_hint": "platform.deepseek.com 获取",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "models": [],
        "key_hint": "填写 base_url + model + key",
    },
}

SYSTEM_PROMPT = """你是一个中药有效成分筛选研究助手（UC 溃疡性结肠炎方向），运行在可视化工作流平台上。

## 你的能力
1. **本地工作流数据**（已完成一次筛选运行，优先使用）：
   - workflow_state / workflow_results / workflow_targets / workflow_detail：查询 11 步工作流状态、Top 候选小分子评分、统一靶点集（临床锚定 + 文献机制）、单成分循证链
   - compound_search：检索候选成分理化属性（OB/DL/SMILES/CID/CAS/结构分类/来源药材），注意中文名列是英文译名，检索用英文名
   - compound_screen：高级筛选（按 OB/DL/分子量/结构分类多条件过滤排序）
   - ppi_network：STRING 蛋白互作网络（PPI 边与度中心性）
   - kegg_enrichment / uniprot_gene：KEGG 通路富集、UniProt 蛋白注释
   - python_calc：在评分表等数据集上做统计分析
   - export_report：把成分列表导出为 CSV 报告文件
2. **在线数据库检索**（需要联网，用于扩展研究）：
   - tcmbank_search / tcmbank_detail：TCMBank 全库（61,965 成分，四实体互查）
   - npass_search_compound：NPASS 天然产物库（204,023 化合物）
   - pubchem_lookup：PubChem 化学属性
   - opentargets_search / opentargets_disease_targets：Open Targets 疾病-靶点关联
   - swisstarget_predict：SwissTargetPrediction 反向靶点预测（耗时 60-70 秒，慎用）
   - ctd_chem_gene：CTD 本地库化学物-基因交互（离线）

## 工作准则
- 用户提问后先判断：本地数据能否回答？能则优先用本地工具（快、离线）
- 需要外部扩展（新疾病靶点、跨库验证、化学属性）时再用在线工具
- 回答要引用具体数据（排名、分数、靶点数），保持科研严谨
- 检索结果要说明来源数据库
- 工具失败时如实说明，建议替代方案
- 用中文回答，语言精炼专业"""

DEFAULT_PARAMS = {
    "max_turns": 8,
    "temperature": 0.3,
    "tool_call_timeout": 60,
    "llm_timeout": 120,
    "llm_max_retries": 2,
    "max_history": 40,
}


class AgentSession:
    """一次对话会话：维护 messages 历史、provider 配置与持久化"""

    def __init__(self, provider_cfg: dict, emitter=None, params: dict = None,
                 history_path: str = None):
        self.cfg = provider_cfg      # {base_url, api_key, model}
        self.emitter = emitter       # SSE 事件推送
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.history_path = history_path
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.lock = threading.Lock()
        self._load_history()

    # ---------------- 对话主循环 ----------------

    def chat(self, user_msg: str, max_turns: int = None) -> dict:
        """主循环（流式）：返回 {ok, reply, tool_calls: [...]}"""
        max_turns = max_turns or self.params.get("max_turns", 8)
        with self.lock:
            self.messages.append({"role": "user", "content": user_msg})
        tool_trace = []
        try:
            for turn in range(max_turns):
                msg = self._stream_llm()

                # 无工具调用 → 最终回答（流式中已推送 delta，这里补终态事件）
                if not msg.get("tool_calls"):
                    reply = msg.get("content") or ""
                    with self.lock:
                        self.messages.append({"role": "assistant", "content": reply})
                    if self.emitter:
                        self.emitter({"type": "chat_reply", "content": reply,
                                      "turns": turn + 1,
                                      "tools_used": [t["name"] for t in tool_trace]})
                    self._save_history()
                    return {"ok": True, "reply": reply, "tool_calls": tool_trace}

                # 有工具调用 → 逐个执行并回填
                with self.lock:
                    self.messages.append(msg)
                for tc in msg["tool_calls"]:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = execute_tool(fn["name"], args, self.emitter)
                    tool_trace.append({"name": fn["name"], "args": args,
                                       "ok": result.get("ok", True)})
                    with self.lock:
                        self.messages.append({
                            "role": "tool", "tool_call_id": tc.get("id") or f"call_{turn}",
                            "content": json.dumps(result, ensure_ascii=False,
                                                  default=str)[:6000]})

            # 达到轮次上限
            reply = "（已达最大工具调用轮次，请基于以上结果继续提问或细化问题）"
            with self.lock:
                self.messages.append({"role": "assistant", "content": reply})
            if self.emitter:
                self.emitter({"type": "chat_reply", "content": reply,
                              "turns": max_turns, "tools_used": [t["name"] for t in tool_trace]})
            self._save_history()
            return {"ok": True, "reply": reply, "tool_calls": tool_trace}

        except requests.Timeout:
            return self._fail(f"LLM 请求超时（{self.params.get('llm_timeout', 120)}s），"
                              "请检查网络或换模型")
        except requests.ConnectionError:
            return self._fail("无法连接 LLM 端点，请检查 base_url 与网络")
        except Exception as e:
            return self._fail(f"{type(e).__name__}: {e}")

    # ---------------- LLM 调用（流式 + 重试） ----------------

    def _stream_llm(self) -> dict:
        """流式调用 LLM：content 增量推送 chat_delta；工具调用增量聚合"""
        retries = int(self.params.get("llm_max_retries", 2))
        timeout = int(self.params.get("llm_timeout", 120))
        payload = {
            "model": self.cfg["model"],
            "messages": self.messages,
            "tools": get_tool_schemas(),
            "temperature": self.params.get("temperature", 0.3),
            "stream": True,
        }
        last_err = None
        for attempt in range(retries + 1):
            try:
                r = requests.post(
                    f"{self.cfg['base_url'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.cfg['api_key']}"},
                    json=payload, timeout=timeout, stream=True)
                if r.status_code == 401:
                    raise PermissionError("API Key 无效（401），请检查密钥")
                if r.status_code == 404:
                    raise LookupError(f"端点或模型不存在（404）：{self.cfg['model']}")
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return self._consume_stream(r)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = e
                if attempt < retries:
                    if self.emitter:
                        self.emitter({"type": "chat_retry", "attempt": attempt + 1,
                                      "max_retries": retries,
                                      "error": str(e)[:150]})
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_err or RuntimeError("LLM 请求失败")

    def _consume_stream(self, r) -> dict:
        """解析 SSE 流：聚合 content 与 tool_calls 增量"""
        content_parts = []
        tool_calls_acc = {}     # index -> {"id","function":{"name","arguments"}}
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                c = delta.get("content")
                if c:
                    content_parts.append(c)
                    if self.emitter:
                        self.emitter({"type": "chat_delta", "delta": c})
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    acc = tool_calls_acc.setdefault(
                        idx, {"id": "", "type": "function",
                              "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name") and not acc["function"]["name"]:
                        acc["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        acc["function"]["arguments"] += fn["arguments"]
        except Exception:
            # 流中断：已有完整 content 且无工具调用时按完成处理，否则上抛触发重试
            if content_parts and not tool_calls_acc:
                pass
            else:
                raise
        msg = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_calls_acc:
            msg["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        return msg

    # ---------------- 历史持久化 ----------------

    def _save_history(self):
        if not self.history_path:
            return
        try:
            with self.lock:
                msgs = self._trimmed_messages()
            tmp = self.history_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "model": self.cfg.get("model", ""),
                           "messages": msgs}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.history_path)
        except Exception:
            pass  # 持久化失败不影响对话

    def _trimmed_messages(self):
        """裁剪历史：保留 system，之后从最近的 user 消息起保留 max_history 条"""
        limit = int(self.params.get("max_history", 40))
        sys_msgs = [m for m in self.messages if m.get("role") == "system"]
        rest = [m for m in self.messages if m.get("role") != "system"]
        if len(rest) <= limit:
            return sys_msgs + rest
        # 从后往前找起点，避免切断 tool 配对：起点必须是 user
        start = len(rest) - limit
        while start < len(rest) and rest[start].get("role") != "user":
            start += 1
        return sys_msgs + rest[start:]

    def _load_history(self):
        if not self.history_path or not os.path.exists(self.history_path):
            return
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            msgs = d.get("messages") or []
            # 只恢复 user/assistant 对话气泡，跳过 tool 消息与裸 assistant 工具调用
            restored = [m for m in msgs
                        if m.get("role") in ("user", "assistant")
                        and m.get("content") and not m.get("tool_calls")]
            if restored:
                self.messages = [{"role": "system",
                                  "content": SYSTEM_PROMPT}] + restored
        except Exception:
            pass

    # ---------------- 其他 ----------------

    def _fail(self, msg):
        if self.emitter:
            self.emitter({"type": "chat_error", "error": msg})
        return {"ok": False, "reply": msg, "tool_calls": []}

    def reset(self):
        with self.lock:
            self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self.history_path and os.path.exists(self.history_path):
            try:
                os.remove(self.history_path)
            except Exception:
                pass
