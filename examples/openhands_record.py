"""Running this example invokes a real model; it is not an offline test."""
import os

from openhands.sdk import LLM, Agent
from agent_session_analyzer import OpenHandsRecorder


def main():
    llm = LLM(model="deepseek/deepseek-flash",
              api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    agent = Agent(llm=llm, tools=[])
    with OpenHandsRecorder("./logs/harness") as recorder:
        conversation = recorder.create_conversation(agent=agent, workspace=os.getcwd())
        try:
            recorder.run(conversation, "请计算 1 + 1，不要调用任何工具。")
            print("Trace:", recorder.last_path)
        finally:
            conversation.close()


if __name__ == "__main__":
    main()
