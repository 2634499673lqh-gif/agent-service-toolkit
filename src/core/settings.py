from enum import StrEnum
from json import loads
from typing import Annotated, Any

from dotenv import find_dotenv
from pydantic import (
    BeforeValidator,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from schema.models import (
    AllModelEnum,
    AnthropicModelName,
    AWSModelName,
    AzureOpenAIModelName,
    DeepseekModelName,
    FakeModelName,
    GoogleModelName,
    GroqModelName,
    OllamaModelName,
    OpenAICompatibleName,
    OpenAIModelName,
    OpenRouterModelName,
    Provider,
    VertexAIModelName,
)


class DatabaseType(StrEnum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MONGO = "mongo"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    def to_logging_level(self) -> int:
        """Convert to Python logging level constant."""
        import logging

        mapping = {
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.CRITICAL: logging.CRITICAL,
        }
        return mapping[self]


def check_str_is_http(x: str) -> str:
    http_url_adapter = TypeAdapter(HttpUrl)
    return str(http_url_adapter.validate_python(x))


def validate_optional_http_preserve(x: str | None) -> str | None:
    """Validate an optional URL without changing its configured spelling."""
    if x is not None:
        TypeAdapter(HttpUrl).validate_python(x)
    return x


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=find_dotenv(),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        validate_default=False,
    )
    MODE: str | None = None

    HOST: str = "0.0.0.0"
    PORT: int = Field(default=8080, ge=1, le=65535)
    GRACEFUL_SHUTDOWN_TIMEOUT: int = Field(default=30, ge=0)
    LOG_LEVEL: LogLevel = LogLevel.WARNING

    AUTH_SECRET: SecretStr | None = None

    OPENAI_API_KEY: SecretStr | None = None
    DEEPSEEK_API_KEY: SecretStr | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    GOOGLE_API_KEY: SecretStr | None = None
    GOOGLE_APPLICATION_CREDENTIALS: SecretStr | None = None
    GROQ_API_KEY: SecretStr | None = None
    USE_AWS_BEDROCK: bool = False
    OLLAMA_MODEL: str | None = None
    OLLAMA_BASE_URL: Annotated[str | None, BeforeValidator(validate_optional_http_preserve)] = None
    USE_FAKE_MODEL: bool = False
    OPENROUTER_API_KEY: SecretStr | None = None

    # If DEFAULT_MODEL is None, it will be set in model_post_init.  Custom
    # provider names (for example Ollama or OpenAI-compatible models) are also
    # valid and are kept as strings.
    DEFAULT_MODEL: AllModelEnum | str | None = None
    AVAILABLE_MODELS: set[AllModelEnum] = Field(default_factory=set)  # type: ignore[assignment]

    # Set openai compatible api, mainly used for proof of concept
    COMPATIBLE_MODEL: str | None = None
    COMPATIBLE_API_KEY: SecretStr | None = None
    COMPATIBLE_BASE_URL: str | None = None

    OPENWEATHERMAP_API_KEY: SecretStr | None = None

    # MCP Configuration
    GITHUB_PAT: SecretStr | None = None
    MCP_GITHUB_SERVER_URL: str = "https://api.githubcopilot.com/mcp/"

    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_PROJECT: str = "default"
    LANGCHAIN_ENDPOINT: Annotated[str, BeforeValidator(check_str_is_http)] = (
        "https://api.smith.langchain.com"
    )
    LANGCHAIN_API_KEY: SecretStr | None = None

    LANGFUSE_TRACING: bool = False
    LANGFUSE_HOST: Annotated[str, BeforeValidator(check_str_is_http)] = "https://cloud.langfuse.com"
    LANGFUSE_PUBLIC_KEY: SecretStr | None = None
    LANGFUSE_SECRET_KEY: SecretStr | None = None

    # Database Configuration
    DATABASE_TYPE: DatabaseType = (
        DatabaseType.SQLITE
    )  # Options: DatabaseType.SQLITE or DatabaseType.POSTGRES
    SQLITE_DB_PATH: str = "checkpoints.db"

    # TaskPilot business persistence is an independent PostgreSQL-only URL.
    # It is intentionally separate from DATABASE_TYPE, which selects the
    # upstream LangGraph checkpoint backend.
    TASKPILOT_DATABASE_URL: SecretStr | None = None

    # PostgreSQL Configuration
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: SecretStr | None = None
    POSTGRES_HOST: str | None = None
    POSTGRES_PORT: int | None = Field(default=None, ge=1, le=65535)
    POSTGRES_DB: str | None = None
    POSTGRES_APPLICATION_NAME: str = "agent-service-toolkit"
    POSTGRES_MIN_CONNECTIONS_PER_POOL: int = Field(default=1, ge=1)
    POSTGRES_MAX_CONNECTIONS_PER_POOL: int = Field(default=1, ge=1)

    # MongoDB Configuration
    MONGO_HOST: str | None = None
    MONGO_PORT: int | None = Field(default=None, ge=1, le=65535)
    MONGO_DB: str | None = None
    MONGO_USER: str | None = None
    MONGO_PASSWORD: SecretStr | None = None
    MONGO_AUTH_SOURCE: str | None = None
    MONGO_TLS: bool = False  # opt-in TLS for MongoDB; set to True for production/Atlas

    # Azure OpenAI Settings
    AZURE_OPENAI_API_KEY: SecretStr | None = None
    AZURE_OPENAI_ENDPOINT: Annotated[
        str | None, BeforeValidator(validate_optional_http_preserve)
    ] = None
    AZURE_OPENAI_API_VERSION: str = "2024-02-15-preview"
    AZURE_OPENAI_DEPLOYMENT_MAP: dict[str, str] = Field(
        default_factory=dict, description="Map of model names to Azure deployment IDs"
    )

    def model_post_init(self, __context: Any) -> None:
        # Compute the provider catalogue per instance; settings objects should
        # never leak model choices into one another.
        self.AVAILABLE_MODELS = set()
        self._validate_database_config()
        self._validate_taskpilot_database_config()
        self._validate_optional_configuration()

        api_keys = {
            Provider.OPENAI: self.OPENAI_API_KEY,
            Provider.OPENAI_COMPATIBLE: self.COMPATIBLE_BASE_URL and self.COMPATIBLE_MODEL,
            Provider.DEEPSEEK: self.DEEPSEEK_API_KEY,
            Provider.ANTHROPIC: self.ANTHROPIC_API_KEY,
            Provider.GOOGLE: self.GOOGLE_API_KEY,
            Provider.VERTEXAI: self.GOOGLE_APPLICATION_CREDENTIALS,
            Provider.GROQ: self.GROQ_API_KEY,
            Provider.AWS: self.USE_AWS_BEDROCK,
            Provider.OLLAMA: self.OLLAMA_MODEL,
            Provider.FAKE: self.USE_FAKE_MODEL,
            Provider.AZURE_OPENAI: self.AZURE_OPENAI_API_KEY,
            Provider.OPENROUTER: self.OPENROUTER_API_KEY,
        }
        active_keys = [k for k, v in api_keys.items() if self._has_value(v)]
        if not active_keys:
            raise ValueError("At least one LLM API key must be provided.")

        # USE_FAKE_MODEL must win the default even when real provider keys are present.
        if self.USE_FAKE_MODEL and self.DEFAULT_MODEL is None:
            self.DEFAULT_MODEL = FakeModelName.FAKE

        for provider in active_keys:
            match provider:
                case Provider.OPENAI:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = OpenAIModelName.GPT_5_NANO
                    self.AVAILABLE_MODELS.update(set(OpenAIModelName))
                case Provider.OPENAI_COMPATIBLE:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = OpenAICompatibleName.OPENAI_COMPATIBLE
                    self.AVAILABLE_MODELS.update(set(OpenAICompatibleName))
                case Provider.DEEPSEEK:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = DeepseekModelName.DEEPSEEK_V4_FLASH
                    self.AVAILABLE_MODELS.update(set(DeepseekModelName))
                case Provider.ANTHROPIC:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = AnthropicModelName.HAIKU_45
                    self.AVAILABLE_MODELS.update(set(AnthropicModelName))
                case Provider.GOOGLE:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = GoogleModelName.GEMINI_38_FLASH
                    self.AVAILABLE_MODELS.update(set(GoogleModelName))
                case Provider.VERTEXAI:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = VertexAIModelName.GEMINI_38_FLASH
                    self.AVAILABLE_MODELS.update(set(VertexAIModelName))
                case Provider.GROQ:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = GroqModelName.GPT_OSS_20B
                    self.AVAILABLE_MODELS.update(set(GroqModelName))
                case Provider.AWS:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = AWSModelName.BEDROCK_HAIKU
                    self.AVAILABLE_MODELS.update(set(AWSModelName))
                case Provider.OLLAMA:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = OllamaModelName.OLLAMA_GENERIC
                    self.AVAILABLE_MODELS.update(set(OllamaModelName))
                case Provider.OPENROUTER:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = OpenRouterModelName.GEMINI_38_FLASH
                    self.AVAILABLE_MODELS.update(set(OpenRouterModelName))
                case Provider.FAKE:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = FakeModelName.FAKE
                    self.AVAILABLE_MODELS.update(set(FakeModelName))
                case Provider.AZURE_OPENAI:
                    if self.DEFAULT_MODEL is None:
                        self.DEFAULT_MODEL = AzureOpenAIModelName.AZURE_GPT_5_MINI
                    self.AVAILABLE_MODELS.update(set(AzureOpenAIModelName))
                    # Validate Azure OpenAI settings if Azure provider is available
                    if not self.AZURE_OPENAI_API_KEY:
                        raise ValueError("AZURE_OPENAI_API_KEY must be set")
                    if not self.AZURE_OPENAI_ENDPOINT:
                        raise ValueError("AZURE_OPENAI_ENDPOINT must be set")
                    if not self.AZURE_OPENAI_DEPLOYMENT_MAP:
                        raise ValueError("AZURE_OPENAI_DEPLOYMENT_MAP must be set")

                    # Parse deployment map if it's a string
                    if isinstance(self.AZURE_OPENAI_DEPLOYMENT_MAP, str):
                        try:
                            self.AZURE_OPENAI_DEPLOYMENT_MAP = loads(
                                self.AZURE_OPENAI_DEPLOYMENT_MAP
                            )
                        except Exception as e:
                            raise ValueError(f"Invalid AZURE_OPENAI_DEPLOYMENT_MAP JSON: {e}")

                    # Validate required deployments exist
                    required_models = {"gpt-5", "gpt-5-mini"}
                    missing_models = required_models - set(self.AZURE_OPENAI_DEPLOYMENT_MAP.keys())
                    if missing_models:
                        raise ValueError(f"Missing required Azure deployments: {missing_models}")
                case _:
                    raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _has_value(value: Any) -> bool:
        """Return whether a setting contains a meaningful, non-blank value."""
        if value is None:
            return False
        if isinstance(value, SecretStr):
            return bool(value.get_secret_value().strip())
        if isinstance(value, str):
            return bool(value.strip())
        return bool(value)

    def _validate_database_config(self) -> None:
        """Validate only the selected persistence backend's configuration."""
        if self.DATABASE_TYPE == DatabaseType.SQLITE:
            return

        if self.DATABASE_TYPE == DatabaseType.POSTGRES:
            required = {
                "POSTGRES_USER": self.POSTGRES_USER,
                "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
                "POSTGRES_HOST": self.POSTGRES_HOST,
                "POSTGRES_PORT": self.POSTGRES_PORT,
                "POSTGRES_DB": self.POSTGRES_DB,
            }
            missing = [name for name, value in required.items() if not self._has_value(value)]
            if missing:
                raise ValueError(
                    "Missing required PostgreSQL configuration: "
                    + ", ".join(missing)
                    + ". These settings are required when DATABASE_TYPE=postgres."
                )
            if self.POSTGRES_MIN_CONNECTIONS_PER_POOL > self.POSTGRES_MAX_CONNECTIONS_PER_POOL:
                raise ValueError(
                    "POSTGRES_MIN_CONNECTIONS_PER_POOL must be less than or equal to "
                    "POSTGRES_MAX_CONNECTIONS_PER_POOL"
                )
            return

        if self.DATABASE_TYPE == DatabaseType.MONGO:
            required = {
                "MONGO_HOST": self.MONGO_HOST,
                "MONGO_PORT": self.MONGO_PORT,
                "MONGO_DB": self.MONGO_DB,
            }
            missing = [name for name, value in required.items() if not self._has_value(value)]
            if missing:
                raise ValueError(
                    "Missing required MongoDB configuration: "
                    + ", ".join(missing)
                    + ". These settings are required when DATABASE_TYPE=mongo."
                )

            auth = {
                "MONGO_USER": self.MONGO_USER,
                "MONGO_PASSWORD": self.MONGO_PASSWORD,
                "MONGO_AUTH_SOURCE": self.MONGO_AUTH_SOURCE,
            }
            configured_auth = [name for name, value in auth.items() if self._has_value(value)]
            if configured_auth and len(configured_auth) != len(auth):
                raise ValueError(
                    "MongoDB authentication settings must be provided together: " + ", ".join(auth)
                )

    def _validate_taskpilot_database_config(self) -> None:
        """Validate an explicitly configured TaskPilot business database URL."""

        if not self._has_value(self.TASKPILOT_DATABASE_URL):
            return
        raw_url = self.TASKPILOT_DATABASE_URL.get_secret_value()  # type: ignore[union-attr]
        if not raw_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("TASKPILOT_DATABASE_URL must use PostgreSQL (postgresql[+psycopg]://)")

    def _validate_optional_configuration(self) -> None:
        """Reject partial opt-in configuration while preserving optional fallbacks."""
        compatible = {
            "COMPATIBLE_MODEL": self.COMPATIBLE_MODEL,
            "COMPATIBLE_API_KEY": self.COMPATIBLE_API_KEY,
            "COMPATIBLE_BASE_URL": self.COMPATIBLE_BASE_URL,
        }
        if any(self._has_value(value) for value in compatible.values()):
            missing = [name for name, value in compatible.items() if not self._has_value(value)]
            # API keys are optional for local OpenAI-compatible servers; model
            # and endpoint identify this provider.
            missing = [name for name in missing if name != "COMPATIBLE_API_KEY"]
            if missing:
                raise ValueError("OpenAI-compatible provider requires: " + ", ".join(missing))

        if self._has_value(self.OLLAMA_BASE_URL) and not self._has_value(self.OLLAMA_MODEL):
            raise ValueError("OLLAMA_MODEL must be set when OLLAMA_BASE_URL is configured")

        azure = {
            "AZURE_OPENAI_API_KEY": self.AZURE_OPENAI_API_KEY,
            "AZURE_OPENAI_ENDPOINT": self.AZURE_OPENAI_ENDPOINT,
            "AZURE_OPENAI_DEPLOYMENT_MAP": self.AZURE_OPENAI_DEPLOYMENT_MAP,
        }
        if any(self._has_value(value) for value in azure.values()):
            missing = [
                name
                for name, value in {
                    "AZURE_OPENAI_API_KEY": self.AZURE_OPENAI_API_KEY,
                    "AZURE_OPENAI_ENDPOINT": self.AZURE_OPENAI_ENDPOINT,
                }.items()
                if not self._has_value(value)
            ]
            if missing:
                raise ValueError(
                    "Azure OpenAI settings must be provided together: " + ", ".join(missing)
                )

        if self.LANGCHAIN_TRACING_V2 and not self._has_value(self.LANGCHAIN_API_KEY):
            raise ValueError("LANGCHAIN_API_KEY must be set when LANGCHAIN_TRACING_V2 is enabled")
        if self.LANGFUSE_TRACING:
            missing = [
                name
                for name, value in {
                    "LANGFUSE_PUBLIC_KEY": self.LANGFUSE_PUBLIC_KEY,
                    "LANGFUSE_SECRET_KEY": self.LANGFUSE_SECRET_KEY,
                }.items()
                if not self._has_value(value)
            ]
            if missing:
                raise ValueError("Langfuse tracing requires: " + ", ".join(missing))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def BASE_URL(self) -> str:
        return f"http://{self.HOST}:{self.PORT}"

    def is_dev(self) -> bool:
        return self.MODE == "dev"


settings = Settings()
