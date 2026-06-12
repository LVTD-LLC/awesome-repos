from __future__ import annotations

import ast
import configparser
import json
import re
import textwrap
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import defusedxml.ElementTree as ET

MAX_STACK_FILE_BYTES = 384_000
MAX_DEPENDENCY_FILES = 80
MAX_DEPENDENCIES_PER_FILE = 300
MAX_STACK_EVIDENCE_PER_SIGNAL = 8

IGNORED_DIRECTORY_PARTS = {
    ".git",
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "bower_components",
    "build",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "site-packages",
    "target",
    "vendor",
    "venv",
}

PACKAGE_MANAGER_LABELS = {
    "bundler": "Bundler",
    "bun": "Bun",
    "cargo": "Cargo",
    "cmake": "CMake",
    "cocoapods": "CocoaPods",
    "composer": "Composer",
    "conan": "Conan",
    "dart-pub": "Dart Pub",
    "dotnet": ".NET SDK",
    "elixir-mix": "Mix",
    "go-modules": "Go modules",
    "gradle": "Gradle",
    "maven": "Maven",
    "meson": "Meson",
    "npm": "npm",
    "pep517": "PEP 517",
    "pdm": "PDM",
    "pip": "pip",
    "pipenv": "Pipenv",
    "pnpm": "pnpm",
    "poetry": "Poetry",
    "spm": "Swift Package Manager",
    "uv": "uv",
    "vcpkg": "vcpkg",
    "yarn": "Yarn",
}

ECOSYSTEM_LABELS = {
    "c-cpp": "C/C++",
    "dart": "Dart",
    "dotnet": ".NET",
    "elixir": "Elixir",
    "go": "Go",
    "java": "Java/Kotlin",
    "javascript": "JavaScript",
    "php": "PHP",
    "python": "Python",
    "ruby": "Ruby",
    "rust": "Rust",
    "swift": "Swift",
}


@dataclass(frozen=True)
class StackRule:
    slug: str
    label: str
    category: str
    ecosystems: tuple[str, ...]
    dependencies: tuple[str, ...]
    prefixes: tuple[str, ...] = ()


STACK_RULES = (
    StackRule("django", "Django", "web framework", ("python",), ("django",)),
    StackRule(
        "django-rest-framework",
        "Django REST Framework",
        "api framework",
        ("python",),
        ("djangorestframework",),
    ),
    StackRule("fastapi", "FastAPI", "web framework", ("python",), ("fastapi",)),
    StackRule("flask", "Flask", "web framework", ("python",), ("flask",)),
    StackRule("starlette", "Starlette", "web framework", ("python",), ("starlette",)),
    StackRule("tornado", "Tornado", "web framework", ("python",), ("tornado",)),
    StackRule("sanic", "Sanic", "web framework", ("python",), ("sanic",)),
    StackRule("scrapy", "Scrapy", "crawler framework", ("python",), ("scrapy",)),
    StackRule("celery", "Celery", "task queue", ("python",), ("celery",)),
    StackRule("streamlit", "Streamlit", "app framework", ("python",), ("streamlit",)),
    StackRule("gradio", "Gradio", "app framework", ("python",), ("gradio",)),
    StackRule("pydantic-ai", "PydanticAI", "agent framework", ("python",), ("pydantic-ai",)),
    StackRule("langchain", "LangChain", "ai framework", ("python",), ("langchain",)),
    StackRule("llama-index", "LlamaIndex", "ai framework", ("python",), ("llama-index",)),
    StackRule("jupyter", "Jupyter", "notebook", ("python",), ("jupyter", "notebook")),
    StackRule("pytest", "pytest", "test framework", ("python",), ("pytest",)),
    StackRule("react", "React", "frontend framework", ("javascript",), ("react",)),
    StackRule("nextjs", "Next.js", "web framework", ("javascript",), ("next",)),
    StackRule("vue", "Vue", "frontend framework", ("javascript",), ("vue",)),
    StackRule("nuxt", "Nuxt", "web framework", ("javascript",), ("nuxt",)),
    StackRule("svelte", "Svelte", "frontend framework", ("javascript",), ("svelte",)),
    StackRule("sveltekit", "SvelteKit", "web framework", ("javascript",), ("@sveltejs/kit",)),
    StackRule("angular", "Angular", "frontend framework", ("javascript",), ("@angular/core",)),
    StackRule("express", "Express", "web framework", ("javascript",), ("express",)),
    StackRule("fastify", "Fastify", "web framework", ("javascript",), ("fastify",)),
    StackRule("nestjs", "NestJS", "web framework", ("javascript",), ("@nestjs/core",)),
    StackRule("astro", "Astro", "web framework", ("javascript",), ("astro",)),
    StackRule(
        "remix",
        "Remix",
        "web framework",
        ("javascript",),
        ("@remix-run/react", "@remix-run/node", "@remix-run/dev"),
    ),
    StackRule("vite", "Vite", "build tool", ("javascript",), ("vite",)),
    StackRule("electron", "Electron", "desktop framework", ("javascript",), ("electron",)),
    StackRule(
        "react-native", "React Native", "mobile framework", ("javascript",), ("react-native",)
    ),
    StackRule("expo", "Expo", "mobile framework", ("javascript",), ("expo",)),
    StackRule("tailwindcss", "Tailwind CSS", "css framework", ("javascript",), ("tailwindcss",)),
    StackRule("rails", "Ruby on Rails", "web framework", ("ruby",), ("rails",)),
    StackRule("sinatra", "Sinatra", "web framework", ("ruby",), ("sinatra",)),
    StackRule("laravel", "Laravel", "web framework", ("php",), ("laravel/framework",)),
    StackRule(
        "symfony",
        "Symfony",
        "web framework",
        ("php",),
        ("symfony/framework-bundle",),
        ("symfony/",),
    ),
    StackRule("axum", "Axum", "web framework", ("rust",), ("axum",)),
    StackRule("actix-web", "Actix Web", "web framework", ("rust",), ("actix-web",)),
    StackRule("rocket", "Rocket", "web framework", ("rust",), ("rocket",)),
    StackRule("warp", "Warp", "web framework", ("rust",), ("warp",)),
    StackRule("bevy", "Bevy", "game engine", ("rust",), ("bevy",)),
    StackRule("tauri", "Tauri", "desktop framework", ("rust",), ("tauri",)),
    StackRule("gin", "Gin", "web framework", ("go",), ("github.com/gin-gonic/gin",)),
    StackRule("echo", "Echo", "web framework", ("go",), ("github.com/labstack/echo",)),
    StackRule("fiber", "Fiber", "web framework", ("go",), ("github.com/gofiber/fiber",)),
    StackRule("cobra", "Cobra", "cli framework", ("go",), ("github.com/spf13/cobra",)),
    StackRule("grpc-go", "gRPC Go", "rpc framework", ("go",), ("google.golang.org/grpc",)),
    StackRule(
        "spring-boot",
        "Spring Boot",
        "web framework",
        ("java",),
        ("spring-boot-starter",),
        ("spring-boot-starter-", "org.springframework.boot:spring-boot-starter"),
    ),
    StackRule(
        "quarkus", "Quarkus", "web framework", ("java",), ("quarkus-core",), ("io.quarkus:",)
    ),
    StackRule(
        "micronaut",
        "Micronaut",
        "web framework",
        ("java",),
        ("micronaut-core",),
        ("io.micronaut:",),
    ),
    StackRule("ktor", "Ktor", "web framework", ("java",), ("ktor-server-core",), ("io.ktor:",)),
    StackRule(
        "android",
        "Android",
        "mobile framework",
        ("java",),
        ("com.android.application", "com.android.library", "com.android.tools.build:gradle"),
    ),
    StackRule(
        "aspnet-core",
        "ASP.NET Core",
        "web framework",
        ("dotnet",),
        ("microsoft.aspnetcore.app",),
        ("microsoft.aspnetcore.",),
    ),
    StackRule(
        "entity-framework",
        "Entity Framework",
        "orm",
        ("dotnet",),
        ("microsoft.entityframeworkcore",),
        ("microsoft.entityframeworkcore.",),
    ),
    StackRule(
        "avalonia", "Avalonia", "desktop framework", ("dotnet",), ("avalonia",), ("avalonia.",)
    ),
    StackRule("phoenix", "Phoenix", "web framework", ("elixir",), ("phoenix",)),
    StackRule(
        "phoenix-liveview", "Phoenix LiveView", "web framework", ("elixir",), ("phoenix_live_view",)
    ),
    StackRule("ecto", "Ecto", "database toolkit", ("elixir",), ("ecto", "ecto_sql")),
    StackRule("flutter", "Flutter", "mobile framework", ("dart",), ("flutter",)),
    StackRule("dart-shelf", "Shelf", "web framework", ("dart",), ("shelf",)),
    StackRule("vapor", "Vapor", "web framework", ("swift",), ("vapor",)),
    StackRule("qt", "Qt", "ui framework", ("c-cpp",), ("qt", "qtbase")),
)

STACK_LABELS = {rule.slug: rule.label for rule in STACK_RULES}


def dependency_ecosystem_label(slug: str) -> str:
    return ECOSYSTEM_LABELS.get(slug, slug)


def package_manager_label(slug: str) -> str:
    return PACKAGE_MANAGER_LABELS.get(slug, slug)


def stack_label(slug: str) -> str:
    return STACK_LABELS.get(slug, slug)


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def _split_path(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def _path_is_ignored(path: str) -> bool:
    parts = [part.lower() for part in _split_path(path)]
    return any(part in IGNORED_DIRECTORY_PARTS for part in parts[:-1])


def _candidate(
    item: dict,
    *,
    ecosystem: str,
    package_manager: str,
    manifest_kind: str,
    parser: str | None,
    fetch_content: bool = True,
) -> dict:
    return {
        "path": item.get("path") or "",
        "ecosystem": ecosystem,
        "package_manager": package_manager,
        "kind": manifest_kind,
        "parser": parser,
        "fetch_content": fetch_content,
        "size": item.get("size"),
        "url": item.get("url") or "",
    }


DependencyFileSpec = dict[str, str | bool | None]

DEPENDENCY_FILE_SPECS_BY_BASENAME: dict[str, DependencyFileSpec] = {
    "package.json": {
        "ecosystem": "javascript",
        "package_manager": "npm",
        "kind": "manifest",
        "parser": "package_json",
    },
    "package-lock.json": {
        "ecosystem": "javascript",
        "package_manager": "npm",
        "kind": "lockfile",
        "parser": "package_lock_json",
    },
    "npm-shrinkwrap.json": {
        "ecosystem": "javascript",
        "package_manager": "npm",
        "kind": "lockfile",
        "parser": "package_lock_json",
    },
    "pnpm-lock.yaml": {
        "ecosystem": "javascript",
        "package_manager": "pnpm",
        "kind": "lockfile",
        "parser": "pnpm_lock",
    },
    "yarn.lock": {
        "ecosystem": "javascript",
        "package_manager": "yarn",
        "kind": "lockfile",
        "parser": "yarn_lock",
    },
    "bun.lock": {
        "ecosystem": "javascript",
        "package_manager": "bun",
        "kind": "lockfile",
        "parser": "bun_lock",
    },
    "bun.lockb": {
        "ecosystem": "javascript",
        "package_manager": "bun",
        "kind": "lockfile",
        "parser": None,
        "fetch_content": False,
    },
    "pyproject.toml": {
        "ecosystem": "python",
        "package_manager": "pep517",
        "kind": "manifest",
        "parser": "pyproject",
    },
    "requirements.txt": {
        "ecosystem": "python",
        "package_manager": "pip",
        "kind": "manifest",
        "parser": "requirements",
    },
    "constraints.txt": {
        "ecosystem": "python",
        "package_manager": "pip",
        "kind": "manifest",
        "parser": "requirements",
    },
    "pipfile": {
        "ecosystem": "python",
        "package_manager": "pipenv",
        "kind": "manifest",
        "parser": "pipfile",
    },
    "pipfile.lock": {
        "ecosystem": "python",
        "package_manager": "pipenv",
        "kind": "lockfile",
        "parser": "pipfile_lock",
    },
    "poetry.lock": {
        "ecosystem": "python",
        "package_manager": "poetry",
        "kind": "lockfile",
        "parser": "poetry_lock",
    },
    "uv.lock": {
        "ecosystem": "python",
        "package_manager": "uv",
        "kind": "lockfile",
        "parser": "poetry_lock",
    },
    "pdm.lock": {
        "ecosystem": "python",
        "package_manager": "pdm",
        "kind": "lockfile",
        "parser": "poetry_lock",
    },
    "setup.py": {
        "ecosystem": "python",
        "package_manager": "pip",
        "kind": "manifest",
        "parser": "python_setup",
    },
    "setup.cfg": {
        "ecosystem": "python",
        "package_manager": "pip",
        "kind": "manifest",
        "parser": "python_setup",
    },
    "cargo.toml": {
        "ecosystem": "rust",
        "package_manager": "cargo",
        "kind": "manifest",
        "parser": "cargo_toml",
    },
    "cargo.lock": {
        "ecosystem": "rust",
        "package_manager": "cargo",
        "kind": "lockfile",
        "parser": "cargo_lock",
    },
    "go.mod": {
        "ecosystem": "go",
        "package_manager": "go-modules",
        "kind": "manifest",
        "parser": "go_mod",
    },
    "go.sum": {
        "ecosystem": "go",
        "package_manager": "go-modules",
        "kind": "lockfile",
        "parser": "go_sum",
    },
    "gemfile": {
        "ecosystem": "ruby",
        "package_manager": "bundler",
        "kind": "manifest",
        "parser": "gemfile",
    },
    "gems.rb": {
        "ecosystem": "ruby",
        "package_manager": "bundler",
        "kind": "manifest",
        "parser": "gemfile",
    },
    "gemfile.lock": {
        "ecosystem": "ruby",
        "package_manager": "bundler",
        "kind": "lockfile",
        "parser": "gemfile_lock",
    },
    "gems.locked": {
        "ecosystem": "ruby",
        "package_manager": "bundler",
        "kind": "lockfile",
        "parser": "gemfile_lock",
    },
    "composer.json": {
        "ecosystem": "php",
        "package_manager": "composer",
        "kind": "manifest",
        "parser": "composer_json",
    },
    "composer.lock": {
        "ecosystem": "php",
        "package_manager": "composer",
        "kind": "lockfile",
        "parser": "composer_lock",
    },
    "pom.xml": {
        "ecosystem": "java",
        "package_manager": "maven",
        "kind": "manifest",
        "parser": "pom_xml",
    },
    "build.gradle": {
        "ecosystem": "java",
        "package_manager": "gradle",
        "kind": "manifest",
        "parser": "gradle",
    },
    "build.gradle.kts": {
        "ecosystem": "java",
        "package_manager": "gradle",
        "kind": "manifest",
        "parser": "gradle",
    },
    "settings.gradle": {
        "ecosystem": "java",
        "package_manager": "gradle",
        "kind": "workspace",
        "parser": "gradle",
    },
    "settings.gradle.kts": {
        "ecosystem": "java",
        "package_manager": "gradle",
        "kind": "workspace",
        "parser": "gradle",
    },
    "gradle.lockfile": {
        "ecosystem": "java",
        "package_manager": "gradle",
        "kind": "workspace",
        "parser": "gradle",
    },
    "directory.packages.props": {
        "ecosystem": "dotnet",
        "package_manager": "dotnet",
        "kind": "workspace",
        "parser": "dotnet_project",
    },
    "packages.config": {
        "ecosystem": "dotnet",
        "package_manager": "dotnet",
        "kind": "workspace",
        "parser": "dotnet_project",
    },
    "global.json": {
        "ecosystem": "dotnet",
        "package_manager": "dotnet",
        "kind": "workspace",
        "parser": "dotnet_project",
    },
    "mix.exs": {
        "ecosystem": "elixir",
        "package_manager": "elixir-mix",
        "kind": "manifest",
        "parser": "mix_exs",
    },
    "mix.lock": {
        "ecosystem": "elixir",
        "package_manager": "elixir-mix",
        "kind": "lockfile",
        "parser": "mix_lock",
    },
    "pubspec.yaml": {
        "ecosystem": "dart",
        "package_manager": "dart-pub",
        "kind": "manifest",
        "parser": "pubspec",
    },
    "pubspec.lock": {
        "ecosystem": "dart",
        "package_manager": "dart-pub",
        "kind": "lockfile",
        "parser": "pubspec_lock",
    },
    "package.swift": {
        "ecosystem": "swift",
        "package_manager": "spm",
        "kind": "manifest",
        "parser": "package_swift",
    },
    "package.resolved": {
        "ecosystem": "swift",
        "package_manager": "spm",
        "kind": "lockfile",
        "parser": "package_swift",
    },
    "podfile": {
        "ecosystem": "swift",
        "package_manager": "cocoapods",
        "kind": "manifest",
        "parser": "podfile",
    },
    "cmakelists.txt": {
        "ecosystem": "c-cpp",
        "package_manager": "cmake",
        "kind": "manifest",
        "parser": "cmake",
    },
    "conanfile.txt": {
        "ecosystem": "c-cpp",
        "package_manager": "conan",
        "kind": "manifest",
        "parser": "conan",
    },
    "conanfile.py": {
        "ecosystem": "c-cpp",
        "package_manager": "conan",
        "kind": "manifest",
        "parser": "conan",
    },
    "vcpkg.json": {
        "ecosystem": "c-cpp",
        "package_manager": "vcpkg",
        "kind": "manifest",
        "parser": "vcpkg_json",
    },
    "meson.build": {
        "ecosystem": "c-cpp",
        "package_manager": "meson",
        "kind": "manifest",
        "parser": "meson",
    },
}

REQUIREMENTS_FILE_SPEC: DependencyFileSpec = {
    "ecosystem": "python",
    "package_manager": "pip",
    "kind": "manifest",
    "parser": "requirements",
}
RUBY_GEMSPEC_FILE_SPEC: DependencyFileSpec = {
    "ecosystem": "ruby",
    "package_manager": "bundler",
    "kind": "manifest",
    "parser": "gemfile",
}
DOTNET_PROJECT_FILE_SPEC: DependencyFileSpec = {
    "ecosystem": "dotnet",
    "package_manager": "dotnet",
    "kind": "manifest",
    "parser": "dotnet_project",
}
DOTNET_SOLUTION_FILE_SPEC: DependencyFileSpec = {
    "ecosystem": "dotnet",
    "package_manager": "dotnet",
    "kind": "workspace",
    "parser": "dotnet_project",
}


def _candidate_from_spec(item: dict, spec: DependencyFileSpec) -> dict:
    return _candidate(
        item,
        ecosystem=str(spec["ecosystem"]),
        package_manager=str(spec["package_manager"]),
        manifest_kind=str(spec["kind"]),
        parser=spec["parser"] if isinstance(spec["parser"], str) else None,
        fetch_content=bool(spec.get("fetch_content", True)),
    )


def _dependency_file_spec(lower_path: str, basename: str) -> DependencyFileSpec | None:
    if spec := DEPENDENCY_FILE_SPECS_BY_BASENAME.get(basename):
        return spec
    if lower_path.startswith(("requirements/", "requirements-")) and basename.endswith(".txt"):
        return REQUIREMENTS_FILE_SPEC
    if basename.endswith(".gemspec"):
        return RUBY_GEMSPEC_FILE_SPEC
    if basename.endswith((".csproj", ".fsproj", ".vbproj")):
        return DOTNET_PROJECT_FILE_SPEC
    if basename.endswith(".sln"):
        return DOTNET_SOLUTION_FILE_SPEC
    return None


def classify_dependency_file(item: dict) -> dict | None:
    if item.get("type") != "blob":
        return None

    path = (item.get("path") or "").strip("/")
    if not path or _path_is_ignored(path):
        return None

    lower_path = path.lower()
    basename = lower_path.rsplit("/", 1)[-1]
    spec = _dependency_file_spec(lower_path, basename)
    if spec is None:
        return None

    return _candidate_from_spec(item, spec)


def dependency_file_candidates(tree_items: list[dict]) -> list[dict]:
    candidates = [
        candidate
        for item in tree_items
        if (candidate := classify_dependency_file(item)) is not None
    ]
    candidates.sort(
        key=lambda candidate: (
            len(_split_path(candidate["path"])),
            0 if candidate["kind"] == "manifest" else 1,
            candidate["path"].lower(),
        )
    )
    return candidates[:MAX_DEPENDENCY_FILES]


def _clean_dependency_name(value: str, ecosystem: str) -> str:
    value = str(value or "").strip().strip("\"'")
    if not value:
        return ""
    if "#egg=" in value:
        value = value.rsplit("#egg=", 1)[-1]
    value = value.split(";", 1)[0].strip()
    value = re.split(r"\s(?:from|@)\s", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    value = re.split(r"[<>=~!]", value, maxsplit=1)[0].strip()
    if "[" in value:
        value = value.split("[", 1)[0].strip()
    value = value.strip("(),")
    if not value or value.startswith(("-", "http://", "https://", "git+")):
        return ""
    value = value.lower()
    if ecosystem in {"python", "ruby"}:
        value = value.replace("_", "-")
    return value


def _dependency_names(values: Iterable[Any], ecosystem: str) -> list[str]:
    return _unique_sorted(_clean_dependency_name(str(value), ecosystem) for value in values)


def _dependency_result(
    dependencies: Iterable[Any] = (),
    package_managers: Iterable[str] = (),
) -> dict[str, list[str]]:
    return {
        "dependencies": list(dependencies),
        "package_managers": _unique_sorted(package_managers),
    }


def _parse_requirement_line(line: str) -> str:
    line = line.strip()
    if not line or line.startswith("#"):
        return ""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-r ", "--requirement", "-c ", "--constraint", "--")):
        return ""
    if "#egg=" in line:
        return line.rsplit("#egg=", 1)[-1]
    return line


def parse_requirements(content: str) -> dict[str, list[str]]:
    dependencies = [
        _clean_dependency_name(_parse_requirement_line(line), "python")
        for line in content.splitlines()
    ]
    return _dependency_result(_unique_sorted(dependencies))


def parse_package_json(content: str) -> dict[str, list[str]]:
    data = json.loads(content)
    dependencies = []
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        dependencies.extend((data.get(key) or {}).keys())
    package_managers = []
    package_manager = data.get("packageManager") or ""
    if isinstance(package_manager, str) and package_manager:
        package_managers.append(package_manager.split("@", 1)[0].lower())
    return _dependency_result(_dependency_names(dependencies, "javascript"), package_managers)


def parse_package_lock_json(content: str) -> dict[str, list[str]]:
    data = json.loads(content)
    dependencies = set()
    packages = data.get("packages") or {}
    if isinstance(packages, dict):
        for path in packages:
            if path.startswith("node_modules/"):
                dependencies.add(path.removeprefix("node_modules/"))
    legacy_dependencies = data.get("dependencies") or {}
    if isinstance(legacy_dependencies, dict):
        dependencies.update(legacy_dependencies.keys())
    return _dependency_result(_dependency_names(dependencies, "javascript"))


def parse_pyproject(content: str) -> dict[str, list[str]]:
    data = tomllib.loads(content)
    project = data.get("project") or {}
    dependencies = list(project.get("dependencies") or [])
    for optional_dependencies in (project.get("optional-dependencies") or {}).values():
        dependencies.extend(optional_dependencies or [])

    tool = data.get("tool") or {}
    poetry = tool.get("poetry") or {}
    if poetry:
        dependencies.extend((poetry.get("dependencies") or {}).keys())
        dependencies.extend((poetry.get("dev-dependencies") or {}).keys())
        for group in (poetry.get("group") or {}).values():
            dependencies.extend(((group or {}).get("dependencies") or {}).keys())

    build_system = data.get("build-system") or {}
    dependencies.extend(build_system.get("requires") or [])
    for dependency_group in (data.get("dependency-groups") or {}).values():
        dependencies.extend(
            dependency for dependency in dependency_group or [] if isinstance(dependency, str)
        )

    package_managers = []
    if "poetry" in tool:
        package_managers.append("poetry")
    if "uv" in tool or data.get("dependency-groups"):
        package_managers.append("uv")
    if "pdm" in tool:
        package_managers.append("pdm")
    return _dependency_result(_dependency_names(dependencies, "python"), package_managers)


def parse_pipfile(content: str) -> dict[str, list[str]]:
    data = tomllib.loads(content)
    dependencies = []
    dependencies.extend((data.get("packages") or {}).keys())
    dependencies.extend((data.get("dev-packages") or {}).keys())
    return _dependency_result(_dependency_names(dependencies, "python"))


def parse_pipfile_lock(content: str) -> dict[str, list[str]]:
    data = json.loads(content)
    dependencies = []
    dependencies.extend((data.get("default") or {}).keys())
    dependencies.extend((data.get("develop") or {}).keys())
    return _dependency_result(_dependency_names(dependencies, "python"))


def parse_poetry_lock(content: str) -> dict[str, list[str]]:
    data = tomllib.loads(content)
    dependencies = [package.get("name") for package in data.get("package") or []]
    return _dependency_result(_dependency_names(dependencies, "python"))


PYTHON_SETUP_REQUIREMENT_FIELDS = {
    "install_requires",
    "setup_requires",
    "tests_require",
}


def _split_setup_requirements(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [_parse_requirement_line(line) for line in value.splitlines()]


def _parse_setup_cfg_dependencies(content: str) -> list[str]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(textwrap.dedent(content))
    except configparser.Error:
        return []

    dependencies = []
    if parser.has_section("options"):
        for field_name in PYTHON_SETUP_REQUIREMENT_FIELDS:
            if parser.has_option("options", field_name):
                dependencies.extend(_split_setup_requirements(parser.get("options", field_name)))

    if parser.has_section("options.extras_require"):
        for _extra_name, extra_requirements in parser.items("options.extras_require"):
            dependencies.extend(_split_setup_requirements(extra_requirements))

    return dependencies


def _literal_string_values(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        values = []
        for item in node.elts:
            values.extend(_literal_string_values(item))
        return values
    return []


def _literal_extra_require_values(node: ast.AST) -> list[str]:
    if not isinstance(node, ast.Dict):
        return []
    values = []
    for value in node.values:
        values.extend(_literal_string_values(value))
    return values


def _parse_setup_py_dependencies(content: str) -> list[str]:
    try:
        tree = ast.parse(textwrap.dedent(content))
    except SyntaxError:
        return []

    dependencies = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        else:
            function_name = ""
        if function_name != "setup":
            continue

        for keyword in node.keywords:
            if keyword.arg in PYTHON_SETUP_REQUIREMENT_FIELDS:
                dependencies.extend(_literal_string_values(keyword.value))
            elif keyword.arg == "extras_require":
                dependencies.extend(_literal_extra_require_values(keyword.value))
    return dependencies


def parse_python_setup(content: str) -> dict[str, list[str]]:
    dependencies = _parse_setup_cfg_dependencies(content) or _parse_setup_py_dependencies(content)
    return _dependency_result(_dependency_names(dependencies, "python"))


def _toml_dependency_keys(section: Any) -> list[str]:
    if isinstance(section, dict):
        return list(section.keys())
    return []


def parse_cargo_toml(content: str) -> dict[str, list[str]]:
    data = tomllib.loads(content)
    dependencies = []
    for section_name in ("dependencies", "dev-dependencies", "build-dependencies"):
        dependencies.extend(_toml_dependency_keys(data.get(section_name)))
    for target in (data.get("target") or {}).values():
        if isinstance(target, dict):
            for section_name in ("dependencies", "dev-dependencies", "build-dependencies"):
                dependencies.extend(_toml_dependency_keys(target.get(section_name)))
    workspace = data.get("workspace") or {}
    dependencies.extend(_toml_dependency_keys(workspace.get("dependencies")))
    return _dependency_result(_dependency_names(dependencies, "rust"))


def parse_cargo_lock(content: str) -> dict[str, list[str]]:
    data = tomllib.loads(content)
    dependencies = [package.get("name") for package in data.get("package") or []]
    return _dependency_result(_dependency_names(dependencies, "rust"))


def parse_go_mod(content: str) -> dict[str, list[str]]:
    dependencies = []
    in_require_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue
        if stripped.startswith("require "):
            dependencies.append(stripped.removeprefix("require ").split()[0])
        elif in_require_block and stripped:
            dependencies.append(stripped.split()[0])
    return _dependency_result(_dependency_names(dependencies, "go"))


def parse_go_sum(content: str) -> dict[str, list[str]]:
    dependencies = [line.split()[0] for line in content.splitlines() if line.strip()]
    dependencies = [dependency.removesuffix("/go.mod") for dependency in dependencies]
    return _dependency_result(_dependency_names(dependencies, "go"))


def parse_gemfile(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"\bgem\s+['\"]([^'\"]+)['\"]", content)
    dependencies.extend(re.findall(r"\.add_(?:runtime_)?dependency\s+['\"]([^'\"]+)['\"]", content))
    return _dependency_result(_dependency_names(dependencies, "ruby"))


def parse_gemfile_lock(content: str) -> dict[str, list[str]]:
    dependencies = []
    in_specs = False
    for line in content.splitlines():
        if line.strip() == "specs:":
            in_specs = True
            continue
        if in_specs and line and not line.startswith(" "):
            in_specs = False
        if in_specs:
            match = re.match(r"\s{4}([A-Za-z0-9_.-]+)\s", line)
            if match:
                dependencies.append(match.group(1))
    return _dependency_result(_dependency_names(dependencies, "ruby"))


def parse_composer_json(content: str) -> dict[str, list[str]]:
    data = json.loads(content)
    dependencies = []
    dependencies.extend((data.get("require") or {}).keys())
    dependencies.extend((data.get("require-dev") or {}).keys())
    dependencies = [
        dependency
        for dependency in dependencies
        if dependency != "php" and not dependency.startswith("ext-")
    ]
    return _dependency_result(_dependency_names(dependencies, "php"))


def parse_composer_lock(content: str) -> dict[str, list[str]]:
    data = json.loads(content)
    dependencies = []
    for package in (data.get("packages") or []) + (data.get("packages-dev") or []):
        dependencies.append(package.get("name"))
    return _dependency_result(_dependency_names(dependencies, "php"))


def _xml_root(content: str):
    return ET.fromstring(content)


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_pom_xml(content: str) -> dict[str, list[str]]:
    root = _xml_root(content)
    dependencies = []
    for dependency in root.iter():
        if _xml_local_name(dependency.tag) != "dependency":
            continue
        group_id = ""
        artifact_id = ""
        for child in dependency:
            name = _xml_local_name(child.tag)
            if name == "groupId":
                group_id = (child.text or "").strip()
            elif name == "artifactId":
                artifact_id = (child.text or "").strip()
        if artifact_id:
            dependencies.append(artifact_id)
            if group_id:
                dependencies.append(f"{group_id}:{artifact_id}")
    return _dependency_result(_dependency_names(dependencies, "java"))


def parse_gradle(content: str) -> dict[str, list[str]]:
    dependencies = []
    dependencies.extend(
        re.findall(r"['\"]([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+)(?::[^'\"]+)?['\"]", content)
    )
    dependencies.extend(re.findall(r"\bid\s*\(?\s*['\"]([^'\"]+)['\"]", content))
    return _dependency_result(_dependency_names(dependencies, "java"))


def parse_dotnet_project(content: str) -> dict[str, list[str]]:
    dependencies = []
    try:
        root = _xml_root(content)
    except ET.ParseError:
        return _dependency_result()
    for element in root.iter():
        local_name = _xml_local_name(element.tag)
        if local_name in {"PackageReference", "FrameworkReference"}:
            dependency = element.attrib.get("Include") or element.attrib.get("Update")
            if dependency:
                dependencies.append(dependency)
    return _dependency_result(_dependency_names(dependencies, "dotnet"))


def parse_mix_exs(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"\{\s*:([A-Za-z0-9_]+)\s*,", content)
    return _dependency_result(_dependency_names(dependencies, "elixir"))


def parse_mix_lock(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r'"([A-Za-z0-9_]+)"\s*:', content)
    return _dependency_result(_dependency_names(dependencies, "elixir"))


def parse_pubspec(content: str) -> dict[str, list[str]]:
    dependencies = []
    active_section = False
    for line in content.splitlines():
        if re.match(r"^(dependencies|dev_dependencies):\s*$", line):
            active_section = True
            continue
        if active_section and line and not line.startswith(" "):
            active_section = False
        if active_section:
            match = re.match(r"\s{2,}([A-Za-z0-9_-]+):", line)
            if match and match.group(1) != "sdk":
                dependencies.append(match.group(1))
    return _dependency_result(_dependency_names(dependencies, "dart"))


def parse_pubspec_lock(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"^\s{2}([A-Za-z0-9_-]+):\s*$", content, flags=re.MULTILINE)
    return _dependency_result(_dependency_names(dependencies, "dart"))


def parse_package_swift(content: str) -> dict[str, list[str]]:
    dependencies = []
    for value in re.findall(r'(?:url|name):\s*"([^"]+)"', content):
        dependency = value.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        dependencies.append(dependency)
    return _dependency_result(_dependency_names(dependencies, "swift"))


def parse_podfile(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"\bpod\s+['\"]([^'\"]+)['\"]", content)
    return _dependency_result(_dependency_names(dependencies, "swift"))


def parse_cmake(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"\bfind_package\s*\(\s*([A-Za-z0-9_.-]+)", content, re.IGNORECASE)
    return _dependency_result(_dependency_names(dependencies, "c-cpp"))


def parse_conan(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"([A-Za-z0-9_.+-]+)/[0-9][^\s'\"\]]*", content)
    return _dependency_result(_dependency_names(dependencies, "c-cpp"))


def parse_vcpkg_json(content: str) -> dict[str, list[str]]:
    data = json.loads(content)
    dependencies = []
    for dependency in data.get("dependencies") or []:
        if isinstance(dependency, str):
            dependencies.append(dependency)
        elif isinstance(dependency, dict):
            dependencies.append(dependency.get("name"))
    return _dependency_result(_dependency_names(dependencies, "c-cpp"))


def parse_meson(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(r"\bdependency\s*\(\s*['\"]([^'\"]+)['\"]", content)
    return _dependency_result(_dependency_names(dependencies, "c-cpp"))


def parse_pnpm_lock(content: str) -> dict[str, list[str]]:
    dependencies = re.findall(
        r"^\s{2,}([/@A-Za-z0-9_.-][^:\s]*):",
        content,
        flags=re.MULTILINE,
    )
    names = []
    for dependency in dependencies:
        parts = dependency.strip("/").split("/")
        if parts and parts[0].startswith("@") and len(parts) > 1:
            names.append(f"{parts[0]}/{parts[1].split('@', 1)[0]}")
        elif parts:
            names.append(parts[0].split("@", 1)[0])
    return _dependency_result(_dependency_names(names, "javascript"))


def parse_yarn_lock(content: str) -> dict[str, list[str]]:
    names = []
    for line in content.splitlines():
        if not line or line.startswith((" ", "#")) or ":" not in line:
            continue
        spec = line.split(":", 1)[0].strip().strip('"')
        first = spec.split(",", 1)[0].strip().strip('"')
        if first.startswith("@"):
            parts = first.split("@")
            names.append("@".join(parts[:2]))
        else:
            names.append(first.split("@", 1)[0])
    return _dependency_result(_dependency_names(names, "javascript"))


def parse_bun_lock(content: str) -> dict[str, list[str]]:
    try:
        return parse_package_lock_json(content)
    except json.JSONDecodeError:
        return parse_yarn_lock(content)


PARSERS: dict[str, Callable[[str], dict[str, list[str]]]] = {
    "bun_lock": parse_bun_lock,
    "cargo_lock": parse_cargo_lock,
    "cargo_toml": parse_cargo_toml,
    "cmake": parse_cmake,
    "composer_json": parse_composer_json,
    "composer_lock": parse_composer_lock,
    "conan": parse_conan,
    "dotnet_project": parse_dotnet_project,
    "gemfile": parse_gemfile,
    "gemfile_lock": parse_gemfile_lock,
    "go_mod": parse_go_mod,
    "go_sum": parse_go_sum,
    "gradle": parse_gradle,
    "meson": parse_meson,
    "mix_exs": parse_mix_exs,
    "mix_lock": parse_mix_lock,
    "package_json": parse_package_json,
    "package_lock_json": parse_package_lock_json,
    "package_swift": parse_package_swift,
    "pipfile": parse_pipfile,
    "pipfile_lock": parse_pipfile_lock,
    "pnpm_lock": parse_pnpm_lock,
    "podfile": parse_podfile,
    "poetry_lock": parse_poetry_lock,
    "pom_xml": parse_pom_xml,
    "pubspec": parse_pubspec,
    "pubspec_lock": parse_pubspec_lock,
    "pyproject": parse_pyproject,
    "python_setup": parse_python_setup,
    "requirements": parse_requirements,
    "vcpkg_json": parse_vcpkg_json,
    "yarn_lock": parse_yarn_lock,
}


def _matches_rule(dependency: str, rule: StackRule) -> bool:
    return dependency in rule.dependencies or any(
        dependency.startswith(prefix) for prefix in rule.prefixes
    )


def _confidence_for_evidence(evidence: list[dict]) -> str:
    if any(item["kind"] == "manifest" for item in evidence):
        return "high"
    if evidence:
        return "medium"
    return "low"


def _detect_stack_signals(dependencies: list[dict]) -> list[dict]:
    signals_by_slug: dict[str, dict] = {}
    seen_evidence: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for dependency in dependencies:
        name = dependency["name"]
        ecosystem = dependency["ecosystem"]
        for rule in STACK_RULES:
            if ecosystem not in rule.ecosystems or not _matches_rule(name, rule):
                continue
            signal = signals_by_slug.setdefault(
                rule.slug,
                {
                    "slug": rule.slug,
                    "label": rule.label,
                    "category": rule.category,
                    "ecosystem": ecosystem,
                    "confidence": "low",
                    "evidence": [],
                },
            )
            evidence_key = (dependency["path"], name)
            if (
                evidence_key not in seen_evidence[rule.slug]
                and len(signal["evidence"]) < MAX_STACK_EVIDENCE_PER_SIGNAL
            ):
                seen_evidence[rule.slug].add(evidence_key)
                signal["evidence"].append(
                    {
                        "path": dependency["path"],
                        "dependency": name,
                        "kind": dependency["kind"],
                    }
                )
            signal["confidence"] = _confidence_for_evidence(signal["evidence"])

    return sorted(signals_by_slug.values(), key=lambda signal: signal["label"].lower())


def detect_repository_stack(
    tree_items: list[dict],
    *,
    fetch_file_text: Callable[[dict], str],
) -> dict[str, Any]:
    candidates = dependency_file_candidates(tree_items)
    dependency_files = []
    dependency_entries = []
    package_managers = set()
    ecosystems = set()

    for candidate in candidates:
        ecosystems.add(candidate["ecosystem"])
        file_record = {
            "path": candidate["path"],
            "ecosystem": candidate["ecosystem"],
            "package_manager": candidate["package_manager"],
            "kind": candidate["kind"],
            "size": candidate["size"],
            "parsed": False,
            "dependency_count": 0,
            "dependencies": [],
        }
        parser_name = candidate.get("parser")
        parser = PARSERS.get(parser_name or "")
        size = candidate.get("size") or 0
        if not candidate.get("fetch_content") or parser is None:
            package_managers.add(candidate["package_manager"])
            file_record["skipped"] = True
            file_record["skip_reason"] = "No text parser is available for this file type."
            dependency_files.append(file_record)
            continue
        if size and size > MAX_STACK_FILE_BYTES:
            package_managers.add(candidate["package_manager"])
            file_record["skipped"] = True
            file_record["skip_reason"] = (
                f"File is larger than the {MAX_STACK_FILE_BYTES} byte parsing cap."
            )
            dependency_files.append(file_record)
            continue

        try:
            parsed = parser(fetch_file_text(candidate))
        except Exception as exc:  # noqa: BLE001 - one malformed manifest should not block sync
            package_managers.add(candidate["package_manager"])
            file_record["error"] = str(exc)[:300]
            dependency_files.append(file_record)
            continue

        dependencies = _dependency_names(parsed.get("dependencies") or [], candidate["ecosystem"])
        file_record["parsed"] = True
        file_record["dependency_count"] = len(dependencies)
        file_record["dependencies"] = dependencies[:MAX_DEPENDENCIES_PER_FILE]
        dependency_files.append(file_record)
        parsed_package_managers = parsed.get("package_managers") or []
        if parsed_package_managers:
            file_record["package_manager"] = parsed_package_managers[0]
            package_managers.update(parsed_package_managers)
        else:
            package_managers.add(candidate["package_manager"])
        for dependency in dependencies:
            dependency_entries.append(
                {
                    "name": dependency,
                    "ecosystem": candidate["ecosystem"],
                    "path": candidate["path"],
                    "kind": candidate["kind"],
                }
            )

    stack_signals = _detect_stack_signals(dependency_entries)
    return {
        "ok": True,
        "dependency_files": dependency_files,
        "dependency_ecosystems": _unique_sorted(ecosystems),
        "package_managers": _unique_sorted(package_managers),
        "detected_stacks": [signal["slug"] for signal in stack_signals],
        "stack_signals": stack_signals,
    }
