"""全局配置 — 基于 pydantic-settings."""

from urllib.parse import quote_plus

from pydantic_settings import BaseSettings

# PC 端扫码登录完成后落回的页面（web-pc 里的前端路由）
WEB_OAUTH_CALLBACK_PATH = "/web-pc/oauth/callback"


class Settings(BaseSettings):
    """全局应用配置."""

    # 应用
    APP_NAME: str = "PhotoService"
    APP_ENV: str = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # 数据库
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_USER: str = "photo_app"
    DB_PASSWORD: str = ""
    DB_NAME: str = "photo_service"
    DATABASE_URL: str = ""  # 可直接设置完整 URL，为空则自动拼接

    @property
    def DATABASE_URL_FINAL(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        pwd = quote_plus(self.DB_PASSWORD) if self.DB_PASSWORD else ""
        return f"mysql+asyncmy://{self.DB_USER}:{pwd}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def DATABASE_URL_SYNC(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL.replace("+aiosqlite", "+pysqlite").replace("+asyncmy", "+pymysql")
        pwd = quote_plus(self.DB_PASSWORD) if self.DB_PASSWORD else ""
        return f"mysql+pymysql://{self.DB_USER}:{pwd}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def IS_SQLITE(self) -> bool:
        """当前是否落在 SQLite（本项目生产只支持 MySQL）."""
        url = self.DATABASE_URL_FINAL
        return url.startswith("sqlite")

    def runtime_config_errors(self) -> list:
        """启动自检：返回致命配置错误列表（空列表 = 通过）.

        目的：把「配置写错」在**启动那一刻**用一句人话讲清楚，
        而不是等到 worker 起来后才抛出难懂的
        ModuleNotFoundError: No module named 'aiosqlite'，
        或运行期才出现 Settings has no attribute 之类的 AttributeError。
        历史事故：服务器 .env 误留了开发用的
        DATABASE_URL=sqlite+aiosqlite:///./test.db，导致容器无限重启。
        """
        errors = []

        # 1) 生产环境禁止 SQLite：并发写入与 ON DUPLICATE KEY UPDATE 都不成立
        if self.APP_ENV == "production" and self.IS_SQLITE:
            errors.append(
                "生产环境禁止使用 SQLite。当前 DATABASE_URL 指向 sqlite "
                f"({self.DATABASE_URL_FINAL})。请改为 MySQL，例如: "
                "mysql+asyncmy://user:password@mysql-host:3306/photo_service"
            )

        # 2) 数据库连接信息缺失（既没给完整 URL，也没给 DB_HOST）
        if not self.DATABASE_URL and not self.DB_HOST:
            errors.append("数据库未配置：请设置 DATABASE_URL 或 DB_HOST/DB_USER/DB_NAME")

        # 3) Redis 地址格式明显不对时给出提示（Redis 不可用会降级，故只是错误）
        if self.REDIS_URL and not self.REDIS_URL.startswith(("redis://", "rediss://", "unix://")):
            errors.append(f"REDIS_URL 格式非法（应以 redis:// 开头）: {self.REDIS_URL}")

        return errors

    # Redis
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Web 登录密码（Web 桌面版专用，无需 OAuth）
    WEB_LOGIN_PASSWORD: str = ""

    # 管理后台初始管理员（用户名固定 admin）
    # ADMIN_INIT_PASSWORD：初始/重置用的密码，留空则用代码默认值 admin_test
    # ADMIN_RESET_PASSWORD_ON_BOOT：true 时每次启动强制把密码重置为上面的值
    #   （忘了密码时临时打开，重置完请关掉；默认 false，不再覆盖已改过的密码）
    ADMIN_INIT_PASSWORD: str = ""
    ADMIN_RESET_PASSWORD_ON_BOOT: bool = False

    # 每日免费次数的「一天」按哪个时区划分（默认北京时间）
    # 只影响每日赠送的重置时刻，不影响任何时间戳的存储（库里仍存 UTC）
    DAILY_BONUS_TIMEZONE: str = "Asia/Shanghai"

    # 微信开放平台
    WECHAT_MINI_APPID: str = ""
    WECHAT_MINI_SECRET: str = ""
    WECHAT_WEB_APPID: str = ""
    WECHAT_WEB_SECRET: str = ""

    # PC 端扫码登录回调地址。
    # 必须与微信开放平台「授权回调域」/ 支付宝应用登记的回调地址一致，
    # 否则平台会拒绝跳转。留空时用 PUBLIC_BASE_URL 拼默认值。
    WECHAT_WEB_REDIRECT_URI: str = ""
    ALIPAY_REDIRECT_URI: str = ""
    # 站点对外基地址，例如 http://119.91.157.252:8081 或 https://your-domain.com
    PUBLIC_BASE_URL: str = ""

    # 微信支付
    WECHAT_MCH_ID: str = ""
    WECHAT_API_V3_KEY: str = ""
    WECHAT_PRIVATE_KEY_PATH: str = ""
    WECHAT_CERT_SERIAL_NO: str = ""
    WECHAT_NOTIFY_URL: str = ""

    # 支付宝
    ALIPAY_APP_ID: str = ""
    ALIPAY_PRIVATE_KEY_PATH: str = ""
    ALIPAY_PUBLIC_KEY_PATH: str = ""
    ALIPAY_NOTIFY_URL: str = ""
    ALIPAY_RETURN_URL: str = ""

    # 文件
    UPLOAD_MAX_MB: int = 10
    TEMP_FILE_TTL_MINUTES: int = 5
    TEMP_FILE_DIR: str = "/data/temp"

    # CORS
    CORS_ORIGINS: str = ""  # 逗号分隔的允许域名列表，为空则根据 APP_ENV 自动设置

    # 限流
    RATE_LIMIT_PER_MINUTE: int = 60

    # 维护模式
    MAINTENANCE_MODE: bool = False

    def web_oauth_redirect_uri(self, provider: str) -> str:
        """PC 端扫码登录的回调地址。

        显式配置优先；否则用 PUBLIC_BASE_URL + 默认回调路径拼出来。
        两者都没有时返回空串，由调用方给出明确的配置提示（而不是 500）。
        """
        explicit = {
            "wechat": self.WECHAT_WEB_REDIRECT_URI,
            "alipay": self.ALIPAY_REDIRECT_URI,
        }.get(provider, "")
        if explicit and explicit.strip():
            return explicit.strip()

        base = (self.PUBLIC_BASE_URL or "").strip().rstrip("/")
        if base:
            return f"{base}{WEB_OAUTH_CALLBACK_PATH}"
        return ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
