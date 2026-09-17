# agent-session-analyzer

轻量 Python 包：记录 Agent 运行过程为 JSONL，并通过本地页面查看。

层级：**session → turn → step → model attempt / tool call**。

支持 dsh 同步 Python SDK；当前源码还新增了 OpenHands 本地同步 SDK 适配（静态参照 1.49.1，未执行验证）。记录模型信息、token 消耗、接口返回的思考内容、工具参数和结果；保留原始事件。查看器支持本包日志及原生未压缩的 dsh `session.v3.jsonl`。

## 安装

Python 3.12+。仓库为私有仓库，需要当前 Git 已有访问权限。使用 GitHub CLI 的机器可先执行 `gh auth login` 和 `gh auth setup-git` 配置 HTTPS 认证：

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
| `turn_id` / `step_id` | 来源轮次 / 步骤编号；OpenHands 为 recorder 定义的派生分组 |
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
## OpenHands 接入（本地新增，尚未发布）

这部分代码尚未包含在此前的 `v0.1.0` 标签中。需要从当前源码安装，
例如在本仓库目录执行 `pip install -e ".[openhands]"`。
适配依据为 OpenHands SDK 1.49.1 的本地同步接口，尚未执行运行验证。

```python
import os
from agent_session_analyzer import OpenHandsRecorder

# agent 使用你原有的 OpenHands Agent，保留原来的 LLM 和 tools 配置。
with OpenHandsRecorder("./logs/harness") as recorder:
    conversation = recorder.create_conversation(agent=agent, workspace=os.getcwd())
    try:
        recorder.run(conversation, "你的任务")
        # 再次 recorder.run(conversation, "后续问题") 会写入同一 session 的新 turn。
        # 首次运行必须传 task；之后 run(conversation) 可恢复暂停的任务。
        print(recorder.last_path)
    finally:
        conversation.close()
```

`OpenHandsRecorder` 管理 JSONL 文件、Conversation 事件回调、LLM completion
和 retry 回调。它串联并恢复已有回调，但不负责关闭 Conversation。
已有事件回调可通过 `create_conversation(callbacks=[...])` 传入。
`OpenHandsAdapter` 是不依赖 SDK 的纯转换器，公开
`begin_turn(task)`、`event(event)`、`completion(filename, log_data, llm_key="main")`、
`retry(llm_key, attempt_number, num_retries, error)`、`end_turn(status)`；
每个方法返回可写入 JSONL 的记录列表。独立使用时需要自行调度回调、串行化和写文件。

记录边界和限制：

- 一个 Conversation 对应一个 session，每次 recorder.run 对应一个 turn，包含恢复调用。
- step 按模型响应及相关工具调用归组，并非 SDK 原生 Agent.step 调用计数。
  无请求开始回调，所以 step 开始时间为 null，不应据此计算精确 step 时长。
- 同一 llm_response_id 只统计一次模型响应，多个工具通过 tool_call_id 关联结果。
  retry_listener 记录 SDK 暴露的中间失败，最终 completion 记录最终尝试。
- token 使用 response.usage；缺失为 null。缓存、推理 token 为分类指标，不能再加到 total_tokens。
  completion 缺失时记录警告和 event_only，不把未知 token 当成零。
- 思考内容仅来自可见的 reasoning_content、thinking blocks 或 Responses reasoning summary。
  普通 thought 独立保留；不可见或加密的推理不尝试还原。raw 保留原始输入输出，请自行控制日志访问。
- 当前仅支持本地同步 Conversation、串行运行，不支持远程/异步及并发共享 LLM。
  钩子在 send_message 初始化完成后挂载，只覆盖 run 期间已登记的 LLM；
  初始化调用、动态替换 LLM、供应商内部重试不保证覆盖。
- SDK 只有 completion callback setter，没有 getter，因此为恢复旧回调读取了
  `_log_completions_callback` 私有字段；升级 SDK 时需检查此处契约。
  未设置旧回调时，运行期间由 recorder 接管 completion 日志出口。
- paused、waiting_for_confirmation、stuck、error 均保留，不算 completed。

离线测试代码位于 `tests/test_openhands.py`，不请求模型，不需要 SDK 或 API key。
先从当前源码安装包，再执行 `python -m unittest discover -s tests -v`。
另有 `examples/openhands_record.py` 可用于真实接入验证，会请求模型并产生费用。
本次未执行测试，由用户自行验证。


## 工程结构

实现按 adapters/ 与 recorders/ 分层，通用协议版本位于 schema.py。旧版平铺模块已删除，请使用包顶层接口或 adapters/recorders 下的模块。目录职责、新增 harness 流程和后续建议见 [ARCHITECTURE.md](ARCHITECTURE.md)。

### 从旧模块迁移

推荐统一使用公开入口：

```python
from agent_session_analyzer import DshAdapter, DshRecorder, OpenHandsAdapter, OpenHandsRecorder
from agent_session_analyzer.schema import SCHEMA
```

需要直接访问实现时，使用 `agent_session_analyzer.adapters.dsh`、
`agent_session_analyzer.adapters.openhands`、`agent_session_analyzer.recorders.dsh`
和 `agent_session_analyzer.recorders.openhands`。
此前的 `adapter`、`recorder`、`openhands_adapter`、`openhands_recorder` 平铺模块已移除，
外部项目若使用这些旧路径，也需要更新导入。包顶层接口与 JSONL 协议不变。
