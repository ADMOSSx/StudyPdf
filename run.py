"""Launcher.  `python run.py` starts the desktop client, `python run.py server [opts]` the server."""
import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from server.main import main as server_main
        server_main()
    else:
        from client.main import main as client_main
        client_main()


if __name__ == "__main__":
    main()
