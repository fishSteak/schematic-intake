"""Launch the local web workbench."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="本机原理图评审工作台")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["SCHEMATIC_WORKBENCH_HOME"] = args.data_dir
    import uvicorn
    uvicorn.run("web_workbench.app:create_app", factory=True, host=args.host, port=args.port, access_log=True)


if __name__ == "__main__":
    main()
