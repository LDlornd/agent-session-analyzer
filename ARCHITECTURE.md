# 工程结构与扩展约定

```text
src/agent_session_analyzer/
  __init__.py             # 稳定的用户入口
  schema.py               # 通用 JSONL 协议版本
  adapters/
    dsh.py                # 原生事件 -> 统一记录
    openhands.py
  recorders/
    dsh.py                # SDK 生命周期、回调与 JSONL 写入
    openhands.py
  reader.py               # 日志读取与指标汇总
  viewer.py               # 本地 HTTP 查看器和 CLI
  viewer.html/css/js      # 随包发布的静态资源
examples/                 # 最小真实接入示例
tests/                   # 离线契约与回归测试
```

业务代码优先使用 `from agent_session_analyzer import OpenHandsRecorder`。
内部代码从 adapters/recorders 的具体模块导入；通用协议常量从 schema 导入。旧版平铺模块已删除。
SDK 依赖保持可选、延迟导入，纯 adapter 不执行模型请求和工具。

## 新增 harness

1. 在 adapters/<name>.py 中实现纯转换，保留 raw 和来源 ID，明确 turn/step 的映射。
2. 在 recorders/<name>.py 中对接 SDK 生命周期、回调恢复、错误传播及文件写入。
3. 在包顶层导出稳定接口；必要依赖声明为 pyproject.toml 的可选 extra。
4. 增加脱敏的离线事件样本、契约测试、最小接入示例和 SDK 支持范围。
5. 检查缺失 usage、重试、多工具、乱序结果、暂停恢复和记录失败，避免伪造零值或成功状态。

## 后续建议（尚未实施）

- 协议独立版本化：新增可选字段保持兼容，破坏性变更才升级 schema；包版本独立维护。
- 随 harness 数量增加，再提取共享 JSONL writer。保持 flush/fsync、异常传播及并发策略明确，先不要强行统一不同 SDK 的生命周期。
- 将各 SDK 的契约与脱敏 fixtures 固定在测试中；后续配置 CI 做离线测试、构建和资源打包检查，真实模型测试单独手动触发。
- 维护 CHANGELOG 与支持版本表；安装文档中的 Git 标签必须对应已发布实现。本次 OpenHands 及结构调整尚未包含在 v0.1.0。
- 日志默认不进 Git。后续增加可配置脱敏、保留期限和大日志索引，避免 raw 请求长期保留敏感输入。
- 查看器规模扩大后再拆成 viewer/ 与 static/；迁移时同步修改入口和 setuptools package-data 并验证安装包资源。

## 用户验证

本次未执行测试、构建、lint 或服务启动。测试由用户自行执行。
Python 3.12+，在仓库根目录执行：

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

预期全部通过；重点检查包顶层导出与分层模块指向同一类、OpenHands 多工具 token 去重、重试归组、回调恢复和已有 dsh 行为。
实际接入 OpenHands 时另装 `python -m pip install -e ".[openhands]"`，配置模型密钥后运行 examples/openhands_record.py；该示例会产生真实模型请求。