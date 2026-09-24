Write a new README.md from scratch for the repository `$repo_name`. Any existing README may be
outdated; do not rely on it as a source of truth.

These are the readable files in the repository:

<file_tree>
$file_tree
</file_tree>

Work in this order:

1. **Read the manifest and build files** (for example `pyproject.toml`, `package.json`,
   `Cargo.toml`, `go.mod`, `Makefile`, `Dockerfile`, CI workflows) to learn the language,
   dependencies, entry points, and the real install, run, test and lint commands.
2. **Find the entry points**: CLI commands, `main` functions, servers, exported packages. Read
   them to understand the main flow from input to output.
3. **Read the core modules of each major directory**, following the flow from the entry points.
   Use `list_files` with a narrower path when a directory is large. Note each component's
   responsibility, main entry points and notable implementation details.
4. **Then write** the README following the required structure and rules, and return it in the
   `markdown` field.
