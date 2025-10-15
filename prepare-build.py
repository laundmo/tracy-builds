#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyyaml",
#     "requests",
# ]
# ///

"""
Prepare Tracy build by creating branch and generating combined workflow.

Usage:
    uv run prepare-build.py <tracy-tag> [--no-push]

Examples:
    python prepare-build.py v0.12.2
    python prepare-build.py v0.11.0 --no-push
"""

import sys
import subprocess
import yaml
import requests
from pathlib import Path
import argparse


def str_presenter(dumper, data):
    if data.count("\n") > 0:
        # Remove any trailing spaces messing out the output.
        block = "\n".join([line.rstrip() for line in data.splitlines()])
        if data.endswith("\n"):
            block += "\n"
        return dumper.represent_scalar("tag:yaml.org,2002:str", block, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, str_presenter)
yaml.representer.SafeRepresenter.add_representer(str, str_presenter)


def run_command(cmd, check=True, capture=False):
    print(f"Running: {' '.join(cmd)}")
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    else:
        result = subprocess.run(cmd, check=check)
        return result.returncode == 0


def fetch_tracy_workflows(tag):
    """Fetch Tracy workflow files from GitHub."""
    print(f"\n=== Fetching Tracy workflows for {tag} ===")

    base_url = (
        f"https://raw.githubusercontent.com/wolfpld/tracy/{tag}/.github/workflows"
    )
    workflows_dir = Path("tracy-workflows")
    workflows_dir.mkdir(exist_ok=True)

    workflows = {
        "build.yml": f"{base_url}/build.yml",
        "linux.yml": f"{base_url}/linux.yml",
    }

    fetched = {}
    for name, url in workflows.items():
        print(f"Fetching {name}...")
        response = requests.get(url)

        if response.status_code == 200:
            workflow_path = workflows_dir / name
            workflow_path.write_text(response.text)
            print(f"  ✓ Saved to {workflow_path}")
            fetched[name] = workflow_path
        else:
            print(f"  ✗ Failed: HTTP {response.status_code}")
            if response.status_code == 404:
                print(f"    (Workflow may not exist for tag {tag})")

    return fetched


def modify_job_checkouts(job_config, tracy_tag):
    """Modify checkout steps in job to use Tracy repo."""
    if "steps" not in job_config:
        return

    for step in job_config["steps"]:
        if "uses" in step and "checkout" in step["uses"]:
            step["with"] = {
                "repository": "wolfpld/tracy",
                "ref": f"${{{{ github.event.inputs.tracy_tag || '{tracy_tag}' }}}}",
            }


def add_artifact_upload(job_config, job_name):
    """Add artifact upload step if not present."""
    if "steps" not in job_config:
        return

    # Check if upload already exists
    has_upload = any(
        "upload-artifact" in str(step.get("uses", "")) for step in job_config["steps"]
    )

    if has_upload:
        return

    # Determine artifact name and path based on job
    os_type = job_config.get("runs-on", "")
    matrix = job_config.get("strategy", {}).get("matrix", {})

    artifact_name = None
    artifact_path = None

    if (
        "windows" in str(os_type).lower()
        or "windows" in str(matrix.get("os", [])).lower()
    ):
        artifact_name = "tracy-windows"
        artifact_path = "**/*.exe"
    elif (
        "macos" in str(os_type).lower() or "macos" in str(matrix.get("os", [])).lower()
    ):
        artifact_name = "tracy-macos"
        artifact_path = "**/Tracy-release\n**/capture-release\n**/csvexport-release\n**/import-chrome-release"
    elif "linux" in job_name.lower() or "container" in job_config:
        artifact_name = "tracy-linux"
        artifact_path = "**/Tracy-release\n**/capture-release\n**/csvexport-release\n**/import-chrome-release"

    if artifact_name:
        print(f"  Adding artifact upload: {artifact_name}")
        job_config["steps"].append(
            {
                "name": "Upload artifacts",
                "uses": "actions/upload-artifact@v4",
                "with": {"name": artifact_name, "path": artifact_path},
            }
        )


def generate_combined_workflow(workflows, tracy_tag):
    """Generate combined workflow from Tracy's workflows."""
    print("\n=== Generating combined workflow ===")

    combined = {
        "name": "Combined Tracy Build",
        "on": {
            "workflow_dispatch": {
                "inputs": {
                    "tracy_tag": {
                        "description": "Tracy tag",
                        "required": False,
                        "type": "string",
                        "default": tracy_tag,
                    }
                }
            }
        },
        "jobs": {},
    }

    # Process build.yml (Windows/macOS)
    if "build.yml" in workflows:
        print("Processing build.yml...")
        with open(workflows["build.yml"], "r") as f:
            build_wf = yaml.safe_load(f)

        if "jobs" in build_wf:
            for job_name, job_config in build_wf["jobs"].items():
                print(f"  Adding job: tracy-{job_name}")
                modify_job_checkouts(job_config, tracy_tag)
                add_artifact_upload(job_config, job_name)
                combined["jobs"][f"tracy-{job_name}"] = job_config

    # Process linux.yml
    if "linux.yml" in workflows:
        print("Processing linux.yml...")
        with open(workflows["linux.yml"], "r") as f:
            linux_wf = yaml.safe_load(f)

        if "jobs" in linux_wf:
            for job_name, job_config in linux_wf["jobs"].items():
                print(f"  Adding job: tracy-linux-{job_name}")
                modify_job_checkouts(job_config, tracy_tag)
                add_artifact_upload(job_config, f"linux-{job_name}")
                combined["jobs"][f"tracy-linux-{job_name}"] = job_config

    # Add release job
    print("Adding release job...")
    all_job_names = list(combined["jobs"].keys())

    with open("./create_release.yml", "r") as f:
        release = yaml.safe_load(f)["create-release"]
    release["needs"] = all_job_names
    combined["jobs"]["create-release"] = release

    # Write combined workflow
    workflow_dir = Path(".github/workflows")
    workflow_dir.mkdir(parents=True, exist_ok=True)

    output_path = workflow_dir / "build-combined.yml"
    with open(output_path, "w") as f:
        yaml.dump(combined, f, default_flow_style=False, sort_keys=False)

    print(f"  ✓ Written to {output_path}")

    return output_path


def commit_and_push(branch, tracy_tag, push=True):
    """Commit changes and optionally push."""
    print("\n=== Committing changes ===")

    # Configure git if needed
    try:
        run_command(["git", "config", "user.name"], capture=True)
    except subprocess.CalledProcessError:
        run_command(["git", "config", "user.name", "github-actions[bot]"])
        run_command(
            [
                "git",
                "config",
                "user.email",
                "github-actions[bot]@users.noreply.github.com",
            ]
        )

    # Add files
    run_command(["git", "add", ".github/workflows/build-combined.yml"])
    run_command(["git", "add", "tracy-workflows/"])

    # Commit
    commit_msg = f"Add combined workflow for {tracy_tag}"
    run_command(
        [
            "git",
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            commit_msg,
        ]
    )

    if push:
        print("\n=== Pushing to remote ===")
        run_command(["git", "push", "origin", branch])
    else:
        print("\n=== Skipping push (--no-push specified) ===")
        print(f"To push manually: git push origin {branch}")


def main():
    parser = argparse.ArgumentParser(
        description="Prepare Tracy build branch and workflow"
    )
    parser.add_argument("tracy_tag", help="Tracy tag to build (e.g., v0.12.2)")
    parser.add_argument(
        "--no-push", action="store_true", help="Do not push to remote (for testing)"
    )
    parser.add_argument(
        "--remote", default="origin", help="Git remote name (default: origin)"
    )

    args = parser.parse_args()

    tracy_tag = args.tracy_tag
    remote = args.remote
    should_push = not args.no_push

    print(f"{'=' * 60}")
    print(f"Preparing build for Tracy {tracy_tag}")
    print(f"{'=' * 60}")

    try:
        run_command(["git", "checkout", "main"], check=False)

        # Step 1: Create build branch
        branch = f"build-{tracy_tag}"

        print(f"\n=== Creating branch: {branch} ===")
        if should_push:
            # Fetch to see if branch exists remotely
            run_command(["git", "fetch", remote], check=False)

            # Check if remote branch exists
            remote_branches = run_command(
                ["git", "ls-remote", "--heads", remote, branch], capture=True
            )

            if remote_branches:
                print(f"Remote branch {branch} exists, deleting...")
                run_command(["git", "push", remote, "--delete", branch], check=False)

        # Check if local branch exists
        local_branches = run_command(["git", "branch", "--list", branch], capture=True)
        if local_branches:
            print(f"Local branch {branch} exists, deleting...")
            run_command(["git", "branch", "-D", branch], check=False)

        # Create new branch
        run_command(["git", "checkout", "-b", branch])

        # Step 2: Fetch Tracy workflows
        workflows = fetch_tracy_workflows(tracy_tag)

        if not workflows:
            print("\n✗ Failed to fetch any workflows")
            return 1

        # Step 3: Generate combined workflow
        combined_path = generate_combined_workflow(workflows, tracy_tag)

        # Step 4: Commit and push
        commit_and_push(branch, tracy_tag, push=should_push)

        print(f"\n{'=' * 60}")
        print("✓ SUCCESS")
        print(f"{'=' * 60}")
        print(f"Branch: {branch}")
        print(f"Combined workflow: {combined_path}")

        if not args.no_push:
            print("\nTo trigger the build:")
            print(
                f"  gh workflow run build-combined.yml --ref {branch} -f tracy_tag={tracy_tag}"
            )

        return 0

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
