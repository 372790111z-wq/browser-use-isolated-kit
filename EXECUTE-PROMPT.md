# Browser Use Isolated Kit 一键执行提示词

```text
这是明确执行授权：请在当前 Mac 上安装或升级 Browser Use 隔离环境。

唯一实现来源：
https://github.com/372790111z-wq/browser-use-isolated-kit

要求：

1. 克隆仓库；如果已经存在，只允许使用 git pull --ff-only 更新，不覆盖本地未提交修改。
2. 完整读取仓库中的 README.md 和 skill/browser-use-isolated/SKILL.md。
3. 先运行 python3 scripts/manage.py doctor，保留原始诊断结果。
4. 首次配置运行 python3 scripts/manage.py install；已有配置运行 python3 scripts/manage.py update。
5. 除非我明确要求升级 Browser Use 本体，否则不要使用 --upstream。
6. 安装后再次运行静态 doctor；需要启动隔离 Chrome 时再运行 doctor --live。
7. 如果 Chrome 出现远程调试安全授权，停下来让我手动确认。
8. 不得读取、复制或输出 Cookie、密码、Token、历史记录、localStorage 或日常 Chrome 标签。
9. 不得回退到日常 Chrome。隔离环境无法修复时，报告实际工具、启动器、原始错误和失败层级后停止。
10. 不得删除专用 Chrome Profile，不得登录 Browser Use Cloud。

最终报告必须包含：

- 仓库版本与提交；
- browser-use 版本；
- wrapper、Profile、CDP、workspace 和 Skill 路径；
- 静态 doctor 与 live doctor 结果；
- 是否应用后台焦点补丁；
- 是否能识别可观察标签池；
- 是否发现隐私数据进入仓库或日志；
- 尚需人工完成的操作。

只有文件真实落盘、诊断通过且没有回退日常 Chrome，才能宣布完成。
```
