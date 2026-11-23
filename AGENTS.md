# Project instructions

Read `STATE.md` second. Keep verified progress there and unresolved limitations
in `ISSUES.md`; update state last. Do not create a parallel handoff file.

- This is a Windows-only experimental Proteus adapter, not a Proteus replacement.
- Preserve existing user changes. Never edit an original circuit or vendor sample
  to test an operation: use disposable copies and close owned processes.
- Do not commit `work/`, generated Proteus projects, DLLs, SDK headers, credentials
  or per-machine configurations. Public source contains no proprietary SDK files.
- Resolve installation folders from the environment/discovery helpers. Do not
  introduce developer-specific absolute paths. Client examples use `py -3`.
- Import connectivity is not simulation equivalence. Keep missing models,
  substitutions and skips visible in reports. Never silently invent IC behaviour.
- Run meaningful offline tests and, when native behaviour changes, real Proteus
  and stdio integration tests. Record report paths and actual results; a skipped
  test does not validate its feature. Do not change tests to conceal failures.
- GitHub-hosted checks cannot test licensed Proteus simulation. Keep CI claims
  distinct from local integration evidence.
- Delegate only bounded work where it pays for itself; explicitly choose a
  suitable inexpensive worker model. Primary agent owns final review.
