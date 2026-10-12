# Vina 可执行文件

对接工具按下面的顺序寻找 AutoDock Vina：

1. 调用参数里的 `vina_path` 或 `--vina`
2. 环境变量 `VINA_EXE`
3. 本目录的 `vina.exe`
4. 系统 `PATH` 中的 `vina` 或 `vina.exe`

仓库不提交二进制。`bin/*.exe` 已在 `.gitignore` 中。检索、网络、文献和试验不需要 Vina；只有对接分支需要它。

## Windows

1. 从 [AutoDock Vina 1.2.5 发布页](https://github.com/ccsb-scripps/AutoDock-Vina/releases/tag/v1.2.5) 下载官方 Windows 构建。
2. 将可执行文件放到本目录，命名为 `vina.exe`。
3. 在仓库根目录检查：

```powershell
$env:PYTHONUTF8 = "1"
python mcp_server/unified_mcp.py --stdio
```

然后调用 `dock_check_environment` 或 `tcm_dependency_status`。`resolved_source` 应显示参数、`VINA_EXE` 或 `bin/vina.exe` 之一。

本流程的 Meeko 准备和日志解析按 Vina 1.2.x 的命令行编写。换主版本前先用一次已知口袋做对照。

## macOS 与 Linux

使用发行版或上游提供的 `vina`，放到 `PATH`，或把绝对路径写入 `VINA_EXE`。不要把 Windows 的 `vina.exe` 拷到这些系统上运行。

Vina 本身是 Apache-2.0。再分发二进制时保留上游许可证和版权声明，见仓库根目录 `NOTICE`。
