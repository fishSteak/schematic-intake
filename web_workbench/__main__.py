"""Launch the local web workbench."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="本机原理图评审工作台")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir")
    parser.add_argument("--root-path", help="公开子路径，例如 /schematic（默认空）")
    parser.add_argument("--allowed-hosts", help="允许的主机名，逗号分隔，不含端口或通配符")
    parser.add_argument("--auth-check-url", help="固定回环认证 GET 地址；默认不启用外部认证")
    parser.add_argument("--login-url", help="主站同源登录路径，默认 /login")
    parser.add_argument("--ui-dir", help="前端构建目录，默认 report_ui/dist")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["SCHEMATIC_WORKBENCH_HOME"] = args.data_dir
    for name in ("root_path", "allowed_hosts", "auth_check_url", "login_url", "ui_dir"):
        value = getattr(args, name)
        if value is not None:
            os.environ["SCHEMATIC_" + name.upper()] = value
    import uvicorn
    uvicorn.run("web_workbench.app:create_app", factory=True, host=args.host, port=args.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1,::1", access_log=True)


if __name__ == "__main__":
    main()
