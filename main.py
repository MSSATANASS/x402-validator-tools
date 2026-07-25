import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python main.py <cli|mcp|proxy|dashboard> [args...]")
        sys.exit(1)

    command = args[0]
    rest = args[1:]

    if command == "cli":
        sys.argv = ["x402-validate", *rest]
        from x402_validator.cli import main as cli_main
        cli_main()
    elif command == "mcp":
        sys.argv = ["x402-mcp", *rest]
        from x402_validator.mcp_server import main as mcp_main
        mcp_main()
    elif command == "proxy":
        from proxy_middleware import main as proxy_main
        proxy_main()
    elif command == "dashboard":
        from dashboard.app import app
        host = rest[0] if rest else "0.0.0.0"
        port = int(rest[1]) if len(rest) > 1 else 5000
        app.run(host=host, port=port, debug=False)
    else:
        print(f"Unknown command: {command}")
        print("Available: cli, mcp, proxy, dashboard")
        sys.exit(1)


if __name__ == "__main__":
    main()
