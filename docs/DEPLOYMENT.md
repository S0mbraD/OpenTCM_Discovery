# 部署说明

任意支持 MCP 的客户端都可以接。stdio 由客户端在需要时拉起一个本地子进程。Streamable HTTP 是常驻服务。Cursor 的配置文件只是例子之一，其他客户端同样用这两种接法。

## 1. 准备环境

```bash
pip install -r requirements.txt
```

Vina 是可选项。在 Windows 上做对接，把官方 1.2.5 二进制放到 `bin/vina.exe`，或设置 `VINA_EXE`。放法见 [bin/README.md](../bin/README.md)。没装 Vina 时，检索、网络、文献和试验工具仍可使用。对接那一支会暂停，并说明缺什么。

## 2. 方案 A：stdio（推荐）

不用单独起服务。客户端每次会话拉起一个子进程。配置如下：

```json
{
  "mcpServers": {
    "open-tcm-discovery": {
      "command": "python",
      "args": ["<仓库绝对路径>/mcp_server/unified_mcp.py", "--stdio"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

将 `<仓库绝对路径>` 替换为你的克隆目录。Windows 的 JSON 里反斜杠要写成 `\\`。不要把某一台机器上的用户目录写进要提交的配置。

客户端拉起子进程之后，用 `list_tools` 看数量。期望是 64。若明显少一截，多半是启动了别的入口，而不是依赖没装齐。stdio 失败时先看 Python 是否在 PATH 里，以及 `PYTHONUTF8` 有没有传进子进程。中文乱码看起来像返回坏了，其实常常是编码。

## 3. 方案 B：StreamableHTTP

统一入口的默认端口是 **8002**，只绑定 `127.0.0.1`。完整服务请跑 `unified_mcp.py`。`open_tcm_discovery_mcp.py` 单独运行时只有检索工具，端口默认是 8000。对接族若单独运行 `structure_docking_mcp.py`，端口默认是 8001。看板默认 8765。不要把检索族的端口写成统一服务的 8002，也不要让这几个进程抢同一个端口。

```powershell
$env:PYTHONUTF8 = "1"
python <仓库绝对路径>\mcp_server\unified_mcp.py --port 8002
```

再注册：

```json
{
  "mcpServers": {
    "open-tcm-discovery": {
      "url": "http://127.0.0.1:8002/mcp"
    }
  }
}
```

服务只绑在本机回环上。浏览器打开 `http://127.0.0.1:8002/mcp` 通常不是一个给人看的网页。MCP 客户端要按 Streamable HTTP 去连这个地址。如果要给其他机器访问，自己处理鉴权和防火墙，仓库不附带账号系统。

端口已经被占用时，进程起不来，客户端会表现为连不上，而不是工具返回空的成分表。换 `--port` 之后，客户端 URL 里的端口要一起改。

## 4. 环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `VINA_EXE` | Vina 可执行文件绝对路径 | 未设置时依次找 `bin/vina.exe` 和 PATH |
| `TCM_HERB_INDEX` | 药材–成分索引 | `<仓库>/data/herb_mol_index.json` |
| `TCM_CACHE_TTL` | 只读检索缓存秒数 | 3600 |
| `PYTHONUTF8` | Windows 中文输出防乱码 | 1 |

Vina 解析顺序：显式参数 > `VINA_EXE` > `bin/vina.exe` > PATH。

## 5. 接入验证

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio

async def go():
    p = StdioServerParameters(
        command="python",
        args=["<仓库绝对路径>/mcp_server/unified_mcp.py", "--stdio"],
        env={"PYTHONUTF8": "1"},
    )
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("tools =", len(names))            # 期望 64
            res = await s.call_tool(
                "tcm_check_database_status", {"params": {"refresh": False}}
            )
            print(res.content[0].text[:200])

asyncio.run(go())
```

看到 `tools = 64`，就说明统一入口接上了。数据库状态那段要再看一眼。上面的例子把 `refresh` 设成 false，返回的是工具里保存的探测表，`snapshot_date` 写着那张表的日期，不是此刻每个网站的现场结论。要看几个关键端点现在通不通，把 `refresh` 设为 true。公网不通时，工具数仍应是 64，状态里会是失败或不可达。

## 6. 常见问题

- Windows 中文乱码：确保 `PYTHONUTF8=1`。乱码出现在工具摘要里时，先改编码，再判断是不是上游失败。
- 对接提示找不到 Vina：按 [bin/README.md](../bin/README.md) 放置官方二进制，或设置 `VINA_EXE`。`tcm_smart_run` 这时会 `paused`，理由里会写 vina executable not found。检索、网络、文献和试验步骤如果已经跑完，不会因为缺 Vina 被抹掉。
- 药对分析提示找不到索引：确认 `data/herb_mol_index.json` 在仓库根的 `data/` 下，或设置 `TCM_HERB_INDEX`。正文以 `Error:` 开头，并写明期望路径。对上的药材少于两味时也是这种错误，不是 Jaccard 等于 0。
- 端口占用：换 `--port` 并同步更新注册 URL。统一入口、检索族、对接族和看板不要抢同一个端口。
- 内网/离线环境：检索类工具依赖公网。离线时先跑 `tcm_dependency_status`，看缺的是 Python 包还是外网。本地整理、成分名对齐和方剂集合重叠不需要网站。TCMSP 旧站需要公网，失败时 `ingredients` 为空，并带 `error`，不会补一行成分。
- 参数校验失败：带参调用要把字段放进 `params`。外层直接放 `goal` 或 `query`，得到的是校验错误，不是空的科学结果。
