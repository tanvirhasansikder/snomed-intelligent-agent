# Applying the audit changes (Windows PowerShell)

These files were produced against the snapshot you uploaded to the Project on 2026-10-16. Apply them on a new branch so your current code stays recoverable.

1. Open PowerShell in your project and check for uncommitted work:

   ```powershell
   cd F:\499project\snomedAgent
   git status
   ```

   If anything is listed, commit it first, or stash it with `git stash push -u -m "before-audit"`.

2. Create a branch:

   ```powershell
   git switch -c audit-repairs-2026-10-17
   ```

3. Unzip and copy the files over your project (replace the zip path if it is elsewhere):

   ```powershell
   Expand-Archive -Path "$HOME\Downloads\snomedAgent-audit-2026-10-17.zip" -DestinationPath "$env:TEMP\snomed-audit" -Force
   Copy-Item -Path "$env:TEMP\snomed-audit\snomedAgent\*" -Destination . -Recurse -Force
   ```

4. Review what changed:

   ```powershell
   git status
   git diff --stat
   ```

   If your local code was newer than the snapshot, those newer edits appear in `git diff` as removed lines. Stop and tell me before continuing. If almost every line of a file shows as changed, that is usually line endings (CRLF vs LF), not content.

5. Install the test dependency and run the tests (no network or database access):

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   .\.venv\Scripts\python.exe -m pytest
   ```

   Expected: `254 passed, 2 skipped, 1 deselected`. The 2 skips need the optional Cypher linter; the deselected test needs a disposable Neo4j database.

6. Start the app and try `Asthma`, `233604007`, and `233604008` (deliberately invalid check digit):

   ```powershell
   streamlit run main.py
   ```

7. To undo everything: `git switch -` then `git branch -D audit-repairs-2026-10-17`.

**Folder name:** the new explanation code is in `explanation/` (the spelling in your upload). If your disk has `explaination/` instead, you will end up with both folders; tell me and I will adapt the code to your folder.

`snomedAgent-audit.patch` shows the same changes as a unified diff for review. It may not apply with `git apply` if your files use CRLF line endings, so copying the files (step 3) is the reliable route.
