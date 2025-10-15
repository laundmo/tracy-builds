# Tracy Builds

Automated builds of [Tracy Profiler](https://github.com/wolfpld/tracy) binaries for Windows, macOS, and Linux.

## How It Works

1. For each Tracy tag, creates a build branch (e.g., `build-v0.12.2`)
2. Fetches Tracy's own workflow files from that tag
3. Extracts the `jobs:` section and combines them
4. Modifies checkout steps to use Tracy repo
5. Runs the combined workflow
6. Creates a GitHub release with all platform binaries