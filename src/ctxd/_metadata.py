from importlib.metadata import PackageNotFoundError, version

SDK_NAME = "ctxd"


def get_sdk_version() -> str:
    try:
        return version(SDK_NAME)
    except PackageNotFoundError:
        return "0.0.0+local"


SDK_VERSION = get_sdk_version()


def get_user_agent() -> str:
    return f"{SDK_NAME}/{SDK_VERSION}"
