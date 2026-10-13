# 部署说明

本仓库提供两种 MCP 部署方式：stdio（本地子进程，随客户端按需拉起）与 StreamableHTTP（常驻服务）。以下以接入 TeleAgent 为例，其他支持 MCP 的客户端流程一致。

## 1. 准备环境

```bash
pip install -r requirements.txt
```

Vina 是可选项。Windows 对接把官方 1.2.5 二进制放到 `bin/vina.exe`，或设置 `VINA_EXE`。放置方式见 [bin/README.md](../bin/README.md)。没有 Vina 时，检索、网络、文献和试验工具仍可使用；对接分支会暂停并说明缺口。

## 2. 方案 A：stdio（推荐）

无需常驻进程，客户端每次会话拉起一次子进程。配置 JSON：

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

将 `<仓库绝对路径>` 替换为实际路径（Windows 示例 `D:\\projects\\open-tcm-discovery`）。

## 3. 方案 B：StreamableHTTP

统一入口的默认端口是 **8002**，只绑定 `127.0.0.1`。不要改用 `open_tcm_discovery_mcp.py`：那个文件单独运行时只有检索族，并且自带默认端口 8000。

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

服务默认只绑定回环地址；对外暴露时自行处理鉴权与防火墙。

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
            print("tools =", len(names))            # 期望 56
            res = await s.call_tool(
                "tcm_check_database_status", {"params": {"refresh": False}}
            )
            print(res.content[0].text[:200])

asyncio.run(go())
```

输出 `tools = 56` 且数据库状态正常返回即接入成功。

## 6. 常见问题

- Windows 中文乱码：确保 `PYTHONUTF8=1`。
- 对接提示找不到 Vina：按 [bin/README.md](../bin/README.md) 放置官方二进制，或设置 `VINA_EXE`。
- 药对分析提示找不到索引：确认 `data/herb_mol_index.json` 在仓库根的 `data/` 下，或设置 `TCM_HERB_INDEX`。
- 端口占用：换 `--port` 并同步更新注册 URL。统一入口与检索族单独进程不要抢同一个端口。
- 内网/离线环境：检索类工具依赖公网，离线时 `tcm_dependency_status` 会列出缺失项。
