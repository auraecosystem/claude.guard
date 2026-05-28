"""Stress tests for the linting and CI infrastructure.

Validates that ESLint, TypeScript, mypy, pylint, codespell, actionlint,
gitleaks, and the SHA-pinning check all work correctly — catching real
issues and not producing false positives on legitimate code.
"""

import json
import subprocess
import shutil

import pytest
import yaml

from tests._helpers import REPO_ROOT

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def eslint_config() -> dict:
    """Parse eslint.config.js by extracting its structure via ESLint itself."""
    result = subprocess.run(
        ["npx", "eslint", "--print-config", ".claude/hooks/lib-hook-io.mjs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"eslint --print-config failed: {result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="session")
def tsconfig() -> dict:
    return json.loads((REPO_ROOT / "tsconfig.json").read_text())


@pytest.fixture(scope="session")
def pyproject() -> dict:
    # Use tomllib on 3.11+, fall back to toml
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


@pytest.fixture(scope="session")
def precommit_config() -> dict:
    return yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())


# ─── ESLint Configuration ──────────────────────────────────────────────────


class TestEslintConfig:
    def test_eslint_config_file_exists(self) -> None:
        assert (REPO_ROOT / "eslint.config.js").exists()

    def test_eslint_passes_on_source_files(self) -> None:
        result = subprocess.run(
            ["npx", "eslint", ".claude/hooks/", "--max-warnings=1"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ESLint errors:\n{result.stdout}"

    @pytest.mark.parametrize(
        "code,expected_rule",
        [
            ('const x = "a";\nif (x == "b") {}\n', "eqeqeq"),
            ("var x = 1;\nexport { x };\n", "no-var"),
            ("const unused = 42;\n", "no-unused-vars"),
        ],
        ids=["strict-equality", "no-var", "unused-vars"],
    )
    def test_eslint_catches_violations(self, code: str, expected_rule: str) -> None:
        """Write bad code into .claude/hooks/ so it matches the ESLint file glob."""
        bad_file = REPO_ROOT / ".claude" / "hooks" / "_test_bad.mjs"
        try:
            bad_file.write_text(code)
            result = subprocess.run(
                ["npx", "eslint", str(bad_file)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode != 0, f"Expected ESLint to fail on:\n{code}"
            assert expected_rule in result.stdout
        finally:
            bad_file.unlink(missing_ok=True)

    def test_eslint_allows_underscore_prefixed_unused_args(self) -> None:
        good_file = REPO_ROOT / ".claude" / "hooks" / "_test_good.mjs"
        try:
            good_file.write_text("export function handler(_req, res) { res.end(); }\n")
            result = subprocess.run(
                ["npx", "eslint", str(good_file)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"ESLint should allow _-prefixed:\n{result.stdout}"
            )
        finally:
            good_file.unlink(missing_ok=True)

    def test_eslint_config_uses_node_globals(self) -> None:
        config_text = (REPO_ROOT / "eslint.config.js").read_text()
        assert "globals.node" in config_text, "ESLint config should spread globals.node"

    def test_eslint_config_enables_recommended(self) -> None:
        config_text = (REPO_ROOT / "eslint.config.js").read_text()
        assert "js.configs.recommended" in config_text


# ─── TypeScript Configuration ──────────────────────────────────────────────


class TestTscConfig:
    def test_tsconfig_exists(self) -> None:
        assert (REPO_ROOT / "tsconfig.json").exists()

    def test_tsc_passes(self) -> None:
        result = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"tsc errors:\n{result.stdout}"

    def test_tsconfig_checks_js(self, tsconfig: dict) -> None:
        opts = tsconfig["compilerOptions"]
        assert opts["allowJs"] is True
        assert opts["checkJs"] is True

    def test_tsconfig_no_emit(self, tsconfig: dict) -> None:
        assert tsconfig["compilerOptions"]["noEmit"] is True

    def test_tsconfig_targets_esm(self, tsconfig: dict) -> None:
        opts = tsconfig["compilerOptions"]
        assert opts["module"] == "NodeNext"
        assert opts["moduleResolution"] == "NodeNext"

    def test_tsconfig_includes_hooks(self, tsconfig: dict) -> None:
        includes = tsconfig.get("include", [])
        assert any(".claude/hooks" in i for i in includes)

    def test_tsconfig_excludes_tests(self, tsconfig: dict) -> None:
        excludes = tsconfig.get("exclude", [])
        assert any("test" in e.lower() for e in excludes)


# ─── Python Static Analysis ────────────────────────────────────────────────


PYTHON_SOURCES = [
    ".claude/hooks/monitor.py",
    ".claude/hooks/redact-secrets.py",
    ".devcontainer/monitor-server.py",
    "bin/lib/vm-progress.py",
]


class TestMypyConfig:
    def test_mypy_config_in_pyproject(self, pyproject: dict) -> None:
        assert "mypy" in pyproject.get("tool", {}), (
            "mypy config missing from pyproject.toml"
        )

    def test_mypy_ignores_missing_imports(self, pyproject: dict) -> None:
        assert pyproject["tool"]["mypy"]["ignore_missing_imports"] is True

    def test_mypy_checks_untyped_defs(self, pyproject: dict) -> None:
        assert pyproject["tool"]["mypy"]["check_untyped_defs"] is True

    def test_mypy_passes_on_sources(self) -> None:
        sources = [str(REPO_ROOT / s) for s in PYTHON_SOURCES]
        result = subprocess.run(
            ["mypy", *sources],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"mypy errors:\n{result.stdout}"


class TestPylintConfig:
    def test_pylint_config_in_pyproject(self, pyproject: dict) -> None:
        assert "pylint" in pyproject.get("tool", {}), (
            "pylint config missing from pyproject.toml"
        )

    def test_pylint_disables_docstring_rules(self, pyproject: dict) -> None:
        disabled = pyproject["tool"]["pylint"]["messages control"]["disable"]
        assert "C0114" in disabled  # missing-module-docstring
        assert "C0116" in disabled  # missing-function-docstring

    @pytest.mark.parametrize("source", PYTHON_SOURCES)
    def test_pylint_passes(self, source: str) -> None:
        result = subprocess.run(
            ["pylint", str(REPO_ROOT / source)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"pylint errors in {source}:\n{result.stdout}"


# ─── Pre-commit Hook Configuration ────────────────────────────────────────


class TestPrecommitConfig:
    def test_precommit_file_exists(self) -> None:
        assert (REPO_ROOT / ".pre-commit-config.yaml").exists()

    @pytest.mark.parametrize(
        "hook_id",
        [
            "actionlint",
            "gitleaks",
            "codespell",
            "shellcheck",
            "shfmt",
            "ruff-check",
            "ruff-format",
            "trailing-whitespace",
            "check-yaml",
            "check-json",
        ],
    )
    def test_hook_defined(self, precommit_config: dict, hook_id: str) -> None:
        all_hooks = []
        for repo in precommit_config["repos"]:
            for hook in repo.get("hooks", []):
                all_hooks.append(hook["id"])
        assert hook_id in all_hooks, (
            f"Hook {hook_id!r} not found in .pre-commit-config.yaml"
        )

    @pytest.mark.parametrize(
        "hook_id",
        ["validate-config", "lint-skills", "check-pinned-actions", "no-future-import"],
    )
    def test_local_hook_defined(self, precommit_config: dict, hook_id: str) -> None:
        local_repos = [r for r in precommit_config["repos"] if r["repo"] == "local"]
        assert local_repos, "No local repo found in pre-commit config"
        local_hooks = [h["id"] for r in local_repos for h in r.get("hooks", [])]
        assert hook_id in local_hooks

    def test_codespell_skips_lockfiles(self, precommit_config: dict) -> None:
        for repo in precommit_config["repos"]:
            for hook in repo.get("hooks", []):
                if hook["id"] == "codespell":
                    args = " ".join(hook.get("args", []))
                    assert "pnpm-lock.yaml" in args
                    assert "uv.lock" in args
                    return
        pytest.fail("codespell hook not found")

    def test_actionlint_version_pinned(self, precommit_config: dict) -> None:
        for repo in precommit_config["repos"]:
            if "actionlint" in repo.get("repo", ""):
                assert repo["rev"].startswith("v")
                assert len(repo["rev"]) > 3  # not just "v1"
                return
        pytest.fail("actionlint repo not found")

    def test_gitleaks_version_pinned(self, precommit_config: dict) -> None:
        for repo in precommit_config["repos"]:
            if "gitleaks" in repo.get("repo", ""):
                assert repo["rev"].startswith("v")
                assert len(repo["rev"]) > 3
                return
        pytest.fail("gitleaks repo not found")


# ─── Codespell ─────────────────────────────────────────────────────────────


class TestCodespell:
    def test_codespell_config_in_pyproject(self, pyproject: dict) -> None:
        assert "codespell" in pyproject.get("tool", {}), "codespell config missing"

    def test_codespell_skips_generated_files(self, pyproject: dict) -> None:
        skip = pyproject["tool"]["codespell"]["skip"]
        assert "node_modules" in skip
        assert "pnpm-lock.yaml" in skip
        assert "uv.lock" in skip
        assert ".mypy_cache" in skip

    def test_codespell_passes_on_repo(self) -> None:
        result = subprocess.run(
            [
                "codespell",
                "--skip=pnpm-lock.yaml,uv.lock,*.pyc,node_modules,coverage,.mypy_cache,.git,.venv",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"codespell found typos:\n{result.stdout}"


# ─── SHA-Pinned Actions (integration with real repo) ───────────────────────


class TestPinnedActionsIntegration:
    def test_all_workflows_are_pinned(self) -> None:
        result = subprocess.run(
            ["bash", ".github/scripts/check-pinned-actions.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Unpinned actions found:\n{result.stdout}"

    def test_all_action_refs_have_version_comment(self) -> None:
        """Every SHA-pinned ref should have a # vX comment for maintainability."""
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        actions_dir = REPO_ROOT / ".github" / "actions"
        missing_comments = []

        for yaml_file in list(workflows_dir.glob("*.yaml")) + list(
            actions_dir.rglob("action.yaml")
        ):
            for i, line in enumerate(yaml_file.read_text().splitlines(), 1):
                if "uses:" not in line:
                    continue
                ref = line.split("uses:")[-1].strip()
                if ref.startswith("./"):
                    continue
                # Has SHA but no version comment?
                if "@" in ref and len(ref.split("@")[-1].split()[0]) == 40:
                    if "#" not in line:
                        missing_comments.append(f"{yaml_file.name}:{i}")

        assert not missing_comments, (
            f"SHA-pinned actions missing version comment: {missing_comments}"
        )


# ─── Package.json Scripts ──────────────────────────────────────────────────


class TestPackageJsonScripts:
    @pytest.fixture(scope="class")
    def package_json(self) -> dict:
        return json.loads((REPO_ROOT / "package.json").read_text())

    def test_lint_script_configured(self, package_json: dict) -> None:
        script = package_json["scripts"]["lint"]
        assert "ERROR" not in script
        assert "eslint" in script

    def test_check_script_configured(self, package_json: dict) -> None:
        script = package_json["scripts"]["check"]
        assert "ERROR" not in script
        assert "tsc" in script

    def test_lint_staged_runs_eslint_on_mjs(self, package_json: dict) -> None:
        mjs_tasks = package_json["lint-staged"].get("*.mjs", [])
        assert any("eslint" in t for t in mjs_tasks), (
            "lint-staged should run eslint on *.mjs"
        )

    def test_lint_staged_runs_prettier_on_mjs(self, package_json: dict) -> None:
        mjs_tasks = package_json["lint-staged"].get("*.mjs", [])
        assert any("prettier" in t for t in mjs_tasks), (
            "lint-staged should run prettier on *.mjs"
        )

    def test_lint_staged_runs_ruff_on_python(self, package_json: dict) -> None:
        py_tasks = package_json["lint-staged"].get("*.py", [])
        assert any("ruff" in t for t in py_tasks), "lint-staged should run ruff on *.py"

    def test_devdeps_include_lint_tools(self, package_json: dict) -> None:
        devdeps = package_json.get("devDependencies", {})
        assert "eslint" in devdeps
        assert "typescript" in devdeps
        assert "@types/node" in devdeps


# ─── CI Workflow Consistency ───────────────────────────────────────────────


class TestCIWorkflows:
    @pytest.fixture(scope="class")
    def workflows(self) -> dict[str, dict]:
        wf_dir = REPO_ROOT / ".github" / "workflows"
        result = {}
        for f in wf_dir.glob("*.yaml"):
            result[f.stem] = yaml.safe_load(f.read_text())
        return result

    def test_lint_workflow_includes_mjs_paths(self, workflows: dict) -> None:
        lint = workflows["lint"]
        # YAML parses `on:` as boolean True
        on_key = True if True in lint else "on"
        pr_paths = lint[on_key]["pull_request"]["paths"]
        assert "**/*.mjs" in pr_paths, "lint.yaml should trigger on .mjs changes"

    def test_lint_workflow_includes_config_paths(self, workflows: dict) -> None:
        lint = workflows["lint"]
        on_key = True if True in lint else "on"
        pr_paths = lint[on_key]["pull_request"]["paths"]
        assert "tsconfig.json" in pr_paths
        assert "eslint.config.js" in pr_paths

    def test_actionlint_workflow_exists(self, workflows: dict) -> None:
        assert "actionlint" in workflows

    def test_actionlint_runs_pinned_check(self, workflows: dict) -> None:
        al = workflows["actionlint"]
        steps = al["jobs"]["actionlint"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("SHA-pinned" in n or "pinned" in n.lower() for n in step_names)

    def test_format_check_runs_codespell(self, workflows: dict) -> None:
        fc = workflows["format-check"]
        steps = fc["jobs"]["format"]["steps"]
        step_runs = [s.get("run", "") for s in steps]
        assert any("codespell" in r for r in step_runs)

    def test_validate_config_runs_mypy(self, workflows: dict) -> None:
        vc = workflows["validate-config"]
        steps = vc["jobs"]["validate"]["steps"]
        step_runs = [s.get("run", "") for s in steps]
        assert any("mypy" in r for r in step_runs)

    def test_validate_config_runs_pylint(self, workflows: dict) -> None:
        vc = workflows["validate-config"]
        steps = vc["jobs"]["validate"]["steps"]
        step_runs = [s.get("run", "") for s in steps]
        assert any("pylint" in r for r in step_runs)

    @pytest.mark.parametrize(
        "workflow_name",
        ["lint", "format-check", "actionlint", "validate-config", "node-tests"],
    )
    def test_workflow_has_timeout(self, workflows: dict, workflow_name: str) -> None:
        wf = workflows[workflow_name]
        for job_name, job in wf.get("jobs", {}).items():
            assert "timeout-minutes" in job, (
                f"{workflow_name}.jobs.{job_name} missing timeout-minutes"
            )

    def test_paths_filters_consistent(self, workflows: dict) -> None:
        """push and pull_request paths should match (CLAUDE.md requirement)."""
        for name, wf in workflows.items():
            on = wf.get("on", {})
            if not isinstance(on, dict):
                continue
            push_paths = None
            pr_paths = None
            if isinstance(on.get("push"), dict):
                push_paths = set(on["push"].get("paths", []))
            if isinstance(on.get("pull_request"), dict):
                pr_paths = set(on["pull_request"].get("paths", []))
            if push_paths is not None and pr_paths is not None:
                assert push_paths == pr_paths, (
                    f"{name}: push paths != pull_request paths.\n"
                    f"  push only: {push_paths - pr_paths}\n"
                    f"  PR only: {pr_paths - push_paths}"
                )
