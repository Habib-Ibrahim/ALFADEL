# Uploading ALFADEL to GitHub without the command line

The repository is small enough to upload through the GitHub website.

1. Sign in to GitHub and create a new repository named `ALFADEL`.
2. When creating it, do **not** initialize it with a README, `.gitignore`, or license, because those files are already included here.
3. Extract the `ALFADEL_GITHUB_v0.1.2.zip` package on your computer.
4. Open the new empty GitHub repository.
5. Choose **Add file → Upload files**.
6. Drag the **contents of the extracted `ALFADEL_GITHUB_v0.1.2` folder** into the upload area. Do not upload the ZIP itself as the repository content.
7. Check that the folders (`alfadel_app`, `alfadel_core`, `docs`, `resources`, `scripts`, etc.) are visible in the upload list.
8. Use a commit message such as `Initial public release of ALFADEL v0.1.2` and commit the files.
9. Open the repository and verify that the README and screenshots render correctly.
10. Before creating the GitHub Release, inspect `resources/` and confirm that all `.adz`, `.jsonl`, and reviewed-overlay placeholder files are 0 bytes.

The package also includes `scripts/check_publication_clean.py`, which can be run locally before later releases.
