"""A minimal example. Running it starts dsh and may incur model usage."""
from agent_session_analyzer import DshRecorder
from deepseek_harness import DeepSeekHarness


def main():
    with DeepSeekHarness(dsh_home="./config/dsh") as harness, DshRecorder() as recorder:
        result = recorder.run(harness, "请计算 1 + 1")
        print(result.final_response)
        print("Trace:", recorder.last_path)


if __name__ == "__main__":
    main()
