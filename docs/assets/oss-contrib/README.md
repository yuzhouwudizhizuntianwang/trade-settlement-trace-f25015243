# 开源贡献凭证（PR / Issue）

| 类型 | 链接 | 内容 |
|---|---|---|
| Issue #7 | https://github.com/Euzohn/ZFundPilot/issues/7 | 文档缺陷：CONTRIBUTING 的启动命令在 Windows 下不可用，且与 README 不一致 |
| PR #8 | https://github.com/Euzohn/ZFundPilot/pull/8 | 修复上述问题：去掉 `PYTHONPATH=src` 前缀，补 Windows 写法（1 个文件 +9 −1） |

**上游仓库**：Euzohn/ZFundPilot（Python + FastAPI 的基金组合分析工具，MIT，★32）—— **第三方仓库，非本人所有**。

**改动内容**（`CONTRIBUTING.md` 一个代码块）：

```diff
-PYTHONPATH=src uvicorn zfundpilot.api:app --reload --port 8000
+# 已执行 pip install -e ".[dev]" 时可直接启动，无需再设置 PYTHONPATH
+uvicorn zfundpilot.api:app --reload --port 8000
```

理由：开发环境一步已经 `pip install -e ".[dev]"`，包已装进环境，不需要再指定源码路径；
而 `VAR=value 命令` 是 POSIX shell 语法，Windows 的 cmd 与 PowerShell 都会报错，
与该仓库 README 声明的「Windows 原生运行同样支持」冲突。

> 图片为 GitHub 官方生成的页面预览图（opengraph），非手工合成。
