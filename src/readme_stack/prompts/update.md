Update the existing README.md of the repository `$repo_name` so that it reflects the code
changes below. This is an edit, not a rewrite.

The current README:

<current_readme>
$current_readme
</current_readme>

Summary of the changes (`git diff --stat`, README.md excluded):

<diff_stat>
$diff_stat
</diff_stat>

The changes (`git diff`, README.md excluded):

<diff_patch>
$diff_patch
</diff_patch>

$truncation_note

Work in this order:

1. **Decide which sections the change affects.** For each changed file, ask whether it alters
   something the README describes or should describe: commands, options, configuration,
   installation, architecture, components, behavior, the project layout. Refactors, tests,
   comments and internal changes that are invisible at the README's level of detail usually
   affect nothing.
2. **Verify with the tools.** The diff shows only fragments; use `read_file` and `list_files`
   to check the current state of the code before you write about it. Do not document
   something based on the diff alone if you can read the file.
3. **Edit only those sections and keep all other text verbatim**: same wording, headings,
   order, formatting and links. Match the README's existing structure and style even where it
   differs from the structure in your instructions; do not reorganize, reformat or "improve"
   unaffected text. The rule to state only verified facts applies to what you add or change;
   do not delete unaffected text just because you did not re-verify it. Remove statements the
   change made false, and add new sections only for genuinely new components or features.

If nothing documentation-relevant changed, return the current README unchanged.

Return the complete updated README (the whole file, not only the edited sections) in the
`markdown` field.
