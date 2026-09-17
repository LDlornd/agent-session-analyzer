# agent-session-analyzer

轻量 Python 包：记录 Agent 运行过程为 JSONL，并通过本地页面查看。

层级：**session → turn → step → model attempt / tool call**。

当前支持 dsh 同步 Python SDK。记录模型信息、token 消耗、接口返回的思考内容、工具参数和结果；保留原始事件与 stream。查看器支持本包日志及原生未压缩的 `session.v3.jsonl`。

## 安装

Python 3.11+。仓库为私有仓库，需要当前 Git 已有访问权限。使用 GitHub CLI 的机器可先执行 `gh auth login` 和 `gh auth setup-git` 配置 HTTPS 认证：

```bash
pip install "git+https://github.com/LDlornd/agent-session-analyzer.git@v0.1.0"
```

使用 uv 的项目：

```bash
uv add "agent-session-analyzer @ git+https://github.com/LDlornd/agent-session-analyzer.git@v0.1.0"
```

仅从 GitHub 安装，尚未发布到 PyPI。包本身没有运行时第三方依赖。接入 dsh 时项目需另外安装 `deepseek-harness-sdk`，也可安装本包的 `dsh` extra。

## 接入 dsh

只需导入、创建 recorder，并把 `harness.run(...)` 换成 `recorder.run(harness, ...)`：

```python
from deepseek_harness import DeepSeekHarness
from agent_session_analyzer import DshRecorder

with DeepSeekHarness(dsh_home="./config/dsh") as harness, DshRecorder("./logs/harness") as recorder:
    result = recorder.run(harness, "请计算 1 + 1")
    print(result.final_response)
    print(recorder.last_path)
```

已有回调仍然可以传入：

```python
result = recorder.run(harness, prompt, on_notification=on_notification)
```

省略 `session_id` 时每次 run 创建新会话。多轮对话显式复用 ID：

```python
recorder.run(harness, "介绍项目", session_id="project-analysis")
recorder.run(harness, "解释调度模块", session_id="project-analysis")
```

以上调用放在同一个 recorder / harness 的上下文中。保留原 SDK 的 `RunResult`。未指定日志目录时默认写入当前工作目录的 `logs/harness`。

## 查看日志

在自己的项目根目录运行：

```bash
agent-session-analyzer
```

打开 **http://127.0.0.1:8765**。默认读取当前工作目录下的 `logs/harness` 和 `config/dsh/sessions`，点击刷新更新文件和当前轨迹。

指定目录及端口：

```bash
agent-session-analyzer --root ./logs/harness --root ./config/dsh/sessions --port 8766
```

也支持 `python -m agent_session_analyzer`。在 uv 项目中可使用 `uv run agent-session-analyzer`。

查看器只监听本机，不上传文件，不需要 CDN。可筛选文件，展开 turn/step、思考文本、工具参数、工具结果和原始事件。单文件上限 100 MiB；暂不读取 `.zstd` 或旧版 dsh 日志。

## 数据协议与统计口径

每行一个事件，`schema` 为 `harness-trace/1`：

| 字段 | 含义 |
| --- | --- |
| `session_id` | 原生会话 ID |
| `recording_id` | 采集器实例 ID |
| `run_id` | 一次 Python run 调用 |
| `turn_id` / `step_id` | 原生轮次 / 步骤编号 |
| `attempt_id` | session 内的 `turn/step/attempt_index` |
| `tool_call_id` | 关联工具调用与工具结果 |
| `source_seq` / `time_ms` | 原生事件序号 / 时间 |
| `observed_at_ms` | 采集接收时间 |
| `model` / `data` | 模型信息与归一化内容 |
| `raw` | 完整原始事件及 stream |

保留原生 turn/step 边界事件；模型结算映射为 `model.attempt`，工具映射为 `tool.call` / `tool.result`。同一个 recorder 中每个 session 一个文件，子会话单独保存并带 root session ID。新的 recorder 为同一 session 创建新分片，不覆盖已有文件；页面当前按文件显示，不跨分片合并。并行运行请各建一个 recorder。

* 每个已结算的 `assistant/message` 或 `assistant/attempt` 对应一个已观测模型尝试。底层完全隐藏的 HTTP 重试不可见。
* `usage_raw` 保留 SDK 值；`data.usage` 统一命名。只累加每次尝试的汇总 usage，不重复累加 stream 内 usage。
* input 可能不含缓存输入，reasoning 可能已包含在 output 中；不擅自计算 total。缺失是 null，界面显示已知部分及覆盖率。
* 思考仅记录提供方实际返回的内容。模型在结算前崩溃时，可能只有未结束 step，不伪造 token 或结果。
* step 耗时包含模型和工具等阶段；工具耗时是 call 到 result 的事件间隔，不等同于精确执行耗时。
* 原生文件可能含历史继承或恢复补齐事件，页面显示提醒；统计覆盖整个文件，不自动当成本次实验成本。
* 完整保留任务文本和工具结果，适合本地研究。不会复制 SDK 配置中的 API key / env，但日志内容本身仍可能含敏感信息。

每行 flush，run 边界和关闭时 fsync。可捕获的异常会记录 `run.failed`，写入失败会向调用方传播。读取只容忍末行尚未写完，中间损坏报错。

## 开发与测试

```bash
uv venv
uv pip install -e .
uv run python -m unittest discover -s tests -v
uv build
```

测试只使用模拟事件，不执行真实模型或工具。源码位于 `src/agent_session_analyzer`；前端资源随 wheel 一起安装。
