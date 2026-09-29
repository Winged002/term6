from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None

from .models import ReasoningMode


@dataclass(slots=True)
class ModelConfig:
    provider: str = "deepseek"
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    fim_base_url: str = "https://api.deepseek.com/beta"
    total_context_tokens: int = 1_048_576
    context_limit: int = 1_000_000
    max_output_tokens: int = 384_000
    temperature: float = 0.2


@dataclass(slots=True)
class ReasoningConfig:
    default: str = "auto"
    max_parallel_high: int = 8
    max_parallel_total: int = 32
    worker_timeout_s: int = 120
    worker_retries: int = 1


@dataclass(slots=True)
class ExecutiveConfig:
    auto_plan: bool = True
    max_workers: int = 6
    max_plan_tokens: int = 3000
    inject_evidence_chars: int = 32000


@dataclass(slots=True)
class FimConfig:
    enabled: bool = True
    max_parallel: int = 32
    default_max_output_tokens: int = 2048
    hard_max_output_tokens: int = 4096
    repair_attempts: int = 1


@dataclass(slots=True)
class SchedulerConfig:
    max_local_reads: int = 64
    max_process_jobs: int = 4
    max_network_jobs: int = 8
    # 0 means unlimited model/tool rounds. Long turns terminate on a real
    # completion or safety conditions rather than an arbitrary iteration count.
    max_tool_iterations: int = 0
    max_turn_seconds: int = 0
    loop_abort_repeats: int = 8
    verification_timeout_s: int = 300


@dataclass(slots=True)
class SecurityConfig:
    confirm_destructive: bool = True
    fail_closed_on_critical_doctor: bool = True
    allow_process_tests: bool = False
    allow_git_write: bool = False
    allow_git_remote: bool = False
    allow_git_provider_write: bool = False
    allow_network: bool = False
    allow_nginx_write: bool = False
    allow_tls_issue: bool = False
    allow_server_write: bool = False


@dataclass(slots=True)
class ContextConfig:
    cache_stable: bool = True
    memory_mode: str = "index+relevant"
    memory_recall_items: int = 5
    memory_recall_chars: int = 6000
    episode_recall_items: int = 5
    episode_recall_chars: int = 8000
    recent_full_turns: int = 4
    active_target_tokens: int = 120_000
    soft_compact_tokens: int = 180_000
    hard_input_tokens: int = 360_000
    safety_tokens: int = 32_000
    tool_inline_chars: int = 24_000
    tool_preview_chars: int = 4_000
    output_none_tokens: int = 16_000
    output_low_tokens: int = 24_000
    output_high_tokens: int = 64_000
    output_max_tokens: int = 96_000


@dataclass(slots=True)
class UIConfig:
    web_enabled: bool = False
    web_host: str = "127.0.0.1"
    web_port: int = 0
    open_browser: bool = False


@dataclass(slots=True)
class BrowserConfig:
    enabled: bool = True
    headless: bool = True
    allow_loopback: bool = True
    allow_public: bool = False
    allow_interaction: bool = True
    allowed_hosts: list[str] = field(default_factory=list)
    timeout_s: int = 30
    navigation_timeout_s: int = 45
    max_batch_pages: int = 16
    screenshot_full_page: bool = True


@dataclass(slots=True)
class VisionConfig:
    enabled: bool = True
    model: str = ""
    max_tokens: int = 4096
    max_images: int = 4
    max_image_bytes: int = 10 * 1024 * 1024


@dataclass(slots=True)
class ImprovementConfig:
    enabled: bool = True
    auto_plan: bool = True
    plan_tokens: int = 6000
    max_surfaces: int = 64
    max_batch_files: int = 24
    require_coverage: bool = True
    prefer_before_after: bool = True
    visible_impact_min_surfaces: int = 3


@dataclass(slots=True)
class SessionConfig:
    autosave: bool = True
    autosave_every_turn: bool = True
    checkpoint_turns: bool = True




@dataclass(slots=True)
class AppRuntimeConfig:
    enabled: bool = True
    docker_timeout_s: int = 300
    wait_timeout_s: int = 120
    open_browser_after_health: bool = True
    auto_repair: bool = False



@dataclass(slots=True)
class OperationsConfig:
    enabled: bool = True
    command_timeout_s: int = 120
    health_timeout_s: int = 180
    require_git: bool = True
    require_clean_git: bool = True
    require_dns: bool = True
    auto_rollback: bool = False
    nginx_sites_available: str = "/etc/nginx/sites-available"
    nginx_sites_enabled: str = "/etc/nginx/sites-enabled"
    nginx_max_body_mb: int = 64
    managed_services: list[str] = field(default_factory=lambda: ["docker", "nginx"])
    server_disk_warn_percent: int = 90


@dataclass(slots=True)
class ProjectConfig:
    enabled: bool = True
    base_dir: str = "projects"
    auto_adopt_apps: bool = True
    max_projects: int = 200


@dataclass(slots=True)
class GitHubConfig:
    enabled: bool = True
    owner: str = ""
    default_private: bool = True
    max_list: int = 100



@dataclass(slots=True)
class ConfigurationConfig:
    enabled: bool = True
    default_environment: str = "production"
    auto_discover: bool = True
    write_dotenv: bool = True
    values_file: str = "configuration.json"
    secret_file: str = "secrets.json"
    max_variables: int = 500
    scan_max_files: int = 1200
    connection_timeout_s: int = 15




@dataclass(slots=True)
class ProductionConfig:
    enabled: bool = True
    background_monitor: bool = False
    monitor_interval_s: int = 300
    max_snapshots: int = 5000
    log_tail_lines: int = 2000
    probe_timeout_s: int = 10
    incident_5xx_rate_percent: float = 5.0
    incident_5xx_min_requests: int = 20
    verify_max_samples: int = 6
    verify_max_interval_s: int = 60
    nginx_log_dir: str = "/var/log/nginx"



@dataclass(slots=True)
class AutonomyConfig:
    enabled: bool = True
    default_human_timeout_s: int = 30
    scheduler_interval_s: int = 1
    auto_resume: bool = True
    max_open_human_tasks: int = 200


@dataclass(slots=True)
class AgentConfig:
    enabled: bool = True
    auto_start: bool = True
    scheduler_interval_s: int = 1
    max_concurrent_owners: int = 4
    max_concurrent_queries: int = 4
    coordinator_only: bool = True
    central_max_iterations: int = 6
    lease_seconds: int = 1800
    owner_reasoning: str = "high"
    owner_max_iterations: int = 0  # deprecated compatibility field; v6.2-ui2 owners are uncapped
    owner_context_target_tokens: int = 60000
    owner_max_output_tokens: int = 32000
    capsule_task_limit: int = 20
    capsule_message_limit: int = 20
    deep_query_tokens: int = 6000


@dataclass(slots=True)
class CreativeConfig:
    enabled: bool = True
    openai_enabled: bool = True
    model: str = "gpt-image-2.5-flare"
    quality: str = "medium"
    size: str = "1536x1024"
    max_options: int = 4
    api_key_env: str = "OPENAI_API_KEY"
    default_timeout_s: int = 30

@dataclass(slots=True)
class SkillConfig:
    enabled: bool = True
    auto_resolve: bool = True
    auto_product_plan: bool = True
    critique: bool = True
    catalog_chars: int = 6000
    context_chars: int = 24000
    product_plan_tokens: int = 5000

@dataclass(slots=True)
class PricingConfig:
    # Conservative peak prices from the DeepSeek Flash profile supplied for v5.
    # These are estimates only; provider-reported cache split is required.
    cache_hit_per_million: float = 0.006
    cache_miss_per_million: float = 0.30
    output_per_million: float = 1.20


@dataclass(slots=True)
class Term5Config:
    root: Path
    state_dir: Path
    api_key: str = ""
    model: ModelConfig = field(default_factory=ModelConfig)
    reasoning: ReasoningConfig = field(default_factory=ReasoningConfig)
    executive: ExecutiveConfig = field(default_factory=ExecutiveConfig)
    fim: FimConfig = field(default_factory=FimConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    improvement: ImprovementConfig = field(default_factory=ImprovementConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    apps: AppRuntimeConfig = field(default_factory=AppRuntimeConfig)
    operations: OperationsConfig = field(default_factory=OperationsConfig)
    projects: ProjectConfig = field(default_factory=ProjectConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    configuration: ConfigurationConfig = field(default_factory=ConfigurationConfig)
    production: ProductionConfig = field(default_factory=ProductionConfig)
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)
    creative: CreativeConfig = field(default_factory=CreativeConfig)
    skills: SkillConfig = field(default_factory=SkillConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    diagnostics: list[str] = field(default_factory=list)

    @property
    def default_reasoning(self) -> ReasoningMode:
        try:
            return ReasoningMode(self.reasoning.default.lower())
        except Exception:
            return ReasoningMode.AUTO


def _apply_section(obj: Any, values: dict[str, Any], *, section: str = "") -> list[str]:
    unknown: list[str] = []
    for key, value in values.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
        else:
            unknown.append(f"{section + '.' if section else ''}{key}")
    return unknown


def _env_bool(name: str, current: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return current
    return v.strip().lower() in {"1", "true", "yes", "on"}


def load_config(root: str | Path | None = None, config_path: str | Path | None = None) -> Term5Config:
    base = Path(root or os.getcwd()).expanduser().resolve()
    cfg = Term5Config(root=base, state_dir=base / ".term5")

    requested = config_path or os.getenv("TERM5_CONFIG")
    path = Path(requested).expanduser() if requested else base / "term5.toml"
    if path.exists() and tomllib is not None:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        known_sections = {"model", "reasoning", "executive", "fim", "scheduler", "security", "context", "ui", "browser", "vision", "improvement", "session", "apps", "operations", "projects", "github", "configuration", "production", "autonomy", "agents", "creative", "skills", "pricing"}
        for extra in sorted(set(raw) - known_sections):
            cfg.diagnostics.append(f"unknown configuration section: {extra}")
        for name in sorted(known_sections):
            section = raw.get(name)
            if section is None:
                continue
            if not isinstance(section, dict):
                cfg.diagnostics.append(f"configuration section {name} must be a table")
                continue
            for key in _apply_section(getattr(cfg, name), section, section=name):
                cfg.diagnostics.append(f"unknown configuration key: {key}")

    cfg.api_key = os.getenv("DEEPSEEK_API_KEY", cfg.api_key)
    cfg.model.base_url = os.getenv("DEEPSEEK_BASE_URL", cfg.model.base_url)
    cfg.model.fim_base_url = os.getenv("DEEPSEEK_FIM_BASE_URL", cfg.model.fim_base_url)
    cfg.model.model = os.getenv("DEEPSEEK_MODEL", cfg.model.model)
    cfg.reasoning.default = os.getenv("TERM5_REASONING", cfg.reasoning.default)
    cfg.reasoning.max_parallel_total = int(os.getenv("TERM5_MAX_PARALLEL", str(cfg.reasoning.max_parallel_total)))
    cfg.reasoning.max_parallel_high = int(os.getenv("TERM5_MAX_PARALLEL_HIGH", str(cfg.reasoning.max_parallel_high)))
    cfg.reasoning.worker_timeout_s = int(os.getenv("TERM5_WORKER_TIMEOUT", str(cfg.reasoning.worker_timeout_s)))
    cfg.reasoning.worker_retries = int(os.getenv("TERM5_WORKER_RETRIES", str(cfg.reasoning.worker_retries)))
    cfg.scheduler.max_tool_iterations = int(os.getenv("TERM5_MAX_TOOL_ITERATIONS", str(cfg.scheduler.max_tool_iterations)))
    cfg.scheduler.max_turn_seconds = int(os.getenv("TERM5_MAX_TURN_SECONDS", str(cfg.scheduler.max_turn_seconds)))
    cfg.scheduler.loop_abort_repeats = int(os.getenv("TERM5_LOOP_ABORT_REPEATS", str(cfg.scheduler.loop_abort_repeats)))
    cfg.executive.auto_plan = _env_bool("TERM5_AUTO_PLAN", cfg.executive.auto_plan)
    cfg.executive.max_workers = int(os.getenv("TERM5_EXECUTIVE_MAX_WORKERS", str(cfg.executive.max_workers)))
    cfg.executive.max_plan_tokens = int(os.getenv("TERM5_EXECUTIVE_PLAN_TOKENS", str(cfg.executive.max_plan_tokens)))
    cfg.model.total_context_tokens = int(os.getenv("TERM5_TOTAL_CONTEXT_TOKENS", str(cfg.model.total_context_tokens)))
    cfg.model.context_limit = int(os.getenv("TERM5_CONTEXT_LIMIT", str(cfg.model.context_limit)))
    cfg.model.max_output_tokens = int(os.getenv("TERM5_MAX_OUTPUT_TOKENS", str(cfg.model.max_output_tokens)))
    cfg.context.active_target_tokens = int(os.getenv("TERM5_ACTIVE_TARGET_TOKENS", str(cfg.context.active_target_tokens)))
    cfg.context.soft_compact_tokens = int(os.getenv("TERM5_SOFT_COMPACT_TOKENS", str(cfg.context.soft_compact_tokens)))
    cfg.context.hard_input_tokens = int(os.getenv("TERM5_HARD_INPUT_TOKENS", str(cfg.context.hard_input_tokens)))
    cfg.context.safety_tokens = int(os.getenv("TERM5_CONTEXT_SAFETY_TOKENS", str(cfg.context.safety_tokens)))
    cfg.context.recent_full_turns = int(os.getenv("TERM5_RECENT_FULL_TURNS", str(cfg.context.recent_full_turns)))
    cfg.context.tool_inline_chars = int(os.getenv("TERM5_TOOL_INLINE_CHARS", str(cfg.context.tool_inline_chars)))
    cfg.context.tool_preview_chars = int(os.getenv("TERM5_TOOL_PREVIEW_CHARS", str(cfg.context.tool_preview_chars)))
    cfg.context.episode_recall_items = int(os.getenv("TERM5_EPISODE_RECALL_ITEMS", str(cfg.context.episode_recall_items)))
    cfg.context.episode_recall_chars = int(os.getenv("TERM5_EPISODE_RECALL_CHARS", str(cfg.context.episode_recall_chars)))
    cfg.fim.default_max_output_tokens = int(os.getenv("TERM5_FIM_MAX_TOKENS", str(cfg.fim.default_max_output_tokens)))
    cfg.security.allow_network = _env_bool("TERM5_ALLOW_NETWORK", cfg.security.allow_network)
    cfg.security.allow_process_tests = _env_bool("TERM5_ALLOW_TESTS", cfg.security.allow_process_tests)
    cfg.security.allow_git_write = _env_bool("TERM5_ALLOW_GIT_WRITE", cfg.security.allow_git_write)
    cfg.security.allow_git_remote = _env_bool("TERM5_ALLOW_GIT_REMOTE", cfg.security.allow_git_remote)
    cfg.security.allow_git_provider_write = _env_bool("TERM5_ALLOW_GIT_PROVIDER_WRITE", cfg.security.allow_git_provider_write)
    cfg.security.allow_nginx_write = _env_bool("TERM5_ALLOW_NGINX_WRITE", cfg.security.allow_nginx_write)
    cfg.security.allow_tls_issue = _env_bool("TERM5_ALLOW_TLS_ISSUE", cfg.security.allow_tls_issue)
    cfg.ui.web_enabled = _env_bool("TERM5_WEB", cfg.ui.web_enabled)
    cfg.ui.web_port = int(os.getenv("TERM5_WEB_PORT", str(cfg.ui.web_port)))
    cfg.browser.enabled = _env_bool("TERM5_BROWSER", cfg.browser.enabled)
    cfg.browser.headless = _env_bool("TERM5_BROWSER_HEADLESS", cfg.browser.headless)
    cfg.browser.allow_public = _env_bool("TERM5_BROWSER_ALLOW_PUBLIC", cfg.browser.allow_public)
    cfg.browser.allow_interaction = _env_bool("TERM5_BROWSER_INTERACTION", cfg.browser.allow_interaction)
    cfg.browser.timeout_s = int(os.getenv("TERM5_BROWSER_TIMEOUT", str(cfg.browser.timeout_s)))
    cfg.browser.navigation_timeout_s = int(os.getenv("TERM5_BROWSER_NAV_TIMEOUT", str(cfg.browser.navigation_timeout_s)))
    cfg.vision.enabled = _env_bool("TERM5_VISION", cfg.vision.enabled)
    cfg.vision.model = os.getenv("TERM5_VISION_MODEL", cfg.vision.model)
    cfg.vision.max_tokens = int(os.getenv("TERM5_VISION_MAX_TOKENS", str(cfg.vision.max_tokens)))
    cfg.improvement.enabled = _env_bool("TERM5_IMPROVEMENT", cfg.improvement.enabled)
    cfg.improvement.auto_plan = _env_bool("TERM5_IMPROVEMENT_PLAN", cfg.improvement.auto_plan)
    cfg.improvement.plan_tokens = int(os.getenv("TERM5_IMPROVEMENT_PLAN_TOKENS", str(cfg.improvement.plan_tokens)))
    cfg.session.autosave = _env_bool("TERM5_AUTOSAVE", cfg.session.autosave)
    cfg.session.checkpoint_turns = _env_bool("TERM5_CHECKPOINT_TURNS", cfg.session.checkpoint_turns)
    cfg.apps.enabled = _env_bool("TERM5_APPS", cfg.apps.enabled)
    cfg.apps.docker_timeout_s = int(os.getenv("TERM5_DOCKER_TIMEOUT", str(cfg.apps.docker_timeout_s)))
    cfg.apps.wait_timeout_s = int(os.getenv("TERM5_APP_WAIT_TIMEOUT", str(cfg.apps.wait_timeout_s)))
    cfg.apps.open_browser_after_health = _env_bool("TERM5_APP_OPEN_BROWSER", cfg.apps.open_browser_after_health)
    cfg.operations.enabled = _env_bool("TERM5_OPERATIONS", cfg.operations.enabled)
    cfg.operations.command_timeout_s = int(os.getenv("TERM5_OPS_COMMAND_TIMEOUT", str(cfg.operations.command_timeout_s)))
    cfg.operations.health_timeout_s = int(os.getenv("TERM5_DEPLOY_HEALTH_TIMEOUT", str(cfg.operations.health_timeout_s)))
    cfg.operations.require_git = _env_bool("TERM5_DEPLOY_REQUIRE_GIT", cfg.operations.require_git)
    cfg.operations.require_clean_git = _env_bool("TERM5_DEPLOY_REQUIRE_CLEAN_GIT", cfg.operations.require_clean_git)
    cfg.operations.require_dns = _env_bool("TERM5_DEPLOY_REQUIRE_DNS", cfg.operations.require_dns)
    cfg.operations.auto_rollback = _env_bool("TERM5_DEPLOY_AUTO_ROLLBACK", cfg.operations.auto_rollback)
    cfg.operations.nginx_sites_available = os.getenv("TERM5_NGINX_SITES_AVAILABLE", cfg.operations.nginx_sites_available)
    cfg.operations.nginx_sites_enabled = os.getenv("TERM5_NGINX_SITES_ENABLED", cfg.operations.nginx_sites_enabled)
    cfg.operations.nginx_max_body_mb = int(os.getenv("TERM5_NGINX_MAX_BODY_MB", str(cfg.operations.nginx_max_body_mb)))
    cfg.operations.server_disk_warn_percent = int(os.getenv("TERM5_SERVER_DISK_WARN_PERCENT", str(cfg.operations.server_disk_warn_percent)))
    services_env = os.getenv("TERM5_MANAGED_SERVICES")
    if services_env is not None:
        cfg.operations.managed_services = [x.strip() for x in services_env.split(",") if x.strip()]
    cfg.security.allow_server_write = _env_bool("TERM5_ALLOW_SERVER_WRITE", cfg.security.allow_server_write)
    cfg.projects.enabled = _env_bool("TERM5_PROJECTS", cfg.projects.enabled)
    cfg.projects.base_dir = os.getenv("TERM5_PROJECTS_DIR", cfg.projects.base_dir)
    cfg.projects.auto_adopt_apps = _env_bool("TERM5_PROJECT_AUTO_ADOPT_APPS", cfg.projects.auto_adopt_apps)
    cfg.projects.max_projects = int(os.getenv("TERM5_PROJECT_MAX", str(cfg.projects.max_projects)))
    cfg.github.enabled = _env_bool("TERM5_GITHUB", cfg.github.enabled)
    cfg.github.owner = os.getenv("TERM5_GITHUB_OWNER", cfg.github.owner)
    cfg.github.default_private = _env_bool("TERM5_GITHUB_DEFAULT_PRIVATE", cfg.github.default_private)
    cfg.github.max_list = int(os.getenv("TERM5_GITHUB_MAX_LIST", str(cfg.github.max_list)))
    cfg.configuration.enabled = _env_bool("TERM5_CONFIGURATION", cfg.configuration.enabled)
    cfg.configuration.default_environment = os.getenv("TERM5_CONFIGURATION_ENV", cfg.configuration.default_environment)
    cfg.configuration.auto_discover = _env_bool("TERM5_CONFIGURATION_DISCOVER", cfg.configuration.auto_discover)
    cfg.configuration.write_dotenv = _env_bool("TERM5_CONFIGURATION_WRITE_DOTENV", cfg.configuration.write_dotenv)
    cfg.configuration.max_variables = int(os.getenv("TERM5_CONFIGURATION_MAX_VARIABLES", str(cfg.configuration.max_variables)))
    cfg.configuration.scan_max_files = int(os.getenv("TERM5_CONFIGURATION_SCAN_MAX_FILES", str(cfg.configuration.scan_max_files)))
    cfg.configuration.connection_timeout_s = int(os.getenv("TERM5_CONFIGURATION_TEST_TIMEOUT", str(cfg.configuration.connection_timeout_s)))
    cfg.production.enabled = _env_bool("TERM5_PRODUCTION", cfg.production.enabled)
    cfg.production.background_monitor = _env_bool("TERM5_PRODUCTION_MONITOR", cfg.production.background_monitor)
    cfg.production.monitor_interval_s = int(os.getenv("TERM5_PRODUCTION_MONITOR_INTERVAL", str(cfg.production.monitor_interval_s)))
    cfg.production.max_snapshots = int(os.getenv("TERM5_PRODUCTION_MAX_SNAPSHOTS", str(cfg.production.max_snapshots)))
    cfg.production.log_tail_lines = int(os.getenv("TERM5_PRODUCTION_LOG_TAIL", str(cfg.production.log_tail_lines)))
    cfg.autonomy.enabled = _env_bool("TERM6_AUTONOMY", cfg.autonomy.enabled)
    cfg.autonomy.default_human_timeout_s = int(os.getenv("TERM6_HUMAN_TIMEOUT", str(cfg.autonomy.default_human_timeout_s)))
    cfg.autonomy.scheduler_interval_s = int(os.getenv("TERM6_AUTONOMY_INTERVAL", str(cfg.autonomy.scheduler_interval_s)))
    cfg.autonomy.auto_resume = _env_bool("TERM6_AUTO_RESUME", cfg.autonomy.auto_resume)
    cfg.autonomy.max_open_human_tasks = int(os.getenv("TERM6_MAX_HUMAN_TASKS", str(cfg.autonomy.max_open_human_tasks)))
    cfg.agents.enabled = _env_bool("TERM61_AGENTS", cfg.agents.enabled)
    cfg.agents.auto_start = _env_bool("TERM61_AGENTS_AUTO_START", cfg.agents.auto_start)
    cfg.agents.scheduler_interval_s = int(os.getenv("TERM61_AGENT_INTERVAL", str(cfg.agents.scheduler_interval_s)))
    cfg.agents.max_concurrent_owners = int(os.getenv("TERM61_MAX_OWNERS", str(cfg.agents.max_concurrent_owners)))
    cfg.agents.max_concurrent_queries = int(os.getenv("TERM61_MAX_OWNER_QUERIES", str(cfg.agents.max_concurrent_queries)))
    cfg.agents.coordinator_only = _env_bool("TERM62_COORDINATOR_ONLY", cfg.agents.coordinator_only)
    cfg.agents.central_max_iterations = int(os.getenv("TERM62_CENTRAL_MAX_ITERATIONS", str(cfg.agents.central_max_iterations)))
    cfg.agents.lease_seconds = int(os.getenv("TERM61_AGENT_LEASE_SECONDS", str(cfg.agents.lease_seconds)))
    cfg.agents.owner_reasoning = os.getenv("TERM61_OWNER_REASONING", cfg.agents.owner_reasoning)
    cfg.agents.owner_context_target_tokens = int(os.getenv("TERM61_OWNER_CONTEXT_TARGET_TOKENS", str(cfg.agents.owner_context_target_tokens)))
    cfg.agents.owner_max_output_tokens = int(os.getenv("TERM61_OWNER_MAX_OUTPUT_TOKENS", str(cfg.agents.owner_max_output_tokens)))
    cfg.agents.capsule_task_limit = int(os.getenv("TERM61_CAPSULE_TASK_LIMIT", str(cfg.agents.capsule_task_limit)))
    cfg.agents.capsule_message_limit = int(os.getenv("TERM61_CAPSULE_MESSAGE_LIMIT", str(cfg.agents.capsule_message_limit)))
    cfg.agents.deep_query_tokens = int(os.getenv("TERM61_DEEP_QUERY_TOKENS", str(cfg.agents.deep_query_tokens)))
    cfg.creative.enabled = _env_bool("TERM6_CREATIVE", cfg.creative.enabled)
    cfg.creative.openai_enabled = _env_bool("TERM6_OPENAI_IMAGES", cfg.creative.openai_enabled)
    cfg.creative.model = os.getenv("TERM6_OPENAI_IMAGE_MODEL", cfg.creative.model)
    cfg.creative.quality = os.getenv("TERM6_OPENAI_IMAGE_QUALITY", cfg.creative.quality)
    cfg.creative.size = os.getenv("TERM6_OPENAI_IMAGE_SIZE", cfg.creative.size)
    cfg.creative.max_options = int(os.getenv("TERM6_CREATIVE_MAX_OPTIONS", str(cfg.creative.max_options)))
    cfg.creative.api_key_env = os.getenv("TERM6_OPENAI_KEY_ENV", cfg.creative.api_key_env)
    cfg.creative.default_timeout_s = int(os.getenv("TERM6_CREATIVE_TIMEOUT", str(cfg.creative.default_timeout_s)))
    cfg.skills.enabled = _env_bool("TERM5_SKILLS", cfg.skills.enabled)
    cfg.skills.auto_resolve = _env_bool("TERM5_SKILL_AUTO_RESOLVE", cfg.skills.auto_resolve)
    cfg.skills.auto_product_plan = _env_bool("TERM5_PRODUCT_PLAN", cfg.skills.auto_product_plan)
    cfg.skills.critique = _env_bool("TERM5_PRODUCT_CRITIQUE", cfg.skills.critique)
    cfg.skills.catalog_chars = int(os.getenv("TERM5_SKILL_CATALOG_CHARS", str(cfg.skills.catalog_chars)))
    cfg.skills.context_chars = int(os.getenv("TERM5_SKILL_CONTEXT_CHARS", str(cfg.skills.context_chars)))
    cfg.skills.product_plan_tokens = int(os.getenv("TERM5_PRODUCT_PLAN_TOKENS", str(cfg.skills.product_plan_tokens)))
    cfg.pricing.cache_hit_per_million = float(os.getenv("TERM5_PRICE_CACHE_HIT", str(cfg.pricing.cache_hit_per_million)))
    cfg.pricing.cache_miss_per_million = float(os.getenv("TERM5_PRICE_CACHE_MISS", str(cfg.pricing.cache_miss_per_million)))
    cfg.pricing.output_per_million = float(os.getenv("TERM5_PRICE_OUTPUT", str(cfg.pricing.output_per_million)))
    validate_config(cfg)
    return cfg


def validate_config(cfg: Term5Config) -> None:
    if cfg.model.total_context_tokens < 65536:
        raise ValueError("model.total_context_tokens must be >= 65536")
    if cfg.model.context_limit <= 0:
        raise ValueError("model.context_limit must be positive")
    if not 1 <= cfg.model.max_output_tokens <= 384_000:
        raise ValueError("model.max_output_tokens must be between 1 and 384000")
    if not 4096 <= cfg.context.active_target_tokens <= cfg.context.soft_compact_tokens <= cfg.context.hard_input_tokens:
        raise ValueError("context budgets must satisfy target <= soft <= hard")
    if cfg.context.hard_input_tokens >= cfg.model.total_context_tokens:
        raise ValueError("context.hard_input_tokens must be below model.total_context_tokens")
    if not 4096 <= cfg.context.safety_tokens < cfg.model.total_context_tokens // 2:
        raise ValueError("context.safety_tokens is out of range")
    if not 1 <= cfg.context.recent_full_turns <= 20:
        raise ValueError("context.recent_full_turns must be between 1 and 20")
    if not 2000 <= cfg.context.tool_inline_chars <= 200000:
        raise ValueError("context.tool_inline_chars must be between 2000 and 200000")
    if not 500 <= cfg.context.tool_preview_chars <= cfg.context.tool_inline_chars:
        raise ValueError("context.tool_preview_chars must be between 500 and tool_inline_chars")
    if not 1 <= cfg.fim.default_max_output_tokens <= cfg.fim.hard_max_output_tokens <= 4096:
        raise ValueError("FIM token limits must satisfy 1 <= default <= hard <= 4096")
    if not 0 <= cfg.fim.repair_attempts <= 2:
        raise ValueError("fim.repair_attempts must be between 0 and 2")
    if cfg.reasoning.max_parallel_total < 1:
        raise ValueError("reasoning.max_parallel_total must be >= 1")
    if not 1 <= cfg.reasoning.max_parallel_high <= cfg.reasoning.max_parallel_total:
        raise ValueError("max_parallel_high must be between 1 and max_parallel_total")
    if not 5 <= cfg.reasoning.worker_timeout_s <= 1800:
        raise ValueError("reasoning.worker_timeout_s must be between 5 and 1800")
    if not 0 <= cfg.reasoning.worker_retries <= 3:
        raise ValueError("reasoning.worker_retries must be between 0 and 3")
    if cfg.scheduler.max_local_reads < 1:
        raise ValueError("scheduler.max_local_reads must be >= 1")
    if cfg.scheduler.max_tool_iterations < 0:
        raise ValueError("scheduler.max_tool_iterations must be >= 0 (0 = unlimited)")
    if cfg.scheduler.max_turn_seconds < 0:
        raise ValueError("scheduler.max_turn_seconds must be >= 0 (0 = unlimited)")
    if cfg.scheduler.max_turn_seconds and cfg.scheduler.max_turn_seconds < 60:
        raise ValueError("scheduler.max_turn_seconds must be 0 or >= 60")
    if not 3 <= cfg.scheduler.loop_abort_repeats <= 100:
        raise ValueError("scheduler.loop_abort_repeats must be between 3 and 100")
    if not 2 <= cfg.executive.max_workers <= min(16, cfg.reasoning.max_parallel_total):
        raise ValueError("executive.max_workers must be between 2 and min(16, max_parallel_total)")
    if not 512 <= cfg.executive.max_plan_tokens <= 8192:
        raise ValueError("executive.max_plan_tokens must be between 512 and 8192")
    if cfg.executive.inject_evidence_chars < 4000:
        raise ValueError("executive.inject_evidence_chars must be >= 4000")
    if cfg.reasoning.default not in {m.value for m in ReasoningMode}:
        raise ValueError("reasoning.default must be auto, none, low, high, or max")
    if not 0 <= cfg.ui.web_port <= 65535:
        raise ValueError("ui.web_port must be between 0 and 65535")
    if not 5 <= cfg.browser.timeout_s <= 300:
        raise ValueError("browser.timeout_s must be between 5 and 300")
    if not 5 <= cfg.browser.navigation_timeout_s <= 300:
        raise ValueError("browser.navigation_timeout_s must be between 5 and 300")
    if not 1 <= cfg.browser.max_batch_pages <= 64:
        raise ValueError("browser.max_batch_pages must be between 1 and 64")
    if not 512 <= cfg.vision.max_tokens <= 16384:
        raise ValueError("vision.max_tokens must be between 512 and 16384")
    if not 1 <= cfg.vision.max_images <= 8:
        raise ValueError("vision.max_images must be between 1 and 8")
    if not 262144 <= cfg.vision.max_image_bytes <= 50 * 1024 * 1024:
        raise ValueError("vision.max_image_bytes must be between 256KiB and 50MiB")
    if not 1000 <= cfg.improvement.plan_tokens <= 12000:
        raise ValueError("improvement.plan_tokens must be between 1000 and 12000")
    if not 1 <= cfg.improvement.max_surfaces <= 256:
        raise ValueError("improvement.max_surfaces must be between 1 and 256")
    if not 1 <= cfg.improvement.max_batch_files <= 64:
        raise ValueError("improvement.max_batch_files must be between 1 and 64")
    if not 10 <= cfg.apps.docker_timeout_s <= 3600:
        raise ValueError("apps.docker_timeout_s must be between 10 and 3600")
    if not 1 <= cfg.apps.wait_timeout_s <= 1800:
        raise ValueError("apps.wait_timeout_s must be between 1 and 1800")
    if not 1000 <= cfg.skills.catalog_chars <= 32000:
        raise ValueError("skills.catalog_chars must be between 1000 and 32000")
    if not 4000 <= cfg.skills.context_chars <= 64000:
        raise ValueError("skills.context_chars must be between 4000 and 64000")
    if not 1000 <= cfg.skills.product_plan_tokens <= 12000:
        raise ValueError("skills.product_plan_tokens must be between 1000 and 12000")
    if not 5 <= cfg.operations.command_timeout_s <= 3600:
        raise ValueError("operations.command_timeout_s must be between 5 and 3600")
    if not 10 <= cfg.operations.health_timeout_s <= 3600:
        raise ValueError("operations.health_timeout_s must be between 10 and 3600")
    if not 1 <= cfg.operations.nginx_max_body_mb <= 4096:
        raise ValueError("operations.nginx_max_body_mb must be between 1 and 4096")
    if not 50 <= cfg.operations.server_disk_warn_percent <= 99:
        raise ValueError("operations.server_disk_warn_percent must be between 50 and 99")
    if len(cfg.operations.managed_services) > 32:
        raise ValueError("operations.managed_services may contain at most 32 services")
    if not cfg.projects.base_dir or Path(cfg.projects.base_dir).is_absolute() or ".." in Path(cfg.projects.base_dir).parts:
        raise ValueError("projects.base_dir must be a non-empty workspace-relative path")
    if not 1 <= cfg.projects.max_projects <= 2000:
        raise ValueError("projects.max_projects must be between 1 and 2000")
    if not 1 <= cfg.github.max_list <= 500:
        raise ValueError("github.max_list must be between 1 and 500")
    if not cfg.configuration.default_environment or len(cfg.configuration.default_environment) > 64:
        raise ValueError("configuration.default_environment must be 1-64 characters")
    if not 1 <= cfg.configuration.max_variables <= 5000:
        raise ValueError("configuration.max_variables must be between 1 and 5000")
    if not 100 <= cfg.configuration.scan_max_files <= 20000:
        raise ValueError("configuration.scan_max_files must be between 100 and 20000")
    if not 2 <= cfg.configuration.connection_timeout_s <= 60:
        raise ValueError("configuration.connection_timeout_s must be between 2 and 60")
    if not 10 <= cfg.production.monitor_interval_s <= 86400:
        raise ValueError("production.monitor_interval_s must be between 10 and 86400")
    if not 100 <= cfg.production.max_snapshots <= 100000:
        raise ValueError("production.max_snapshots must be between 100 and 100000")
    if not 100 <= cfg.production.log_tail_lines <= 10000:
        raise ValueError("production.log_tail_lines must be between 100 and 10000")
    if not 1 <= cfg.production.probe_timeout_s <= 120:
        raise ValueError("production.probe_timeout_s must be between 1 and 120")
    if not 0.1 <= float(cfg.production.incident_5xx_rate_percent) <= 100.0:
        raise ValueError("production.incident_5xx_rate_percent must be between 0.1 and 100")
    if not 1 <= cfg.production.incident_5xx_min_requests <= 1000000:
        raise ValueError("production.incident_5xx_min_requests must be between 1 and 1000000")
    if not 1 <= cfg.production.verify_max_samples <= 20:
        raise ValueError("production.verify_max_samples must be between 1 and 20")
    if not 0 <= cfg.production.verify_max_interval_s <= 600:
        raise ValueError("production.verify_max_interval_s must be between 0 and 600")
    if not 5 <= cfg.autonomy.default_human_timeout_s <= 86400:
        raise ValueError("autonomy.default_human_timeout_s must be between 5 and 86400")
    if not 1 <= cfg.autonomy.scheduler_interval_s <= 60:
        raise ValueError("autonomy.scheduler_interval_s must be between 1 and 60")
    if not 1 <= cfg.autonomy.max_open_human_tasks <= 5000:
        raise ValueError("autonomy.max_open_human_tasks must be between 1 and 5000")
    if not 1 <= cfg.agents.scheduler_interval_s <= 60:
        raise ValueError("agents.scheduler_interval_s must be between 1 and 60")
    if not 1 <= cfg.agents.max_concurrent_owners <= 32:
        raise ValueError("agents.max_concurrent_owners must be between 1 and 32")
    if not 1 <= cfg.agents.max_concurrent_queries <= 32:
        raise ValueError("agents.max_concurrent_queries must be between 1 and 32")
    if not 2 <= cfg.agents.central_max_iterations <= 20:
        raise ValueError("agents.central_max_iterations must be between 2 and 20")
    if not 60 <= cfg.agents.lease_seconds <= 86400:
        raise ValueError("agents.lease_seconds must be between 60 and 86400")
    if cfg.agents.owner_reasoning not in {m.value for m in ReasoningMode}:
        raise ValueError("agents.owner_reasoning must be auto, none, low, high, or max")
    if not 4096 <= cfg.agents.owner_context_target_tokens <= cfg.context.hard_input_tokens:
        raise ValueError("agents.owner_context_target_tokens must be between 4096 and context.hard_input_tokens")
    if not 1024 <= cfg.agents.owner_max_output_tokens <= 384000:
        raise ValueError("agents.owner_max_output_tokens must be between 1024 and 384000")
    if not 4 <= cfg.agents.capsule_task_limit <= 200:
        raise ValueError("agents.capsule_task_limit must be between 4 and 200")
    if not 4 <= cfg.agents.capsule_message_limit <= 200:
        raise ValueError("agents.capsule_message_limit must be between 4 and 200")
    if not 512 <= cfg.agents.deep_query_tokens <= 32000:
        raise ValueError("agents.deep_query_tokens must be between 512 and 32000")
    if not cfg.creative.model or len(cfg.creative.model) > 200:
        raise ValueError("creative.model must be 1-200 characters")
    if cfg.creative.quality not in {"low", "medium", "high", "xhigh", "max", "auto"}:
        raise ValueError("creative.quality must be low, medium, high, xhigh, max, or auto")
    if not 1 <= cfg.creative.max_options <= 4:
        raise ValueError("creative.max_options must be between 1 and 4")
    if not 5 <= cfg.creative.default_timeout_s <= 86400:
        raise ValueError("creative.default_timeout_s must be between 5 and 86400")
    if not cfg.creative.api_key_env or len(cfg.creative.api_key_env) > 160:
        raise ValueError("creative.api_key_env must be 1-160 characters")
    for name in (cfg.configuration.values_file, cfg.configuration.secret_file):
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("configuration values/secret files must be state-relative paths")
    if min(cfg.pricing.cache_hit_per_million, cfg.pricing.cache_miss_per_million, cfg.pricing.output_per_million) < 0:
        raise ValueError("pricing values cannot be negative")
