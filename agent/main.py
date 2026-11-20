import sys

from maa.agent.agent_server import AgentServer

# import 副作用即完成自定义识别/动作注册（冬雪乐园翻牌等）
import park


def main():
    # AgentServer 上下文中 Toolkit 不可用，静默忽略
    try:
        from maa.toolkit import Toolkit

        Toolkit.init_option("./")
    except Exception:
        pass

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
